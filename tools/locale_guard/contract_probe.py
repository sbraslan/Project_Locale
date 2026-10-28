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

SOURCE_SUFFIXES = {
    ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".inl", ".ipp",
    ".py", ".pyw", ".lua", ".rc",
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
LOCALE_ENUM_RE = re.compile(r"^\s*([A-Z0-9_]+)\s*=\s*(\d+)\s*,", re.MULTILINE)


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


def search_terms(
    issue: dict,
    numeric_symbols: dict[str, str] | None = None,
) -> list[tuple[str, str]]:
    terms: list[tuple[str, str]] = []
    key = base_logical_key(issue.get("key"))
    numeric_key = bool(key and key.isdigit())

    if key and not numeric_key:
        terms.append(("key", key))
    elif numeric_key and numeric_symbols:
        symbol = numeric_symbols.get(key)
        if symbol:
            terms.append(("enum_key", symbol))

    source = issue.get("source_value")
    target = issue.get("target_value")

    source_fragments = literal_fragments(source)
    target_fragments = literal_fragments(target)

    # Numeric locale keys (for example locale_string IDs) often have no
    # symbolic identifier in source code. If the ordinary 8-character
    # threshold yields nothing, allow a controlled 5-character fallback so
    # short but meaningful literals such as "Round" can still be probed.
    if numeric_key and not source_fragments:
        source_fragments = literal_fragments(source, min_length=5)
    if numeric_key and not target_fragments:
        target_fragments = literal_fragments(target, min_length=5)

    for fragment in source_fragments:
        terms.append(("source_text", fragment))
    for fragment in target_fragments:
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


def load_locale_enum_map(locale_header: Path) -> dict[str, str]:
    if not locale_header.is_file():
        return {}
    text = read_text_safely(locale_header)
    if text is None:
        return {}
    return {
        numeric_id: symbol
        for symbol, numeric_id in LOCALE_ENUM_RE.findall(text)
    }


def classify_evidence_path(repo_name: str, relative_path: Path) -> str:
    parts = {part.casefold() for part in relative_path.parts}
    posix = relative_path.as_posix().casefold()

    if "locale/locale/country/" in posix:
        return "locale_reference"
    if "extern" in parts:
        return "noise"
    if relative_path.suffix.lower() not in SOURCE_SUFFIXES:
        return "reference"
    return "source"


def evidence_strength(match_type: str, term: str, evidence_kind: str) -> str:
    if evidence_kind != "source":
        return "reference" if evidence_kind in {"reference", "locale_reference"} else "noise"
    if match_type in {"key", "enum_key"}:
        return "strong"
    normalized = SPACE_RE.sub(" ", term).strip()
    if len(normalized) >= 18:
        return "strong"
    if len(normalized) >= 10:
        return "medium"
    return "weak"


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


def build_candidate_index(
    repo_roots: Sequence[tuple[str, Path]],
) -> list[tuple[str, Path, Path, str]]:
    candidates: list[tuple[str, Path, Path, str]] = []
    for repo_name, repo_root in repo_roots:
        if not repo_root.is_dir():
            continue
        for path in iter_text_files(repo_root):
            relative_path = path.relative_to(repo_root)
            evidence_kind = classify_evidence_path(repo_name, relative_path)
            if evidence_kind == "noise":
                continue
            candidates.append((repo_name, repo_root, path, evidence_kind))

    candidates.sort(
        key=lambda item: (
            {
                "source": 0,
                "reference": 1,
                "locale_reference": 2,
            }.get(item[3], 3),
            item[0].casefold(),
            item[2].as_posix().casefold(),
        )
    )
    return candidates


def scan_issue(
    issue: dict,
    candidates: Sequence[tuple[str, Path, Path, str]],
    max_matches: int,
    numeric_symbols: dict[str, str] | None = None,
    text_cache: dict[str, str | None] | None = None,
) -> dict:
    terms = search_terms(issue, numeric_symbols)
    exact_terms = [
        item for item in terms
        if item[0] in {"key", "enum_key"}
    ]
    text_terms = [
        item for item in terms
        if item[0] not in {"key", "enum_key"}
    ]
    text_terms.sort(key=lambda item: -len(item[1]))

    if text_cache is None:
        text_cache = {}

    matches: list[dict] = []
    seen_locations: set[tuple[str, str, int, str]] = set()

    def scan_phase(
        phase_candidates: Sequence[tuple[str, Path, Path, str]],
        phase_terms: Sequence[tuple[str, str]],
        *,
        allowed_strengths: set[str] | None = None,
        limit: int,
    ) -> None:
        if not phase_terms or len(matches) >= limit:
            return

        for repo_name, repo_root, path, evidence_kind in phase_candidates:
            cache_key = path.as_posix()
            if cache_key not in text_cache:
                text_cache[cache_key] = read_text_safely(path)
            text = text_cache[cache_key]
            if text is None:
                continue

            relative_path = path.relative_to(repo_root)
            for line_number, line in enumerate(text.splitlines(), 1):
                folded = line.casefold()
                for kind, term in phase_terms:
                    if term.casefold() not in folded:
                        continue

                    strength = evidence_strength(kind, term, evidence_kind)
                    if allowed_strengths is not None and strength not in allowed_strengths:
                        continue

                    location_key = (
                        repo_name,
                        relative_path.as_posix(),
                        line_number,
                        term.casefold(),
                    )
                    if location_key in seen_locations:
                        continue
                    seen_locations.add(location_key)

                    matches.append({
                        "repository": repo_name,
                        "path": relative_path.as_posix(),
                        "line": line_number,
                        "match_type": kind,
                        "term": term,
                        "evidence_kind": evidence_kind,
                        "strength": strength,
                        "snippet": line.strip()[:500],
                    })
                    break

                if len(matches) >= limit:
                    return

    source_candidates = [
        item for item in candidates
        if item[3] == "source"
    ]
    reference_candidates = [
        item for item in candidates
        if item[3] in {"reference", "locale_reference"}
    ]

    # 1) Exact symbolic/enum evidence from executable source.
    scan_phase(
        source_candidates,
        exact_terms,
        allowed_strengths={"strong"},
        limit=max_matches,
    )

    # 2) Strong/medium text evidence from executable source.
    scan_phase(
        source_candidates,
        text_terms,
        allowed_strengths={"strong", "medium"},
        limit=max_matches,
    )

    # 3) Keep a very small amount of weak source evidence for diagnostics only.
    weak_limit = min(max_matches, len(matches) + 3)
    scan_phase(
        source_candidates,
        text_terms,
        allowed_strengths={"weak"},
        limit=weak_limit,
    )

    # 4) Locale/reference copies are supporting evidence, never source proof.
    scan_phase(
        reference_candidates,
        exact_terms,
        limit=max_matches,
    )
    scan_phase(
        reference_candidates,
        text_terms,
        limit=max_matches,
    )

    source_matches = [
        match for match in matches
        if match["evidence_kind"] == "source"
    ]
    strong_source_matches = [
        match for match in source_matches
        if match["strength"] == "strong"
    ]
    locale_reference_matches = [
        match for match in matches
        if match["evidence_kind"] == "locale_reference"
    ]

    return {
        "issue": issue,
        "search_terms": [
            {"kind": kind, "term": term}
            for kind, term in terms
        ],
        "match_count": len(matches),
        "source_match_count": len(source_matches),
        "strong_source_match_count": len(strong_source_matches),
        "locale_reference_match_count": len(locale_reference_matches),
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

    numeric_symbols = load_locale_enum_map(
        workspace / "Project_ServerSRC" / "game" / "src" / "Locale.hpp"
    )

    candidates = build_candidate_index(repo_roots)
    text_cache: dict[str, str | None] = {}

    results = [
        scan_issue(
            issue,
            candidates,
            args.max_matches,
            numeric_symbols=numeric_symbols,
            text_cache=text_cache,
        )
        for issue in issues
    ]

    missing_repos = [
        name for name, path in repo_roots
        if not path.is_dir()
    ]
    with_matches = sum(1 for item in results if item["match_count"])
    without_matches = len(results) - with_matches
    with_source_evidence = sum(
        1 for item in results if item["strong_source_match_count"]
    )
    without_source_evidence = len(results) - with_source_evidence

    report = {
        "schema_version": 1,
        "write_enabled": False,
        "workspace": workspace.as_posix(),
        "repositories": [
            {"name": name, "path": path.as_posix(), "exists": path.is_dir()}
            for name, path in repo_roots
        ],
        "missing_repositories": missing_repos,
        "numeric_symbol_count": len(numeric_symbols),
        "indexed_file_count": len(candidates),
        "issue_count": len(results),
        "issues_with_matches": with_matches,
        "issues_without_matches": without_matches,
        "issues_with_source_evidence": with_source_evidence,
        "issues_without_source_evidence": without_source_evidence,
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
        f"with_matches={with_matches} without_matches={without_matches} "
        f"with_source_evidence={with_source_evidence} "
        f"without_source_evidence={without_source_evidence}"
    )
    if missing_repos:
        print("missing repositories: " + ", ".join(missing_repos))
    print(f"report: {Path(args.output).as_posix()}")

    for item in results:
        issue = item["issue"]
        print(
            f"  {issue.get('path')} :: {issue.get('key')} :: "
            f"{issue.get('code')} => matches={item['match_count']} "
            f"source={item['source_match_count']} "
            f"strong_source={item['strong_source_match_count']} "
            f"locale_refs={item['locale_reference_match_count']}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
