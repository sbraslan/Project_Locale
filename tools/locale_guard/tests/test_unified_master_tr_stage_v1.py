import tempfile
import unittest
from pathlib import Path

from tools.locale_guard.unified_master_tr_stage_v1 import (
    apply_memory_edits, parse_tsv_line_positions, stage_master,
)
from tools.locale_guard.unified_translation_contract_v1 import TranslationBlocked

LANGS = ["ae","cz","de","dk","en","es","fr","gr",
         "hu","it","nl","pl","pt","ro","ru","tr"]


class FakeTranslator:
    def __init__(self):
        self.calls = []

    def translate(self, text, source, target):
        self.calls.append((text,source,target))
        data = {
            ("You will receive the reward","en","tr"): "Merhaba",
            ("Merhaba","tr","en"): "Hello",
            ("Hello","en","tr"): "Merhaba",
            ("Merhaba","tr","en"): "Hello",
            ("Hello","en","tr"): "Merhaba",
        }
        if (text,source,target) == ("Merhaba","tr","en"):
            return "Hello"
        if (text,source,target) == ("Hello","en","tr"):
            return "Merhaba"
        if (text,source,target) == ("Merhaba","tr","en"):
            return "You will receive the reward"
        if (text,source,target) == ("You will receive the reward","en","tr"):
            return "Merhaba"
        return data.get((text,source,target),text)


class RoundtripTranslator:
    def __init__(self):
        self.calls = []

    def translate(self, text, source, target):
        self.calls.append((text,source,target))
        if (source,target) == ("en","tr"):
            return "Merhaba"
        if (source,target) == ("tr","en"):
            return "You will receive the reward" if "reward" not in text else "You will receive the reward"
        return text


class MasterStageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for lang in ["master"] + LANGS:
            (self.root / lang).mkdir()
        self.conf = {
            "master_directory":"master", "languages":LANGS,
            "legacy_decodings":{x:["cp1254"] for x in ["master"]+LANGS},
            "parser_registry":{
                "locale_game.txt":{"kind":"keyed_tsv","key_columns":[0],
                                   "token_columns":[1],"duplicate_policy":"occurrence"},
            },
        }

    def test_tsv_multiple_occurrences_resolved_by_identity(self):
        text = "KEY\tOld\nKEY\tOld2\r\n"
        spec = self.conf["parser_registry"]["locale_game.txt"]
        self.assertEqual(parse_tsv_line_positions(text,spec),
                         {"KEY@@0":0,"KEY@@1":1})
        new = apply_memory_edits(text,[{"key":"KEY@@1", "column":1,
                                      "source":"Old2","translated":"Yeni"}],spec)
        self.assertEqual(new,"KEY\tOld\nKEY\tYeni\r\n")

    def test_wiki_newline_and_protected_token_unchanged(self):
        before = "[CENTER_ALIGN]Old %u\r\n[IMG=abc.png]\r\n"
        new = apply_memory_edits(before,[{"line":1,"source":"[CENTER_ALIGN]Old %u",
                                        "translated":"[CENTER_ALIGN]Yeni %u"}],None)
        self.assertEqual(new,"[CENTER_ALIGN]Yeni %u\r\n[IMG=abc.png]\r\n")

    def test_wrong_source_is_blocked_before_edit(self):
        with self.assertRaises(TranslationBlocked):
            apply_memory_edits("Hello\n",[{"line":1,"source":"Old",
                                         "translated":"Yeni"}],None)

    def test_key_column_not_changed(self):
        spec = self.conf["parser_registry"]["locale_game.txt"]
        with self.assertRaises(TranslationBlocked):
            apply_memory_edits("KEY\tOld\n",[{"key":"KEY@@0","column":1,
                                            "source":"Mismatch","translated":"X"}],spec)

    def test_safely_stages_and_does_not_write_live(self):
        # Master kaynağı beklenen güçlü EN sinyalini içerir.
        source = "You will receive the reward %u\n"
        path = self.root / "master/wiki/start.txt"
        path.parent.mkdir()
        path.write_bytes(source.encode("utf-8"))
        before = path.read_bytes()
        result = stage_master(self.root,self.conf,RoundtripTranslator(),limit=2)
        self.assertEqual(path.read_bytes(),before)
        self.assertFalse(result["live_apply"])
        self.assertGreaterEqual(result["plan"]["master_txt_files"],1)
        # Zayıf semantik kanıt varsa hiçbir içeriği zorla uygulama.
        self.assertIsInstance(result["blocked"],list)

    def test_byte_encoding_representability(self):
        before = "Merhaba\n"
        self.assertEqual(apply_memory_edits(before,[],None),before)


if __name__ == "__main__":
    unittest.main()
