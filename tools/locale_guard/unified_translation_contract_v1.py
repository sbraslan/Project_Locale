#!/usr/bin/env python3
"""Master -> Türkçe -> İngilizce köprü -> 16 dil: güvenli çeviri sözleşmesi.

Bu modül sadece BELLEKTE çevirir ve doğrular. Canlı dosya yazmaz,
onay vermez, şüpheli çeviriyi başarı gibi işaretlemez.
"""
from __future__ import annotations

import hashlib
import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Protocol

from tools.locale_guard.locale_guard import (
    BRACE_RE, BRACKET_DIRECTIVE_RE, ESCAPE_RE, METIN_PIPE_RE, PRINTF_RE,
    URL_RE, layout_tokens, protected_tokens, suspicious_foreign_language,
)

# Orijinal motorun yakalamadığı iki yaygın Metin2/Python biçimi.
PYTHON_NAMED_PRINTF = re.compile(
    r"%\([A-Za-z_][A-Za-z_0-9]*\)[-+#0 ]*(?:\d+)?(?:\.\d+)?[diuoxXfFeEgGaAcsp]"
)
TAG = re.compile(r"</?[A-Za-z][A-Za-z0-9:_-]*(?:\s+[^<>]*?)?\s*/?>")
MARKER = re.compile(r"__MT2PROTECTED_\d+__")
WHITESPACE_STRUCTURE = re.compile(r"[\t\r\n]")
FORBIDDEN = ("\ufffd", "\x00")
MIN_BRIDGE_SCORE = 0.72
MIN_TARGET_SCORE = 0.68


class TranslationBlocked(RuntimeError):
    pass


