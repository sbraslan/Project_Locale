#!/usr/bin/env python3
"""Yalnız Master'daki açık EN/DE metinlerin Türkçe staging önizlemesini oluşturur.

Teknik token ve orijinal codec/EOL korunur. Türkçe metinler otomatik
uygulanmaz. Belirsiz master içeriği ayrıca raporlanır.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import urllib.error
import urllib.request
import uuid
from collections import Counter, defaultdict
from pathlib import Path

from tools.locale_guard.locale_guard import (
    decode_text, load_config, protected_tokens, layout_tokens,
)
from tools.locale_guard.unified_master_first_plan_v1 import (
    build_plan, checked_file, digest,
)
from tools.locale_guard.unified_translation_contract_v1 import (
    TranslationBlocked, segment_translate, similarity, technical_contract,
    verify_bridge,
)

LOCAL_ONLY = "http://127.0.0.1:5000/translate"


class LocalLibreTranslate:
    def __init__(self, url=LOCAL_ONLY):
        if url != LOCAL_ONLY:
            raise TranslationBlocked("Yalnız yerel çeviri servisi yetkilidir")
        self.url = url

    def translate(self, text: str, source: str, target: str) -> str:
        if source == target:
            return text
        payload = json.dumps({"q": text, "source": source,
                              "target": target, "format": "text"},
                             ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.url, data=payload, method="POST",
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise TranslationBlocked(
                "Yerel LibreTranslate HTTP " + str(exc.code)
                + " | " + str(exc.reason)
                + " | Servis dil modeli veya istek ayarlarını kontrol edin"
            ) from exc
        except urllib.error.URLError as exc:
            raise TranslationBlocked(
                "Yerel LibreTranslate bağlantısı kurulamadı: "
                + str(exc.reason) + " | 127.0.0.1:5000 kontrol edin"
            ) from exc
        except (ValueError, OSError) as exc:
            raise TranslationBlocked(
                "Yerel LibreTranslate cevabı JSON/UTF-8 olarak çözümlenemedi: "
                + type(exc).__name__
            ) from exc
        translated = result.get("translatedText")
        if not isinstance(translated, str) or not translated.strip():
            raise TranslationBlocked("Yerel çeviri servisi boş metin döndürdü")
        return translated


def parse_tsv_line_positions(text: str, spec: dict) -> dict[str, int]:
    """Orijinal keyed_tsv_rows ile aynı anahtar/occurrence kurallarını kullanır."""
    positions = {}
    occurrence = Counter()
    logical = 0
    excluded = set(spec.get("key_exclude_columns", []))
    keys = spec.get("key_columns", [0])
    head_count = int(spec.get("header_rows", 0))
    for index, raw in enumerate(text.splitlines(keepends=True)):
        line = raw.rstrip("\r\n")
        if not line.strip() or "\t" not in line or line.lstrip().startswith("#"):
            continue
        if logical < head_count:
            logical += 1
            continue
        logical += 1
        cols = line.split("\t")
        indices = ([i for i in range(len(cols)) if i not in excluded]
                   if excluded else keys)
        if not indices or any(i >= len(cols) for i in indices):
            continue
        base = "|".join(cols[i] for i in indices)
        if spec.get("duplicate_policy", "error") == "occurrence":
            key = f"{base}@@{occurrence[base]}"
            occurrence[base] += 1
        else:
            if base in positions:
                raise TranslationBlocked("Tekrarlı TSV anahtarı: " + base)
            key = base
        positions[key] = index
    return positions


def apply_memory_edits(text: str, changes: list[dict], spec: dict | None) -> str:
    """Belirli satır/sütunu RAM'de değiştir; hiçbir yazma işlemi yapma."""
    lines = text.splitlines(keepends=True)
    positions = parse_tsv_line_positions(text, spec) if spec else {}
    occupied = set()
    for change in changes:
        idx = (positions[change["key"]] if spec else change["line"] - 1)
        col = change.get("column")
        loc = (idx, col)
        if loc in occupied:
            raise TranslationBlocked("Tekrarlı staging hedefi: " + str(loc))
        occupied.add(loc)
        original = lines[idx]
        body = original.rstrip("\r\n")
        ending = original[len(body):]
        if spec is not None:
            cells = body.split("\t")
            if col is None or col >= len(cells) or cells[col] != change["source"]:
                raise TranslationBlocked("Staging TSV kaynak drift: " + str(loc))
            cells[col] = change["translated"]
            result = "\t".join(cells)
        else:
            if body != change["source"]:
                raise TranslationBlocked("Staging wiki satır drift: " + str(loc))
            result = change["translated"]
        technical_contract(body if spec is None else change["source"],
                           result if spec is None else change["translated"])
        lines[idx] = result + ending
    if not changes:
        return text
    result = "".join(lines)
    if (len(result.splitlines(keepends=True)) != len(lines) or
            result.count("\t") != text.count("\t")):
        raise TranslationBlocked("Staging yapısı değişmiş")
    return result


