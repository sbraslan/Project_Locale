import json
import tempfile
import unittest
from pathlib import Path

from tools.locale_guard.unified_qa_cache_v1 import (
    CACHE_NAME, load_cache, validated_bridge, validated_target, write_cache,
)
from tools.locale_guard.unified_translation_contract_v1 import sha


class QaCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.guard = Path(self.temp.name) / ".locale_guard"
        self.guard.mkdir()

    def test_new_cache_is_empty(self):
        self.assertEqual(load_cache(self.guard), {})

    def test_save_and_reuse_cache_with_backup(self):
        write_cache(self.guard, {"a": {"status": "TEKNIK_ADAY"}})
        self.assertIn("a", load_cache(self.guard))
        write_cache(self.guard, {"b": {"status": "ANLAMSAL_INCELEME"}})
        backups = list((self.guard / "qa_cache_backups").glob("*.bak"))
        self.assertEqual(len(backups), 1)
        self.assertIn("b", load_cache(self.guard))
        self.assertNotIn("a", load_cache(self.guard))

    def test_old_or_malformed_cache_is_not_trusted(self):
        (self.guard / CACHE_NAME).write_text(
            '{"items":{"42":{"_verified":"existing_en"}}}', encoding="utf-8")
        with self.assertRaises(RuntimeError):
            load_cache(self.guard)

    def test_bridge_source_hash_and_token_must_match(self):
        obj = {"status": "TEKNIK_ADAY", "source_tr_sha256": sha("Seviye %u"),
               "english": "Level %u", "back_tr": "Seviye %u"}
        self.assertIsNotNone(validated_bridge({"k": obj}, "k", "Seviye %u"))
        self.assertIsNone(validated_bridge({"k": obj}, "k", "Seviye %s"))
        obj["english"] = "Level %d"
        self.assertIsNone(validated_bridge({"k": obj}, "k", "Seviye %u"))

    def test_target_cache_encoding_and_origin_are_checked(self):
        obj = {"status": "TEKNIK_ADAY", "source_tr_sha256": sha("Merhaba"),
               "english_sha256": sha("Hello"), "language": "de",
               "encoding": "cp1252", "text": "Hallo"}
        cache = {"k": obj}
        self.assertIsNotNone(validated_target(cache,"k","Merhaba","Hello","de","cp1252"))
        self.assertIsNone(validated_target(cache,"k","Merhaba","Hi","de","cp1252"))
        self.assertIsNone(validated_target(cache,"k","Merhaba","Hello","de","utf-8"))


if __name__ == "__main__":
    unittest.main()
