import tempfile
import unittest
from pathlib import Path

from tools.locale_guard.translation_queue_preview_v1 import (
    build_queue, editable_columns,
)


class TranslationQueuePreviewV1Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for name in ("master", "tr", "en"):
            (self.root / name).mkdir()
        self.config = {
            "master_directory": "master",
            "languages": ["tr", "en"],
            "translation_language_codes": {"tr": "tr", "en": "en"},
            "legacy_decodings": {"master": ["cp1254"],
                                 "tr": ["cp1254"], "en": ["cp1252"]},
            "parser_registry": {"locale_game.txt": {
                "kind": "keyed_tsv", "key_columns": [0],
                "token_columns": [1], "structural_columns": [2],
            }},
        }

    def tearDown(self):
        self.temp.cleanup()

    def put(self, lang, name, text):
        (self.root / lang / name).write_bytes(text.encode("utf-8"))

    def test_missing_row_becomes_review_only_queue(self):
        self.put("master", "locale_game.txt",
                 "1\tGünaydın\tcallable\n2\tSeviye %d\tcallable\n")
        self.put("tr", "locale_game.txt", "1\tGünaydın\tcallable\n")
        self.put("en", "locale_game.txt", "1\tMorning\tcallable\n")
        original = (self.root / "en" / "locale_game.txt").read_bytes()
        result = build_queue(self.root, self.config)
        self.assertEqual(result["missing_key_rows"], 2)
        self.assertEqual(result["translation_cells"], 2)
        self.assertFalse(result["automatic_translate"])
        self.assertFalse(result["live_apply"])
        self.assertFalse(result["source_semantic_approval"])
        self.assertEqual({task["column"] for task in result["tasks"]}, {1})
        self.assertTrue(all(task["protected_tokens"] == ["%d"]
                            for task in result["tasks"]))
        self.assertEqual((self.root / "en" / "locale_game.txt").read_bytes(), original)

    def test_structural_columns_never_translated(self):
        spec = {"key_columns": [0], "token_columns": [1],
                "structural_columns": [2]}
        self.assertEqual(editable_columns(spec, 3), [1])
        bad = {"key_columns": [0], "token_columns": [1, 2],
               "structural_columns": [2]}
        with self.assertRaises(RuntimeError):
            editable_columns(bad, 3)

    def test_flexible_columns_ignore_absent_values(self):
        spec = {"key_columns": [0], "token_columns": [1, 2, 3],
                "column_count_policy": "flexible"}
        self.assertEqual(editable_columns(spec, 2), [1])
        self.assertEqual(editable_columns(spec, 1), [])

    def test_disabled_translation_schema(self):
        spec = {"key_columns": [0], "token_columns": "disabled"}
        self.assertEqual(editable_columns(spec, 7), [])

    def test_empty_and_numeric_cells_not_sent_to_translation(self):
        self.config["parser_registry"]["locale_game.txt"]["token_columns"] = "all_non_key"
        self.config["parser_registry"]["locale_game.txt"].pop("structural_columns")
        self.put("master", "locale_game.txt", "1\t\n2\t12345\n3\tSelam\n")
        self.put("tr", "locale_game.txt", "4\tEski\n")
        result = build_queue(self.root, self.config, ["tr"])
        self.assertEqual(result["translation_cells"], 1)
        self.assertGreaterEqual(result["held_cells_or_rows"], 1)
        self.assertEqual(result["tasks"][0]["source"], "Selam")


if __name__ == "__main__":
    unittest.main()
