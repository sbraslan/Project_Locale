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
            with self.assertRaises(TranslationBlocked):
                express(self.root,with_tests=False,
                        service=False,approval=fake_approval)
            stage.assert_not_called()

    def test_zero_accepted_translations_report_block_reasons(self):
        original = self.file.read_bytes()
        failed_stage = {
            "changes": [], "blocked": [
                {"path": "wiki/guide.txt", "status": "GUVENLIK_ENGELI",
                 "reason": "Yerel LibreTranslate bağlantısı kurulamadı"},
                {"path": "wiki/guide.txt", "status": "ANLAMSAL_INCELEME",
                 "source": "Welcome hero"},
            ], "queued_not_attempted": 934, "staged_files": {},
            "live_apply": False,
        }
        with patch("tools.locale_guard.unified_express_pipeline_v1.load_config",
                   return_value=self.conf), \
             patch("tools.locale_guard.unified_express_pipeline_v1.stage_master",
                   return_value=failed_stage):
            result = express(self.root, with_tests=False, service=True,
                             provider=object(), limit=2)
        self.assertEqual(result["status"], "CEVIRI_ENGELLERI_INCELENMELI")
        self.assertEqual(self.file.read_bytes(), original)
        self.assertFalse(result["live_apply"])
        reports = list((self.root / ".locale_guard" / "unified_express")
                       .glob("*/master_staging_engeller.json"))
        self.assertEqual(len(reports), 1)
        import json
        data = json.loads(reports[0].read_text(encoding="utf-8"))
        self.assertEqual(data["blocked_count"], 2)
        self.assertIn("Yerel LibreTranslate bağlantısı kurulamadı",
                      data["blocked_reasons"])
        self.assertEqual(data["blocked"][0]["path"], "wiki/guide.txt")

    def test_successful_stage_still_needs_human_approval(self):
        ready_stage = {
            "changes": [{"status": "INSAN_ONAYI_BEKLIYOR"}],
            "blocked": [], "queued_not_attempted": 935,
            "staged_files": {}, "live_apply": False,
        }
        with patch("tools.locale_guard.unified_express_pipeline_v1.load_config",
                   return_value=self.conf), \
             patch("tools.locale_guard.unified_express_pipeline_v1.stage_master",
                   return_value=ready_stage):
            result = express(self.root, with_tests=False, service=True,
                             provider=object(), limit=1)
        self.assertEqual(result["status"], "ONIZLEME_HAZIR_ONAY_BEKLIYOR")
        self.assertFalse(result["live_apply"])
        self.assertFalse(result["all_16_languages_ready"])

    def test_missing_server_blocks_before_first_translation_attempt(self):
        from tools.locale_guard.translation_service_preflight_v1 import ServicePreflightError
        source_bytes = self.file.read_bytes()
        with patch("tools.locale_guard.unified_express_pipeline_v1.load_config",
                   return_value=self.conf), \
             patch("tools.locale_guard.unified_express_pipeline_v1.ensure_service",
                   side_effect=ServicePreflightError("LibreTranslate kurulu değil")) as preflight, \
             patch("tools.locale_guard.unified_express_pipeline_v1.stage_master") as translator:
            result = express(self.root, with_tests=False, service=True, limit=10)
        self.assertEqual(result["status"], "CEVIRI_SERVISI_HAZIR_DEGIL")
        self.assertEqual(result["service_preflight"]["attempted_translation_cells"], 0)
        self.assertEqual(self.file.read_bytes(), source_bytes)
        preflight.assert_called_once()
        translator.assert_not_called()

    def test_healthy_server_check_is_once_before_staging(self):
        empty_stage = {"changes": [], "blocked": [],
                       "queued_not_attempted": 0, "staged_files": {}}
        def check_order(*args, **kwargs):
            self.assertTrue(checker.called)
            return empty_stage
        with patch("tools.locale_guard.unified_express_pipeline_v1.load_config",
                   return_value=self.conf), \
             patch("tools.locale_guard.unified_express_pipeline_v1.ensure_service",
                   return_value={"status":"HAZIR", "action":"OTOMATIK_BASLATILDI"}) as checker, \
             patch("tools.locale_guard.unified_express_pipeline_v1.stage_master",
                   side_effect=check_order) as staging:
            result = express(self.root, with_tests=False, service=True, limit=10)
        checker.assert_called_once_with(
            self.root, required=("de","en","tr"), autostart=True)
        staging.assert_called_once()
        self.assertEqual(result["service_preflight"]["action"], "OTOMATIK_BASLATILDI")

    def test_no_autostart_flag_is_forwarded(self):
        with patch("tools.locale_guard.unified_express_pipeline_v1.load_config",
                   return_value=self.conf), \
             patch("tools.locale_guard.unified_express_pipeline_v1.ensure_service",
                   side_effect=ServicePreflightError("kapalı")) as checker, \
             patch("tools.locale_guard.unified_express_pipeline_v1.stage_master") as stage:
            from tools.locale_guard.translation_service_preflight_v1 import ServicePreflightError
            result = express(self.root, with_tests=False,
                             service=True, limit=10, autostart=False)
        checker.assert_called_once_with(
            self.root, required=("de","en","tr"), autostart=False)
        stage.assert_not_called()
        self.assertEqual(result["status"], "CEVIRI_SERVISI_HAZIR_DEGIL")

    def test_test_failure_prevents_reports_and_live_changes(self):
        with patch("tools.locale_guard.unified_express_pipeline_v1.check_tests",
                   return_value=False):
            with self.assertRaises(TranslationBlocked):
                express(self.root,with_tests=True)
        self.assertFalse((self.root / ".locale_guard" / "unified_express").exists())
        self.assertEqual(self.file.read_bytes(), b"Welcome hero [ENTER] %u\n")


if __name__ == "__main__":
    unittest.main()
