import tempfile
import unittest
from pathlib import Path
from tools.locale_guard.contract_probe import (
    base_logical_key,
    classify_evidence_path,
    classify_match_evidence,
    evidence_strength,
    literal_fragments,
    load_locale_enum_map,
    search_terms,
)
from tools.locale_guard.repair_plan import build_repair_plan
from tools.locale_guard.runtime_decision_check import resolve_evidence_path, verify_decisions
from tools.locale_guard.locale_guard import (
    cp1254_mojibake_candidate,
    protected_tokens,
    printf_tokens,
    printf_contract_tokens,
    markup_tokens,
    layout_tokens,
    suspicious_foreign_language,
    tsv_rows,
    inspect_file,
    decode_text,
    keyed_tsv_rows,
    parse_git_porcelain,
    structural_issue_breakdown,
    compare_registered_tsv,
    Issue,
)

class LocaleGuardTests(unittest.TestCase):
    def test_cp1254_mojibake_preview(self):
        self.assertEqual(cp1254_mojibake_candidate("Kuþak 1"), "Kuşak 1")
        self.assertEqual(cp1254_mojibake_candidate("Saldýr"), "Saldır")
        self.assertIsNone(cp1254_mojibake_candidate("Kuşak 1"))

    def test_protected_tokens(self):
        value = "Rate %.1f%% |cffff9d9d|H|h%s|h|r [CENTER_ALIGN]"
        self.assertEqual(
            protected_tokens(value),
            ["%.1f", "%%", "|cffff9d9d", "|H", "|h", "%s", "|h", "|r", "[CENTER_ALIGN]"]
        )

    def test_literal_percent_text_is_not_printf(self):
        self.assertEqual(protected_tokens("100% deadly"), [])
        self.assertEqual(protected_tokens("20% EXP"), [])
        self.assertEqual(protected_tokens("+10% fire resistance"), [])
        self.assertEqual(protected_tokens("attack value % d"), ["% d"])

    def test_plain_brackets_and_turkish_words_are_not_markup(self):
        self.assertEqual(protected_tokens("Inventory [I]"), [])
        self.assertEqual(protected_tokens("[DS] Your reward"), [])
        self.assertEqual(protected_tokens("Saçı sarıya boyar.|Her 3 seviyede"), [])
        self.assertEqual(
            protected_tokens("[CENTER_ALIGN] |cffff9d9d|H|h%s|h|r"),
            ["[CENTER_ALIGN]", "|cffff9d9d", "|H", "|h", "%s", "|h", "|r"],
        )

    def test_layout_tokens_are_separate(self):
        self.assertEqual(layout_tokens("Line 1\\nLine 2\\n"), ["\\n", "\\n"])

    def test_printf_contract_ignores_literal_percent(self):
        self.assertEqual(printf_contract_tokens("%d%%"), ["d"])
        self.assertEqual(printf_contract_tokens("%%%d"), ["d"])

    def test_printf_contract_distinguishes_argument_types(self):
        self.assertEqual(printf_contract_tokens("%d %s %lu"), ["d", "s", "lu"])
        self.assertNotEqual(printf_contract_tokens("%d"), printf_contract_tokens("%s"))

    def test_percent_before_turkish_word_is_not_printf(self):
        self.assertEqual(printf_tokens("15% düşürür"), [])
        self.assertEqual(printf_tokens("20% deneyim"), [])

    def test_printf_contract_allows_visual_flag_difference(self):
        self.assertEqual(printf_contract_tokens("% d"), ["d"])
        self.assertEqual(printf_contract_tokens("%d"), ["d"])
        self.assertNotEqual(printf_tokens("% d"), printf_tokens("%d"))

    def test_printf_suffixes_remain_valid_placeholders(self):
        self.assertEqual(printf_tokens("%dx %ss %dm %dH"), ["%d", "%s", "%d", "%d"])

    def test_markup_tokens_do_not_include_plain_labels(self):
        self.assertEqual(markup_tokens("[DS] Reward [I]"), [])

    def test_occurrence_policy_preserves_repeated_keys(self):
        rows, duplicates = keyed_tsv_rows(
            "KEY\tA\nKEY\tB\n",
            duplicate_policy="occurrence",
        )
        self.assertEqual(set(rows), {"KEY@@0", "KEY@@1"})
        self.assertEqual(duplicates, [])

    def test_excluded_key_column_uses_structural_columns(self):
        rows, duplicates = keyed_tsv_rows(
            "1\tEnglish Name\t30000\n1\tTürkçe Ad\t30001\n",
            key_exclude_columns=(1,),
        )
        self.assertEqual(set(rows), {"1|30000", "1|30001"})
        self.assertEqual(duplicates, [])

    def test_german_contamination_warning(self):
        language, counts = suspicious_foreign_language(
            "Beende die Mission, um deine Belohnung zu erhalten."
        )
        self.assertEqual(language, "de")
        self.assertGreaterEqual(counts["de"], 2)

    def test_tsv_trailing_columns_are_preserved(self):
        rows, duplicates = tsv_rows("10\tName\t\n")
        self.assertEqual(rows["10"], ["10", "Name", ""])
        self.assertEqual(duplicates, [])

    def test_report_paths_are_repo_relative(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.txt"
            path.write_text("KEY\tValue\n", encoding="utf-8")
            info, _, issues = inspect_file(path, "tr", "sample.txt")
            self.assertEqual(info.path, "sample.txt")
            self.assertTrue(all(issue.path == "tr/sample.txt" for issue in issues))

    def test_utf8_is_preferred_before_legacy_fallback(self):
        data = "Türkçe".encode("utf-8")
        text, encoding, bom = decode_text(
            data,
            language="tr",
            legacy_encodings=("cp1254",),
        )
        self.assertEqual(text, "Türkçe")
        self.assertEqual(encoding, "utf-8")
        self.assertFalse(bom)

    def test_cp1251_fallback_is_lossless(self):
        data = "Привет".encode("cp1251")
        text, encoding, bom = decode_text(
            data,
            language="ru",
            legacy_encodings=("cp1251",),
        )
        self.assertEqual(text, "Привет")
        self.assertEqual(encoding, "cp1251")
        self.assertFalse(bom)

    def test_git_porcelain_parser(self):
        entries = parse_git_porcelain(
            " M gr/mob_names.txt\nMM it/insult.txt\n"
        )
        self.assertEqual(
            entries,
            [
                (" M", "gr/mob_names.txt"),
                ("MM", "it/insult.txt"),
            ],
        )

    def test_composite_tsv_keys_are_not_false_duplicates(self):
        rows, duplicates = keyed_tsv_rows(
            "1\t1\tQuestion A\n1\t2\tQuestion B\n",
            key_columns=(0, 1),
        )
        self.assertEqual(set(rows), {"1|1", "1|2"})
        self.assertEqual(duplicates, [])

    def test_header_row_can_be_skipped(self):
        rows, duplicates = keyed_tsv_rows(
            "VNUM\tNAME\n101\tWolf\n",
            key_columns=(0,),
            header_rows=1,
        )
        self.assertEqual(set(rows), {"101"})
        self.assertEqual(duplicates, [])

    def test_structural_issue_breakdown_groups_by_file(self):
        rows = structural_issue_breakdown([
            Issue("error", "TR_MISSING_KEY", "tr/a.txt", "x"),
            Issue("error", "PRINTF_CONTRACT_MISMATCH", "tr/a.txt", "x"),
            Issue("error", "CANONICAL_MISSING_KEY", "en/b.txt", "x"),
            Issue("info", "TR_FOREIGN_LANGUAGE_SUSPECT", "tr/a.txt", "x"),
        ])
        self.assertEqual(rows[0]["path"], "tr/a.txt")
        self.assertEqual(rows[0]["total"], 2)
        self.assertEqual(rows[1]["path"], "en/b.txt")
        self.assertEqual(rows[1]["total"], 1)

    def test_repair_plan_is_read_only_and_fail_closed(self):
        plan = build_repair_plan({
            "issues": [
                {"severity": "error", "code": "CANONICAL_MISSING_KEY", "path": "en/itemdesc.txt"},
                {"severity": "error", "code": "TR_MISSING_KEY", "path": "tr/skilldesc.txt"},
                {"severity": "error", "code": "PRINTF_CONTRACT_MISMATCH", "path": "tr/locale_game.txt"},
                {"severity": "error", "code": "ENCODING_UNDECODABLE", "path": "gr/mob_names.txt"},
            ]
        })
        self.assertFalse(plan["write_enabled"])
        self.assertTrue(plan["fail_closed"])
        self.assertEqual(plan["categories"]["canonical_backfill"]["count"], 1)
        self.assertEqual(plan["categories"]["turkish_backfill"]["count"], 1)
        self.assertEqual(plan["categories"]["runtime_contract_review"]["count"], 1)
        self.assertEqual(plan["categories"]["encoding_integrity"]["count"], 1)
        self.assertEqual(plan["categories"]["canonical_backfill"]["hard_count"], 0)
        self.assertEqual(plan["categories"]["turkish_backfill"]["hard_count"], 0)
        self.assertEqual(plan["categories"]["runtime_contract_review"]["hard_count"], 1)
        self.assertEqual(plan["categories"]["encoding_integrity"]["hard_count"], 1)
        self.assertEqual(plan["hard_blocker_count"], 2)

    def test_contract_probe_uses_symbolic_key_and_source_literals(self):
        issue = {
            "key": "GAME_PICK_MONEY@@0",
            "source_value": "You have received %d Yang.",
            "target_value": "%s Yang aldın.",
        }
        self.assertEqual(base_logical_key(issue["key"]), "GAME_PICK_MONEY")
        terms = search_terms(issue)
        self.assertIn(("key", "GAME_PICK_MONEY"), terms)
        self.assertIn(("source_text", "You have received Yang."), terms)

    def test_contract_probe_numeric_key_uses_text_fragments(self):
        issue = {
            "key": "276",
            "source_value": "Round %lu",
            "target_value": "%u. Tur",
        }
        terms = search_terms(issue)
        self.assertFalse(any(kind == "key" for kind, _ in terms))
        self.assertIn(("source_text", "Round"), terms)

    def test_contract_probe_numeric_key_uses_server_enum_symbol(self):
        issue = {
            "key": "276",
            "source_value": "Round %lu",
            "target_value": "%u. Tur",
        }
        terms = search_terms(issue, {"276": "LU_RUNDE"})
        self.assertIn(("enum_key", "LU_RUNDE"), terms)

    def test_contract_probe_loads_locale_enum_map(self):
        with tempfile.TemporaryDirectory() as tmp:
            header = Path(tmp) / "Locale.hpp"
            header.write_text(
                "enum class ELocaleStrings {\n"
                "    LU_RUNDE = 276,\n"
                "    AKTUELLE_TEILNEHMER = 282,\n"
                "};\n",
                encoding="utf-8",
            )
            mapping = load_locale_enum_map(header)
            self.assertEqual(mapping["276"], "LU_RUNDE")
            self.assertEqual(mapping["282"], "AKTUELLE_TEILNEHMER")

    def test_enum_definition_is_not_runtime_source_evidence(self):
        kind = classify_match_evidence(
            "enum_key",
            "LU_RUNDE",
            Path("game/src/Locale.hpp"),
            "    LU_RUNDE = 276,",
            "source",
        )
        self.assertEqual(kind, "symbol_definition")
        self.assertEqual(
            evidence_strength("enum_key", "LU_RUNDE", kind),
            "definition",
        )

    def test_enum_usage_outside_definition_remains_source_evidence(self):
        kind = classify_match_evidence(
            "enum_key",
            "LU_RUNDE",
            Path("game/src/some_feature.cpp"),
            "SendSomething(ELocaleStrings::LU_RUNDE, round);",
            "source",
        )
        self.assertEqual(kind, "source")
        self.assertEqual(
            evidence_strength("enum_key", "LU_RUNDE", kind),
            "strong",
        )

    def test_contract_probe_separates_source_from_locale_reference(self):
        self.assertEqual(
            classify_evidence_path(
                "Project_Binary",
                Path("root/game.py"),
            ),
            "source",
        )
        self.assertEqual(
            classify_evidence_path(
                "Project_Binary",
                Path("locale/locale/country/tr/locale_game.txt"),
            ),
            "locale_reference",
        )
        self.assertEqual(
            classify_evidence_path(
                "Project_ClientSrc",
                Path("extern/Python315/Lib/subprocess.py"),
            ),
            "noise",
        )
        self.assertEqual(evidence_strength("key", "GAME_PICK_MONEY", "source"), "strong")
        self.assertEqual(evidence_strength("source_text", "Cooldown", "source"), "weak")

    def test_malformed_printf_precision_is_attributed_to_source(self):
        issues = compare_registered_tsv(
            "en/locale_string.txt",
            "tr/locale_string.txt",
            "638\tChance %. 1f\n",
            "638\tŞans %.1f\n",
            set(),
            "en",
            "tr",
            {
                "key_columns": [0],
                "token_columns": [1],
            },
        )
        malformed = [issue for issue in issues if issue.code == "MALFORMED_PRINTF_SYNTAX"]
        self.assertEqual(len(malformed), 1)
        self.assertEqual(malformed[0].path, "en/locale_string.txt")
        self.assertEqual(malformed[0].language, "en")

    def test_locale_game_callable_tag_mismatch_is_explicit(self):
        issues = compare_registered_tsv(
            "en/locale_game.txt",
            "tr/locale_game.txt",
            "KEY\tValue %d\tSA\n",
            "KEY\tDeğer %d\n",
            set(),
            "en",
            "tr",
            {
                "key_columns": [0],
                "token_columns": [1],
                "structural_columns": [2],
                "column_count_policy": "strict",
            },
        )
        codes = [issue.code for issue in issues]
        self.assertIn("STRUCTURAL_COLUMN_MISMATCH", codes)
        self.assertNotIn("COLUMN_COUNT_MISMATCH", codes)

    def test_runtime_decision_path_resolution_allows_src_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            actual = (
                workspace
                / "Project_ClientSrc"
                / "Src"
                / "UserInterface"
                / "PythonSkill.cpp"
            )
            actual.parent.mkdir(parents=True)
            actual.write_text("example", encoding="utf-8")

            resolved, error = resolve_evidence_path(
                workspace,
                "Project_ClientSrc",
                "UserInterface/PythonSkill.cpp",
            )

        self.assertIsNone(error)
        self.assertIsNotNone(resolved)
        self.assertTrue(
            resolved.as_posix().endswith(
                "Project_ClientSrc/Src/UserInterface/PythonSkill.cpp"
            )
        )

    def test_runtime_decision_check_accepts_intrinsic_malformed_fix(self):
        plan = {
            "categories": {
                "runtime_contract_review": {
                    "issues": [{
                        "path": "en/locale_string.txt",
                        "key": "638",
                        "code": "MALFORMED_PRINTF_SYNTAX",
                        "source_value": "Chance %. 1f",
                        "target_value": "Şans %.1f",
                    }]
                }
            }
        }
        manifest = {
            "decisions": [{
                "path": "en/locale_string.txt",
                "key": "638",
                "code": "MALFORMED_PRINTF_SYNTAX",
                "status": "resolved",
                "side": "source",
                "current_value": "Chance %. 1f",
                "proposed_value": "Chance %.1f",
                "evidence": [],
            }]
        }
        with tempfile.TemporaryDirectory() as tmp:
            report = verify_decisions(plan, manifest, Path(tmp))
        self.assertEqual(report["error_count"], 0)
        self.assertTrue(report["ready_for_dry_run_patch"])

    def test_runtime_decision_check_verifies_external_evidence(self):
        plan = {
            "categories": {
                "runtime_contract_review": {
                    "issues": [{
                        "path": "tr/locale_game.txt",
                        "key": "KEY@@0",
                        "code": "PRINTF_CONTRACT_MISMATCH",
                        "source_value": "Value %d",
                        "target_value": "Değer %s",
                    }]
                }
            }
        }
        manifest = {
            "decisions": [{
                "path": "tr/locale_game.txt",
                "key": "KEY@@0",
                "code": "PRINTF_CONTRACT_MISMATCH",
                "status": "resolved",
                "side": "target",
                "current_value": "Değer %s",
                "proposed_value": "Değer %d",
                "evidence": [{
                    "repo": "Project_Binary",
                    "path": "root/example.py",
                    "contains": "localeInfo.KEY % value",
                }],
            }]
        }
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            source = workspace / "Project_Binary" / "root" / "example.py"
            source.parent.mkdir(parents=True)
            source.write_text("localeInfo.KEY % value\n", encoding="utf-8")
            report = verify_decisions(plan, manifest, workspace)
        self.assertEqual(report["error_count"], 0)
        self.assertEqual(report["evidence_check_count"], 1)

    def test_cp1250_fallback_is_lossless(self):
        data = "Příliš žluťoučký kůň".encode("cp1250")
        text, encoding, bom = decode_text(
            data,
            language="cz",
            legacy_encodings=("cp1250",),
        )
        self.assertEqual(text, "Příliš žluťoučký kůň")
        self.assertEqual(encoding, "cp1250")
        self.assertFalse(bom)

if __name__ == "__main__":
    unittest.main()
