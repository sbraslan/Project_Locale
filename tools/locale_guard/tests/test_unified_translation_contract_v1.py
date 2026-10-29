import unittest

from tools.locale_guard.unified_translation_contract_v1 import (
    TranslationBlocked, cache_key, cache_lookup, segment_translate,
    technical_contract, token_spans, verify_bridge, verify_target,
)


class StubProvider:
    def __init__(self):
        self.calls = []

    def translate(self, text, source, target):
        self.calls.append((text, source, target))
        dictionary = {
            ("Merhaba", "tr", "en"): "Hello",
            ("Hello", "en", "tr"): "Merhaba",
            ("Hello", "en", "de"): "Hallo",
            ("Hallo", "de", "en"): "Hello",
            ("Sword", "en", "gr"): "Ξίφος",
            ("Ξίφος", "gr", "en"): "Sword",
        }
        return dictionary.get((text, source, target), text)


class UnifiedContractTests(unittest.TestCase):
    def test_printf_brace_named_and_markup_never_enter_provider(self):
        provider = StubProvider()
        before = "Merhaba %u %(player)s {name} |cFF0000 [CENTER_ALIGN] <b> Merhaba </b>\\n"
        after = segment_translate(provider, before, "tr", "en")
        technical_contract(before, after)
        self.assertIn("%u %(player)s {name} |cFF0000 [CENTER_ALIGN] <b>", after)
        for text, _, _ in provider.calls:
            for forbidden in ("%u", "%(player)s", "{name}", "|cFF0000",
                              "[CENTER_ALIGN]", "<b>", "</b>", "\\n"):
                self.assertNotIn(forbidden, text)

    def test_mutated_token_is_rejected(self):
        with self.assertRaises(TranslationBlocked):
            technical_contract("Seviye %d", "Level %s")
        with self.assertRaises(TranslationBlocked):
            technical_contract("Merhaba %(player)s", "Hello %(user)s")
        with self.assertRaises(TranslationBlocked):
            technical_contract("Tıkla [CENTER_ALIGN]", "Click [RIGHT_ALIGN]")

    def test_whitespace_and_tab_unchanged(self):
        provider = StubProvider()
        self.assertEqual(segment_translate(provider, "  Merhaba  ", "tr", "en"),
                         "  Hello  ")
        self.assertEqual(segment_translate(provider, "1\tMerhaba", "tr", "en"),
                         "1\tHello")

    def test_bridge_back_translation_is_candidate_not_approval(self):
        p = StubProvider()
        result = verify_bridge(p, "Merhaba")
        self.assertEqual(result["english"], "Hello")
        self.assertEqual(result["back_tr"], "Merhaba")
        self.assertEqual(result["status"], "TEKNIK_ADAY")
        self.assertFalse(result["human_approved"])

    def test_bridge_low_score_demands_review(self):
        class BadProvider(StubProvider):
            def translate(self, text, source, target):
                if (source, target) == ("en", "tr"):
                    return "Başka bir hikâye"
                return super().translate(text, source, target)
        result = verify_bridge(BadProvider(), "Merhaba")
        self.assertEqual(result["status"], "ANLAMSAL_INCELEME")

    def test_target_encoding_and_roundtrip(self):
        p = StubProvider()
        result = verify_target(p, "Merhaba", "Hello", "de", "de", "cp1252")
        self.assertEqual(result["text"], "Hallo")
        self.assertFalse(result["live_apply_allowed"])

    def test_unencodable_characters_block(self):
        with self.assertRaises(TranslationBlocked):
            verify_target(StubProvider(), "Merhaba", "Sword", "gr", "gr", "cp1252")

    def test_unapproved_cache_never_reused(self):
        key = cache_key("Seviye %d", "tr", "en", "abc")
        cache = {key: {"source_sha256": __import__("hashlib").sha256(
            "Seviye %d".encode("utf-8")).hexdigest(),
            "target_language": "en", "human_approved": False,
            "contract_validated": True, "text": "Level %d"}}
        self.assertIsNone(cache_lookup(cache, key, "Seviye %d", "en"))
        cache[key]["human_approved"] = True
        self.assertEqual(cache_lookup(cache, key, "Seviye %d", "en"), "Level %d")
        cache[key]["text"] = "Level %s"
        with self.assertRaises(TranslationBlocked):
            cache_lookup(cache, key, "Seviye %d", "en")

    def test_utf16_special_file_is_not_translation_input(self):
        raw = "parola\r\n".encode("utf-16")
        self.assertTrue(raw.startswith(b"\xff\xfe"))
        # Kelime filtreleri ayrı sınıfta korunur, çeviri çekirdeğine gönderilmez.
        with self.assertRaises(TranslationBlocked):
            segment_translate(StubProvider(), "parola\x00", "it", "tr")


if __name__ == "__main__":
    unittest.main()
