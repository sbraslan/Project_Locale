#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Sequence


def read_text_safely(path: Path) -> str | None:
    if not path.is_file():
        return None
    data = path.read_bytes()
    if b"\x00" in data[:4096]:
        return None
    for encoding in (
        "utf-8-sig", "utf-8", "cp1250", "cp1251", "cp1252",
        "cp1253", "cp1254", "cp1256", "latin-1",
    ):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None


def issue_identity(issue: dict) -> tuple[str, str, str]:
    return (
        str(issue.get("path", "")),
        str(issue.get("key", "")),
        str(issue.get("code", "")),
    )


def verify_decisions(
    plan: dict,
    manifest: dict,
    workspace: Path,
) -> dict:
    errors: list[str] = []
    runtime_issues = plan["categories"]["runtime_contract_review"]["issues"]
    issue_map = {issue_identity(issue): issue for issue in runtime_issues}

    decisions = manifest.get("decisions", [])
    decision_map: dict[tuple[str, str, str], dict] = {}
    duplicate_decisions: list[tuple[str, str, str]] = []

    for decision in decisions:
        identity = issue_identity(decision)
        if identity in decision_map:
            duplicate_decisions.append(identity)
        decision_map[identity] = decision

    plan_ids = set(issue_map)
    decision_ids = set(decision_map)

    missing = sorted(plan_ids - decision_ids)
    extra = sorted(decision_ids - plan_ids)

    if duplicate_decisions:
        errors.append(f"duplicate decisions: {duplicate_decisions}")
    if missing:
        errors.append(f"runtime issues missing from decision manifest: {missing}")
    if extra:
        errors.append(f"decision manifest contains stale/extra issues: {extra}")

    evidence_checks = 0
    evidence_failures = 0
    resolved = 0
    unresolved = 0

    for identity, decision in decision_map.items():
        issue = issue_map.get(identity)
        if issue is None:
            continue

        status = decision.get("status")
        if status == "resolved":
            resolved += 1
        elif status == "unresolved":
            unresolved += 1
        else:
            errors.append(f"{identity}: invalid status {status!r}")
            continue

        side = decision.get("side")
        if side not in {"source", "target"}:
            errors.append(f"{identity}: invalid side {side!r}")
            continue

        plan_value_field = "source_value" if side == "source" else "target_value"
        actual_value = issue.get(plan_value_field)
        expected_value = decision.get("current_value")
        if actual_value != expected_value:
            errors.append(
                f"{identity}: current value drifted on {side}: "
                f"manifest={expected_value!r} plan={actual_value!r}"
            )

        proposed = decision.get("proposed_value")
        if status == "resolved" and proposed is None:
            errors.append(f"{identity}: resolved decision has no proposed_value")
        if status == "unresolved" and proposed is not None:
            errors.append(f"{identity}: unresolved decision must not propose a value")

        evidence = decision.get("evidence", [])
        if status == "resolved" and not evidence:
            if decision.get("code") != "MALFORMED_PRINTF_SYNTAX":
                errors.append(f"{identity}: resolved decision has no source evidence")
            else:
                malformed = re.search(
                    r"%\.\s+\d+[fFeEgGaA]",
                    str(expected_value or ""),
                )
                if not malformed:
                    errors.append(
                        f"{identity}: malformed printf decision no longer "
                        "matches the deterministic syntax rule"
                    )

        for proof in evidence:
            evidence_checks += 1
            repo = proof.get("repo")
            rel_path = proof.get("path")
            needle = proof.get("contains")
            if not repo or not rel_path or not needle:
                evidence_failures += 1
                errors.append(f"{identity}: incomplete evidence record {proof!r}")
                continue

            source_path = workspace / str(repo) / str(rel_path)
            text = read_text_safely(source_path)
            if text is None:
                evidence_failures += 1
                errors.append(
                    f"{identity}: evidence file missing/undecodable: "
                    f"{repo}/{rel_path}"
                )
                continue

            if str(needle) not in text:
                evidence_failures += 1
                errors.append(
                    f"{identity}: evidence snippet not found in "
                    f"{repo}/{rel_path}: {needle!r}"
                )

    return {
        "schema_version": 1,
        "write_enabled": False,
        "runtime_issue_count": len(runtime_issues),
        "decision_count": len(decisions),
        "resolved_count": resolved,
        "unresolved_count": unresolved,
        "evidence_check_count": evidence_checks,
        "evidence_failure_count": evidence_failures,
        "missing_decision_count": len(missing),
        "extra_decision_count": len(extra),
        "error_count": len(errors),
        "errors": errors,
        "ready_for_dry_run_patch": (
            len(errors) == 0
            and len(missing) == 0
            and len(extra) == 0
            and resolved > 0
        ),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify evidence-backed Locale Guard runtime decisions."
    )
    parser.add_argument("--root", default=".", help="Project_Locale root.")
    parser.add_argument(
        "--workspace",
        default="..",
        help="Parent workspace containing sibling repositories.",
    )
    parser.add_argument(
        "--plan",
        default=".locale_guard/repair_plan.json",
        help="Repair plan path relative to Project_Locale.",
    )
    parser.add_argument(
        "--decisions",
        default="tools/locale_guard/runtime_contract_decisions.json",
        help="Decision manifest path relative to Project_Locale.",
    )
    parser.add_argument(
        "--output",
        default=".locale_guard/runtime_decision_check.json",
        help="Verification report path relative to Project_Locale.",
    )
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    workspace = (root / args.workspace).resolve()

    plan = json.loads((root / args.plan).read_text(encoding="utf-8"))
    manifest = json.loads((root / args.decisions).read_text(encoding="utf-8"))

    report = verify_decisions(plan, manifest, workspace)

    output_path = root / args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(
        "runtime-decision-check: "
        f"issues={report['runtime_issue_count']} "
        f"decisions={report['decision_count']} "
        f"resolved={report['resolved_count']} "
        f"unresolved={report['unresolved_count']} "
        f"evidence_checks={report['evidence_check_count']} "
        f"errors={report['error_count']}"
    )
    print(f"ready_for_dry_run_patch={report['ready_for_dry_run_patch']}")
    print(f"report: {Path(args.output).as_posix()}")

    for error in report["errors"]:
        print(f"  ERROR: {error}")

    return 0 if report["error_count"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
