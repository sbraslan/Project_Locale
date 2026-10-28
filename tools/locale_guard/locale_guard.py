#!/usr/bin/env python3
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Sequence

PRINTF_RE = re.compile(
    r"%(?:\d+\$)?[-+#0 ']*(?:\d+|\*)?(?:\.(?:\d+|\*))?"
    r"(?:hh|h|ll|l|j|z|t|L)?[diuoxXfFeEgGaAcspn%]"
)
BRACE_RE = re.compile(r"\{[^{}\r\n]*\}")
METIN_PIPE_RE = re.compile(r"\|(?:c[0-9A-Fa-f]{6,8}|H|h|r)")
BRACKET_DIRECTIVE_RE = re.compile(r"\[[A-Z_][A-Z0-9_]*(?:\s*=[^\]\r\n]*)?(?:;[^\]\r\n]*)?\]")
ESCAPE_RE = re.compile(r"\\(?:[nrt\\'\"0]|x[0-9A-Fa-f]{2}|u[0-9A-Fa-f]{4})")
URL_RE = re.compile(r"(?i)\bhttps?://[^\s\t]+")

TR_CP1254_MOJIBAKE_MAP = str.maketrans({
    "þ": "ş",
    "Þ": "Ş",
    "ý": "ı",
    "Ý": "İ",
    "ð": "ğ",
    "Ð": "Ğ",
})
TR_CP1254_BAD_CHARS = frozenset("þÞýÝðÐ")
UTF8_MOJIBAKE_MARKERS = ("Ã", "Â", "Ä", "Å", "â€", "â€™", "â€œ", "â€", "�")

DE_WORDS = frozenset({
    "der","die","das","den","dem","des","ein","eine","einer","einen","einem",
    "und","oder","nicht","noch","mit","für","auf","wenn","sie","ist","sind",
    "werden","wird","zu","von","um","belohnung","mission","objekt","erhalten",
    "benötigt","verbleibender","sammle","besiege","beende","jeder","monat"
})
EN_WORDS = frozenset({
    "the","a","an","and","or","not","with","for","if","you","your","is","are",
    "will","to","from","reward","mission","item","receive","required","remaining",
    "collect","defeat","complete","every","month"
})
TR_WORDS = frozenset({
    "ve","veya","değil","ile","için","eğer","sen","senin","bir","bu","şu",
    "olan","olarak","görev","ödül","eşya","nesne","al","kalan","topla","yen",
    "tamamla","her","ay","gerekli","kullan","oyuncu","lonca"
})
WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

@dataclass
class Issue:
    severity: str
    code: str
    path: str
    message: str
    line: int | None = None
    key: str | None = None
    suggestion: str | None = None
    language: str | None = None

@dataclass
class FileInfo:
    path: str
    size: int
    sha256: str
    encoding: str
    bom: bool
    newline: str
    final_newline: bool
    mode: str
    lines: int

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def newline_style(data: bytes) -> str:
    crlf = data.count(b"\r\n")
    lf = data.count(b"\n") - crlf
    cr = data.count(b"\r") - crlf
    kinds = sum(bool(x) for x in (crlf, lf, cr))
    if kinds > 1:
        return "mixed"
    if crlf:
        return "CRLF"
    if lf:
        return "LF"
    if cr:
        return "CR"
    return "none"

def decode_text(
    data: bytes,
    *,
    language: str,
    legacy_encodings: Sequence[str] = (),
) -> tuple[str, str, bool]:
    bom = data.startswith(b"\xef\xbb\xbf")
    if bom:
        return data.decode("utf-8-sig"), "utf-8-sig", True

    try:
        return data.decode("utf-8"), "utf-8", False
    except UnicodeDecodeError as utf8_error:
        for encoding in legacy_encodings:
            try:
                text = data.decode(encoding)
            except (UnicodeDecodeError, LookupError):
                continue

            # A fallback is accepted only when it is lossless for the original bytes.
            try:
                if text.encode(encoding) != data:
                    continue
            except (UnicodeEncodeError, LookupError):
                continue

            return text, encoding, False

        raise utf8_error

