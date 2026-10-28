#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Sequence

from tools.locale_guard.locale_guard import (
    audit_repository,
    decode_text,
    load_config,
    newline_style,
)
from tools.locale_guard.repair_plan import build_repair_plan
from tools.locale_guard.runtime_dry_run import (
    apply_cell_change,
    build_dry_run,
    decision_identity,
    patch_path_for_decision,
    sha256_bytes,
)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    temp_path = path.with_name(f".{path.name}.locale_guard.tmp")
    if temp_path.exists():
        temp_path.unlink()
    try:
        temp_path.write_bytes(data)
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def backup_token(file_reports: Sequence[dict]) -> str:
    payload = "\n".join(
        f"{item['path']}:{item['original_sha256']}:{item['simulated_sha256']}"
        for item in sorted(file_reports, key=lambda value: value["path"])
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def git_diff_check(root: Path, paths: Sequence[str]) -> tuple[bool, str]:
    completed = subprocess.run(
        ["git", "diff", "--check", "--", *paths],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    output = (completed.stdout + completed.stderr).strip()
    return completed.returncode == 0, output


def simulate_bytes_from_report(
    *,
    root: Path,
    config: dict,
    file_report: dict,
) -> bytes:
    repo_path = str(file_report["path"])
    path = root / repo_path
    original_bytes = path.read_bytes()

    if sha256_bytes(original_bytes) != file_report["original_sha256"]:
        raise RuntimeError(
            f"{repo_path}: source hash drifted after dry-run generation"
        )

    language = repo_path.split("/", 1)[0]
    legacy_map = config.get("legacy_decodings", {})
    text, encoding, had_bom = decode_text(
        original_bytes,
        language=language,
        legacy_encodings=legacy_map.get(language, []),
    )

    if encoding != file_report["encoding"] or had_bom != file_report["bom"]:
        raise RuntimeError(
            f"{repo_path}: encoding/BOM drifted after dry-run generation"
        )
    if newline_style(original_bytes) != file_report["newline_style"]:
        raise RuntimeError(
            f"{repo_path}: newline style drifted after dry-run generation"
        )

    lines = text.splitlines(keepends=True)
    if text and not lines:
        lines = [text]

    for change in file_report["changes"]:
        apply_cell_change(
            lines,
            logical_key=str(change["key"]),
            column=int(change["column"]),
            current_value=str(change["before"]),
            proposed_value=str(change["after"]),
            structural=change["code"] == "STRUCTURAL_COLUMN_MISMATCH",
        )

    simulated_text = "".join(lines)
    simulated_bytes = simulated_text.encode(encoding)

    if sha256_bytes(simulated_bytes) != file_report["simulated_sha256"]:
        raise RuntimeError(
            f"{repo_path}: regenerated simulated hash differs from approved dry run"
        )

    if newline_style(simulated_bytes) != file_report["newline_style"]:
        raise RuntimeError(
            f"{repo_path}: simulated newline style changed unexpectedly"
        )

    roundtrip_text, roundtrip_encoding, roundtrip_bom = decode_text(
        simulated_bytes,
        language=language,
        legacy_encodings=legacy_map.get(language, []),
    )
    if (
        roundtrip_text != simulated_text
        or roundtrip_encoding != encoding
        or roundtrip_bom != had_bom
    ):
        raise RuntimeError(
            f"{repo_path}: simulated bytes failed encoding round-trip"
        )

    return simulated_bytes


def post_validate(
    *,
    root: Path,
    config: dict,
    manifest: dict,
    dry_run_report: dict,
) -> dict:
    post_audit = audit_repository(root, config)
    post_plan = build_repair_plan(post_audit)
    post_runtime = post_plan["categories"]["runtime_contract_review"]["issues"]
    post_ids = {decision_identity(issue) for issue in post_runtime}

    unresolved_ids = {
        decision_identity(decision)
        for decision in manifest.get("decisions", [])
        if decision.get("status") == "unresolved"
    }
    resolved_ids = {
        decision_identity(decision)
        for decision in manifest.get("decisions", [])
        if decision.get("status") == "resolved"
    }

    remaining_resolved = sorted(resolved_ids & post_ids)
    missing_unresolved = sorted(unresolved_ids - post_ids)
    unexpected_runtime = sorted(post_ids - unresolved_ids)

    file_checks: list[dict] = []
    for file_report in dry_run_report["files"]:
        path = root / file_report["path"]
        data = path.read_bytes()
        language = file_report["path"].split("/", 1)[0]
        text, encoding, bom = decode_text(
            data,
            language=language,
            legacy_encodings=config.get("legacy_decodings", {}).get(language, []),
        )
        del text
        actual_sha = sha256_bytes(data)
        actual_newline = newline_style(data)
        ok = (
            actual_sha == file_report["simulated_sha256"]
            and encoding == file_report["encoding"]
            and bom == file_report["bom"]
            and actual_newline == file_report["newline_style"]
        )
        file_checks.append({
            "path": file_report["path"],
            "ok": ok,
            "sha256": actual_sha,
            "expected_sha256": file_report["simulated_sha256"],
            "encoding": encoding,
            "expected_encoding": file_report["encoding"],
            "bom": bom,
            "expected_bom": file_report["bom"],
            "newline_style": actual_newline,
            "expected_newline_style": file_report["newline_style"],
        })

    diff_ok, diff_output = git_diff_check(
        root,
        [item["path"] for item in dry_run_report["files"]],
    )

    success = (
        not remaining_resolved
        and not missing_unresolved
        and not unexpected_runtime
        and all(item["ok"] for item in file_checks)
        and diff_ok
    )

    return {
        "success": success,
        "post_runtime_issue_count": len(post_runtime),
        "remaining_resolved": remaining_resolved,
        "missing_unresolved": missing_unresolved,
        "unexpected_runtime": unexpected_runtime,
        "file_checks": file_checks,
        "git_diff_check_ok": diff_ok,
        "git_diff_check_output": diff_output,
        "post_audit_summary": post_audit.get("summary", {}),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Transactionally apply only evidence-backed Locale Guard runtime fixes."
    )
    parser.add_argument("--root", default=".", help="Project_Locale root.")
    parser.add_argument("--workspace", default="..")
    parser.add_argument("--config", default="tools/locale_guard/config.json")
    parser.add_argument(
        "--decisions",
        default="tools/locale_guard/runtime_contract_decisions.json",
    )
    parser.add_argument(
        "--report",
        default=".locale_guard/runtime_apply.json",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Required explicit switch. Without this flag no locale file is written.",
    )
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    workspace = (root / args.workspace).resolve()
    config = load_config(root / args.config)
    manifest = json.loads((root / args.decisions).read_text(encoding="utf-8"))

    fresh_audit = audit_repository(root, config)
    fresh_plan = build_repair_plan(fresh_audit)
    dry_run_report, _ = build_dry_run(
        root=root,
        workspace=workspace,
        config=config,
        plan=fresh_plan,
        manifest=manifest,
    )

    if not args.apply:
        print(
            "runtime-apply: validation passed, but --apply was not supplied; "
            "no locale files were written."
        )
        print(
            f"eligible files={dry_run_report['file_count']} "
            f"changes={dry_run_report['planned_change_count']}"
        )
        return 0

    if (
        dry_run_report["planned_change_count"] != 13
        or dry_run_report["resolved_decision_count"] != 13
        or dry_run_report["unresolved_decision_count"] != 7
    ):
        raise RuntimeError(
            "Approved runtime decision cardinality drifted; expected "
            "13 resolved changes and 7 unresolved decisions."
        )

    token = backup_token(dry_run_report["files"])
    backup_root = root / ".locale_guard" / "backups" / f"runtime_{token}"
    if backup_root.exists():
        raise RuntimeError(
            f"Backup directory already exists; refusing to overwrite: {backup_root}"
        )

    prepared: dict[str, bytes] = {}
    original: dict[str, bytes] = {}

    for file_report in dry_run_report["files"]:
        repo_path = file_report["path"]
        path = root / repo_path
        original_bytes = path.read_bytes()
        if sha256_bytes(original_bytes) != file_report["original_sha256"]:
            raise RuntimeError(f"{repo_path}: source hash changed before apply")
        original[repo_path] = original_bytes
        prepared[repo_path] = simulate_bytes_from_report(
            root=root,
            config=config,
            file_report=file_report,
        )

    backup_root.mkdir(parents=True, exist_ok=False)
    for repo_path, data in original.items():
        backup_path = backup_root / repo_path
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path.write_bytes(data)

    applied_paths: list[str] = []
    rollback_performed = False
    validation: dict | None = None

    try:
        for repo_path in sorted(prepared):
            atomic_write_bytes(root / repo_path, prepared[repo_path])
            applied_paths.append(repo_path)

        validation = post_validate(
            root=root,
            config=config,
            manifest=manifest,
            dry_run_report=dry_run_report,
        )
        if not validation["success"]:
            raise RuntimeError(
                "Post-apply validation failed; rollback required. "
                f"Details: {validation}"
            )

    except Exception:
        rollback_performed = True
        for repo_path, data in original.items():
            atomic_write_bytes(root / repo_path, data)

        rollback_errors = []
        for repo_path, data in original.items():
            actual = (root / repo_path).read_bytes()
            if actual != data:
                rollback_errors.append(repo_path)

        if rollback_errors:
            raise RuntimeError(
                "Apply failed and rollback verification also failed for: "
                + ", ".join(rollback_errors)
            )
        raise

    report = {
        "schema_version": 1,
        "write_enabled": True,
        "apply_performed": True,
        "rollback_performed": rollback_performed,
        "backup_token": token,
        "backup_directory": backup_root.relative_to(root).as_posix(),
        "applied_paths": applied_paths,
        "planned_change_count": dry_run_report["planned_change_count"],
        "resolved_decision_count": dry_run_report["resolved_decision_count"],
        "unresolved_decision_count": dry_run_report["unresolved_decision_count"],
        "post_validation": validation,
    }

    report_path = root / args.report
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(
        "runtime-apply: SUCCESS "
        f"files={len(applied_paths)} "
        f"changes={dry_run_report['planned_change_count']}"
    )
    print(f"backup: {report['backup_directory']}")
    print("post_validation=True")
    print("rollback_performed=False")
    print(f"report: {Path(args.report).as_posix()}")
    print("Changes are local working-tree edits only; nothing was committed.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
