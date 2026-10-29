import tempfile
import unittest
from pathlib import Path

from tools.locale_guard.unified_master_first_plan_v1 import (
    build_plan, extract_master_cells, is_candidate, source_classification,
)


LANGUAGES = [
    "ae", "cz", "de", "dk", "en", "es", "fr", "gr",
    "hu", "it", "nl", "pl", "pt", "ro", "ru", "tr",
]


class MasterFirstPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for lang in ["master"] + LANGUAGES:
            (self.root / lang).mkdir()
        self.config = {
            "master_directory": "master",
            "languages": LANGUAGES,
            "legacy_decodings": {lang: ["cp1254"] for lang in ["master"] + LANGUAGES},
            "parser_registry": {
                "locale_game.txt": {
                    "kind": "keyed_tsv",
                    "key_columns": [0],
                    "token_columns": [1],
                },
                "monster_card.txt": {
                    "kind": "keyed_tsv",
                    "key_columns": [0, 1],
                    "token_columns": "disabled",
                },
            },
        }

    def write(self, folder, file, text):
        target = self.root / folder / file
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode("utf-8"))

    def test_mixed_wiki_and_tsv_inventory_all_16_languages(self):
        self.write("master", "wiki/help.txt", "[CENTER_ALIGN]You will receive the reward %u.\r\n")
        self.write("master", "locale_game.txt", "K1\tSavaşçı %u\nK2\tGood morning\n")
        self.write("master", "insult.txt", "banned word\n")
        self.write("master", "unregistered.txt", "unknown format\n")
        self.write("tr", "locale_game.txt", "K1\tSavaşçı %u\nEXTRA\tOrphan\n")
        report = build_plan(self.root, self.config)
        self.assertEqual(report["master_txt_files"], 4)
        self.assertEqual(report["languages"], LANGUAGES)
        self.assertFalse(report["live_apply"])
        self.assertFalse(report["automatic_delete"])
        self.assertFalse(report["source_master_approved"])
        self.assertGreaterEqual(report["master_cells_reviewed"], 3)
        self.assertTrue(any(x["operation"] == "FAZLA_ANAHTAR_INCELE"
                            and x["language"] == "tr" and x["key"] == "EXTRA"
                            for x in report["sync_proposals"]))
        self.assertTrue(any(x["operation"] == "EKSIK_ANAHTAR_CEVIR"
                            and x["language"] == "tr" and x["key"] == "K2"
                            for x in report["sync_proposals"]))
        self.assertTrue(any(x["status"] == "KAYITSIZ_DOSYA_PARSER_INCELEMESI"
                            for x in report["held"]))

    def test_technical_only_line_is_excluded(self):
        self.assertFalse(is_candidate("[CENTER_ALIGN]|cFF00AA"))
        self.assertFalse(is_candidate("\t \t"))
        self.assertTrue(is_candidate("[CENTER_ALIGN]Hello %u"))

    def test_disabled_schema_never_translated(self):
        self.write("master", "monster_card.txt", "1\t2\tText\n")
        entries, held = extract_master_cells(self.root, self.config, "monster_card.txt")
        self.assertEqual(entries, [])
        self.assertEqual(held[0]["status"], "OZEL_DOSYA_BAYT_KORUMA")

    def test_unregistered_file_stays_in_review(self):
        self.write("master", "mystery.txt", "Some random text\n")
        entries, held = extract_master_cells(self.root, self.config, "mystery.txt")
        self.assertEqual(entries, [])
        self.assertEqual(held[0]["status"], "KAYITSIZ_DOSYA_PARSER_INCELEMESI")

    def test_wiki_filename_and_line_identity_is_preserved(self):
        self.write("master", "wiki/guides/quest.txt",
                   "[IMG=quest.png]\nReward %u is here.\r\n")
        entries, held = extract_master_cells(
            self.root, self.config, "wiki/guides/quest.txt")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["line"], 2)
        self.assertIn("%u", entries[0]["protected_tokens"])

    def test_known_description_file_is_freeform_without_tsv_rewrite(self):
        self.write("master", "wonexchange_desc.txt",
                   "[DELAY value;0]\nExchange currencies[ENTER]\n")
        entries, held = extract_master_cells(
            self.root, self.config, "wonexchange_desc.txt")
        self.assertEqual(held, [])
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["mode"], "wiki_line")
        self.assertEqual(entries[0]["line"], 2)

    def test_unknown_description_not_auto_approved(self):
        self.write("master", "new_unknown_desc.txt", "Exchange currencies[ENTER]\n")
        entries, held = extract_master_cells(
            self.root, self.config, "new_unknown_desc.txt")
        self.assertEqual(entries, [])
        self.assertEqual(held[0]["status"], "KAYITSIZ_DOSYA_PARSER_INCELEMESI")

    def test_tab_in_freeform_halts_translation(self):
        self.write("master", "wonexchange_desc.txt", "1\tOld\n")
        entries, held = extract_master_cells(
            self.root, self.config, "wonexchange_desc.txt")
        self.assertEqual(entries, [])
        self.assertEqual(held[0]["status"], "SERBEST_METINDE_TSV_TAB_ENGELI")

    def test_all_bracket_directives_not_treated_as_text(self):
        self.assertFalse(is_candidate("[DELAY value;0][ENTER][IMG=hero]"))
        self.assertTrue(is_candidate("[ENTER]Merchant goods"))

    def test_source_classification_never_approves(self):
        item = source_classification({"source": "Collect the reward now"})
        self.assertFalse(item["live_apply_allowed"])
        self.assertIn(item["status"], {
            "MASTER_TURKCEYE_CEVIR_BEKLIYOR",
            "MASTER_KAYNAK_DIL_INCELEMESI",
        })


if __name__ == "__main__":
    unittest.main()