class Provider(Protocol):
    def translate(self, text: str, source: str, target: str) -> str:
        ...


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalized(value: str) -> str:
    value = value.casefold()
    value = re.sub(r"[^\w]+", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


def similarity(a: str, b: str) -> float:
    na, nb = normalized(a), normalized(b)
    if not na and not nb:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio() if na and nb else 0.0


def token_spans(text: str) -> list[tuple[int, int, str]]:
    """Teknik dizeleri eşsiz, çakışmasız aralıklara ayır."""
    found = []
    for regex in (PYTHON_NAMED_PRINTF, PRINTF_RE, BRACE_RE, METIN_PIPE_RE,
                  BRACKET_DIRECTIVE_RE, ESCAPE_RE, URL_RE, TAG,
                  WHITESPACE_STRUCTURE):
        for m in regex.finditer(text):
            found.append((m.start(), m.end(), m.group()))
    found.sort(key=lambda s: (s[0], -(s[1]-s[0])))
    spans = []
    cursor = 0
    for start, end, token in found:
        if start < cursor:
            continue
        spans.append((start, end, token))
        cursor = end
    return spans


def technical_contract(source: str, target: str) -> None:
    if any(s in target for s in FORBIDDEN):
        raise TranslationBlocked("NUL veya Unicode kayıp karakteri bulundu")
    src = [t for _, _, t in token_spans(source)]
    dst = [t for _, _, t in token_spans(target)]
    if src != dst:
        raise TranslationBlocked("Kod, URL, işaretleyici veya placeholder değişti")
    # Var olan LocaleGuard'ın kurallarını da gevşetmeden uygula.
    if protected_tokens(source) != protected_tokens(target):
        raise TranslationBlocked("LocaleGuard protected-token sözleşmesi bozuldu")
    if layout_tokens(source) != layout_tokens(target):
        raise TranslationBlocked("LocaleGuard kaçış/layout sözleşmesi bozuldu")
    if source.count("\t") != target.count("\t"):
        raise TranslationBlocked("TSV sekme adedi değişti")
    if "\n" in source or "\r" in source or "\n" in target or "\r" in target:
        if (source.count("\n"), source.count("\r")) != (
            target.count("\n"), target.count("\r")
        ):
            raise TranslationBlocked("Satır sonu düzeni değişti")
    if MARKER.search(target) and not MARKER.search(source):
        raise TranslationBlocked("Koruyucu sahte marker metne sızdı")


def segment_translate(provider: Provider, text: str, source: str, target: str) -> str:
    """Teknik span'leri servise hiç gönderme, baş/son boşlukları koru."""
    if source == target:
        return text
    if not isinstance(text, str) or any(b in text for b in FORBIDDEN):
        raise TranslationBlocked("Geçersiz kaynak metin")
    if MARKER.search(text):
        raise TranslationBlocked("Önceden var olan koruma marker'ı")
    result = []
    cursor = 0

    def human(section: str) -> str:
        if not section or not any(ch.isalpha() for ch in section):
            return section
        left = len(section) - len(section.lstrip())
        right = len(section) - len(section.rstrip())
        prefix = section[:left]
        suffix = section[len(section)-right:] if right else ""
        body = section[left:len(section)-right if right else len(section)]
        if not body:
            return section
        converted = provider.translate(body, source, target)
        if not isinstance(converted, str) or not converted.strip():
            raise TranslationBlocked("Çeviri servisi boş veya geçersiz metin döndürdü")
        if any(b in converted for b in FORBIDDEN) or "\t" in converted or "\r" in converted or "\n" in converted:
            raise TranslationBlocked("Çeviri servisi yapısal karakter ekledi")
        if MARKER.search(converted):
            raise TranslationBlocked("Çeviri servisi sahte marker ekledi")
        return prefix + converted + suffix

    for start, end, token in token_spans(text):
        result.append(human(text[cursor:start]))
        result.append(token)
        cursor = end
    result.append(human(text[cursor:]))
    translated = "".join(result)
    technical_contract(text, translated)
    return translated


def verify_bridge(provider: Provider, turkish: str, *,
                  english: str | None = None) -> dict:
    """TR->EN->TR kontrolü; düşük skor otomatik onaya dönüşmez."""
    source_english = english if english is not None else segment_translate(
        provider, turkish, "tr", "en"
    )
    technical_contract(turkish, source_english)
    back = segment_translate(provider, source_english, "en", "tr")
    technical_contract(turkish, back)
    score = similarity(turkish, back)
    detected, evidence = suspicious_foreign_language(turkish)
    return {
        "source_tr_sha256": sha(turkish),
        "english": source_english,
        "english_sha256": sha(source_english),
        "back_tr": back,
        "bridge_score": round(score, 4),
        "source_tr_foreign_hint": detected,
        "source_tr_evidence": evidence,
        "status": ("ANLAMSAL_INCELEME" if score < MIN_BRIDGE_SCORE or
                   detected in ("en", "de") else "TEKNIK_ADAY"),
        "human_approved": False,
    }


def verify_target(provider: Provider, turkish: str, english: str,
                  lang: str, code: str, encoding: str) -> dict:
    """TR->EN->TARGET->EN ve EN->TR kıyasları; encoding strict."""
    if lang == "tr":
        candidate = turkish
        back_en = english
    elif lang == "en":
        candidate = english
        back_en = english
    else:
        candidate = segment_translate(provider, english, "en", code)
        back_en = segment_translate(provider, candidate, code, "en")
    technical_contract(turkish, candidate)
    technical_contract(english, back_en)
    try:
        raw = candidate.encode(encoding, "strict")
        if raw.decode(encoding, "strict") != candidate:
            raise TranslationBlocked("Tekrar kodlama farklı sonuç üretti")
    except (LookupError, UnicodeError) as exc:
        raise TranslationBlocked(
            "Hedef dilin mevcut kodlamasına kayıpsız yazılamıyor: " + lang
        ) from exc
    score = similarity(english, back_en)
    return {
        "language": lang, "language_code": code, "encoding": encoding,
        "source_tr_sha256": sha(turkish), "english_sha256": sha(english),
        "text": candidate, "target_sha256": sha(candidate),
        "back_english": back_en, "target_roundtrip_score": round(score, 4),
        "status": "TEKNIK_ADAY" if score >= MIN_TARGET_SCORE else "ANLAMSAL_INCELEME",
        "human_approved": False, "live_apply_allowed": False,
    }


def cache_key(text: str, source: str, target: str, glossary_sha: str,
              provider_version: str = "v1") -> str:
    raw = json.dumps(
        {"v": 1, "source_sha256": sha(text), "from": source, "to": target,
         "glossary_sha256": glossary_sha, "provider_version": provider_version},
        sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return sha(raw)


def cache_lookup(cache: dict, key: str, source: str,
                 target: str) -> str | None:
    entry = cache.get(key)
    if not isinstance(entry, dict):
        return None
    if (entry.get("source_sha256") != sha(source)
            or entry.get("target_language") != target
            or entry.get("human_approved") is not True
            or entry.get("contract_validated") is not True):
        return None
    text = entry.get("text")
    if not isinstance(text, str):
        return None
    technical_contract(source, text)
    return text
