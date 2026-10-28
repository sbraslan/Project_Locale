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
