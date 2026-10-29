#!/usr/bin/env python3
"""16 dil ve Türkçe master için salt-okunur tüm TXT çeviri/senkron planı.

Canlı dosya yazmaz. Sadece '.locale_guard' altına yeni, benzersiz
ve yeniden yazılmayan JSON raporu kaydeder. Öncelik: master -> Türkçe;
ancak master onayı sonrası 16 dil için İngilizce köprü açılabilir.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import uuid
from collections import Counter
from pathlib import Path

from tools.locale_guard.incremental_scan_v1 import (
    FileContentError, parse_rows, read_file, safe_path,
)
from tools.locale_guard.locale_guard import (
    BRACKET_DIRECTIVE_RE, METIN_PIPE_RE, load_config, protected_tokens,
    relative_txt_paths, suspicious_foreign_language,
)
from tools.locale_guard.translation_queue_preview_v1 import editable_columns
from tools.locale_guard.unified_translation_contract_v1 import ALL_BRACKET_DIRECTIVE

ENGINE = "master-first-unified-v1"
SPECIAL = {"insult.txt", "skilldesc.txt", "monster_card.txt"}
# Gerçek GitHub Master örneklerinde denetlenen, sekmesiz serbest metinler.
# Patchnotes, ini/proto ve bilinmeyen TXT dosyaları özellikle DAHİL DEĞİL.
FREEFORM_MASTER_FILES = frozenset({
    "attendance_desc.txt",
    "black_and_white_desc.txt",
    "catchking_event_desc.txt",
    "catchking_event_simple_desc.txt",
    "desc_battle_field.txt",
    "empiredesc_a.txt", "empiredesc_b.txt", "empiredesc_c.txt",
    "findm_event_desc.txt", "fish_event_desc.txt",
    "jobdesc_assassin.txt", "jobdesc_shaman.txt", "jobdesc_sura.txt",
    "jobdesc_warrior.txt", "jobdesc_wolfman.txt",
    "mini_game_okey_desc.txt", "pet_primium_feedstuff_desc.txt",
    "wonexchange_desc.txt",
})
FORBIDDEN_SOURCE = ("\ufffd", "\x00")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def checked_file(root: Path, language: str, rel: str) -> Path:
    path = root / language / safe_path(rel)
    cursor = path
    while cursor != root:
        if cursor.is_symlink():
            raise RuntimeError("Sembolik bağlantı tespit edildi: " + str(path))
        cursor = cursor.parent
    return path


def translation_surface(line: str) -> str:
    result = ALL_BRACKET_DIRECTIVE.sub(" ", line)
    result = BRACKET_DIRECTIVE_RE.sub(" ", result)
    return METIN_PIPE_RE.sub(" ", result).strip()


def is_candidate(line: str) -> bool:
    return bool(translation_surface(line)) and any(
        letter.isalpha() for letter in translation_surface(line)
    )


def extract_master_cells(root: Path, config: dict, rel: str) -> tuple[list[dict], list[dict]]:
    """Kayıtlı TSV veya wiki serbest metin; bilinmeyen biçimi asla tahmin etme."""
    blockers = []
    rows = []
    if rel in SPECIAL or Path(rel).name in SPECIAL:
        return rows, [{"path": rel, "status": "OZEL_DOSYA_BAYT_KORUMA"}]
    path = checked_file(root, config["master_directory"], rel)
    raw = path.read_bytes()
    try:
        text, file_hash = read_file(root, config["master_directory"], rel, config)
    except FileContentError as exc:
        return rows, [{"path": rel, "status": "KAYNAK_KODLAMA_ENGELI", "detail": str(exc)}]
    spec = config.get("parser_registry", {}).get(rel)
    allowed_freeform = rel.startswith("wiki/") or rel in FREEFORM_MASTER_FILES
    if spec is None and not allowed_freeform:
        return rows, [{"path": rel, "status": "KAYITSIZ_DOSYA_PARSER_INCELEMESI"}]
    if spec is None and "\t" in text:
        return rows, [{"path": rel, "status": "SERBEST_METINDE_TSV_TAB_ENGELI"}]
    if spec is not None:
        if spec.get("kind") != "keyed_tsv" or spec.get("token_columns") == "disabled":
            return rows, [{"path": rel, "status": "YAPISAL_DOSYA_CEVIRI_YASAK"}]
        try:
            entries = parse_rows(text, spec, rel, "master")
        except RuntimeError as exc:
            return rows, [{"path": rel, "status": "ANAHTAR_PARSER_ENGELI", "detail": str(exc)}]
        for key, columns in entries.items():
            try:
                selected = editable_columns(spec, len(columns))
            except RuntimeError as exc:
                blockers.append({"path": rel, "key": key, "status": "SUTUN_SOZLESMESI", "detail": str(exc)})
                continue
            for col in selected:
                original = columns[col]
                if is_candidate(original):
                    rows.append({
                        "path": rel, "mode": "keyed_tsv", "key": key,
                        "column": col, "source": original,
                        "master_file_sha256": file_hash,
                        "master_cell_sha256": digest(original.encode("utf-8")),
                        "protected_tokens": protected_tokens(original),
                    })
    else:
        # Wiki serbest metin satırları: metnin haricindeki directive sabit kalır.
        for line_num, original in enumerate(text.splitlines(), 1):
            if is_candidate(original):
                rows.append({
                    "path": rel, "mode": "wiki_line", "line": line_num,
                    "source": original, "master_file_sha256": file_hash,
                    "master_cell_sha256": digest(original.encode("utf-8")),
                    "protected_tokens": protected_tokens(original),
                })
    return rows, blockers


def source_classification(cell: dict) -> dict:
    text = cell["source"]
    lang, evidence = suspicious_foreign_language(translation_surface(text))
    # Güçlü kanıt dışında kesin dil/çeviri kararı vermiyoruz.
    status = (
        "MASTER_TURKCEYE_CEVIR_BEKLIYOR" if lang in ("en", "de")
        else "MASTER_KAYNAK_DIL_INCELEMESI"
    )
    return {**cell, "detected_language": lang or "belirsiz",
            "language_evidence": evidence, "status": status,
            "target_language": "tr", "live_apply_allowed": False}


def build_plan(root: Path, config: dict) -> dict:
    master_dir = config["master_directory"]
    langs = list(config["languages"])
    if len(langs) != 16 or set(langs) != {
        "ae", "cz", "de", "dk", "en", "es", "fr", "gr",
        "hu", "it", "nl", "pl", "pt", "ro", "ru", "tr",
    }:
        raise RuntimeError("16 dilin beklenen listesiyle yapılandırma uyuşmuyor")
    if master_dir != "master" or not (root / "master").is_dir():
        raise RuntimeError("master/ kaynağı bulunamadı")
    for lang in langs:
        folder = root / lang
        if not folder.is_dir() or folder.is_symlink():
            raise RuntimeError("Hedef dil klasörü geçersiz: " + lang)

    master_files = relative_txt_paths(root / master_dir)
    tasks = []
    held = []
    inventory = {}
    foreign = Counter()
    for rel in sorted(master_files):
        cells, exceptions = extract_master_cells(root, config, rel)
        held.extend(exceptions)
        for cell in cells:
            detected = source_classification(cell)
            tasks.append(detected)
            foreign[detected["detected_language"]] += 1
        inventory[rel] = {"cells": len(cells), "exceptions": len(exceptions)}

    sync = []
    for lang in langs:
        target_files = relative_txt_paths(root / lang)
        extra = sorted(target_files - master_files)
        missing = sorted(master_files - target_files)
        for rel in extra:
            sync.append({"language": lang, "path": rel,
                         "operation": "FAZLA_DOSYA_INCELE",
                         "safe_to_delete": False})
        for rel in missing:
            sync.append({"language": lang, "path": rel,
                         "operation": "EKSIK_DOSYA_HAZIRLA",
                         "safe_to_create": False})
        for rel in sorted(target_files & master_files):
            spec = config.get("parser_registry", {}).get(rel)
            if spec is None or spec.get("token_columns") == "disabled":
                continue
            # Anahtar karşılaştırmasını, encoding denetimleriyle birlikte yap.
            try:
                master_text, _ = read_file(root, master_dir, rel, config)
                target_text, _ = read_file(root, lang, rel, config)
                src = parse_rows(master_text, spec, rel, master_dir)
                dst = parse_rows(target_text, spec, rel, lang)
            except (FileContentError, RuntimeError) as exc:
                held.append({"language": lang, "path": rel,
                             "status": "HEDEF_YAPI_KODLAMA_ENGELI",
                             "detail": str(exc)})
                continue
            for key in sorted(src.keys() - dst.keys()):
                sync.append({"language": lang, "path": rel, "key": key,
                             "operation": "EKSIK_ANAHTAR_CEVIR",
                             "safe_to_create": False})
            for key in sorted(dst.keys() - src.keys()):
                sync.append({"language": lang, "path": rel, "key": key,
                             "operation": "FAZLA_ANAHTAR_INCELE",
                             "safe_to_delete": False})

    report = {
        "schema_version": 1, "engine": ENGINE,
        "master_first": True, "master_language_target": "tr",
        "translation_bridge": "tr -> en -> target -> en -> tr",
        "source_master_approved": False, "english_bridge_approved": False,
        "all_16_languages_ready": False, "live_apply": False,
        "automatic_delete": False, "provider_called": False,
        "master_txt_files": len(master_files), "languages": langs,
        "master_cells_reviewed": len(tasks),
        "language_hints": dict(foreign),
        "master_translation_queue_count": sum(
            item["status"] == "MASTER_TURKCEYE_CEVIR_BEKLIYOR" for item in tasks
        ),
        "source_language_review_count": sum(
            item["status"] == "MASTER_KAYNAK_DIL_INCELEMESI" for item in tasks
        ),
        "blocked_files_count": len(held),
        "sync_review_count": len(sync),
        "master_translation_tasks": tasks,
        "held": held, "sync_proposals": sync,
        "files": inventory,
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Türkçe master/16 dil birleşik ön plan")
    parser.add_argument("--root", default=".")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    try:
        conf = load_config(root / "tools/locale_guard/config.json")
        report = build_plan(root, conf)
        guard = root / ".locale_guard"
        if not guard.is_dir() or guard.is_symlink():
            raise RuntimeError("Güvenli rapor klasörü bulunamadı")
        name = ("unified_master_first_" +
                dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
                + "_" + uuid.uuid4().hex[:8] + ".json")
        target = guard / name
        payload = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        with target.open("xb") as out:
            out.write(payload)
            out.flush()
            os.fsync(out.fileno())
        print("=" * 67)
        print("MASTER ÖNCE TÜRKÇE - 16 DİL GÜVENLİ ÖN PLAN")
        print("=" * 67)
        print("Master TXT dosyası:", report["master_txt_files"])
        print("İncelenen aday metin:", report["master_cells_reviewed"])
        print("Türkçe çeviri adayı:", report["master_translation_queue_count"])
        print("Kaynak dili inceleme:", report["source_language_review_count"])
        print("Özel/kodlama/şema engeli:", report["blocked_files_count"])
        print("Dosya/anahtar ekleme-silme önerisi:", report["sync_review_count"])
        print("Rapor:", target.relative_to(root))
        print("Çeviri servisi çağrılmadı. Canlı dosyalara yazılmadı.")
        return 0
    except (RuntimeError, ValueError, OSError, UnicodeError) as exc:
        print("[ENGEL] Birleşik plan oluşturulamadı:", exc)
        print("Master ve dil dosyalarına dokunulmadı.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
