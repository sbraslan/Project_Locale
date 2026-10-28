#!/usr/bin/env python3
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path
from typing import Sequence

from tools.locale_guard.locale_guard import (
    audit_repository,
    decode_text,
    load_config,
    suspicious_foreign_language,
)
from tools.locale_guard.repair_plan import build_repair_plan

TEXT_EXTENSIONS = {".txt", ".sub", ".py", ".lua"}
SKIP_NAMES = {"item_proto", "mob_proto"}


def canonical_backfill_groups(plan: dict) -> list[dict]:
    issues = plan["categories"]["canonical_backfill"]["issues"]
    by_path: dict[str, list[dict]] = collections.defaultdict(list)

    for issue in issues:
        by_path[str(issue["path"])].append(issue)

    rows: list[dict] = []
    for path, path_issues in by_path.items():
        keys = [str(issue.get("key", "")) for issue in path_issues]
        rows.append({
            "path": path,
            "count": len(path_issues),
            "sample_keys": keys[:20],
        })

    rows.sort(key=lambda row: (-row["count"], row["path"]))
    return rows


def canonical_language_suspects(root: Path, config: dict) -> list[dict]:
    en_root = root / config["canonical_language"]
    legacy = config.get("legacy_decodings", {}).get("en", [])
    suspects: list[dict] = []

    if not en_root.is_dir():
        return suspects

    for path in sorted(en_root.rglob("*")):
        if not path.is_file():
            continue
        if path.name in SKIP_NAMES:
            continue
        if path.suffix.casefold() not in TEXT_EXTENSIONS:
            continue

        raw = path.read_bytes()
        try:
            text, encoding, bom = decode_text(
                raw,
                language="en",
                legacy_encodings=legacy,
            )
        except UnicodeDecodeError:
            continue

        de_score = 0
        en_score = 0
        tr_score = 0
        examples: list[dict] = []

        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            _, counts = suspicious_foreign_language(line)
            de_score += counts["de"]
            en_score += counts["en"]
            tr_score += counts["tr"]

            if (
                counts["de"] >= 2
                and counts["de"] >= counts["en"] + 1
                and len(examples) < 8
            ):
                examples.append({
                    "line": line_number,
                    "de_score": counts["de"],
                    "en_score": counts["en"],
                    "text": line[:240],
                })

        strong_german = (
            de_score >= 6
            and de_score >= en_score + 4
            and bool(examples)
        )
        if not strong_german:
            continue

        suspects.append({
            "path": path.relative_to(root).as_posix(),
            "classification": "GERMAN_CONTENT_SUSPECT",
            "confidence": "heuristic_review_required",
            "encoding": encoding,
            "bom": bom,
            "de_score": de_score,
            "en_score": en_score,
            "tr_score": tr_score,
            "examples": examples,
        })

    suspects.sort(key=lambda row: (-row["de_score"], row["path"]))
    return suspects


def build_canonical_review(root: Path, config: dict) -> dict:
    audit = audit_repository(root, config)
    plan = build_repair_plan(audit)

    backfill_groups = canonical_backfill_groups(plan)
    language_suspects = canonical_language_suspects(root, config)

    return {
        "schema_version": 1,
        "write_enabled": False,
        "canonical_language": config["canonical_language"],
        "canonical_backfill_count": plan["categories"]["canonical_backfill"]["count"],
        "canonical_backfill_files": len(backfill_groups),
        "canonical_backfill_groups": backfill_groups,
        "language_suspect_count": len(language_suspects),
        "language_suspects": language_suspects,
        "runtime_contract_remaining": plan["categories"]["runtime_contract_review"]["count"],
        "encoding_integrity_remaining": plan["categories"]["encoding_integrity"]["count"],
        "notes": [
            "CANONICAL_MISSING_KEY means TR has a logical unit that EN lacks.",
            "No target-only logical unit is deleted automatically.",
            "Language-suspect detection is heuristic and never auto-translates or auto-replaces text.",
            "A canonical EN backfill requires separate evidence/translation review before any write.",
        ],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only review of canonical EN completeness and language contamination."
    )
    parser.add_argument("--root", default=".", help="Project_Locale root.")
    parser.add_argument("--config", default="tools/locale_guard/config.json")
    parser.add_argument("--report", default=".locale_guard/canonical_review.json")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    config = load_config(root / args.config)
    report = build_canonical_review(root, config)

    report_path = root / args.report
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print("canonical-review:")
    print(f"  backfill_keys={report['canonical_backfill_count']}")
    print(f"  backfill_files={report['canonical_backfill_files']}")
    print(f"  german_suspect_files={report['language_suspect_count']}")
    print(f"  runtime_contract_remaining={report['runtime_contract_remaining']}")
    print(f"  encoding_integrity_remaining={report['encoding_integrity_remaining']}")
    print("  write_enabled=False")

    for row in report["canonical_backfill_groups"]:
        sample = ", ".join(row["sample_keys"][:8])
        print(f"  BACKFILL {row['path']}: {row['count']} [{sample}]")

    for row in report["language_suspects"]:
        print(
            f"  LANGUAGE {row['path']}: "
            f"de={row['de_score']} en={row['en_score']} "
            f"review={row['confidence']}"
        )

    print(f"report: {Path(args.report).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
