import json
import tempfile
import unittest
from pathlib import Path

from tools.locale_guard.unified_targets_stage_v1 import (
    APPROVAL, approved_source, build_staging, staged_target, target_codec,
)
from tools.locale_guard.unified_translation_contract_v1 import TranslationBlocked

LANGUAGES = [
    "ae", "cz", "de", "dk", "en", "es", "fr", "gr",
    "hu", "it", "nl", "pl", "pt", "ro", "ru", "tr",
]


class FakeProvider:
    def translate(self, text, source, target):
        if (source,target,text) == ("tr","en","Merhaba"):
            return "Hello"
        if (source,target,text) == ("en","tr","Hello"):
            return "Merhaba"
        if (source,target,text) == ("en","de","Hello"):
            return "Hallo"
        if (source,target,text) == ("de","en","Hallo"):
            return "Hello"
        return text


class TargetStagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / ".locale_guard").mkdir()
        for lang in ["master"] + LANGUAGES:
            (self.root / lang).mkdir()
        self.config = {
            "master_directory": "master",
            "languages": LANGUAGES,
            "legacy_decodings": {x:["cp1254"] for x in ["master"] + LANGUAGES},
            "translation_language_codes": {x:x for x in LANGUAGES},
            "parser_registry": {"locale_game.txt": {
                "kind": "keyed_tsv", "key_columns": [0], "token_columns": [1],
            }},
        }
        self.master = self.root/"master"/"locale_game.txt"
        self.master.write_bytes("LABEL\tMerhaba\n".encode("utf-8"))
        self.approval = self.root/".locale_guard"/"master_approval.json"

    def save_approval(self, **changes):
        data = {
            "status": APPROVAL, "human_approved": True,
            "all_master_text_reviewed": True,
            "master_files": {
                "locale_game.txt": __import__("hashlib").sha256(
                    self.master.read_bytes()).hexdigest(),
            },
        }
        data.update(changes)
        self.approval.write_text(json.dumps(data),encoding="utf-8")

    def test_no_manifest_blocks_target_without_writing(self):
        with self.assertRaises(FileNotFoundError):
            approved_source(self.root,self.config,self.approval)
        self.assertFalse((self.root/"de"/"locale_game.txt").exists())

    def test_partial_or_unsigned_approval_rejected(self):
        self.save_approval(all_master_text_reviewed=False)
        with self.assertRaises(TranslationBlocked):
            approved_source(self.root,self.config,self.approval)

    def test_modified_master_blocks_old_approval(self):
        self.save_approval()
        self.master.write_bytes("LABEL\tchanged\n".encode("utf-8"))
        with self.assertRaises(TranslationBlocked):
            approved_source(self.root,self.config,self.approval)

    def test_staging_single_language_and_preserve_live(self):
        self.save_approval()
        cache = {}
        budget = [2]
        result = staged_target(self.root,self.config,FakeProvider(),
                               "de","locale_game.txt",cache,budget)
        self.assertEqual(result["translated_cells"], 1)
        self.assertFalse(result["live_apply_allowed"])
        self.assertEqual(result["bytes"], b"LABEL\tHallo\n")
        self.assertFalse((self.root/"de"/"locale_game.txt").exists())

    def test_full_build_never_creates_live_language_files(self):
        self.save_approval()
        result = build_staging(self.root,self.config,FakeProvider(),
                               self.approval,max_cells=5)
        self.assertFalse(result["live_apply_allowed"])
        self.assertFalse(result["all_16_languages_ready"])
        self.assertFalse((self.root/"en"/"locale_game.txt").exists())
        self.assertGreaterEqual(len(result["files"]),1)

    def test_unreadable_target_never_silently_converted(self):
        self.save_approval()
        (self.root/"de"/"locale_game.txt").write_bytes(b"LABEL\t\x81\n")
        self.config["legacy_decodings"]["de"] = ["cp1252"]
        with self.assertRaises(UnicodeDecodeError):
            target_codec(self.root,self.config,"de","locale_game.txt")


if __name__ == "__main__":
    unittest.main()
