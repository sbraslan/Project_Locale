#!/usr/bin/env python3
from __future__ import annotations

import argparse
import difflib
import json
from pathlib import Path
from typing import Sequence

from tools.locale_guard.locale_guard import (
    audit_repository,
    decode_text,
    load_config,
    newline_style,
)
from tools.locale_guard.repair_plan import build_repair_plan
from tools.locale_guard.runtime_dry_run import git_dirty_paths, sha256_bytes


def split_line_ending(line: str) -> tuple[str, str]:
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n") or line.endswith("\r"):
        return line[:-1], line[-1:]
    return line, ""


def line_key(line: str) -> str | None:
    body, _ = split_line_ending(line)
    if not body.strip() or "\t" not in body:
        return None
    return body.split("\t", 1)[0]


def key_indices(lines: list[str], key: str) -> list[int]:
    return [index for index, line in enumerate(lines) if line_key(line) == key]


def unique_key_index(lines: list[str], key: str) -> int:
    matches = key_indices(lines, key)
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected exactly one row for key {key!r}; found {len(matches)}"
        )
    return matches[0]


def exact_columns(line: str) -> list[str]:
    body, _ = split_line_ending(line)
    return body.split("\t")


def verify_stage_shape(plan: dict, manifest: dict) -> dict:
    issues = plan["categories"]["canonical_backfill"]["issues"]
    expected_total = int(manifest["expected_canonical_missing_total"])
    if len(issues) != expected_total:
        raise RuntimeError(
            f"Canonical missing-key count drifted: expected {expected_total}, "
            f"found {len(issues)}"
        )

    stage_keys = {str(entry["key"]) for entry in manifest["entries"]}
    stage_issue_ids = {
        (str(issue.get("path", "")), str(issue.get("key", "")))
        for issue in issues
        if str(issue.get("path", "")) == manifest["target_path"]
    }
    missing_stage = sorted(
        key for key in stage_keys
        if (manifest["target_path"], key) not in stage_issue_ids
    )
    if missing_stage:
        raise RuntimeError(
            "Stage keys are no longer canonical-missing: " + ", ".join(missing_stage)
        )

    deferred = [
        issue for issue in issues
        if not (
            str(issue.get("path", "")) == manifest["target_path"]
            and str(issue.get("key", "")) in stage_keys
        )
    ]
    expected_deferred = int(manifest["expected_deferred_translation_count"])
    if len(deferred) != expected_deferred:
        raise RuntimeError(
            f"Deferred canonical count drifted: expected {expected_deferred}, "
            f"found {len(deferred)}"
        )

    unexpected_deferred_paths = sorted({
        str(issue.get("path", ""))
        for issue in deferred
        if str(issue.get("path", "")) != "en/itemdesc.txt"
    })
    if unexpected_deferred_paths:
        raise RuntimeError(
            "Stage-1 expected all deferred keys to be en/itemdesc.txt; found: "
            + ", ".join(unexpected_deferred_paths)
        )

    return {
        "canonical_missing_total": len(issues),
        "stage_change_count": len(stage_keys),
        "deferred_translation_count": len(deferred),
        "deferred_path": "en/itemdesc.txt",
    }


