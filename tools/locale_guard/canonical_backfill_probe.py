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
    keyed_tsv_rows,
    load_config,
)
from tools.locale_guard.repair_plan import build_repair_plan


def parser_for_path(config: dict, repo_path: str) -> dict:
    name = Path(repo_path).name
    spec = config.get("parser_registry", {}).get(name)
    if not spec or spec.get("kind") != "keyed_tsv":
        raise RuntimeError(f"No keyed TSV parser registered for {repo_path}")
    return spec


def load_rows_for_language(
    *,
    root: Path,
    config: dict,
    language: str,
    relative_path: str,
    parser_spec: dict,
) -> tuple[dict[str, list[str]], str | None]:
    path = root / language / relative_path
    if not path.is_file():
        return {}, "file_missing"

    raw = path.read_bytes()
    try:
        text, _, _ = decode_text(
            raw,
            language=language,
            legacy_encodings=config.get("legacy_decodings", {}).get(language, []),
        )
    except UnicodeDecodeError:
        return {}, "encoding_undecodable"

    rows, duplicates = keyed_tsv_rows(
        text,
        key_columns=tuple(parser_spec.get("key_columns", [0])),
        key_exclude_columns=tuple(parser_spec.get("key_exclude_columns", [])),
        header_rows=int(parser_spec.get("header_rows", 0)),
        duplicate_policy=parser_spec.get("duplicate_policy", "error"),
    )
    if duplicates and parser_spec.get("duplicate_policy", "error") != "occurrence":
        return rows, f"duplicate_keys:{len(duplicates)}"

    return rows, None


def build_backfill_probe(root: Path, config: dict) -> dict:
    audit = audit_repository(root, config)
    plan = build_repair_plan(audit)
    issues = plan["categories"]["canonical_backfill"]["issues"]

    grouped: dict[str, list[dict]] = collections.defaultdict(list)
    for issue in issues:
        grouped[str(issue["path"])].append(issue)

    languages = list(config.get("languages", []))
    canonical = config["canonical_language"]
    noncanonical = [lang for lang in languages if lang != canonical]

    file_reports: list[dict] = []
    overall_presence = collections.Counter()
    overall_tr_only = 0
    overall_multi = 0

    for canonical_path, path_issues in sorted(grouped.items()):
        if not canonical_path.startswith(canonical + "/"):
            raise RuntimeError(f"Unexpected canonical path: {canonical_path}")

        relative_path = canonical_path.split("/", 1)[1]
        parser_spec = parser_for_path(config, canonical_path)

        rows_by_lang: dict[str, dict[str, list[str]]] = {}
        load_errors: dict[str, str] = {}

        for language in noncanonical:
            rows, error = load_rows_for_language(
                root=root,
                config=config,
                language=language,
                relative_path=relative_path,
                parser_spec=parser_spec,
            )
            rows_by_lang[language] = rows
            if error:
                load_errors[language] = error

        coverage = collections.Counter()
        key_reports: list[dict] = []
        tr_only_count = 0
        multi_locale_count = 0

        for issue in path_issues:
            key = str(issue.get("key", ""))
            present_locales = [
                lang for lang in noncanonical
                if key in rows_by_lang.get(lang, {})
            ]
            for lang in present_locales:
                coverage[lang] += 1
                overall_presence[lang] += 1

            tr_only = present_locales == ["tr"]
            if tr_only:
                tr_only_count += 1
                overall_tr_only += 1
            if len(present_locales) > 1:
                multi_locale_count += 1
                overall_multi += 1

            samples = {}
            for lang in present_locales[:6]:
                samples[lang] = rows_by_lang[lang][key]

            key_reports.append({
                "key": key,
                "present_locales": present_locales,
                "present_locale_count": len(present_locales),
                "tr_only": tr_only,
                "samples": samples,
            })

        file_reports.append({
            "path": canonical_path,
            "relative_path": relative_path,
            "missing_key_count": len(path_issues),
            "tr_only_count": tr_only_count,
            "multi_locale_count": multi_locale_count,
            "coverage_by_locale": dict(sorted(coverage.items())),
            "load_errors": load_errors,
            "sample_keys": key_reports[:20],
            "keys": key_reports,
        })

    return {
        "schema_version": 1,
        "write_enabled": False,
        "canonical_language": canonical,
        "missing_key_count": len(issues),
        "file_count": len(file_reports),
        "tr_only_count": overall_tr_only,
        "multi_locale_count": overall_multi,
        "presence_by_locale": dict(sorted(overall_presence.items())),
        "files": file_reports,
        "notes": [
            "This probe never writes locale files.",
            "TR-only means the missing EN logical key exists only in TR among configured non-EN locales.",
            "Presence in multiple locales is evidence of key propagation only, not proof that any translation is semantically correct.",
            "Backfill text must still preserve placeholders, markup, tabs, column shape, encoding and newline contracts.",
        ],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only cross-locale evidence probe for canonical EN backfill keys."
    )
    parser.add_argument("--root", default=".", help="Project_Locale root.")
    parser.add_argument("--config", default="tools/locale_guard/config.json")
    parser.add_argument("--report", default=".locale_guard/canonical_backfill_probe.json")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    config = load_config(root / args.config)
    report = build_backfill_probe(root, config)

    report_path = root / args.report
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print("canonical-backfill-probe:")
    print(f"  missing_keys={report['missing_key_count']}")
    print(f"  files={report['file_count']}")
    print(f"  tr_only={report['tr_only_count']}")
    print(f"  multi_locale={report['multi_locale_count']}")
    print("  write_enabled=False")

    for file_report in report["files"]:
        print(
            f"  FILE {file_report['path']}: "
            f"missing={file_report['missing_key_count']} "
            f"tr_only={file_report['tr_only_count']} "
            f"multi_locale={file_report['multi_locale_count']}"
        )
        if file_report["coverage_by_locale"]:
            coverage = " ".join(
                f"{lang}={count}"
                for lang, count in file_report["coverage_by_locale"].items()
            )
            print(f"    coverage: {coverage}")
        if file_report["load_errors"]:
            errors = " ".join(
                f"{lang}:{error}"
                for lang, error in file_report["load_errors"].items()
            )
            print(f"    load_errors: {errors}")

        for sample in file_report["sample_keys"][:8]:
            langs = ",".join(sample["present_locales"]) or "<none>"
            print(
                f"    KEY {sample['key']}: "
                f"present={sample['present_locale_count']} [{langs}]"
            )

    print(f"report: {Path(args.report).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
