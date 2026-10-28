#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Iterable, Sequence

DEFAULT_REPOS = (
    "Project_ClientSrc",
    "Project_ServerSRC",
    "Project_Game",
    "Project_Binary",
)

TEXT_SUFFIXES = {
    ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".inl", ".ipp",
    ".py", ".pyw", ".lua", ".txt", ".json", ".xml", ".vcxproj", ".props",
    ".targets", ".cmake", ".mk", ".rc", ".ini", ".cfg", ".conf",
}

SKIP_DIRS = {
    ".git", ".vs", ".idea", ".vscode", "__pycache__", "build", "Build",
    "Debug", "Release", "x64", "Win32", "obj", "objects", "cache", ".cache",
}

PRINTF_LIKE_RE = re.compile(
    r"%%|%(?:\d+\$)?[-+#0 ']*(?:\d+|\*)?(?:\.(?:\d+|\*))?"
    r"(?:hh|h|ll|l|j|z|t|L)?[diuoxXfFeEgGaAcspn]"
)
SPACE_RE = re.compile(r"\s+")


def base_logical_key(key: str | None) -> str:
    if not key:
        return ""
    return key.rsplit("@@", 1)[0]


def literal_fragments(value: str | None, min_length: int = 8) -> list[str]:
    if not value:
        return []
    stripped = PRINTF_LIKE_RE.sub(" ", value)
    stripped = stripped.replace("\\n", " ").replace("\\r", " ").replace("\\t", " ")
    fragments = []
    for piece in re.split(r"[|\[\]{}()<>:;,]+", stripped):
        piece = SPACE_RE.sub(" ", piece).strip()
        if len(piece) >= min_length:
            fragments.append(piece)
    fragments.sort(key=len, reverse=True)
    return fragments[:4]


def search_terms(issue: dict) -> list[tuple[str, str]]:
    terms: list[tuple[str, str]] = []
    key = base_logical_key(issue.get("key"))
    if key and not key.isdigit():
        terms.append(("key", key))

    source = issue.get("source_value")
    target = issue.get("target_value")
    for fragment in literal_fragments(source):
        terms.append(("source_text", fragment))
    for fragment in literal_fragments(target):
        terms.append(("target_text", fragment))

    dedup: list[tuple[str, str]] = []
    seen: set[str] = set()
    for kind, term in terms:
        folded = term.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        dedup.append((kind, term))
    return dedup


def iter_text_files(root: Path) -> Iterable[Path]:
    if not root.is_dir():
        return
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        yield path


def read_text_safely(path: Path) -> str | None:
    data = path.read_bytes()
    if b"\x00" in data[:4096]:
        return None
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "cp1254", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None


def scan_issue(
    issue: dict,
    repo_roots: Sequence[tuple[str, Path]],
    max_matches: int,
) -> dict:
    terms = search_terms(issue)
    matches: list[dict] = []

    for repo_name, repo_root in repo_roots:
        if not repo_root.is_dir():
            continue
        for path in iter_text_files(repo_root):
            text = read_text_safely(path)
            if text is None:
                continue
            lines = text.splitlines()
            for line_number, line in enumerate(lines, 1):
                folded = line.casefold()
                for kind, term in terms:
                    if term.casefold() not in folded:
                        continue
                    matches.append({
                        "repository": repo_name,
                        "path": path.relative_to(repo_root).as_posix(),
                        "line": line_number,
                        "match_type": kind,
                        "term": term,
                        "snippet": line.strip()[:500],
                    })
                    break
                if len(matches) >= max_matches:
                    break
            if len(matches) >= max_matches:
                break
        if len(matches) >= max_matches:
            break

    return {
        "issue": issue,
        "search_terms": [
            {"kind": kind, "term": term}
            for kind, term in terms
        ],
        "match_count": len(matches),
        "matches": matches,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only source-code probe for Locale Guard runtime contract findings."
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Project_Locale repository root.",
    )
    parser.add_argument(
        "--workspace",
        default="..",
        help="Parent workspace containing sibling project repositories.",
    )
    parser.add_argument(
        "--plan",
        default=".locale_guard/repair_plan.json",
        help="Repair-plan JSON path relative to Project_Locale.",
    )
    parser.add_argument(
        "--output",
        default=".locale_guard/contract_probe.json",
        help="Probe report path relative to Project_Locale.",
    )
    parser.add_argument("--max-matches", type=int, default=20)
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    workspace = (root / args.workspace).resolve()
    plan_path = root / args.plan
    plan = json.loads(plan_path.read_text(encoding="utf-8"))

    issues = plan["categories"]["runtime_contract_review"]["issues"]
    repo_roots = [
        (name, workspace / name)
        for name in DEFAULT_REPOS
    ]

    results = [
        scan_issue(issue, repo_roots, args.max_matches)
        for issue in issues
    ]

    missing_repos = [
        name for name, path in repo_roots
        if not path.is_dir()
    ]
    with_matches = sum(1 for item in results if item["match_count"])
    without_matches = len(results) - with_matches

    report = {
        "schema_version": 1,
        "write_enabled": False,
        "workspace": workspace.as_posix(),
        "repositories": [
            {"name": name, "path": path.as_posix(), "exists": path.is_dir()}
            for name, path in repo_roots
        ],
        "missing_repositories": missing_repos,
        "issue_count": len(results),
        "issues_with_matches": with_matches,
        "issues_without_matches": without_matches,
        "results": results,
    }

    output_path = root / args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(
        f"contract-probe: issues={len(results)} "
        f"with_matches={with_matches} without_matches={without_matches}"
    )
    if missing_repos:
        print("missing repositories: " + ", ".join(missing_repos))
    print(f"report: {Path(args.output).as_posix()}")

    for item in results:
        issue = item["issue"]
        print(
            f"  {issue.get('path')} :: {issue.get('key')} :: "
            f"{issue.get('code')} => matches={item['match_count']}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
