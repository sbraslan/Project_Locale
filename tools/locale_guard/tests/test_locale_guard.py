import tempfile
import unittest
from pathlib import Path
from tools.locale_guard.locale_guard import (
    cp1254_mojibake_candidate,
    protected_tokens,
    suspicious_foreign_language,
    tsv_rows,
    inspect_file,
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

if __name__ == "__main__":
    unittest.main()
