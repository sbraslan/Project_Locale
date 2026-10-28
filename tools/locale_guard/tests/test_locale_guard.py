import tempfile
import unittest
from pathlib import Path
from tools.locale_guard.locale_guard import (
    cp1254_mojibake_candidate,
    protected_tokens,
    suspicious_foreign_language,
    tsv_rows,
    inspect_file,
    decode_text,
    keyed_tsv_rows,
    parse_git_porcelain,
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
