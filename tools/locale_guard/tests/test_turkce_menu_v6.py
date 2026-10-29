import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tools.locale_guard import turkce_menu_v6 as menu


class TurkceMenuV6Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_confirmation_requires_exact_word(self):
        self.assertFalse(menu.onay_iste("Deneme", cevap=lambda _: ""))
        self.assertFalse(menu.onay_iste("Deneme", cevap=lambda _: "evet"))
        self.assertTrue(menu.onay_iste("Deneme", cevap=lambda _: "EVET"))
        self.assertFalse(menu.onay_iste("Kurulum", "KUR", cevap=lambda _: "EVET"))
        self.assertTrue(menu.onay_iste("Kurulum", "KUR", cevap=lambda _: "KUR"))

    def test_main_menu_denied_control_starts_nothing(self):
        with patch.object(menu, "onay_iste", return_value=False), \
             patch.object(menu, "modulu_calistir") as runner:
            menu.tam_kontrol(self.root)
            runner.assert_not_called()

    def test_action_menu_denied_scan_starts_nothing(self):
        with patch.object(menu, "secim_al", side_effect=["1", "G"]), \
             patch.object(menu, "onay_iste", return_value=False), \
             patch.object(menu, "modulu_calistir") as runner:
            menu.islem_menusu(self.root)
            runner.assert_not_called()

    def test_github_menu_denied_fetch_starts_nothing(self):
        with patch.object(menu, "secim_al", side_effect=["2", "G"]), \
             patch.object(menu, "onay_iste", return_value=False), \
             patch.object(menu, "git_getir") as fetch:
            menu.github_menusu(self.root)
            fetch.assert_not_called()

    def test_live_apply_denied_by_outer_confirmation(self):
        with patch.object(menu, "secim_al", side_effect=["5", "G"]), \
             patch.object(menu, "onay_iste", return_value=False), \
             patch.object(menu, "canli_modul") as executor:
            menu.islem_menusu(self.root)
            executor.assert_not_called()

    def test_old_316_live_apply_is_locked_even_with_approval(self):
        with patch.object(menu, "secim_al", side_effect=["5", "G"]), \
             patch.object(menu, "onay_iste", return_value=True), \
             patch.object(menu, "canli_modul") as executor:
            menu.islem_menusu(self.root)
            executor.assert_not_called()

    def test_unified_master_plan_requires_confirmation(self):
        with patch.object(menu, "secim_al", side_effect=["8", "G"]), \
             patch.object(menu, "onay_iste", return_value=False), \
             patch.object(menu, "modulu_calistir") as runner:
            menu.islem_menusu(self.root)
            runner.assert_not_called()

    def test_unified_plan_after_confirmation_uses_read_only_module(self):
        with patch.object(menu, "secim_al", side_effect=["8", "G"]), \
             patch.object(menu, "onay_iste", return_value=True), \
             patch.object(menu, "modulu_calistir") as runner:
            menu.islem_menusu(self.root)
            runner.assert_called_once_with(
                self.root, "unified",
                "tools.locale_guard.unified_express_pipeline_v1", "--root", ".")

    def test_setup_allowlist_never_contains_locale(self):
        self.assertTrue(menu.KURULACAKLAR)
        for name in menu.KURULACAKLAR:
            self.assertFalse(name.startswith("master/"))
            self.assertFalse(any(
                name.startswith(lang + "/")
                for lang in ("tr", "en", "gr", "it", "de", "ru", "fr")
            ))
            self.assertTrue(name.endswith((".py", ".bat")))

    def test_path_traversal_is_refused(self):
        for value in ("../master/x", "tr/../../x", "C:/temp", "/etc/passwd",
                      "tools\\locale_guard\\bad.py"):
            with self.subTest(value=value):
                with self.assertRaises(RuntimeError):
                    menu.guvenli_yol(self.root, value)

    def test_symlink_path_is_refused(self):
        outside = Path(self.tmp.name).parent
        link = self.root / "fake_link"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Sembolik bağlantı oluşturma yetkisi bulunamadı")
        with self.assertRaises(RuntimeError):
            menu.guvenli_yol(self.root, "fake_link/target.py")

    def test_git_merge_refuses_other_branch(self):
        result = SimpleNamespace(returncode=0, stdout="main\n", stderr="")
        with patch.object(menu, "git_depo_dogrula"), \
             patch.object(menu, "komut", return_value=result), \
             patch.object(menu, "calistir") as merge:
            menu.git_esitle(self.root)
            merge.assert_not_called()

    def test_old_report_is_not_presented_as_new(self):
        report = self.root / ".locale_guard" / "incremental_scan_v1.json"
        report.parent.mkdir()
        report.write_text('{"counts":{"MISSING_KEY_TRANSLATION":99}}',
                          encoding="utf-8")
        old = menu.rapor_damgasi(self.root, ".locale_guard/incremental_scan_v1.json")
        with patch.object(menu, "rapor_ozeti") as summary:
            menu.guncel_rapor_ozeti(
                self.root, ".locale_guard/incremental_scan_v1.json", old
            )
            summary.assert_not_called()

    def test_fresh_report_can_be_summarized(self):
        report = self.root / ".locale_guard" / "incremental_scan_v1.json"
        report.parent.mkdir()
        report.write_text('{"counts":{"MISSING_KEY_TRANSLATION":99}}',
                          encoding="utf-8")
        with patch.object(menu, "rapor_ozeti") as summary:
            menu.guncel_rapor_ozeti(
                self.root, ".locale_guard/incremental_scan_v1.json", None
            )
            summary.assert_called_once()

    def test_wrong_git_origin_is_refused(self):
        result = SimpleNamespace(returncode=0, stdout="https://github.com/other/repo\n")
        with patch.object(menu, "komut", return_value=result):
            with self.assertRaises(RuntimeError):
                menu.git_depo_dogrula(self.root)


if __name__ == "__main__":
    unittest.main()
