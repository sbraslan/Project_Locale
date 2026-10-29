#!/usr/bin/env python3
"""Artımlı Master/dil fark taraması; canlı dosyalara yazmaz."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from pathlib import Path, PurePosixPath

from tools.locale_guard.locale_guard import (
    classify_mode, decode_text, keyed_tsv_rows, load_config, relative_txt_paths,
)


def require(ok: bool, message: str) -> None:
    if not ok:
        raise RuntimeError("[ENGEL] " + message)


class FileContentError(RuntimeError):
    """Dosya verisi güvenle okunamadı; satır taraması sürdürülür, uygulama engellenir."""


def safe_path(value: str) -> Path:
    p = PurePosixPath(value)
    require(bool(value) and p.as_posix() == value and not p.is_absolute()
            and ".." not in p.parts and ":" not in value and "\\" not in value,
            "Güvensiz dosya yolu: " + str(value))
    return Path(*p.parts)


def read_file(root: Path, lang: str, rel: str, config: dict) -> tuple[str, str]:
    p = root / lang / safe_path(rel)
    require(p.is_file(), "Dosya bulunamadı: " + str(p))
    cursor = p
    while cursor != root:
        require(not cursor.is_symlink(), "Sembolik bağlantı yasak: " + str(cursor))
        cursor = cursor.parent
    raw = p.read_bytes()
    if b"\x00" in raw:
        positions = [i for i, value in enumerate(raw) if value == 0]
        raise FileContentError(
            "NUL içerik: " + str(p.relative_to(root))
            + " | NUL_sayısı=" + str(len(positions))
            + " | ilk_konumlar=" + repr(positions[:12])
            + " | ilk_32_bayt=" + raw[:32].hex(" ")
            + " | SHA256=" + hashlib.sha256(raw).hexdigest()
            + " | kaynak dosya değiştirilmedi"
        )
    try:
        text, codec, _ = decode_text(
            raw, language=lang,
            legacy_encodings=config.get("legacy_decodings", {}).get(lang, []),
        )
    except (UnicodeDecodeError, LookupError) as exc:
        position = getattr(exc, "start", None)
        sample = (raw[:32] if position is None
                  else raw[max(0, position - 12):position + 16])
        raise FileContentError(
            "Kodlama çözümlenemedi: " + str(p.relative_to(root))
            + " | UTF8_BOM=" + str(raw.startswith(bytes.fromhex("efbbbf")))
            + " | hata_pozisyonu=" + str(position)
            + " | bayt_örneği=" + sample.hex(" ")
            + " | tanımlı_kodlamalar="
            + repr(config.get("legacy_decodings", {}).get(lang, []))
            + " | ayrıntı=" + str(exc)
        ) from exc
    if text.encode(codec) != raw or "\ufffd" in text:
        raise FileContentError(
            "Kodlama bütünlüğü hatası: " + str(p.relative_to(root))
        )
    return text, hashlib.sha256(raw).hexdigest()


def validate_nontranslatable_filter(root: Path, lang: str, rel: str,
                                   config: dict) -> str:
    """İçeriği asla çevrilmeyen kelime filtresinin bayt biçimini doğrula."""
    require(rel == "insult.txt", "Desteklenmeyen filtre türü")
    p = root / lang / safe_path(rel)
    require(p.is_file() and not p.is_symlink(), "Filtre dosyası geçersiz")
    raw = p.read_bytes()
    if raw.startswith(bytes.fromhex("fffe")):
        codec = "utf-16"
    elif raw.startswith(bytes.fromhex("feff")):
        codec = "utf-16"
    else:
        try:
            read_file(root, lang, rel, config)
        except FileContentError:
            raise
        return hashlib.sha256(raw).hexdigest()
    try:
        decoded = raw.decode(codec, "strict")
        require(decoded.encode(codec) == raw and "\ufffd" not in decoded,
                "UTF-16 filtre bayt bütünlüğü hatası: " + lang + "/" + rel)
    except UnicodeError as exc:
        raise FileContentError(
            "UTF-16 filtre dosyası okunamadı: " + lang + "/" + rel
            + " | " + str(exc)
        ) from exc
    return hashlib.sha256(raw).hexdigest()


def read_raw_hash(root: Path, lang: str, rel: str) -> str:
    """Yazma yapılmayacak yapısal dosya: yalnız ham bayt kimliği."""
    p = root / lang / safe_path(rel)
    require(p.is_file(), "Yapısal dosya eksik: " + lang + "/" + rel)
    cursor = p
    while cursor != root:
        require(not cursor.is_symlink(), "Yapısal dosyada symlink: " + str(cursor))
        cursor = cursor.parent
    return hashlib.sha256(p.read_bytes()).hexdigest()


def parse_rows(text: str, spec: dict, rel: str, lang: str) -> dict:
    require(spec.get("kind") == "keyed_tsv", "Tanınmayan parser: " + rel)
    rows, duplicates = keyed_tsv_rows(
        text,
        key_columns=tuple(spec.get("key_columns", [0])),
        key_exclude_columns=tuple(spec.get("key_exclude_columns", [])),
        header_rows=int(spec.get("header_rows", 0)),
        duplicate_policy=spec.get("duplicate_policy", "error"),
    )
    require(not duplicates, f"Tekrarlı anahtar: {lang}/{rel}: {duplicates[:5]}")
    return rows


def row_hash(columns: list[str]) -> str:
    value = json.dumps(columns, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def collect(root: Path, config: dict, baseline: dict | None = None,
            languages: list[str] | None = None) -> dict:
    master_lang = config["master_directory"]
    master = root / master_lang
    require(master.is_dir() and not master.is_symlink(), "Master klasörü geçersiz")
    targets = languages if languages is not None else config["languages"]
    require(bool(targets) and len(set(targets)) == len(targets)
            and all(t in config["languages"] and t != master_lang for t in targets),
            "Dil seçimi geçersiz")
    if baseline is not None:
        require(baseline.get("schema_version") == 1
                and baseline.get("status") == "APPROVED_SYNC_BASELINE"
                and isinstance(baseline.get("languages"), dict),
                "Onaylı senkronizasyon baseline dosyası geçersiz")
    files = relative_txt_paths(master)
    registry = config.get("parser_registry", {})
    results = []
    counts = Counter()
    cache = {}

    def add(status: str, lang: str, rel: str, **details) -> None:
        results.append({"status": status, "language": lang, "path": rel, **details})
        counts[status] += 1

    for lang in targets:
        target = root / lang
        require(target.is_dir() and not target.is_symlink(), "Dil klasörü geçersiz: " + lang)
        target_files = relative_txt_paths(target)
        for rel in sorted(files | target_files):
            safe_path(rel)
            if rel not in files:
                add("EXTRA_FILE_REVIEW", lang, rel)
                continue
            if rel not in target_files:
                add("MISSING_FILE_TRANSLATION", lang, rel)
                continue
            spec = registry.get(rel)
            if spec is not None and spec.get("token_columns") == "disabled":
                add("SCHEMA_TRANSLATION_DISABLED", lang, rel,
                    master_sha256=read_raw_hash(root, master_lang, rel),
                    target_sha256=read_raw_hash(root, lang, rel),
                    translation_allowed=False,
                    reason="Çevrilecek sütun şemada tanımlı değil; baytlar korunuyor.")
                continue
            if rel == "insult.txt":
                try:
                    master_digest = validate_nontranslatable_filter(
                        root, master_lang, rel, config)
                    target_digest = validate_nontranslatable_filter(
                        root, lang, rel, config)
                except FileContentError as exc:
                    add("FILE_ENCODING_REVIEW", lang, rel, reason=str(exc))
                else:
                    add("SPECIAL_FILE_PRESERVED", lang, rel,
                        master_sha256=master_digest,
                        target_sha256=target_digest,
                        translation_allowed=False)
                continue
            if rel not in cache:
                try:
                    cache[rel] = read_file(root, master_lang, rel, config)
                except FileContentError as exc:
                    cache[rel] = exc
            if isinstance(cache[rel], FileContentError):
                add("FILE_ENCODING_REVIEW", lang, rel,
                    affected_side=master_lang, reason=str(cache[rel]))
                continue
            master_text, master_sha = cache[rel]
            try:
                target_text, target_sha = read_file(root, lang, rel, config)
            except FileContentError as exc:
                add("FILE_ENCODING_REVIEW", lang, rel,
                    affected_side=lang, reason=str(exc))
                continue
            if spec is None or spec.get("kind") != "keyed_tsv":
                add("FREEFORM_OR_UNREGISTERED_REVIEW", lang, rel,
                    master_sha256=master_sha, target_sha256=target_sha)
                continue
            if classify_mode(master_text) != "tsv" or classify_mode(target_text) != "tsv":
                add("PARSER_SHAPE_REVIEW", lang, rel)
                continue
            source_rows = parse_rows(master_text, spec, rel, master_lang)
            target_rows = parse_rows(target_text, spec, rel, lang)
            for key in sorted(source_rows.keys() - target_rows.keys()):
                add("MISSING_KEY_TRANSLATION", lang, rel, key=key,
                    source_sha256=row_hash(source_rows[key]))
            for key in sorted(target_rows.keys() - source_rows.keys()):
                add("EXTRA_KEY_REVIEW", lang, rel, key=key)
            entry = (baseline or {}).get("languages", {}).get(lang, {}).get(rel)
            if entry is None:
                add("BASELINE_REQUIRED", lang, rel,
                    shared_keys=len(source_rows.keys() & target_rows.keys()),
                    master_sha256=master_sha, target_sha256=target_sha)
            else:
                require(isinstance(entry, dict) and isinstance(entry.get("master_rows"), dict),
                        "Bozuk baseline kayıt yapısı: " + lang + "/" + rel)
                previous = entry["master_rows"]
                for key in sorted(source_rows.keys() & target_rows.keys()):
                    current = row_hash(source_rows[key])
                    if previous.get(key) != current:
                        add("SOURCE_CHANGED_RETRANSLATION", lang, rel,
                            key=key, source_sha256=current)
    return {
        "schema_version": 1,
        "mode": "READ_ONLY_DELTA_SCAN_WITH_BLOCKERS" if counts["FILE_ENCODING_REVIEW"]
                else "READ_ONLY_DELTA_SCAN",
        "source": master_lang, "languages": targets,
        "counts": dict(sorted(counts.items())),
        "action_count": len(results), "automatic_apply": False,
        "notes": "Fazla anahtar silinmez; başlangıç referansı yoksa eski çeviri senkron sayılmaz.",
        "items": results,
    }


def main(argv: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(description="Artımlı dil taraması; canlı dosyalara yazmaz.")
    cli.add_argument("--root", default=".")
    cli.add_argument("--config", default="tools/locale_guard/config.json")
    cli.add_argument("--baseline", default=".locale_guard/last_synced_master_v1.json")
    cli.add_argument("--output", default=".locale_guard/incremental_scan_v1.json")
    cli.add_argument("--language", action="append")
    args = cli.parse_args(argv)
    root = Path(args.root).resolve()
    try:
        config = load_config(root / args.config)
        baseline_path = root / args.baseline
        baseline = (json.loads(baseline_path.read_text(encoding="utf-8-sig"))
                    if baseline_path.is_file() else None)
        result = collect(root, config, baseline, args.language)
        output = root / args.output
        require(output.resolve().is_relative_to((root / ".locale_guard").resolve()),
                "Rapor yalnız .locale_guard içinde olabilir")
        require(not output.is_symlink(), "Rapor yolu sembolik bağlantı")
        output.parent.mkdir(parents=True, exist_ok=True)
        tmp = output.with_suffix(output.suffix + ".tmp")
        require(not tmp.exists() and not tmp.is_symlink(), "Geçici rapor zaten var")
        raw = (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        with tmp.open("xb") as handle:
            handle.write(raw)
        os.replace(tmp, output)
        print("=" * 68)
        print("ARTIMLI DİL TARAMASI - MASTER / TÜM DİLLER")
        print("=" * 68)
        print("Dil sayısı:", len(result["languages"]), "Bulgular:", result["action_count"])
        for status, count in result["counts"].items():
            print(f"{status}: {count}")
        print("Rapor:", output.relative_to(root))
        if result["counts"].get("FILE_ENCODING_REVIEW", 0):
            print("[ENGEL] Kodlama/NUL inceleme kaydı var. Diğer dosyalar tarandı.")
            print("Onaylı uygulama: KİLİTLİ. Raporda ayrıntılar mevcut.")
            print("Canlı dosya değişikliği: HAYIR")
            return 2
        print("Canlı dosya değişikliği: HAYIR")
        return 0
    except (RuntimeError, OSError, ValueError, UnicodeError, KeyError, TypeError) as exc:
        print("[ENGEL]", str(exc))
        print("Canlı dosyalara yazılmadı.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