def protected_tokens(text: str) -> list[str]:
    tokens: list[tuple[int, str]] = []
    for regex in (PRINTF_RE, BRACE_RE, METIN_PIPE_RE, BRACKET_DIRECTIVE_RE, ESCAPE_RE, URL_RE):
        for match in regex.finditer(text):
            tokens.append((match.start(), match.group(0)))
    tokens.sort(key=lambda value: value[0])
    return [token for _, token in tokens]

def cp1254_mojibake_candidate(text: str) -> str | None:
    if not any(ch in text for ch in TR_CP1254_BAD_CHARS):
        return None
    candidate = text.translate(TR_CP1254_MOJIBAKE_MAP)
    return candidate if candidate != text else None

def utf8_mojibake_candidate(text: str) -> str | None:
    if not any(marker in text for marker in UTF8_MOJIBAKE_MARKERS):
        return None
    for legacy in ("cp1252", "latin-1"):
        try:
            candidate = text.encode(legacy).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if candidate != text:
            return candidate
    return None

def suspicious_foreign_language(text: str) -> tuple[str | None, dict[str, int]]:
    scrubbed = BRACKET_DIRECTIVE_RE.sub(" ", text)
    scrubbed = METIN_PIPE_RE.sub(" ", scrubbed)
    words = [word.casefold() for word in WORD_RE.findall(scrubbed)]
    counts = {
        "de": sum(word in DE_WORDS for word in words),
        "en": sum(word in EN_WORDS for word in words),
        "tr": sum(word in TR_WORDS for word in words),
    }
    if counts["de"] >= 2 and counts["de"] > counts["tr"] + 1:
        return "de", counts
    if counts["en"] >= 3 and counts["en"] > counts["tr"] + 1:
        return "en", counts
    return None, counts

def classify_mode(text: str) -> str:
    nonempty = [line for line in text.splitlines() if line.strip()]
    if not nonempty:
        return "empty"
    tabbed = [line for line in nonempty if "\t" in line]
    if len(tabbed) / len(nonempty) >= 0.60:
        return "tsv"
    return "freeform"

def keyed_tsv_rows(
    text: str,
    *,
    key_columns: Sequence[int] = (0,),
    header_rows: int = 0,
    comment_prefixes: Sequence[str] = ("#",),
) -> tuple[dict[str, list[str]], list[str]]:
    rows: dict[str, list[str]] = {}
    duplicates: list[str] = []
    logical_index = 0

    for raw in text.splitlines():
        if not raw.strip() or "\t" not in raw:
            continue
        if any(raw.lstrip().startswith(prefix) for prefix in comment_prefixes):
            continue

        if logical_index < header_rows:
            logical_index += 1
            continue
        logical_index += 1

        columns = raw.split("\t")
        if any(index >= len(columns) for index in key_columns):
            continue

        key_parts = [columns[index] for index in key_columns]
        key = "|".join(key_parts)
        if key in rows:
            duplicates.append(key)
        else:
            rows[key] = columns

    return rows, duplicates

def tsv_rows(text: str) -> tuple[dict[str, list[str]], list[str]]:
    return keyed_tsv_rows(text)

def relative_txt_paths(base: Path) -> set[str]:
    return {
        path.relative_to(base).as_posix()
        for path in base.rglob("*.txt")
        if path.is_file()
    }

def parse_git_porcelain(output: str) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for line in output.splitlines():
        if len(line) < 4:
            continue
        status = line[:2]
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        entries.append((status, path.replace("\\", "/")))
    return entries

