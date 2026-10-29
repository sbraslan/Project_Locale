import tempfile
import unittest
from pathlib import Path

from tools.locale_guard.incremental_scan_v1 import collect, row_hash


class IncrementalScanV1Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for lang in ("master", "tr", "en"):
            (self.root / lang).mkdir()
        self.config = {
            "master_directory": "master",
            "languages": ["tr", "en"],
            "legacy_decodings": {"master": ["cp1254"], "tr": ["cp1254"], "en": ["cp1252"]},
            "parser_registry": {"sample.txt": {"kind": "keyed_tsv", "key_columns": [0]}},
        }

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, lang, name, text):
        (self.root / lang / name).write_bytes(text.encode("utf-8"))

    def test_missing_extra_and_unknown_baseline(self):
        self.put("master", "sample.txt", "1\tBir\n2\tİki\n")
        self.put("tr", "sample.txt", "1\tBir\n3\tÜç\n")
        self.put("en", "sample.txt", "1\tOne\n2\tTwo\n")
        data = collect(self.root, self.config)
        self.assertEqual(data["counts"]["MISSING_KEY_TRANSLATION"], 1)
        self.assertEqual(data["counts"]["EXTRA_KEY_REVIEW"], 1)
        self.assertEqual(data["counts"]["BASELINE_REQUIRED"], 2)
        self.assertFalse(data["automatic_apply"])
        self.assertEqual((self.root / "tr" / "sample.txt").read_text(encoding="utf-8"), "1\tBir\n3\tÜç\n")

    def test_changed_key_with_approved_baseline(self):
        self.put("master", "sample.txt", "1\tYeni\n2\tAynı\n")
        self.put("tr", "sample.txt", "1\tEski\n2\tAynı\n")
        baseline = {
            "schema_version": 1, "status": "APPROVED_SYNC_BASELINE",
            "languages": {"tr": {"sample.txt": {"master_rows": {
                "1": row_hash(["1", "Eski"]), "2": row_hash(["2", "Aynı"]),
            }}}},
        }
        data = collect(self.root, self.config, baseline, ["tr"])
        changed = [item["key"] for item in data["items"]
                   if item["status"] == "SOURCE_CHANGED_RETRANSLATION"]
        self.assertEqual(changed, ["1"])
        self.assertNotIn("BASELINE_REQUIRED", data["counts"])

    def test_unregistered_file_requires_review(self):
        self.put("master", "story.txt", "Bir anlatı\n")
        self.put("tr", "story.txt", "Bir anlatı\n")
        data = collect(self.root, self.config, languages=["tr"])
        self.assertEqual(data["counts"]["FREEFORM_OR_UNREGISTERED_REVIEW"], 1)

    def test_duplicate_keys_block(self):
        self.put("master", "sample.txt", "1\tBir\n1\tİki\n")
        self.put("tr", "sample.txt", "1\tOne\n")
        with self.assertRaises(RuntimeError):
            collect(self.root, self.config, languages=["tr"])

    def test_invalid_baseline_blocks(self):
        self.put("master", "sample.txt", "1\tBir\n")
        self.put("tr", "sample.txt", "1\tBir\n")
        with self.assertRaises(RuntimeError):
            collect(self.root, self.config,
                    baseline={"schema_version": 1, "status": "UNAPPROVED"},
                    languages=["tr"])

    def test_invalid_utf8_bom_reports_path_without_writing(self):
        self.put("master", "sample.txt", "1\tBir\n")
        self.put("tr", "sample.txt", "1\tBir\n")
        raw = bytes.fromhex("efbbbf") + b"1\t" + bytes.fromhex("e3") + b"\n"
        (self.root / "tr" / "sample.txt").write_bytes(raw)
        result = collect(self.root, self.config, languages=["tr"])
        self.assertEqual(result["counts"]["FILE_ENCODING_REVIEW"], 1)
        bad = [x for x in result["items"]
               if x["status"] == "FILE_ENCODING_REVIEW"]
        self.assertEqual(bad[0]["path"], "sample.txt")
        self.assertEqual(bad[0]["affected_side"], "tr")
        self.assertIn("NUL", bad[0]["reason"]) if b"\x00" in raw else self.assertIn("UTF8_BOM=True", bad[0]["reason"])
        self.assertEqual(
            (self.root / "tr" / "sample.txt").read_bytes(), raw
        )

    def test_nul_file_isolated_and_other_files_scanned(self):
        self.put("master", "sample.txt", "1\tBir\n")
        self.put("tr", "sample.txt", "1\tBir\n")
        self.put("master", "story.txt", "Güvenli metin\n")
        self.put("tr", "story.txt", "Güvenli metin\n")
        raw = b"1\tValue\x00broken\n"
        (self.root / "tr" / "sample.txt").write_bytes(raw)
        result = collect(self.root, self.config, languages=["tr"])
        self.assertEqual(result["mode"], "READ_ONLY_DELTA_SCAN_WITH_BLOCKERS")
        self.assertEqual(result["counts"]["FILE_ENCODING_REVIEW"], 1)
        self.assertEqual(result["counts"]["FREEFORM_OR_UNREGISTERED_REVIEW"], 1)
        failure = next(x for x in result["items"]
                       if x["status"] == "FILE_ENCODING_REVIEW")
        self.assertIn("NUL_sayısı=1", failure["reason"])
        self.assertEqual((self.root / "tr" / "sample.txt").read_bytes(), raw)

    def test_utf16_word_filter_preserved_without_translation(self):
        self.put("master", "insult.txt", "örnek\n")
        raw = "parola\r\nküfür\r\n".encode("utf-16")
        (self.root / "tr" / "insult.txt").write_bytes(raw)
        data = collect(self.root, self.config, languages=["tr"])
        self.assertEqual(data["counts"]["SPECIAL_FILE_PRESERVED"], 1)
        self.assertNotIn("FILE_ENCODING_REVIEW", data["counts"])
        self.assertFalse(data["automatic_apply"])
        self.assertEqual((self.root / "tr" / "insult.txt").read_bytes(), raw)

    def test_nul_word_filter_without_utf16_bom_is_not_whitelisted(self):
        self.put("master", "insult.txt", "test\n")
        raw = b"x\x00y\n"
        (self.root / "tr" / "insult.txt").write_bytes(raw)
        data = collect(self.root, self.config, languages=["tr"])
        self.assertEqual(data["counts"]["FILE_ENCODING_REVIEW"], 1)
        self.assertEqual((self.root / "tr" / "insult.txt").read_bytes(), raw)

    def test_missing_file_never_created(self):
        self.put("master", "sample.txt", "1\tBir\n")
        data = collect(self.root, self.config, languages=["tr"])
        self.assertEqual(data["counts"]["MISSING_FILE_TRANSLATION"], 1)
        self.assertFalse((self.root / "tr" / "sample.txt").exists())


if __name__ == "__main__":
    unittest.main()
