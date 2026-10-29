#!/usr/bin/env python3
"""Fail-closed checkpoint and isolated master->tr rehearsal. No live apply."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path, PurePosixPath

from tools.locale_guard.locale_guard import decode_text, layout_tokens, protected_tokens

PREVIEW_SHA = "aa529ec168d1765b63339ded4fd583a4e08a4b539f76a62b14d52282465226bd"
CANDIDATE_SHA = "37b2f2fbba4bf90a611eef71c7a95cfa1ab9c94491688b65f2efe7bf603b4acb"
PACK_SHA = "bfa10b05469ed8167f1646e18b9be49856834084fa0dbb63c331f4c6bd4a4896"
EXCLUDED = ("wiki/systems/dragon_alchemy.txt", 28, 0)


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise RuntimeError("[BLOCK] " + reason)


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def relative_path(value: str) -> Path:
    require(isinstance(value, str) and bool(value), "Empty file path")
    p = PurePosixPath(value)
    require(
        not p.is_absolute() and ".." not in p.parts and "." not in p.parts
        and "\\" not in value and ":" not in value,
        "Unsafe path: " + str(value),
    )
    return Path(*p.parts)


def read_safe(root: Path, path: Path, expected_sha: str | None = None) -> bytes:
    require(path.is_relative_to(root), "Path outside repository")
    cursor = path
    while cursor != root:
        require(not cursor.is_symlink(), "Symlink path denied: " + str(cursor))
        cursor = cursor.parent
    require(path.is_file(), "Missing file: " + str(path))
    raw = path.read_bytes()
    if expected_sha is not None:
        require(digest(raw) == expected_sha, "SHA256 drift: " + str(path))
    return raw


def json_safe(root: Path, path: Path, expected: str) -> dict:
    return json.loads(read_safe(root, path, expected).decode("utf-8-sig"))


def split_eol(line: str) -> tuple[str, str]:
    for ending in ("\r\n", "\n", "\r"):
        if line.endswith(ending):
            return line[:-len(ending)], ending
    return line, ""


def verify_file_cells(
    *, rel: str, target: str, preview: str, master: str, candidate: str,
) -> tuple[int, int, int]:
    old = target.splitlines(keepends=True)
    new = preview.splitlines(keepends=True)
    source = master.splitlines(keepends=True)
    suggested = candidate.splitlines(keepends=True)
    require(len(old) == len(new) == len(source) == len(suggested),
            rel + ": line count drift")
    changed = unapproved = candidate_diff = 0
    for index, lines in enumerate(zip(old, new, source, suggested), start=1):
        a, b, m, c = (split_eol(x) for x in lines)
        require(a[1] == b[1], rel + ": changed newline at " + str(index))
        acol, bcol, mcol, ccol = (v[0].split("\t") for v in (a, b, m, c))
        require(len(acol) == len(bcol) == len(mcol) == len(ccol),
                rel + ": column count drift at " + str(index))
        for column, (before, after, original, proposal) in enumerate(
            zip(acol, bcol, mcol, ccol)
        ):
            key = (rel, index, column)
            if original != proposal:
                candidate_diff += 1
            if before == after:
                if original != proposal and before != proposal:
                    require(key == EXCLUDED, "Unexpected unapplied cell: " + str(key))
                    require(before == original, "Excluded row changed: " + str(key))
                    unapproved += 1
                continue
            require(key != EXCLUDED, "Unapproved change applied: " + str(key))
            require(before == original, "Target did not match master: " + str(key))
            require(after == proposal, "Preview not equal to candidate: " + str(key))
            require(protected_tokens(before) == protected_tokens(after),
                    "Protected token changed: " + str(key))
            require(layout_tokens(before) == layout_tokens(after),
                    "Layout token changed: " + str(key))
            require("\ufffd" not in after, "Unicode replacement char: " + str(key))
            changed += 1
    return changed, unapproved, candidate_diff


def validate(root: Path) -> tuple[dict, dict[str, bytes]]:
    guard = root / ".locale_guard"
    stage = guard / "master_tr_merge_preview_v1"
    candidate_root = guard / "staging_master_tr_v3_semantic_candidate"
    preview = json_safe(root, stage / "manifest.json", PREVIEW_SHA)
    candidate = json_safe(root, candidate_root / "candidate_manifest.json", CANDIDATE_SHA)
    pack = json_safe(root, guard / "master_to_tr_semantic_review_pack_v3.json", PACK_SHA)
    require(root.name == "Project_Locale", "Not Project_Locale")
    require(preview.get("status") == "MASTER_TR_MERGE_PREVIEW_UNAPPROVED",
            "Wrong preview state")
    require(preview.get("file_count") == candidate.get("file_count") == 46,
            "Expected 46 files")
    require(preview.get("planned_change_count") == 316, "Expected 316 changes")
    require(preview.get("skipped_unapproved_count") == 1, "Expected one hold")
    require(preview.get("candidate_manifest_sha256") == CANDIDATE_SHA,
            "Parent candidate mismatch")
    require(candidate.get("human_approved_count") == 0
            and candidate.get("apply_authorized") is False
            and preview.get("apply_authorized") is False,
            "Approval state changed; stop and review")
    manifests = (preview["files"], candidate["files"], pack["file_inventory"])
    maps = [{item["path"]: item for item in items} for items in manifests]
    require(all(len(items) == len(mapping) == 46 for items, mapping in zip(manifests, maps)),
            "Duplicate manifest paths")
    require(set(maps[0]) == set(maps[1]) == set(maps[2]), "Inventory mismatch")
    previews: dict[str, bytes] = {}
    target_hashes: dict[str, str] = {}
    master_hashes: dict[str, str] = {}
    changes = holds = candidate_diffs = 0
    for rel in sorted(maps[0]):
        path = relative_path(rel)
        entry, ce, source = (mp[rel] for mp in maps)
        tr_path = root / "tr" / path
        master_path = root / "master" / path
        raw_tr = read_safe(root, tr_path, entry["target_before_sha256"])
        raw_master = read_safe(root, master_path, source["source_sha256"])
        raw_preview = read_safe(root, stage / "tr" / path, entry["preview_sha256"])
        raw_candidate = read_safe(root, candidate_root / path, ce["candidate_sha256"])
        current, current_codec, current_bom = decode_text(
            raw_tr, language="tr", legacy_encodings=("cp1254",)
        )
        proposed, proposed_codec, proposed_bom = decode_text(
            raw_preview, language="tr", legacy_encodings=("cp1254",)
        )
        require((current_codec, current_bom) == (proposed_codec, proposed_bom),
                rel + ": target encoding/BOM changed")
        require(current.encode(current_codec) == raw_tr
                and proposed.encode(proposed_codec) == raw_preview,
                rel + ": target encoding roundtrip failed")
        codec = source["encoding"]
        master = raw_master.decode(codec, "strict")
        candidate_text = raw_candidate.decode(codec, "strict")
        require(master.encode(codec) == raw_master
                and candidate_text.encode(codec) == raw_candidate,
                rel + ": candidate encoding roundtrip failed")
        n, h, d = verify_file_cells(
            rel=rel, target=current, preview=proposed,
            master=master, candidate=candidate_text,
        )
        require(n == entry["changes"], rel + ": change count mismatch")
        changes += n
        holds += h
        candidate_diffs += d
        previews[rel] = raw_preview
        target_hashes[rel] = entry["target_before_sha256"]
        master_hashes[rel] = source["source_sha256"]
    require((changes, holds, candidate_diffs) == (316, 1, 317),
            "Expected changes=316, hold=1, candidate differences=317")
    # Ensure files were not modified during verification.
    for rel in previews:
        p = relative_path(rel)
        read_safe(root, root / "tr" / p, target_hashes[rel])
        read_safe(root, root / "master" / p, master_hashes[rel])
    summary = {
        "status": "TEST_ONLY_UNAPPROVED",
        "master": "master",
        "target": "tr",
        "preview_manifest_sha256": PREVIEW_SHA,
        "candidate_manifest_sha256": CANDIDATE_SHA,
        "file_count": len(previews),
        "planned_change_count": changes,
        "excluded_unapproved_count": holds,
        "candidate_difference_count": candidate_diffs,
        "human_approved_count": 0,
        "live_apply_performed": False,
        "files": [{"path": rel, "sha256": digest(raw)} for rel, raw in sorted(previews.items())],
    }
    return summary, previews


def create_checkpoint(root: Path, summary: dict) -> Path:
    folder = root / ".locale_guard" / "checkpoints"
    require(not folder.is_symlink(), "Symlink checkpoint directory")
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / ("master_tr_" + PREVIEW_SHA[:12] + ".json")
    data = (json.dumps(summary, ensure_ascii=True, indent=2) + "\n").encode("ascii")
    if target.exists() or target.is_symlink():
        require(read_safe(root, target) == data, "Existing checkpoint differs")
        return target
    with target.open("xb") as handle:
        handle.write(data)
    return target


def rehearse(root: Path, summary: dict, previews: dict[str, bytes]) -> Path:
    guard = root / ".locale_guard"
    output = guard / ("test_apply_master_tr_" + PREVIEW_SHA[:12])
    require(not output.is_symlink(), "Rehearsal output is a symlink")
    report = (json.dumps(summary, ensure_ascii=True, indent=2) + "\n").encode("ascii")
    if output.exists():
        require(output.is_dir(), "Rehearsal output not a directory")
        require(read_safe(root, output / "report.json") == report,
                "Existing rehearsal report drift")
        for rel, raw in previews.items():
            require(read_safe(root, output / "tr" / relative_path(rel)) == raw,
                    "Existing rehearsal file drift: " + rel)
        return output
    with tempfile.TemporaryDirectory(prefix=".test-apply-tmp-", dir=guard) as folder:
        staged = Path(folder) / "rehearsal"
        staged.mkdir()
        for rel, raw in previews.items():
            p = staged / "tr" / relative_path(rel)
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("xb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            require(p.read_bytes() == raw, "Staged write mismatch: " + rel)
        with (staged / "report.json").open("xb") as handle:
            handle.write(report)
            handle.flush()
            os.fsync(handle.fileno())
        require(not output.exists(), "Output appeared during rehearsal")
        staged.rename(output)
    return output


def main() -> int:
    cli = argparse.ArgumentParser(description="Safe progress checkpoint and test-only TR rehearsal")
    cli.add_argument("action", choices=("status", "checkpoint", "rehearse"))
    cli.add_argument("--root", default=".")
    args = cli.parse_args()
    root = Path(args.root).resolve()
    try:
        summary, previews = validate(root)
        print("MASTER -> TR VERIFIED: files=46 changes=316 skipped_unapproved=1")
        print("CODEC / TOKENS / MASTER & TR HASHES: PASS")
        if args.action == "checkpoint":
            print("CHECKPOINT:", create_checkpoint(root, summary))
        elif args.action == "rehearse":
            print("ISOLATED TEST:", rehearse(root, summary, previews))
            print("TEST APPLY: PASS (sandbox only)")
        print("LIVE TR MODIFIED: NO")
        print("MASTER MODIFIED: NO")
        print("LIVE APPLY AUTHORIZED: NO")
        return 0
    except (RuntimeError, OSError, ValueError, UnicodeError, KeyError, TypeError) as error:
        print("[BLOCK]", error)
        print("LIVE APPLY: NOT PERFORMED")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
