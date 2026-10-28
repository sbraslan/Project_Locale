#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from tools.locale_guard.locale_guard import audit_repository, load_config

RUNTIME_CODES = {
    "PRINTF_CONTRACT_MISMATCH",
    "MARKUP_TOKEN_MISMATCH",
    "COLUMN_COUNT_MISMATCH",
    "STRUCTURAL_COLUMN_MISMATCH",
    "DUPLICATE_KEY",
}

LAYOUT_CODES = {
    "PRINTF_FORMAT_DIFFERENCE",
    "LAYOUT_TOKEN_MISMATCH",
}

ENCODING_CODES = {
    "ENCODING_UNDECODABLE",
    "WORKTREE_BYTES_DIFFER_FROM_HEAD",
    "WORKTREE_DIRTY",
    "NEWLINE_MIXED",
    "HEAD_BLOB_UNDECODABLE",
    "HEAD_BLOB_NEWLINE",
    "REPLACEMENT_CHAR",
    "TR_CP1254_MOJIBAKE",
    "TR_UTF8_MOJIBAKE",
}

MANIFEST_CODES = {
    "TARGET_FILE_MISSING",
    "TARGET_FILE_EXTRA",
    "MASTER_TR_PATH_MISMATCH",
    "MASTER_TR_CONTENT_MISMATCH",
}


def build_repair_plan(report: dict) -> dict:
    categories = {
        "canonical_backfill": {
            "description": "TR has a logical unit that canonical EN does not have.",
            "issues": [],
        },
        "turkish_backfill": {
            "description": "Canonical EN has a logical unit that TR does not have.",
            "issues": [],
        },
        "runtime_contract_review": {
            "description": "Potential runtime-format or structural contract mismatch. Never auto-fix.",
            "issues": [],
        },
        "layout_review": {
            "description": "Non-fatal formatting/layout difference requiring review.",
            "issues": [],
        },
        "encoding_integrity": {
            "description": "Encoding, raw-byte, or newline integrity anomaly.",
            "issues": [],
        },
        "manifest_review": {
            "description": "Language file manifests differ and need policy classification.",
            "issues": [],
        },
        "informational": {
            "description": "Heuristic/non-blocking observations.",
            "issues": [],
        },
    }

    for issue in report.get("issues", []):
        code = issue.get("code")
        if code == "CANONICAL_MISSING_KEY":
            bucket = "canonical_backfill"
        elif code == "TR_MISSING_KEY":
            bucket = "turkish_backfill"
        elif code in RUNTIME_CODES:
            bucket = "runtime_contract_review"
        elif code in LAYOUT_CODES:
            bucket = "layout_review"
        elif code in ENCODING_CODES:
            bucket = "encoding_integrity"
        elif code in MANIFEST_CODES:
            bucket = "manifest_review"
        else:
            bucket = "informational"
        categories[bucket]["issues"].append(issue)

    hard_categories = {"runtime_contract_review", "encoding_integrity"}
    for name, category in categories.items():
        category["count"] = len(category["issues"])
        category["hard_count"] = (
            sum(
                1 for issue in category["issues"]
                if issue.get("severity") == "error"
            )
            if name in hard_categories
            else 0
        )

    hard_blocker_count = sum(
        categories[name]["hard_count"]
        for name in hard_categories
    )

    return {
        "schema_version": 1,
        "write_enabled": False,
        "fail_closed": True,
        "hard_blocker_count": hard_blocker_count,
        "categories": categories,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a read-only repair plan from a fresh Locale Guard audit."
    )
    parser.add_argument("--root", default=".", help="Repository root.")
    parser.add_argument("--config", default="tools/locale_guard/config.json")
    parser.add_argument("--report", default=".locale_guard/report.json")
    parser.add_argument("--plan", default=".locale_guard/repair_plan.json")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    config = load_config(root / args.config)
    report = audit_repository(root, config)

    report_path = root / args.report
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    plan = build_repair_plan(report)
    plan_path = root / args.plan
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(
        json.dumps(plan, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"repair-plan: hard_blockers={plan['hard_blocker_count']}")
    for name, category in plan["categories"].items():
        print(f"  {name}: {category['count']}")
    print(f"report: {Path(args.report).as_posix()}")
    print(f"plan: {Path(args.plan).as_posix()}")

    return 2 if plan["hard_blocker_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
