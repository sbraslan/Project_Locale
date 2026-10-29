#!/usr/bin/env python3
"""Onaylı Türkçe Master -> EN köprü -> 16 dil: güvenli hedef staging.

Sadece staging dosyaları oluşturur. Mevcut hedeflerin üstüne yazmaz.
Kaynak master SHA ve insan onay belgesi yoksa hedef aşaması açılmaz.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import uuid
from pathlib import Path

from tools.locale_guard.locale_guard import decode_text, load_config, relative_txt_paths
from tools.locale_guard.unified_master_first_plan_v1 import (
    checked_file, digest, extract_master_cells,
)
from tools.locale_guard.unified_master_tr_stage_v1 import (
    LocalLibreTranslate, apply_memory_edits,
)
from tools.locale_guard.unified_translation_contract_v1 import (
    TranslationBlocked, cache_key, verify_bridge, verify_target,
)
from tools.locale_guard.unified_qa_cache_v1 import (
    load_cache, validated_bridge, validated_target, write_cache,
)

APPROVAL = "APPROVED_FULL_TR_MASTER"
PHASE = "16-language-target-staging-v1"


def approved_source(root: Path, config: dict, document: Path) -> dict:
    """Kaynak manifesti bütün Master dosyalarının SHA-256 kaydını içermeli."""
    guard = root / ".locale_guard"
    if document.is_symlink() or not document.is_relative_to(guard):
        raise TranslationBlocked("Onay belgesi yalnız .locale_guard içinde olabilir")
    content = json.loads(document.read_text(encoding="utf-8"))
    if (content.get("status") != APPROVAL
            or content.get("human_approved") is not True
            or content.get("all_master_text_reviewed") is not True):
        raise TranslationBlocked("Master bütünüyle Türkçe olarak onaylanmamış")
    expected = content.get("master_files")
    paths = relative_txt_paths(root / config["master_directory"])
    if not isinstance(expected, dict) or set(expected) != paths:
        raise TranslationBlocked("Master dosya kümesi onayla eşleşmiyor")
    for rel in sorted(paths):
        p = checked_file(root, "master", rel)
        if digest(p.read_bytes()) != expected[rel]:
            raise TranslationBlocked("Master onayından sonra dosya değişmiş: " + rel)
    return content


def target_codec(root: Path, config: dict, lang: str, rel: str) -> tuple[str, str]:
    path = checked_file(root, lang, rel)
    if not path.is_file():
        codecs = config["legacy_decodings"].get(lang, [])
        if not codecs:
            raise TranslationBlocked("Dil kodlama ayarı yok: " + lang)
        return codecs[0], ""
    raw = path.read_bytes()
    if b"\x00" in raw:
        raise TranslationBlocked("Hedef dosyada NUL var: " + lang + "/" + rel)
    text, codec, _ = decode_text(
        raw, language=lang,
        legacy_encodings=config["legacy_decodings"].get(lang, []))
    if text.encode(codec, "strict") != raw:
        raise TranslationBlocked("Hedef dosyanın byte roundtrip hatası")
    return codec, digest(raw)


def staged_target(root: Path, config: dict, provider, lang: str, rel: str,
                  cache: dict, budget: list[int]) -> dict:
    path = checked_file(root, "master", rel)
    raw = path.read_bytes()
    text, source_codec, _ = decode_text(
        raw, language="master",
        legacy_encodings=config["legacy_decodings"].get("master", []))
    editable, blocked = extract_master_cells(root, config, rel)
    if blocked:
        raise TranslationBlocked(
            "Master dosyası inceleme engelli: " + rel + ":"
            + ",".join(x["status"] for x in blocked))
    if not editable:
        raise TranslationBlocked("Dosyada çevrilecek kayıt yok: " + rel)

    dest_codec, target_sha = target_codec(root, config, lang, rel)
    requested_code = config["translation_language_codes"][lang]
    edits = []
    for item in editable:
        if budget[0] <= 0:
            raise TranslationBlocked("Pilot çeviri sınırı doldu; staging eksik bırakılmaz")
        budget[0] -= 1
        tr = item["source"]
        bridge_key = cache_key(tr, "tr", "en", "contract-v1")
        bridge = validated_bridge(cache, bridge_key, tr)
        if bridge is None:
            bridge = verify_bridge(provider, tr)
            cache[bridge_key] = bridge
        if bridge["status"] != "TEKNIK_ADAY" or bridge["source_tr_sha256"] != digest(tr.encode("utf-8")):
            raise TranslationBlocked("Türkçe İngilizce köprü anlamsal inceleme istiyor")
        english = bridge["english"]

        identity = cache_key(english + "\x00" + tr, "en", requested_code,
                             "contract-v1")
        candidate = validated_target(
            cache, identity, tr, english, lang, dest_codec)
        if candidate is None:
            candidate = verify_target(
                provider, tr, english, lang, requested_code, dest_codec)
            cache[identity] = candidate
        if (candidate["status"] != "TEKNIK_ADAY" or
                candidate["source_tr_sha256"] != digest(tr.encode("utf-8"))):
            raise TranslationBlocked("Hedef çeviri İngilizce geri kontrolünden geçemedi")
        edits.append({
            **{k: item[k] for k in ("key", "column", "line") if k in item},
            "source": tr, "translated": candidate["text"],
        })

    spec = config.get("parser_registry", {}).get(rel)
    content = apply_memory_edits(text, edits, spec)
    try:
        target_raw = content.encode(dest_codec, "strict")
        if target_raw.decode(dest_codec, "strict") != content:
            raise TranslationBlocked("Hedef kodlama bayt yuvarlaması hatalı")
    except (UnicodeError, LookupError) as exc:
        raise TranslationBlocked(
            "Hedef kodlamayla kayıpsız yazılamıyor: " + lang + "/" + rel
        ) from exc
    return {
        "language": lang, "path": rel, "source_master_sha256": digest(raw),
        "existing_target_sha256": target_sha or None,
        "staged_sha256": digest(target_raw), "encoding": dest_codec,
        "translated_cells": len(edits), "bytes": target_raw,
        "human_approved": False, "live_apply_allowed": False,
    }


def build_staging(root: Path, config: dict, provider,
                  source_manifest: Path, *, max_cells: int = 50,
                  cache: dict | None = None) -> dict:
    approved_source(root, config, source_manifest)
    if max_cells < 1 or max_cells > 100000:
        raise RuntimeError("Geçersiz işlem bütçesi")
    budget = [max_cells]
    if cache is None:
        cache = {}
    ready = []
    holds = []
    source_files = sorted(relative_txt_paths(root / "master"))
    for lang in config["languages"]:
        for rel in source_files:
            # insult ve parser-kilitli dosyalar kendi ham baytlarıyla kalır.
            if rel in ("insult.txt", "monster_card.txt", "skilldesc.txt"):
                continue
            if budget[0] == 0:
                holds.append({"lang": lang, "path": rel, "reason": "PILOT_LIMIT"})
                continue
            try:
                result = staged_target(root, config, provider, lang, rel, cache, budget)
                ready.append(result)
            except (RuntimeError, UnicodeError, ValueError, KeyError) as exc:
                holds.append({"lang": lang, "path": rel, "reason": str(exc)})
    return {
        "source_manifest": str(source_manifest.relative_to(root)),
        "files": ready, "blocked_files": holds,
        "cache_entries": len(cache),
        "cells_remaining_budget": budget[0],
        "live_apply_allowed": False,
        "all_16_languages_ready": False,
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Master/TR onayı sonrası 16 dil staging")
    p.add_argument("--root", default=".")
    p.add_argument("--source-manifest", required=True)
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--service", choices=("off", "local"), default="off")
    args = p.parse_args()
    root = Path(args.root).resolve()
    try:
        if root.name != "Project_Locale":
            raise TranslationBlocked("Project_Locale dışında işlem yasak")
        guard = root / ".locale_guard"
        if not guard.is_dir() or guard.is_symlink():
            raise TranslationBlocked(".locale_guard geçersiz")
        if args.service != "local":
            print("[BİLGİ] Yerel çeviri servisi kapalı. Canlı işlem yok.")
            return 0
        manifest_path = (root / args.source_manifest).resolve()
        if not manifest_path.is_relative_to(guard) or manifest_path.is_symlink():
            raise TranslationBlocked("Onay belgesi güvenli klasör dışında")
        conf = load_config(root / "tools/locale_guard/config.json")
        qa_cache = load_cache(guard)
        result = build_staging(
            root, conf, LocalLibreTranslate(), manifest_path,
            max_cells=args.limit, cache=qa_cache)
        identifier = (dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
                      + "_" + uuid.uuid4().hex[:8])
        folder = guard / ("hedef_diller_staging_" + identifier)
        folder.mkdir(mode=0o700)
        manifest = dict(result)
        manifest["files"] = []
        for info in result["files"]:
            data = info["bytes"]
            path = folder / "staging" / info["language"] / info["path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            manifest["files"].append({k: v for k, v in info.items() if k != "bytes"})
        with (folder / "manifest.json").open("x", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        # Onay yerine geçmeyen QA aday cache'i; kaynak hash ile yeniden doğrulanır.
        write_cache(guard, qa_cache)
        print("TAMAM: Staging dosyası:", len(result["files"]))
        print("İnceleme/engel:", len(result["blocked_files"]))
        print("Benzersiz önbellek girdisi:", result["cache_entries"])
        print("16 dil canlı senkronizasyonu: HAYIR")
        print("Master ve dil klasörleri değiştirilmedi.")
        return 0
    except (RuntimeError, OSError, ValueError, UnicodeError, KeyError) as exc:
        print("[ENGEL] 16 dil staging çalıştırılamadı:", exc)
        print("Canlı uygulama yapılmadı.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