def build_stage1_dry_run(
    *,
    root: Path,
    config: dict,
    manifest: dict,
) -> tuple[dict, str]:
    if manifest.get("write_enabled") is not False:
        raise RuntimeError("Stage manifest must remain write_enabled=false")

    expected_stage = int(manifest["expected_stage_change_count"])
    entries = manifest.get("entries", [])
    if len(entries) != expected_stage:
        raise RuntimeError(
            f"Manifest entry count drifted: expected {expected_stage}, "
            f"found {len(entries)}"
        )

    audit = audit_repository(root, config)
    plan = build_repair_plan(audit)
    stage_shape = verify_stage_shape(plan, manifest)

    source_path = str(manifest["source_path"])
    target_path = str(manifest["target_path"])
    dirty = git_dirty_paths(root, [target_path])
    if dirty:
        raise RuntimeError(
            f"Refusing canonical dry run because target file is dirty: {dirty}"
        )

    source_file = root / source_path
    target_file = root / target_path
    if not source_file.is_file() or not target_file.is_file():
        raise RuntimeError("Canonical stage source/target file is missing")

    source_bytes = source_file.read_bytes()
    target_bytes = target_file.read_bytes()

    source_lang = str(manifest["source_language"])
    target_lang = str(manifest["target_language"])
    source_text, source_encoding, source_bom = decode_text(
        source_bytes,
        language=source_lang,
        legacy_encodings=config.get("legacy_decodings", {}).get(source_lang, []),
    )
    target_text, target_encoding, target_bom = decode_text(
        target_bytes,
        language=target_lang,
        legacy_encodings=config.get("legacy_decodings", {}).get(target_lang, []),
    )

    source_newline = newline_style(source_bytes)
    target_newline = newline_style(target_bytes)
    if source_newline == "mixed" or target_newline == "mixed":
        raise RuntimeError("Mixed newline style detected in stage source/target")

    source_lines = source_text.splitlines(keepends=True)
    target_lines = target_text.splitlines(keepends=True)

    previous_key = str(manifest["previous_anchor_key"])
    next_key = str(manifest["next_anchor_key"])
    source_prev = unique_key_index(source_lines, previous_key)
    source_next = unique_key_index(source_lines, next_key)
    target_prev = unique_key_index(target_lines, previous_key)
    target_next = unique_key_index(target_lines, next_key)

    expected_source_keys = [previous_key] + [
        str(entry["key"]) for entry in entries
    ] + [next_key]
    actual_source_keys = [
        line_key(source_lines[index])
        for index in range(source_prev, source_next + 1)
    ]
    if actual_source_keys != expected_source_keys:
        raise RuntimeError(
            "Source anchor segment drifted: "
            f"expected {expected_source_keys}, found {actual_source_keys}"
        )

    if target_next != target_prev + 1:
        between = [
            line_key(target_lines[index])
            for index in range(target_prev + 1, target_next)
        ]
        raise RuntimeError(
            "Target anchors are no longer adjacent; refusing insertion. "
            f"Between={between}"
        )

    for entry in entries:
        key = str(entry["key"])
        if key_indices(target_lines, key):
            raise RuntimeError(f"Target already contains stage key {key}")

        source_index = unique_key_index(source_lines, key)
        actual = exact_columns(source_lines[source_index])
        expected = [str(value) for value in entry["columns"]]
        if actual != expected:
            raise RuntimeError(
                f"Source row drift for {key}: expected={expected!r} actual={actual!r}"
            )

    _, target_anchor_ending = split_line_ending(target_lines[target_prev])
    if not target_anchor_ending:
        if target_newline == "CRLF":
            target_anchor_ending = "\r\n"
        elif target_newline == "LF":
            target_anchor_ending = "\n"
        elif target_newline == "CR":
            target_anchor_ending = "\r"
        else:
            raise RuntimeError(
                f"Cannot determine target line ending for {target_path}"
            )

    inserted_lines = [
        "\t".join(str(value) for value in entry["columns"]) + target_anchor_ending
        for entry in entries
    ]
    simulated_lines = list(target_lines)
    simulated_lines[target_next:target_next] = inserted_lines
    simulated_text = "".join(simulated_lines)
    simulated_bytes = simulated_text.encode(target_encoding)

    if newline_style(simulated_bytes) != target_newline:
        raise RuntimeError("Target newline contract changed during simulation")

    roundtrip_text, roundtrip_encoding, roundtrip_bom = decode_text(
        simulated_bytes,
        language=target_lang,
        legacy_encodings=config.get("legacy_decodings", {}).get(target_lang, []),
    )
    if roundtrip_text != simulated_text:
        raise RuntimeError("Simulated canonical bytes do not round-trip")
    if roundtrip_encoding != target_encoding or roundtrip_bom != target_bom:
        raise RuntimeError("Target encoding/BOM identity changed during simulation")

    diff = "".join(difflib.unified_diff(
        target_text.splitlines(keepends=True),
        simulated_text.splitlines(keepends=True),
        fromfile=f"a/{target_path}",
        tofile=f"b/{target_path}",
    ))
    if not diff:
        raise RuntimeError("Canonical stage dry run produced no diff")

    report = {
        "schema_version": 1,
        "write_enabled": False,
        "apply_performed": False,
        "stage": manifest["stage"],
        "source_path": source_path,
        "target_path": target_path,
        "source_encoding": source_encoding,
        "source_bom": source_bom,
        "source_newline_style": source_newline,
        "target_encoding": target_encoding,
        "target_bom": target_bom,
        "target_newline_style": target_newline,
        "original_sha256": sha256_bytes(target_bytes),
        "simulated_sha256": sha256_bytes(simulated_bytes),
        "planned_change_count": len(entries),
        "planned_entries": entries,
        "previous_anchor_key": previous_key,
        "next_anchor_key": next_key,
        "stage_shape": stage_shape,
    }
    return report, diff


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only dry run for exact-copy canonical EN backfill stage 1."
    )
    parser.add_argument("--root", default=".", help="Project_Locale root.")
    parser.add_argument("--config", default="tools/locale_guard/config.json")
    parser.add_argument(
        "--manifest",
        default="tools/locale_guard/canonical_backfill_stage1.json",
    )
    parser.add_argument(
        "--report",
        default=".locale_guard/canonical_stage1_dry_run.json",
    )
    parser.add_argument(
        "--diff",
        default=".locale_guard/canonical_stage1_dry_run.diff",
    )
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    config = load_config(root / args.config)
    manifest = json.loads((root / args.manifest).read_text(encoding="utf-8"))

    report, diff = build_stage1_dry_run(
        root=root,
        config=config,
        manifest=manifest,
    )

    report_path = root / args.report
    diff_path = root / args.diff
    report_path.parent.mkdir(parents=True, exist_ok=True)
    diff_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    diff_path.write_text(diff, encoding="utf-8")

    print("canonical-stage1-dry-run:")
    print(f"  changes={report['planned_change_count']}")
    print(
        f"  deferred_translation="
        f"{report['stage_shape']['deferred_translation_count']}"
    )
    print(
        f"  target={report['target_path']} "
        f"encoding={report['target_encoding']} "
        f"newline={report['target_newline_style']}"
    )
    print(
        f"  anchors={report['previous_anchor_key']} -> "
        f"{report['next_anchor_key']}"
    )
    print("  apply_performed=False")
    print(f"report: {Path(args.report).as_posix()}")
    print(f"diff: {Path(args.diff).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