def git_dirty_locale_paths(
    root: Path,
    directories: Sequence[str],
) -> tuple[list[tuple[str, str]], str | None]:
    try:
        completed = subprocess.run(
            [
                "git", "status", "--porcelain=v1",
                "--untracked-files=no", "--",
                *directories,
            ],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        return [], str(exc)

    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip()
        return [], message or f"git status returned {completed.returncode}"

    return parse_git_porcelain(completed.stdout), None

def git_head_blob_bytes(
    root: Path,
    repo_path: str,
) -> tuple[bytes | None, str | None]:
    try:
        completed = subprocess.run(
            ["git", "show", f"HEAD:{repo_path}"],
            cwd=root,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        return None, str(exc)

    if completed.returncode != 0:
        message = completed.stderr.decode("utf-8", errors="replace").strip()
        return None, message or f"git show returned {completed.returncode}"

    return completed.stdout, None

def structural_issue_breakdown(issues: Sequence[Issue]) -> list[dict]:
    structural_codes = {
        "TR_MISSING_KEY",
        "TR_EXTRA_KEY",
        "DUPLICATE_KEY",
        "COLUMN_COUNT_MISMATCH",
        "PROTECTED_TOKEN_MISMATCH",
    }
    by_path: dict[str, collections.Counter[str]] = {}
    for issue in issues:
        if issue.code not in structural_codes:
            continue
        counter = by_path.setdefault(issue.path, collections.Counter())
        counter[issue.code] += 1

    rows = []
    for path, counter in by_path.items():
        rows.append({
            "path": path,
            "total": sum(counter.values()),
            "codes": dict(counter),
        })
    rows.sort(key=lambda row: (-row["total"], row["path"]))
    return rows

def inspect_file(
    path: Path,
    lang: str,
    relative: str,
    legacy_encodings: Sequence[str] = (),
) -> tuple[FileInfo | None, str | None, list[Issue]]:
    issues: list[Issue] = []
    display_path = f"{lang}/{relative}"
    data = path.read_bytes()
    try:
        text, encoding, bom = decode_text(
            data,
            language=lang,
            legacy_encodings=legacy_encodings,
        )
    except UnicodeDecodeError as exc:
        issues.append(Issue(
            "error", "ENCODING_UNDECODABLE", display_path,
            f"Text file could not be decoded safely: {exc}",
            language=lang
        ))
        return None, None, issues

    newline = newline_style(data)
    if newline == "mixed":
        issues.append(Issue(
            "error", "NEWLINE_MIXED", display_path,
            "Mixed newline styles detected.",
            language=lang
        ))

    if "\ufffd" in text:
        issues.append(Issue(
            "error", "REPLACEMENT_CHAR", display_path,
            "Unicode replacement character U+FFFD found; data was already lost or mis-decoded.",
            language=lang
        ))

    info = FileInfo(
        path=relative,
        size=len(data),
        sha256=sha256_bytes(data),
        encoding=encoding,
        bom=bom,
        newline=newline,
        final_newline=data.endswith((b"\n", b"\r")),
        mode=classify_mode(text),
        lines=len(text.splitlines()),
    )
    return info, text, issues

def audit_turkish_text(display_path: str, text: str, language: str) -> list[Issue]:
    issues: list[Issue] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        cp_candidate = cp1254_mojibake_candidate(line)
        if cp_candidate:
            issues.append(Issue(
                "error", "TR_CP1254_MOJIBAKE", display_path,
                "Windows-1254 mojibake characters detected.",
                line=line_number, suggestion=cp_candidate, language=language
            ))
        utf8_candidate = utf8_mojibake_candidate(line)
        if utf8_candidate:
            issues.append(Issue(
                "error", "TR_UTF8_MOJIBAKE", display_path,
                "Probable UTF-8/legacy-codepage mojibake detected.",
                line=line_number, suggestion=utf8_candidate, language=language
            ))
        detected_language, counts = suspicious_foreign_language(line)
        if detected_language:
            issues.append(Issue(
                "info", "TR_FOREIGN_LANGUAGE_SUSPECT", display_path,
                f"Line looks more like {detected_language.upper()} than Turkish (scores={counts}).",
                line=line_number, language=language
            ))
    return issues

def compare_registered_tsv(
    en_path: str,
    tr_path: str,
    en_text: str,
    tr_text: str,
    metadata_keys: set[str],
    en_language: str,
    tr_language: str,
    parser_spec: dict,
) -> list[Issue]:
    issues: list[Issue] = []
    key_columns = tuple(parser_spec.get("key_columns", [0]))
    header_rows = int(parser_spec.get("header_rows", 0))
    token_columns = parser_spec.get("token_columns", "all_non_key")

    en_rows, en_duplicates = keyed_tsv_rows(
        en_text,
        key_columns=key_columns,
        header_rows=header_rows,
    )
    tr_rows, tr_duplicates = keyed_tsv_rows(
        tr_text,
        key_columns=key_columns,
        header_rows=header_rows,
    )

    for key in en_duplicates:
        issues.append(Issue(
            "error", "DUPLICATE_KEY", en_path,
            "Duplicate logical TSV key.", key=key, language=en_language
        ))
    for key in tr_duplicates:
        issues.append(Issue(
            "error", "DUPLICATE_KEY", tr_path,
            "Duplicate logical TSV key.", key=key, language=tr_language
        ))

    def is_metadata_key(key: str) -> bool:
        return len(key_columns) == 1 and key in metadata_keys

    en_keys = {key for key in en_rows if not is_metadata_key(key)}
    tr_keys = {key for key in tr_rows if not is_metadata_key(key)}

    for key in sorted(en_keys - tr_keys):
        issues.append(Issue(
            "error", "TR_MISSING_KEY", tr_path,
            "Logical key exists in EN but is missing in TR.",
            key=key, language=tr_language
        ))
    for key in sorted(tr_keys - en_keys):
        issues.append(Issue(
            "warning", "TR_EXTRA_KEY", tr_path,
            "Logical key exists in TR but not in EN.",
            key=key, language=tr_language
        ))

    for key in sorted(en_keys & tr_keys):
        en_columns = en_rows[key]
        tr_columns = tr_rows[key]
        if len(en_columns) != len(tr_columns):
            issues.append(Issue(
                "error", "COLUMN_COUNT_MISMATCH", tr_path,
                f"TSV column count differs from EN ({len(tr_columns)} vs {len(en_columns)}).",
                key=key, language=tr_language
            ))
            continue

        if token_columns == "disabled":
            continue
        if token_columns == "all_non_key":
            columns_to_check = [
                index for index in range(len(en_columns))
                if index not in key_columns
            ]
        else:
            columns_to_check = [
                int(index) for index in token_columns
                if int(index) < len(en_columns)
            ]

        for column_index in columns_to_check:
            en_value = en_columns[column_index]
            tr_value = tr_columns[column_index]
            en_tokens = collections.Counter(protected_tokens(en_value))
            tr_tokens = collections.Counter(protected_tokens(tr_value))
            if en_tokens != tr_tokens:
                issues.append(Issue(
                    "error", "PROTECTED_TOKEN_MISMATCH", tr_path,
                    f"Protected tokens differ from EN in column {column_index}: "
                    f"EN={dict(en_tokens)} TR={dict(tr_tokens)}",
                    key=key, language=tr_language
                ))
    return issues

def audit_repository(root: Path, config: dict) -> dict:
    languages = config["languages"]
    canonical = config["canonical_language"]
    turkish = config["turkish_language"]
    master = config["master_directory"]
    metadata_keys = set(config.get("metadata_keys", []))
    parser_registry = config.get("parser_registry", {})
    legacy_decodings = config.get("legacy_decodings", {})

    issues: list[Issue] = []

    dirty_entries, git_error = git_dirty_locale_paths(
        root,
        [*languages, master],
    )
    if git_error:
        issues.append(Issue(
            "warning", "GIT_STATUS_UNAVAILABLE", ".",
            f"Could not verify locale working-tree cleanliness: {git_error}"
        ))
    else:
        for status, dirty_path in dirty_entries:
            language = dirty_path.split("/", 1)[0] if "/" in dirty_path else None
            issues.append(Issue(
                "error", "WORKTREE_DIRTY", dirty_path,
                f"Tracked locale file differs from the Git index (status={status!r}). "
                "Audit results may not represent the branch baseline.",
                language=language,
            ))

    files: dict[str, list[dict]] = {}
    texts: dict[tuple[str, str], str] = {}
    path_manifests: dict[str, set[str]] = {}
    raw_hashes: dict[tuple[str, str], str] = {}

    for language in languages + [master]:
        language_dir = root / language
        if not language_dir.is_dir():
            issues.append(Issue(
                "error", "LANG_DIR_MISSING", language,
                "Language directory is missing.",
                language=language
            ))
            continue

        language_infos: list[dict] = []
        manifest = relative_txt_paths(language_dir)
        path_manifests[language] = manifest
        encoding_candidates = tuple(legacy_decodings.get(language, []))

        for relative in sorted(manifest):
            path = language_dir / relative
            raw_hashes[(language, relative)] = sha256_bytes(path.read_bytes())
            info, text, file_issues = inspect_file(
                path,
                language,
                relative,
                encoding_candidates,
            )
            issues.extend(file_issues)

            anomaly_codes = {issue.code for issue in file_issues}
            if anomaly_codes & {"ENCODING_UNDECODABLE", "NEWLINE_MIXED"}:
                repo_path = f"{language}/{relative}"
                head_bytes, head_error = git_head_blob_bytes(root, repo_path)
                if head_error:
                    issues.append(Issue(
                        "warning", "HEAD_BLOB_UNAVAILABLE", repo_path,
                        f"Could not read HEAD blob for anomaly diagnostics: {head_error}",
                        language=language,
                    ))
                elif head_bytes is not None:
                    worktree_bytes = path.read_bytes()
                    worktree_sha = sha256_bytes(worktree_bytes)
                    head_sha = sha256_bytes(head_bytes)

                    if worktree_sha != head_sha:
                        issues.append(Issue(
                            "error", "WORKTREE_BYTES_DIFFER_FROM_HEAD", repo_path,
                            "Working-tree bytes differ from the HEAD blob even though "
                            "Git status may consider filters/normalization. "
                            f"worktree_sha256={worktree_sha} head_sha256={head_sha}",
                            language=language,
                        ))

                    if "ENCODING_UNDECODABLE" in anomaly_codes:
                        try:
                            _, head_encoding, _ = decode_text(
                                head_bytes,
                                language=language,
                                legacy_encodings=encoding_candidates,
                            )
                        except UnicodeDecodeError:
                            issues.append(Issue(
                                "info", "HEAD_BLOB_UNDECODABLE", repo_path,
                                "HEAD blob is also undecodable with the configured rules.",
                                language=language,
                            ))
                        else:
                            issues.append(Issue(
                                "info", "HEAD_BLOB_DECODES", repo_path,
                                f"HEAD blob decodes successfully as {head_encoding}.",
                                language=language,
                            ))

                    if "NEWLINE_MIXED" in anomaly_codes:
                        issues.append(Issue(
                            "info", "HEAD_BLOB_NEWLINE", repo_path,
                            f"HEAD blob newline style is {newline_style(head_bytes)}.",
                            language=language,
                        ))

            if info:
                language_infos.append(asdict(info))
            if text is not None:
                texts[(language, relative)] = text
                if language == turkish:
                    issues.extend(audit_turkish_text(f"{language}/{relative}", text, language))
        files[language] = language_infos

    canonical_paths = path_manifests.get(canonical, set())
    for language in languages:
        if language == canonical:
            continue
        language_paths = path_manifests.get(language, set())
        for relative in sorted(canonical_paths - language_paths):
            issues.append(Issue(
                "error", "TARGET_FILE_MISSING", f"{language}/{relative}",
                f"File exists in {canonical} but not in {language}.",
                language=language
            ))
        for relative in sorted(language_paths - canonical_paths):
            issues.append(Issue(
                "warning", "TARGET_FILE_EXTRA", f"{language}/{relative}",
                f"File exists in {language} but not in {canonical}.",
                language=language
            ))

    turkish_paths = path_manifests.get(turkish, set())
    for relative in sorted(canonical_paths & turkish_paths):
        en_text = texts.get((canonical, relative))
        tr_text = texts.get((turkish, relative))
        if en_text is None or tr_text is None:
            continue
        parser_spec = parser_registry.get(relative)
        if (
            parser_spec
            and parser_spec.get("kind") == "keyed_tsv"
            and classify_mode(en_text) == "tsv"
            and classify_mode(tr_text) == "tsv"
        ):
            issues.extend(compare_registered_tsv(
                f"{canonical}/{relative}",
                f"{turkish}/{relative}",
                en_text,
                tr_text,
                metadata_keys,
                canonical,
                turkish,
                parser_spec,
            ))

    master_paths = path_manifests.get(master, set())
    for relative in sorted(turkish_paths | master_paths):
        if relative not in turkish_paths or relative not in master_paths:
            issues.append(Issue(
                "error", "MASTER_TR_PATH_MISMATCH", f"{master}/{relative}",
                "TR/master file manifest differs.",
                language=master
            ))
            continue

        if raw_hashes.get((turkish, relative)) != raw_hashes.get((master, relative)):
            issues.append(Issue(
                "error", "MASTER_TR_CONTENT_MISMATCH", f"{master}/{relative}",
                "master/ is not byte-identical to tr/.",
                language=master
            ))

    unparsed_tsv_files = sorted(
        relative
        for relative in canonical_paths
        if relative not in parser_registry
        and (canonical, relative) in texts
        and classify_mode(texts[(canonical, relative)]) == "tsv"
    )

    structural_breakdown = structural_issue_breakdown(issues)

    counts = collections.Counter(issue.severity for issue in issues)
    encoding_summary = {
        language: dict(collections.Counter(
            item["encoding"] for item in language_files
        ))
        for language, language_files in files.items()
    }

    return {
        "schema_version": 2,
        "canonical_language": canonical,
        "turkish_language": turkish,
        "master_directory": master,
        "summary": dict(counts),
        "encoding_summary": encoding_summary,
        "unparsed_tsv_files": unparsed_tsv_files,
        "structural_breakdown": structural_breakdown,
        "files": files,
        "issues": [asdict(issue) for issue in issues],
    }

def load_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)
    required = {"languages", "canonical_language", "turkish_language", "master_directory"}
    missing = required - set(config)
    if missing:
        raise ValueError(f"Missing config keys: {sorted(missing)}")
    return config

def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail-closed audit for Project_Locale.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    audit_parser = subparsers.add_parser("audit", help="Audit locale files without modifying anything.")
    audit_parser.add_argument("--root", default=".", help="Repository root.")
    audit_parser.add_argument("--config", default="tools/locale_guard/config.json")
    audit_parser.add_argument("--report", default=".locale_guard/report.json")
    audit_parser.add_argument("--fail-on-warning", action="store_true")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    config = load_config(root / args.config)
    report = audit_repository(root, config)

    report_path = root / args.report
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8"
    )

    errors = report["summary"].get("error", 0)
    warnings = report["summary"].get("warning", 0)
    infos = report["summary"].get("info", 0)
    print(f"locale-guard: errors={errors} warnings={warnings} info={infos}")
    print(f"report: {Path(args.report).as_posix()}")

    undecodable = [
        issue for issue in report["issues"]
        if issue["code"] == "ENCODING_UNDECODABLE"
    ]
    if undecodable and len(undecodable) <= 10:
        print("undecodable files:")
        for issue in undecodable:
            print(f"  - {issue['path']}")

    unparsed = report.get("unparsed_tsv_files", [])
    if unparsed:
        print(f"unparsed TSV files: {len(unparsed)}")

    structural = report.get("structural_breakdown", [])
    if structural:
        print("top structural issue files:")
        for row in structural[:10]:
            codes = ", ".join(
                f"{code}={count}"
                for code, count in sorted(row["codes"].items())
            )
            print(f"  - {row['path']}: total={row['total']} ({codes})")

    if errors:
        return 2
    if warnings and args.fail_on_warning:
        return 3
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
