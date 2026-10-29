import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.locale_guard import master_tr_live_apply_v1 as live
from tools.locale_guard.runtime_readiness_v1 import runtime_kontrol


class MasterTrLiveApplyV1Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "Project_Locale"
        self.root.mkdir()
        for folder in (".locale_guard", "master", "tr"):
            (self.root / folder).mkdir()
        self.old = {}
        self.previews = {}
        for i in range(46):
            name = "sample_" + str(i).zfill(2) + ".txt"
            n = 271 if i == 0 else 1
            before = "".join(f"K{j}\tEski{j}\n" for j in range(n)).encode("utf-8")
            after = "".join(f"K{j}\tYeni{j}\n" for j in range(n)).encode("utf-8")
            for lang in ("master", "tr"):
                (self.root / lang / name).write_bytes(before)
            self.old[name] = before
            self.previews[name] = after
        self.rehearsal_patch = patch(
            "tools.locale_guard.master_tr_rehearsal.rehearse",
            return_value=self.root,
        )
        self.rehearsal_patch.start()
        self.addCleanup(self.rehearsal_patch.stop)
        self.summary = {
            "planned_change_count": 316,
            "excluded_unapproved_count": 1,
            "preview_manifest_sha256": live.PREVIEW_SHA,
            "candidate_manifest_sha256": live.CANDIDATE_SHA,
        }

    def tearDown(self):
        self.tmp.cleanup()

    def approve(self):
        path = live.inceleme_olustur(self.root, self.summary, self.previews)
        self.assertEqual(path.read_text(encoding="utf-8").count("\n"), 317)
        self.assertTrue(live.onayla(
            self.root, self.summary, self.previews,
            input_fn=lambda _: live.ACK_REVIEW
        ))

    def assert_old(self):
        for name, old in self.old.items():
            self.assertEqual((self.root / "master" / name).read_bytes(), old)
            self.assertEqual((self.root / "tr" / name).read_bytes(), old)

    def test_review_report_contains_all_316_cells(self):
        path = live.inceleme_olustur(self.root, self.summary, self.previews)
        content = path.read_text(encoding="utf-8")
        self.assertIn("Dosya\tSatır\tSütun\tÖnce\tSonra", content)
        self.assertEqual(len(content.splitlines()), 317)
        self.assertIn('"Eski0"\t"Yeni0"', content)
        self.assert_old()

    def test_no_approval_no_apply(self):
        with self.assertRaises(RuntimeError):
            live.uygula(self.root, self.summary, self.previews,
                        input_fn=lambda _: live.ACK_APPLY)
        self.assert_old()

    def test_review_does_not_mean_auto_approval(self):
        live.inceleme_olustur(self.root, self.summary, self.previews)
        self.assertFalse(live.onayla(
            self.root, self.summary, self.previews,
            input_fn=lambda _: "EVET"
        ))
        self.assertFalse((self.root / live.APPROVAL_REL).exists())
        self.assert_old()

    def test_approval_does_not_mean_auto_apply(self):
        self.approve()
        self.assertFalse(live.uygula(
            self.root, self.summary, self.previews,
            input_fn=lambda _: "EVET"
        ))
        self.assert_old()
        self.assertFalse((self.root / live.TRANSACTIONS_REL).exists())

    def test_transaction_and_explicit_rollback(self):
        self.approve()
        self.assertTrue(live.uygula(
            self.root, self.summary, self.previews,
            input_fn=lambda _: live.ACK_APPLY
        ))
        for name, after in self.previews.items():
            self.assertEqual((self.root / "master" / name).read_bytes(), after)
            self.assertEqual((self.root / "tr" / name).read_bytes(), after)
        folder = live.son_islem(self.root)
        journal = json.loads((folder / "islem.json").read_text(encoding="utf-8"))
        self.assertEqual(journal["durum"], "UYGULANDI")
        self.assertEqual(len(journal["dosyalar"]), 92)
        self.assertFalse(live.geri_al(self.root, input_fn=lambda _: "EVET"))
        self.assertTrue(live.geri_al(self.root, input_fn=lambda _: live.ACK_ROLLBACK))
        self.assert_old()
        journal = json.loads((folder / "islem.json").read_text(encoding="utf-8"))
        self.assertEqual(journal["durum"], "GERI_ALINDI")

    def test_runtime_readiness_only_after_verified_apply(self):
        with self.assertRaises(RuntimeError):
            runtime_kontrol(self.root)
        self.approve()
        self.assertTrue(live.uygula(
            self.root, self.summary, self.previews,
            input_fn=lambda _: live.ACK_APPLY,
        ))
        ready = runtime_kontrol(self.root)
        self.assertEqual(ready["durum"], "TR_RUNTIME_ON_KONTROL_GECTI")
        self.assertEqual(ready["dogrulanan_dosya"], 92)
        self.assertFalse(ready["istemciye_kopyalandi"])
        self.assertTrue(live.geri_al(
            self.root, input_fn=lambda _: live.ACK_ROLLBACK
        ))
        with self.assertRaises(RuntimeError):
            runtime_kontrol(self.root)

    def test_partial_write_failure_restores_all_92_files(self):
        self.approve()
        count = [0]

        def failing_writer(path, raw):
            count[0] += 1
            if count[0] == 12:
                raise OSError("simulated write failure")
            live.kaydet_atomik(path, raw)

        with self.assertRaises(OSError):
            live.uygula(
                self.root, self.summary, self.previews,
                input_fn=lambda _: live.ACK_APPLY, write_fn=failing_writer,
            )
        self.assert_old()
        folder = live.son_islem(self.root)
        journal = json.loads((folder / "islem.json").read_text(encoding="utf-8"))
        self.assertEqual(journal["durum"], "GERI_ALINDI")

    def test_master_tr_raw_divergence_blocks_before_first_write(self):
        self.approve()
        target = self.root / "master" / "sample_00.txt"
        target.write_bytes(b"deviated")
        with self.assertRaises(RuntimeError):
            live.uygula(self.root, self.summary, self.previews,
                        input_fn=lambda _: live.ACK_APPLY)
        self.assertEqual(target.read_bytes(), b"deviated")
        self.assertFalse((self.root / live.TRANSACTIONS_REL).exists())

    def test_user_changes_after_apply_are_not_overwritten_on_rollback(self):
        self.approve()
        self.assertTrue(live.uygula(
            self.root, self.summary, self.previews,
            input_fn=lambda _: live.ACK_APPLY,
        ))
        target = self.root / "tr" / "sample_01.txt"
        target.write_bytes(b"human edit after apply")
        with self.assertRaises(RuntimeError):
            live.geri_al(self.root, input_fn=lambda _: live.ACK_ROLLBACK)
        self.assertEqual(target.read_bytes(), b"human edit after apply")

    def test_review_report_diff_tamper_prevents_approval(self):
        path = live.inceleme_olustur(self.root, self.summary, self.previews)
        path.write_text("forged", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            live.onayla(self.root, self.summary, self.previews,
                        input_fn=lambda _: live.ACK_REVIEW)
        self.assertFalse((self.root / live.APPROVAL_REL).exists())

    def test_invalid_path_is_rejected(self):
        for name in ("../x", "C:/x", "/tmp/x", "x\\y"):
            with self.subTest(name=name):
                with self.assertRaises(RuntimeError):
                    live.dosya_yolu(self.root, "tr", name)


if __name__ == "__main__":
    unittest.main()
