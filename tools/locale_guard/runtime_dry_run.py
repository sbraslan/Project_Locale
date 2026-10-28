#!/usr/bin/env python3
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Sequence

from tools.locale_guard.locale_guard import decode_text, load_config, newline_style
from tools.locale_guard.runtime_decision_check import verify_decisions


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def decision_identity(value: dict) -> tuple[str, str, str]:
    return (
        str(value.get("path", "")),
        str(value.get("key", "")),
        str(value.get("code", "")),
    )


def patch_path_for_decision(decision: dict) -> str:
    path = str(decision["path"])
    if decision.get("side") == "source" and path.startswith("tr/"):
        return "en/" + path.split("/", 1)[1]
    return path


def parse_occurrence_key(key: str) -> tuple[str, int]:
    if "@@" not in key:
        return key, 0
    base, raw_occurrence = key.rsplit("@@", 1)
    try:
        occurrence = int(raw_occurrence)
    except ValueError as exc:
        raise ValueError(f"Invalid occurrence key: {key!r}") from exc
    if occurrence < 0:
        raise ValueError(f"Negative occurrence key: {key!r}")
    return base, occurrence


def split_line_ending(line: str) -> tuple[str, str]:
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n") or line.endswith("\r"):
        return line[:-1], line[-1:]
    return line, ""


def locate_tsv_row(lines: list[str], logical_key: str) -> tuple[int, list[str], str]:
    base_key, wanted_occurrence = parse_occurrence_key(logical_key)
    occurrence = 0

    for index, line in enumerate(lines):
        body, ending = split_line_ending(line)
        if "\t" not in body:
            continue
        if body.lstrip().startswith("#"):
            continue

        columns = body.split("\t")
        if not columns or columns[0] != base_key:
            continue

        if occurrence == wanted_occurrence:
            return index, columns, ending
        occurrence += 1

    raise KeyError(
        f"Logical key not found: {logical_key!r} "
        f"(base={base_key!r}, occurrence={wanted_occurrence})"
    )


def apply_cell_change(
    lines: list[str],
    *,
    logical_key: str,
    column: int,
    current_value: str,
    proposed_value: str,
    structural: bool,
) -> dict:
    line_index, columns, ending = locate_tsv_row(lines, logical_key)

    actual_value = columns[column] if column < len(columns) else ""
    if actual_value != current_value:
        raise ValueError(
            f"{logical_key}: cell drift at column {column}: "
            f"expected={current_value!r} actual={actual_value!r}"
        )

    original_columns = list(columns)

    if structural and proposed_value == "":
        if column < len(columns):
            if column == len(columns) - 1:
                columns.pop()
            else:
                columns[column] = ""
    else:
        while len(columns) <= column:
            columns.append("")
        columns[column] = proposed_value

    lines[line_index] = "\t".join(columns) + ending

    return {
        "line_number": line_index + 1,
        "column": column,
        "before_columns": original_columns,
        "after_columns": columns,
    }


def encode_same(text: str, encoding: str) -> bytes:
    return text.encode(encoding)


def git_dirty_paths(root: Path, paths: Sequence[str]) -> list[str]:
    if not paths:
        return []
    completed = subprocess.run(
        ["git", "status", "--porcelain=v1", "--", *paths],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            completed.stderr.strip()
            or completed.stdout.strip()
            or f"git status returned {completed.returncode}"
        )
    return [
        line[3:].replace("\\", "/")
        for line in completed.stdout.splitlines()
        if len(line) >= 4
    ]


