import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.locale_guard.unified_express_pipeline_v1 import express
from tools.locale_guard.unified_translation_contract_v1 import TranslationBlocked

LANGS = [
    "ae","cz","de","dk","en","es","fr","gr",
    "hu","it","nl","pl","pt","ro","ru","tr",
]


class ExpressTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "Project_Locale"
        self.root.mkdir()
        (self.root / ".locale_guard").mkdir()
        for lang in ["master"] + LANGS:
            (self.root / lang).mkdir()
        self.file = self.root / "master" / "wiki" / "guide.txt"
        self.file.parent.mkdir()
        self.file.write_bytes(b"Welcome hero [ENTER] %u\n")
        self.conf = {
            "master_directory": "master", "languages": LANGS,
            "legacy_decodings": {lang:["cp1252"] for lang in ["master"] + LANGS},
            "parser_registry": {},
        }

    def test_no_provider_no_approval_runs_read_only_plan(self):
        source = self.file.read_bytes()
        with patch("tools.locale_guard.unified_express_pipeline_v1.load_config",
                   return_value=self.conf):
            result = express(self.root,with_tests=False)
        self.assertEqual(self.file.read_bytes(),source)
        self.assertFalse(result["live_apply"])
        self.assertFalse(result["all_16_languages_ready"])
        self.assertEqual(result["status"],"ONIZLEME_HAZIR_ONAY_BEKLIYOR")
        self.assertEqual(result["steps"][-1]["status"],
                         "TAM_TURKCE_MASTER_ONAYI_BEKLENIYOR")

    def test_source_approval_omitted_never_stages_targets(self):
        with patch("tools.locale_guard.unified_express_pipeline_v1.load_config",
                   return_value=self.conf), \
             patch("tools.locale_guard.unified_express_pipeline_v1.build_staging") as stage:
            express(self.root,with_tests=False)
            stage.assert_not_called()

    def test_wrong_root_is_refused(self):
        with self.assertRaises(TranslationBlocked):
            express(self.root / "master",with_tests=False)

    def test_no_master_approval_cannot_reach_target_provider(self):
        fake_approval = self.root / ".locale_guard" / "missing.json"
        with patch("tools.locale_guard.unified_express_pipeline_v1.load_config",
                   return_value=self.conf), \
             patch("tools.locale_guard.unified_express_pipeline_v1.build_staging") as stage:
            with self.assertRaises(FileNotFoundError):
                express(self.root,with_tests=False,
                        service=False,approval=fake_approval)
            stage.assert_not_called()

    def test_test_failure_prevents_reports_and_live_changes(self):
        with patch("tools.locale_guard.unified_express_pipeline_v1.check_tests",
                   return_value=False):
            with self.assertRaises(TranslationBlocked):
                express(self.root,with_tests=True)
        self.assertFalse((self.root / ".locale_guard" / "unified_express").exists())
        self.assertEqual(self.file.read_bytes(), b"Welcome hero [ENTER] %u\n")


if __name__ == "__main__":
    unittest.main()