def stage_master(root: Path, config: dict, provider, *,
                 limit: int = 50) -> dict:
    if limit < 1 or limit > 10000:
        raise RuntimeError("Çeviri sınırı 1-10000 aralığında olmalı")
    plan = build_plan(root, config)
    candidates = [task for task in plan["master_translation_tasks"]
                  if task["status"] == "MASTER_TURKCEYE_CEVIR_BEKLIYOR"]
    changes = []
    blocked = []
    per_file = defaultdict(list)
    for task in candidates[:limit]:
        try:
            text = task["source"]
            translated = segment_translate(
                provider, text, task["detected_language"], "tr")
            technical_contract(text, translated)
            back_source = segment_translate(
                provider, translated, "tr", task["detected_language"])
            source_score = similarity(text, back_source)
            bridge = verify_bridge(provider, translated)
            master_path = checked_file(root, "master", task["path"])
            raw = master_path.read_bytes()
            decoded, codec, _ = decode_text(
                raw, language="master",
                legacy_encodings=config["legacy_decodings"].get("master", []))
            if translated.encode(codec, "strict").decode(codec) != translated:
                raise TranslationBlocked("Master codec roundtrip uyuşmaz")
            if source_score < 0.72 or bridge["status"] != "TEKNIK_ADAY":
                blocked.append({
                    "path": task["path"], "source": text,
                    "translated": translated, "status": "ANLAMSAL_INCELEME",
                    "back_source": back_source, "source_score": source_score,
                    "bridge": bridge})
                continue
            item = {**task, "translated": translated,
                    "back_source": back_source,
                    "source_score": round(source_score, 4),
                    "bridge": bridge, "status": "INSAN_ONAYI_BEKLIYOR",
                    "approved": False}
            changes.append(item)
            per_file[task["path"]].append(item)
        except (TranslationBlocked, UnicodeError, KeyError, ValueError,
                RuntimeError) as exc:
            blocked.append({"path": task["path"], "source": task["source"],
                            "status": "GUVENLIK_ENGELI", "reason": str(exc)})

    staging = {}
    for rel, edits in per_file.items():
        path = checked_file(root, "master", rel)
        raw = path.read_bytes()
        text, codec, bom = decode_text(
            raw, language="master",
            legacy_encodings=config["legacy_decodings"].get("master", []))
        spec = config.get("parser_registry", {}).get(rel)
        after = apply_memory_edits(text, edits, spec)
        new_raw = after.encode(codec, "strict")
        if new_raw.decode(codec, "strict") != after:
            raise TranslationBlocked("Staging kodlaması kayıpsız değil: " + rel)
        staging[rel] = {
            "old_sha256": digest(raw), "new_sha256": digest(new_raw),
            "codec": codec, "bom": bom, "bytes": new_raw,
        }
    return {
        "plan": plan, "changes": changes, "blocked": blocked,
        "queued_not_attempted": max(0, len(candidates) - limit),
        "staged_files": staging, "live_apply": False,
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Master EN/DE -> Türkçe güvenli staging")
    p.add_argument("--root", default=".")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--service", choices=("off", "local"), default="off")
    args = p.parse_args()
    root = Path(args.root).resolve()
    try:
        if root.name != "Project_Locale":
            raise RuntimeError("Yalnız Project_Locale klasöründe çalışır")
        config = load_config(root / "tools/locale_guard/config.json")
        guard = root / ".locale_guard"
        if not guard.is_dir() or guard.is_symlink():
            raise RuntimeError("Güvenli .locale_guard dizini yok")
        if args.service == "off":
            print("[BİLGİ] Çeviri servisi kapalı; hiçbir canlı işlem yapılmayacak.")
            print("Yerel servisle denemek için --service local seçilebilir.")
            return 0
        from tools.locale_guard.translation_service_preflight_v1 import ensure_service
        ensure_service(root, required=("de", "en", "tr"))
        result = stage_master(root, config, LocalLibreTranslate(), limit=args.limit)
        uid = (dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
               + "_" + uuid.uuid4().hex[:8])
        destination = guard / ("master_turkce_staging_" + uid)
        destination.mkdir(mode=0o700)
        output = {}
        for rel, info in result["staged_files"].items():
            path = destination / "staging" / "master" / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as f:
                f.write(info["bytes"])
                f.flush()
                os.fsync(f.fileno())
            output[rel] = {k: v for k, v in info.items() if k != "bytes"}
        manifest = {
            "engine": "master-translate-staging-v1",
            "files": output,
            "changes": result["changes"],
            "blocked": result["blocked"],
            "queued_not_attempted": result["queued_not_attempted"],
            "master_review_complete": False,
            "human_approved": False, "live_apply_allowed": False,
            "target_language_apply_allowed": False,
        }
        with (destination / "manifest.json").open("x", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        print("[HAZIR] Türkçe master staging:", destination.relative_to(root))
        print("Önizleme adayı:", len(result["changes"]))
        print("Manuel inceleme/engel:", len(result["blocked"]))
        print("Çevrilecek bekleyen:", result["queued_not_attempted"])
        print("Canlı master ve 16 dil değiştirilmedi.")
        return 0
    except (OSError, RuntimeError, UnicodeError, KeyError, ValueError) as exc:
        print("[ENGEL] Türkçe staging durduruldu:", exc)
        print("Canlı master ve dil dosyalarına yazılmadı.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
