#!/usr/bin/env python3
"""SHA kaynak kimliğine bağlı, geçici QA adayı önbelleği.

Bu cache "insan onayı" değildir. Eski proto translation_cache.json
otomatik ithal edilmez. Canlı locale dosyalarına hiç erişmez.
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from tools.locale_guard.unified_translation_contract_v1 import (
    TranslationBlocked, technical_contract, sha,
)

CACHE_NAME = "unified_qa_cache_v1.json"


def load_cache(guard: Path) -> dict:
    if guard.is_symlink() or not guard.is_dir():
        raise TranslationBlocked("Önbellek klasörü güvenli değil")
    path = guard / CACHE_NAME
    if path.is_symlink():
        raise TranslationBlocked("Sembolik bağlantılı cache yasak")
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or not isinstance(data.get("entries"), dict):
        raise TranslationBlocked("Önbellek şeması bozuk; kullanılmayacak")
    return data["entries"]


def validated_bridge(cache: dict, key: str, turkish: str):
    value = cache.get(key)
    if not isinstance(value, dict):
        return None
    if (value.get("status") != "TEKNIK_ADAY"
            or value.get("source_tr_sha256") != sha(turkish)):
        return None
    translated = value.get("english")
    back = value.get("back_tr")
    if not isinstance(translated, str) or not isinstance(back, str):
        return None
    try:
        technical_contract(turkish, translated)
        technical_contract(turkish, back)
    except TranslationBlocked:
        return None
    return value


def validated_target(cache: dict, key: str, turkish: str,
                     english: str, language: str, codec: str):
    value = cache.get(key)
    if not isinstance(value, dict):
        return None
    if (value.get("status") != "TEKNIK_ADAY"
            or value.get("source_tr_sha256") != sha(turkish)
            or value.get("english_sha256") != sha(english)
            or value.get("language") != language
            or value.get("encoding") != codec):
        return None
    translated = value.get("text")
    if not isinstance(translated, str):
        return None
    try:
        technical_contract(turkish, translated)
        raw = translated.encode(codec, "strict")
        if raw.decode(codec, "strict") != translated:
            return None
    except (TranslationBlocked, LookupError, UnicodeError):
        return None
    return value


def write_cache(guard: Path, entries: dict) -> None:
    """Önce eskisinin ayrı yedeği alınır, sonra atomik değişim."""
    if not isinstance(entries, dict):
        raise TranslationBlocked("Önbellek veri türü geçersiz")
    guard = guard.resolve()
    if not guard.is_dir():
        raise TranslationBlocked("Önbellek klasörü yok")
    path = guard / CACHE_NAME
    if path.is_symlink():
        raise TranslationBlocked("Cache symlink nedeniyle reddedildi")
    payload = (json.dumps(
        {"schema_version": 1, "entries": entries},
        ensure_ascii=False, sort_keys=True, indent=2,
    ) + "\n").encode("utf-8")
    if path.is_file() and path.read_bytes() == payload:
        return
    if path.is_file():
        previous = path.read_bytes()
        backup_dir = guard / "qa_cache_backups"
        if backup_dir.is_symlink():
            raise TranslationBlocked("Cache yedek klasöründe symlink")
        backup_dir.mkdir(exist_ok=True)
        backup = backup_dir / (CACHE_NAME + "." + uuid.uuid4().hex + ".bak")
        with backup.open("xb") as file:
            file.write(previous)
            file.flush()
            os.fsync(file.fileno())
    temp = guard / ("." + CACHE_NAME + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temp.open("xb") as file:
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()
