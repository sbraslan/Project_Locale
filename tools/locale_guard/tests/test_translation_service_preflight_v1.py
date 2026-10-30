import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tools.locale_guard.translation_service_preflight_v1 import (
    ServicePreflightError, check_ready, ensure_service, discover_start_command,
)

MOD = "tools.locale_guard.translation_service_preflight_v1"


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / ".locale_guard").mkdir()
        self.ready = {"status": "HAZIR", "languages": ["de", "en", "tr"],
                      "required": ["de", "en", "tr"], "translation_probe": True}

    def test_running_service_reused_without_any_process_launch(self):
        with patch(MOD + ".local_port_listens", return_value=True), \
             patch(MOD + ".check_ready", return_value=self.ready), \
             patch(MOD + "._launch") as launch:
            result = ensure_service(self.root)
            self.assertEqual(result["action"], "MEVCUT_SERVIS_KULLANILDI")
            launch.assert_not_called()

    def test_not_installed_stops_before_translation(self):
        with patch(MOD + ".local_port_listens", return_value=False), \
             patch(MOD + ".discover_start_command", return_value=None), \
             patch(MOD + "._launch") as launch:
            with self.assertRaisesRegex(ServicePreflightError, "kurulum"):
                ensure_service(self.root)
            launch.assert_not_called()

    def test_autostart_disabled_refuses_launch(self):
        with patch(MOD + ".local_port_listens", return_value=False), \
             patch(MOD + "._launch") as launch:
            with self.assertRaisesRegex(ServicePreflightError, "devre dışı"):
                ensure_service(self.root, autostart=False)
            launch.assert_not_called()

    def test_existing_wrong_port_service_never_killed_or_replaced(self):
        with patch(MOD + ".local_port_listens", return_value=True), \
             patch(MOD + ".check_ready", side_effect=ServicePreflightError("Yanlış API")), \
             patch(MOD + "._launch") as launch:
            with self.assertRaisesRegex(ServicePreflightError, "5000 portunda"):
                ensure_service(self.root)
            launch.assert_not_called()

    def test_server_started_once_and_probed_before_returning(self):
        process = SimpleNamespace(pid=1234, poll=lambda: None)
        with patch(MOD + ".local_port_listens", side_effect=[False, True]), \
             patch(MOD + ".discover_start_command",
                   return_value=["C:/Python/Scripts/libretranslate.exe"]), \
             patch(MOD + "._launch", return_value=process) as launch, \
             patch(MOD + ".check_ready", return_value=self.ready) as probe:
            result = ensure_service(self.root)
            launch.assert_called_once()
            probe.assert_called_once_with(("de", "en", "tr"))
            self.assertEqual(result["action"], "OTOMATIK_BASLATILDI")
            self.assertEqual(result["pid"], 1234)

    def test_exited_server_blocked_without_translation(self):
        process = SimpleNamespace(pid=1234, poll=lambda: 2)
        with patch(MOD + ".local_port_listens", return_value=False), \
             patch(MOD + ".discover_start_command",
                   return_value=["C:/Python/Scripts/libretranslate.exe"]), \
             patch(MOD + "._launch", return_value=process):
            with self.assertRaisesRegex(ServicePreflightError, "exit=2"):
                ensure_service(self.root)

    def test_mojibake_response_detected_from_decoded_json(self):
        def fake_fetch(path, payload=None, timeout=4):
            if path == "/languages":
                return [{"code": c} for c in ("tr", "en", "de")]
            return {"translatedText": "GÃ¶revi tamamla"}
        with patch(MOD + "._fetch_json", side_effect=fake_fetch):
            with self.assertRaisesRegex(ServicePreflightError, "UTF-8"):
                check_ready()

    def test_missing_languages_prevents_translation_requests(self):
        with patch(MOD + "._fetch_json",
                   return_value=[{"code": "de"}, {"code": "en"}]) as fetch:
            with self.assertRaisesRegex(ServicePreflightError, "tr"):
                check_ready()
            fetch.assert_called_once_with("/languages")

    def test_valid_local_translation_passes_preflight(self):
        def fake_fetch(path, payload=None, timeout=4):
            if path == "/languages":
                return [{"code": c} for c in ("de", "tr", "en")]
            if payload["source"] == "de":
                return {"translatedText": "Görevi tamamla."}
            if payload["target"] == "en":
                return {"translatedText": "Complete the mission."}
            return {"translatedText": "Görevi tamamla."}
        with patch(MOD + "._fetch_json", side_effect=fake_fetch):
            result = check_ready()
        self.assertEqual(result["status"], "HAZIR")
        self.assertTrue(result["translation_probe"])

    def test_override_requires_a_known_executable_not_arbitrary_shell(self):
        with patch.dict("os.environ", {"METIN2_LIBRETRANSLATE_EXE": "cmd.exe"}):
            with self.assertRaises(ServicePreflightError):
                discover_start_command()


if __name__ == "__main__":
    unittest.main()
