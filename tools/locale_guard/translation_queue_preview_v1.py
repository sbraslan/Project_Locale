#!/usr/bin/env python3
"""Güvenli artımlı çeviri iş kuyruğu; çeviri servisi ve canlı uygulama yok."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from tools.locale_guard.incremental_scan_v1 import (
    collect, parse_rows, read_file, require, row_hash, safe_path,
)
from tools.locale_guard.locale_guard import (
    layout_tokens, load_config, protected_tokens,
)


def editable_columns(spec: dict, width: int) -> list[int]:
    """Sadece açıkça çevrilebilir olarak kaydedilmiş sütunları seç."""
    token = spec.get("token_columns")
    if token == "disabled" or token is None:
        return []
    keys = set(spec.get("key_columns", [0]))
    excluded = set(spec.get("key_exclude_columns", []))
    if excluded:
        keys = set(range(width)) - excluded
    structural = set(spec.get("structural_columns", []))
    if token == "all_non_key":
        selected = set(range(width)) - keys
    elif isinstance(token, list) and all(type(i) is int for i in token):
        selected = set(token)
    else:
        raise RuntimeError("[ENGEL] Bilinmeyen çeviri sütun politikası: " + str(token))
    require(all(0 <= i < width for i in selected),
            "Çeviri sütunu satır sınırları dışında")
    require(not (selected & (keys | structural)),
            "Anahtar/yapısal sütun çeviri için işaretlenmiş")
    return sorted(selected)


def build_queue(root: Path, config: dict, languages: list[str] | None = None,
                baseline: dict | None = None) -> dict:
    """Taze taramadan yalnız yeni/eksik anahtarları çeviri kuyruğuna al."""
    scan = collect(root, config, baseline=baseline, languages=languages)
    master = config["master_directory"]
    registry = config.get("parser_registry", {})
    cache: dict[str, dict] = {}
    tasks: list[dict] = []
    holds: list[dict] = []
    empty_cells = 0
    missing_rows = 0

    for issue in scan["items"]:
        if issue["status"] != "MISSING_KEY_TRANSLATION":
            continue
        missing_rows += 1
        lang, rel, key = issue["language"], issue["path"], issue["key"]
        spec = registry.get(rel)
        require(isinstance(spec, dict) and spec.get("kind") == "keyed_tsv",
                "Kayıtlı olmayan parser kuyruğa giremez: " + rel)
        if rel not in cache:
            source_text, file_sha = read_file(root, master, rel, config)
            cache[rel] = {
                "rows": parse_rows(source_text, spec, rel, master),
                "master_sha256": file_sha,
            }
        rows = cache[rel]["rows"]
        require(key in rows, "Kaynak anahtar kayboldu: " + rel + ":" + key)
        columns = rows[key]
        require(row_hash(columns) == issue["source_sha256"],
                "Kaynak satır sürümü değişti: " + rel + ":" + key)
        indices = editable_columns(spec, len(columns))
        if not indices:
            holds.append({
                "language": lang, "path": rel, "key": key,
                "reason": "TRANSLATION_SCHEMA_DISABLED_OR_EMPTY",
            })
            continue
        prepared = 0
        for column in indices:
            original = columns[column]
            if not original.strip():
                empty_cells += 1
                continue
            if not any(ch.isalpha() for ch in original):
                holds.append({
                    "language": lang, "path": rel, "key": key,
                    "column": column, "reason": "NO_TRANSLATABLE_LETTERS",
                })
                continue
            tasks.append({
                "language": lang,
                "language_code": config.get("translation_language_codes", {}).get(lang, lang),
                "path": rel,
                "key": key,
                "column": column,
                "source": original,
                "source_language": "tr",
                "master_file_sha256": cache[rel]["master_sha256"],
                "source_row_sha256": issue["source_sha256"],
                "protected_tokens": protected_tokens(original),
                "layout_tokens": layout_tokens(original),
                "status": "SOURCE_REVIEW_REQUIRED",
                "translation_provider_allowed": False,
                "live_apply_allowed": False,
            })
            prepared += 1
        if prepared == 0:
            holds.append({
                "language": lang, "path": rel, "key": key,
                "reason": "NO_TRANSLATABLE_COLUMNS_AFTER_FILTER",
            })

    blockers = [
        {"language": i["language"], "path": i["path"],
         "status": i["status"], "reason": i.get("reason", "")}
        for i in scan["items"] if i["status"] in (
            "FILE_ENCODING_REVIEW", "PARSER_SHAPE_REVIEW")
    ]
    return {
        "schema_version": 1,
        "status": "TRANSLATION_QUEUE_PREVIEW_NOT_APPROVED",
        "source_directory": master,
        "source_language": "tr",
        "target_languages": scan["languages"],
        "scan_counts": scan["counts"],
        "missing_key_rows": missing_rows,
        "translation_cells": len(tasks),
        "empty_source_cells_skipped": empty_cells,
        "held_cells_or_rows": len(holds),
        "encoding_or_parser_blockers": len(blockers),
        "source_semantic_approval": False,
        "automatic_translate": False,
        "live_apply": False,
        "instructions": (
            "Kaynak metinlerin dil ve anlam kontrolü şarttır. "
            "Tokenları/TSV anahtarlarını asla çevirmeyin. "
            "Bu kuyruk yalnız önizlemedir ve uygulamaya yetki vermez."
        ),
        "tasks": tasks,
        "holds": holds,
        "blockers": blockers,
    }


def main(argv: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(description="Artımlı çeviri kuyruğu önizlemesi")
    cli.add_argument("--root", default=".")
    cli.add_argument("--config", default="tools/locale_guard/config.json")
    cli.add_argument("--baseline", default=".locale_guard/last_synced_master_v1.json")
    cli.add_argument("--output", default=".locale_guard/translation_queue_preview_v1.json")
    cli.add_argument("--language", action="append")
    args = cli.parse_args(argv)
    root = Path(args.root).resolve()
    try:
        config = load_config(root / args.config)
        baseline_path = root / safe_path(args.baseline)
        baseline = (json.loads(baseline_path.read_text(encoding="utf-8-sig"))
                    if baseline_path.is_file() else None)
        result = build_queue(root, config, args.language, baseline)
        output = root / safe_path(args.output)
        guard = root / ".locale_guard"
        require(output.parent == guard and guard.is_dir() and not guard.is_symlink(),
                "Kuyruk yalnız .locale_guard köküne yazılabilir")
        require(not output.is_symlink(), "Sembolik çıktı yolu yasak")
        payload = (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        if output.exists():
            require(output.read_bytes() == payload,
                    "Mevcut kuyruk farklı; eski dosyayı silmeden işlem durduruldu")
        else:
            with output.open("xb") as file:
                file.write(payload)
        print("=" * 70)
        print("ARTIMLI ÇEVİRİ KUYRUĞU - ONAYSIZ ÖNİZLEME")
        print("=" * 70)
        print("Eksik anahtar satırı:", result["missing_key_rows"])
        print("Çeviri adayı hücre:", result["translation_cells"])
        print("Manuel incelemeye ayrılan:", result["held_cells_or_rows"])
        print("Kodlama/parser engeli:", result["encoding_or_parser_blockers"])
        print("Rapor:", output.relative_to(root))
        print("Otomatik çeviri: HAYIR | Canlı uygulama: HAYIR")
        return 0
    except (RuntimeError, OSError, ValueError, UnicodeError, KeyError, TypeError) as exc:
        print("[ENGEL]", exc)
        print("Canlı locale dosyaları değiştirilmedi.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