def build_dry_run(
    *,
    root: Path,
    workspace: Path,
    config: dict,
    plan: dict,
    manifest: dict,
) -> tuple[dict, str]:
    verification = verify_decisions(plan, manifest, workspace)
    if verification["error_count"]:
        raise RuntimeError(
            "Runtime decision verification failed: "
            + "; ".join(verification["errors"])
        )

    runtime_issues = plan["categories"]["runtime_contract_review"]["issues"]
    issue_map = {decision_identity(issue): issue for issue in runtime_issues}

    resolved = [
        decision
        for decision in manifest.get("decisions", [])
        if decision.get("status") == "resolved"
    ]
    unresolved = [
        decision
        for decision in manifest.get("decisions", [])
        if decision.get("status") == "unresolved"
    ]

    by_path: dict[str, list[dict]] = defaultdict(list)
    for decision in resolved:
        identity = decision_identity(decision)
        issue = issue_map.get(identity)
        if issue is None:
            raise RuntimeError(f"Resolved decision is not in repair plan: {identity}")

        patch_path = patch_path_for_decision(decision)
        by_path[patch_path].append({
            "decision": decision,
            "issue": issue,
        })

    patch_paths = sorted(by_path)
    dirty = git_dirty_paths(root, patch_paths)
    if dirty:
        raise RuntimeError(
            "Refusing dry-run generation because patch-target locale files "
            f"are dirty: {dirty}"
        )

    file_reports: list[dict] = []
    diff_chunks: list[str] = []
    change_count = 0

    legacy_map = config.get("legacy_decodings", {})

    for repo_path in patch_paths:
        path = root / repo_path
        if not path.is_file():
            raise RuntimeError(f"Patch target does not exist: {repo_path}")

        original_bytes = path.read_bytes()
        original_newline = newline_style(original_bytes)
        if original_newline == "mixed":
            raise RuntimeError(
                f"Patch target has mixed newlines; manual review required: {repo_path}"
            )

        language = repo_path.split("/", 1)[0]
        text, encoding, had_bom = decode_text(
            original_bytes,
            language=language,
            legacy_encodings=legacy_map.get(language, []),
        )

        original_text = text
        lines = text.splitlines(keepends=True)
        if text and not lines:
            lines = [text]

        changes: list[dict] = []
        for item in by_path[repo_path]:
            decision = item["decision"]
            issue = item["issue"]
            column = issue.get("column")
            if column is None:
                raise RuntimeError(
                    f"Decision has no audited column: {decision_identity(decision)}"
                )

            current_value = str(decision.get("current_value", ""))
            proposed_value = decision.get("proposed_value")
            if proposed_value is None:
                raise RuntimeError(
                    f"Resolved decision has no proposed value: "
                    f"{decision_identity(decision)}"
                )

            change = apply_cell_change(
                lines,
                logical_key=str(decision["key"]),
                column=int(column),
                current_value=current_value,
                proposed_value=str(proposed_value),
                structural=decision.get("code") == "STRUCTURAL_COLUMN_MISMATCH",
            )
            change.update({
                "issue_path": decision["path"],
                "patch_path": repo_path,
                "key": decision["key"],
                "code": decision["code"],
                "side": decision["side"],
                "reason": decision.get("reason"),
                "before": current_value,
                "after": proposed_value,
            })
            changes.append(change)
            change_count += 1

        simulated_text = "".join(lines)
        simulated_bytes = encode_same(simulated_text, encoding)

        if had_bom and not simulated_bytes.startswith(b"\xef\xbb\xbf"):
            raise RuntimeError(f"UTF-8 BOM was not preserved: {repo_path}")
        if not had_bom and simulated_bytes.startswith(b"\xef\xbb\xbf"):
            raise RuntimeError(f"Unexpected UTF-8 BOM introduced: {repo_path}")

        simulated_newline = newline_style(simulated_bytes)
        if simulated_newline != original_newline:
            raise RuntimeError(
                f"Newline style changed for {repo_path}: "
                f"{original_newline} -> {simulated_newline}"
            )

        roundtrip_text, roundtrip_encoding, roundtrip_bom = decode_text(
            simulated_bytes,
            language=language,
            legacy_encodings=legacy_map.get(language, []),
        )
        if roundtrip_text != simulated_text:
            raise RuntimeError(f"Simulated bytes do not round-trip: {repo_path}")
        if roundtrip_encoding != encoding or roundtrip_bom != had_bom:
            raise RuntimeError(
                f"Encoding identity changed for {repo_path}: "
                f"{encoding}/{had_bom} -> {roundtrip_encoding}/{roundtrip_bom}"
            )

        diff = "".join(difflib.unified_diff(
            original_text.splitlines(keepends=True),
            simulated_text.splitlines(keepends=True),
            fromfile=f"a/{repo_path}",
            tofile=f"b/{repo_path}",
        ))
        if not diff:
            raise RuntimeError(
                f"Resolved decisions produced no diff for {repo_path}"
            )
        diff_chunks.append(diff)

        file_reports.append({
            "path": repo_path,
            "encoding": encoding,
            "bom": had_bom,
            "newline_style": original_newline,
            "original_sha256": sha256_bytes(original_bytes),
            "simulated_sha256": sha256_bytes(simulated_bytes),
            "original_size": len(original_bytes),
            "simulated_size": len(simulated_bytes),
            "change_count": len(changes),
            "changes": changes,
        })

    report = {
        "schema_version": 1,
        "write_enabled": False,
        "apply_performed": False,
        "decision_gate": {
            "error_count": verification["error_count"],
            "ready_for_dry_run_patch": verification["ready_for_dry_run_patch"],
        },
        "resolved_decision_count": len(resolved),
        "unresolved_decision_count": len(unresolved),
        "planned_change_count": change_count,
        "file_count": len(file_reports),
        "files": file_reports,
        "unresolved_skipped": [
            {
                "path": decision["path"],
                "key": decision["key"],
                "code": decision["code"],
                "reason": decision.get("reason"),
            }
            for decision in unresolved
        ],
    }

    return report, "\n".join(diff_chunks)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate a byte-safe, read-only Locale Guard patch preview."
    )
    parser.add_argument("--root", default=".", help="Project_Locale root.")
    parser.add_argument("--workspace", default="..")
    parser.add_argument("--config", default="tools/locale_guard/config.json")
    parser.add_argument("--plan", default=".locale_guard/repair_plan.json")
    parser.add_argument(
        "--decisions",
        default="tools/locale_guard/runtime_contract_decisions.json",
    )
    parser.add_argument(
        "--report",
        default=".locale_guard/runtime_dry_run.json",
    )
    parser.add_argument(
        "--diff",
        default=".locale_guard/runtime_dry_run.diff",
    )
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    workspace = (root / args.workspace).resolve()
    config = load_config(root / args.config)
    plan = json.loads((root / args.plan).read_text(encoding="utf-8"))
    manifest = json.loads((root / args.decisions).read_text(encoding="utf-8"))

    report, diff = build_dry_run(
        root=root,
        workspace=workspace,
        config=config,
        plan=plan,
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

    print(
        "runtime-dry-run: "
        f"files={report['file_count']} "
        f"changes={report['planned_change_count']} "
        f"resolved={report['resolved_decision_count']} "
        f"unresolved_skipped={report['unresolved_decision_count']}"
    )
    print("apply_performed=False")
    print(f"report: {Path(args.report).as_posix()}")
    print(f"diff: {Path(args.diff).as_posix()}")

    for file_report in report["files"]:
        print(
            f"  {file_report['path']}: "
            f"changes={file_report['change_count']} "
            f"encoding={file_report['encoding']} "
            f"newline={file_report['newline_style']}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
