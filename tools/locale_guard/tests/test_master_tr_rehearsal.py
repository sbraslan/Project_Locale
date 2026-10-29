import unittest

from tools.locale_guard.master_tr_rehearsal import (
    relative_path,
    verify_file_cells,
)


class MasterTrRehearsalSafetyTests(unittest.TestCase):
    def test_traversal_and_absolute_paths_are_rejected(self):
        for value in ("../tr/battle_pass.txt", "/tmp/evil", "C:/evil", r"tr\evil"):
            with self.subTest(path=value):
                with self.assertRaises(RuntimeError):
                    relative_path(value)

    def test_one_safe_cell_change(self):
        got = verify_file_cells(
            rel="battle_pass.txt",
            target="KEY\tEski\n", preview="KEY\tYeni\n",
            master="KEY\tEski\n", candidate="KEY\tYeni\n",
        )
        self.assertEqual(got, (1, 0, 1))

    def test_no_silent_overwrite_of_existing_translation(self):
        with self.assertRaises(RuntimeError):
            verify_file_cells(
                rel="battle_pass.txt",
                target="KEY\tBaska\n", preview="KEY\tYeni\n",
                master="KEY\tEski\n", candidate="KEY\tYeni\n",
            )

    def test_placeholder_mutation_is_blocked(self):
        with self.assertRaises(RuntimeError):
            verify_file_cells(
                rel="battle_pass.txt",
                target="KEY\tDeger %s\n", preview="KEY\tDeger %d\n",
                master="KEY\tDeger %s\n", candidate="KEY\tDeger %d\n",
            )

    def test_only_explicitly_excluded_cell_can_stay_unchanged(self):
        lines = ["same\n"] * 27
        before = "".join(lines) + "Eski\n"
        desired = "".join(lines) + "Yeni\n"
        got = verify_file_cells(
            rel="wiki/systems/dragon_alchemy.txt",
            target=before, preview=before, master=before, candidate=desired,
        )
        self.assertEqual(got, (0, 1, 1))

    def test_unapproved_change_is_never_applied(self):
        lines = ["same\n"] * 27
        before = "".join(lines) + "Eski\n"
        desired = "".join(lines) + "Yeni\n"
        with self.assertRaises(RuntimeError):
            verify_file_cells(
                rel="wiki/systems/dragon_alchemy.txt",
                target=before, preview=desired, master=before, candidate=desired,
            )


if __name__ == "__main__":
    unittest.main()
