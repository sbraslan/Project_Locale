# Locale Guard v1

This directory contains the first, read-only safety layer for the Metin2 Project_Locale automation.

## Why this exists

The current repository cannot safely be translated by a simple "read EN, translate, append to every language" script.

Observed repository facts before v1 was created:

- `master/` and `tr/` currently point to the same Git tree, so master is effectively a Turkish snapshot.
- Turkish files are not uniformly healthy. For example, `locale_interface.txt` contains classic Windows-1254 mojibake such as `Kuþak`, `Saldýr`, `Ýksirler`, while other Turkish files contain correct Unicode Turkish.
- Some files under the Turkish/master tree contain non-Turkish content (German was observed in `battle_pass.txt` and wiki text).
- Language directories do not have perfectly identical file manifests.
- `item_proto` and `mob_proto` are not UTF-8 text and must stay outside this text translation pipeline.
- Locale directory names are not always translation API language codes. Examples: `ae -> ar`, `cz -> cs`, `dk -> da`, `gr -> el`.

## v1 rule: no writes

v1 only audits. It never repairs, translates, appends, deletes, reorders, or rewrites locale files.

Run:

```powershell
python tools/locale_guard/locale_guard.py audit --root .
```

The report is written to:

```text
.locale_guard/report.json
```

Exit codes:

- `0`: no fatal errors
- `2`: one or more fatal audit errors
- `3`: warnings were present and `--fail-on-warning` was requested

## Guards implemented in v1

- UTF-8 / UTF-8-BOM detection and Turkish CP1254 fallback
- mixed-newline detection
- Unicode replacement-character detection
- Turkish Windows-1254 mojibake preview detection
- UTF-8-vs-legacy mojibake preview detection
- heuristic foreign-language contamination warnings for TR/master
- EN-driven TXT path manifest checks
- duplicate TSV key detection
- EN/TR key-set checks
- TSV column-count checks
- protected token checks for printf placeholders, Metin2 pipe markup, bracket directives, escape sequences and URLs
- byte-level TR/master mirror check using SHA-256

## Planned next gates

The writer/translator must remain disabled until the audit report is understood and the baseline is clean.

After that, the pipeline will be added in separate stages:

1. deterministic Turkish repair with preview + explicit change journal
2. parser registry per file family, so only human-readable columns are translatable
3. EN -> TR translation for missing/corrupt/non-Turkish text with token masking
4. TR -> EN back-translation plus semantic-equivalence gate
5. validated TR -> master snapshot promotion
6. EN -> target-language translation
7. target -> EN back-translation plus semantic-equivalence gate
8. add/update only the changed logical units, never rewrite unrelated rows
9. dry-run diff, backup/checkpoint, rollback journal and idempotency checks
10. CI gate and PR-only workflow; direct writes to main stay forbidden

The translation layer must never use a whole-file blind replacement strategy.


## Locale Guard v2 decoding rules

The audit always tries UTF-8 first. If UTF-8 fails, it may use the configured
legacy code page for that locale. The fallback is accepted only when decoding
and re-encoding produces the exact original byte sequence.

Configured legacy families currently include:

- CP1250: cz, hu, pl, ro
- CP1251: ru
- CP1252: de, dk, en, es, fr, it, nl, pt
- CP1253: gr
- CP1254: tr and master
- CP1256: ae

Important: these fallbacks are input-decoding rules only. The audit never
rewrites or normalizes the source file encoding.

File manifests are now built from actual filesystem paths independently of
whether text decoding succeeds. Therefore an undecodable file is not
misreported as a missing file.

TR/master equality is checked from raw byte SHA-256 values, not decoded text.


## Parser registry

Cross-language TSV comparison is now fail-closed and allowlisted. A file is
compared only when its format is explicitly registered in config.json.

Examples:

- locale_game / locale_interface / locale_string: single logical key
- locale_quiz: composite key (category, question id)
- shop_deco: composite key (category, entry)
- guildbuildinglist: only the NAME column is treated as translatable
- pet_skill: only name and description columns are treated as translatable
- skilldesc: key and column shape checks only for now; token comparison is
  intentionally disabled until the skill column schema is fully mapped

Unknown TSV formats are listed in report.unparsed_tsv_files and are not
silently interpreted with a generic parser.

TR_FOREIGN_LANGUAGE_SUSPECT is heuristic-only and is therefore informational,
not a blocking warning.


## Working-tree integrity gate

The audit now checks tracked locale directories with Git before interpreting
their contents. If a tracked locale file differs from the Git index,
WORKTREE_DIRTY is emitted as a blocking error.

This prevents local editor, unpacker, code-page conversion, or line-ending
changes from being mistaken for defects in the repository baseline.

The guard never restores or rewrites a dirty file automatically.


## Repair planning and runtime contract probe

Generate a read-only repair plan:

    python -m tools.locale_guard.repair_plan --root .

The plan never edits locale files. It separates canonical EN backfill, Turkish
backfill, runtime-contract review, layout review, encoding integrity, manifest
review and informational findings.

For runtime-format findings, probe the sibling project repositories from the
Project_Locale checkout:

    python -m tools.locale_guard.contract_probe --root . --workspace ..

The probe scans Project_ClientSrc, Project_ServerSRC, Project_Game and
Project_Binary read-only and writes:

    .locale_guard/contract_probe.json

For symbolic locale keys it searches the exact key. For numeric locale_string
entries it derives stable literal text fragments from the EN/TR values. The
probe is evidence collection only; it never changes source or locale files.

Printf validation distinguishes:
- argument contract differences such as %d vs %s or %lu vs %u (blocking),
- formatting-only differences such as % d vs %d (non-blocking review),
- literal %% (does not consume a vararg),
- layout escapes such as \n (separate non-blocking review).


## Runtime decision verification

Runtime-contract fixes are locked in:

    tools/locale_guard/runtime_contract_decisions.json

The manifest is read-only policy data. It separates evidence-backed decisions
from unresolved contracts and does not modify locale files.

Verify the current repair plan and every external evidence snippet before any
future patch generation:

    python -m tools.locale_guard.runtime_decision_check --root . --workspace ..

The verifier fails closed when:
- a runtime issue is missing from the decision manifest,
- the manifest contains a stale/extra issue,
- a current source/target value drifted,
- an evidence file or exact evidence snippet is missing,
- a resolved decision has no proposed value,
- an unresolved decision accidentally proposes a value.

A successful verification only means the resolved subset is eligible for a
future dry-run patch. It never applies locale changes.
