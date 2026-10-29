#!/usr/bin/env python3
"""Tek komut Master-first Express: tüm dosyaları tarar, sonra güvenli staging.

Uygulama ve silme bu sürümde özellikle KAPALI. İnsan onaylı, SHA
kilitli tam Türkçe master olmadan hedef dillere kesinlikle geçmez.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import uuid
from collections import Counter
from pathlib import Path

from tools.locale_guard.locale_guard import load_config
from tools.locale_guard.unified_master_first_plan_v1 import build_plan
from tools.locale_guard.unified_master_tr_stage_v1 import (
    LocalLibreTranslate, stage_master,
)
from tools.locale_guard.unified_targets_stage_v1 import (
    approved_source, build_staging,
)
from tools.locale_guard.unified_translation_contract_v1 import TranslationBlocked


def save_json(path: Path, document: dict) -> None:
    if path.is_symlink():
        raise TranslationBlocked("Sembolik çıktı yolu yasak")
    payload = (json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2)
               + "\n").encode("utf-8")
    with path.open("xb") as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())


def safe_report_dir(root: Path) -> Path:
    if root.name != "Project_Locale":
        raise TranslationBlocked("Yalnız Project_Locale üzerinde çalışır")
    guard = root / ".locale_guard"
    if not guard.is_dir() or guard.is_symlink():
        raise TranslationBlocked(".locale_guard güvenli değil")
    base = guard / "unified_express"
    if base.is_symlink():
        raise TranslationBlocked("Express klasörü sembolik bağlantı")
    base.mkdir(exist_ok=True)
    uid = (dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
           + "_" + uuid.uuid4().hex[:8])
    path = base / uid
    path.mkdir(mode=0o700)
    return path


def check_tests(root: Path) -> bool:
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [sys.executable, "-B", "-m", "unittest", "discover", "-s",
         "tools/locale_guard/tests", "-q"],
        cwd=root, capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=environment,
    )
    print("Bütün güvenlik testleri:", "BAŞARILI" if completed.returncode == 0 else "ENGEL")
    if completed.returncode:
        print((completed.stdout + completed.stderr)[-1200:])
    return completed.returncode == 0


def express(root: Path, *, service: bool = False,
            limit: int = 50, approval: Path | None = None,
            with_tests: bool = True, provider=None) -> dict:
    if limit < 1:
        raise TranslationBlocked("Hedef pilot limit pozitif olmalı")
    guard = root / ".locale_guard"
    if root.name != "Project_Locale" or not guard.is_dir() or guard.is_symlink():
        raise TranslationBlocked("Proje dizini/rapor klasörü geçersiz")
    if with_tests and not check_tests(root):
        raise TranslationBlocked("Birim testleri başarısız")
    config = load_config(root / "tools/locale_guard/config.json")
    report_dir = safe_report_dir(root)
    result = {
        "schema_version": 1, "status": "BASLADI",
        "source_directory": "master", "languages": config["languages"],
        "master_complete": False, "all_16_languages_ready": False,
        "live_apply": False, "auto_delete": False, "runtime_started": False,
        "steps": [],
    }
    try:
        plan = build_plan(root, config)
        save_json(report_dir / "master_16_dil_tarama.json", plan)
        result["steps"].append({"step": "TÜM_DOSYALARI_TARA", "status": "TAMAMLANDI",
                                "txt": plan["master_txt_files"],
                                "cells": plan["master_cells_reviewed"]})
        result["master_pending_foreign_hint"] = plan["master_translation_queue_count"]
        result["master_language_review_pending"] = plan["source_language_review_count"]
        print("Master TXT:", plan["master_txt_files"],
              " | Master çeviri adayı:", result["master_pending_foreign_hint"])
        if service:
            local = provider if provider is not None else LocalLibreTranslate()
            stage = stage_master(root, config, local, limit=limit)
            error_counts = Counter(
                str(item.get("reason", item.get("status", "BİLİNMİYOR")))
                for item in stage["blocked"]
            )
            stage_status = ("INCELEME_BEKLIYOR" if stage["changes"]
                            else "SIFIR_CEVIRI_ADAYI_KABUL_EDILDI")
            stage_info = {
                "status": stage_status,
                "attempted": len(stage["changes"]) + len(stage["blocked"]),
                "candidates": len(stage["changes"]),
                "held": len(stage["blocked"]),
                "held_reasons": dict(error_counts.most_common()),
                "remaining": stage["queued_not_attempted"],
                "master_staged_file_sha256": {
                    name: info["new_sha256"]
                    for name, info in stage["staged_files"].items()
                },
            }
            # Staging yalnız express çalışma klasörüne yazılır; asıl locale'a değil.
            for rel, info in stage["staged_files"].items():
                dest = report_dir / "master_staging" / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                with dest.open("xb") as f:
                    f.write(info["bytes"])
                    f.flush()
                    os.fsync(f.fileno())
            save_json(report_dir / "master_staging_ozet.json", stage_info)
            # Tek tek engelleri kaydet: 0 dosya durumunda sessiz başarısızlık yok.
            # Master/ ve 16 dilin canlı klasörlerine dokunulmaz.
            save_json(report_dir / "master_staging_engeller.json", {
                "schema_version": 1,
                "status": stage_status,
                "attempted": stage_info["attempted"],
                "blocked_count": len(stage["blocked"]),
                "blocked_reasons": dict(error_counts.most_common()),
                "blocked": stage["blocked"],
                "live_apply": False,
            })
            result["steps"].append({"step": "MASTER_TURKCE_STAGING",
                                    **stage_info})
            print("Master staging dosyası:", len(stage["staged_files"]),
                  "| İnsani onay gereklidir.")
            print("Denenen:", stage_info["attempted"],
                  "| Kabul edilen hücre:", len(stage["changes"]),
                  "| İncelemeye ayrılan:", len(stage["blocked"]))
            for reason, number in error_counts.most_common(6):
                print(" - Engel:", number, "kayıt |", reason)
            print("Ayrıntı: ", (report_dir / "master_staging_engeller.json").relative_to(root))
        else:
            result["steps"].append({
                "step": "MASTER_TURKCE_STAGING",
                "status": "CEVIRI_SERVISI_KAPALI",
            })
        if approval is not None:
            # Tüm Master'ın Türkçe olmasına ilişkin SHA onayı KESİNLİKLE gerekli.
            approved_source(root, config, approval)
            if not service:
                raise TranslationBlocked("İngilizce köprü için yerel çeviri servisi kapalı")
            target_stage = build_staging(
                root, config, local, approval, max_cells=limit)
            staged = []
            for item in target_stage["files"]:
                dest = report_dir / "hedef_staging" / item["language"] / item["path"]
                dest.parent.mkdir(parents=True, exist_ok=True)
                with dest.open("xb") as f:
                    f.write(item["bytes"])
                    f.flush()
                    os.fsync(f.fileno())
                staged.append({k: v for k, v in item.items() if k != "bytes"})
            save_json(report_dir / "16_dil_staging.json", {
                "files": staged, "held": target_stage["blocked_files"],
                "all_16_languages_ready": False, "live_apply": False,
            })
            result["steps"].append({
                "step": "16_DIL_KOPRU_STAGING",
                "status": "INSAN_ONAYI_BEKLIYOR",
                "files": len(staged),
                "held": len(target_stage["blocked_files"]),
            })
        else:
            result["steps"].append({
                "step": "16_DIL_KOPRU_STAGING",
                "status": "TAM_TURKCE_MASTER_ONAYI_BEKLENIYOR",
            })
        result["status"] = (
            "CEVIRI_ENGELLERI_INCELENMELI"
            if service and not stage["changes"] and stage["blocked"]
            else "ONIZLEME_HAZIR_ONAY_BEKLIYOR"
        )
    except Exception as exc:
        result["status"] = "GUVENLIK_ENGELI"
        result["error"] = str(exc)
        raise
    finally:
        save_json(report_dir / "sonuc.json", result)
        print("EXPRESS rapor klasörü:", report_dir.relative_to(root))
        print("Canlı Master / 16 dil değiştirildi: HAYIR")
    return result


def main() -> int:
    p = argparse.ArgumentParser(description="Master Türkçe -> EN -> 16 dil Express güvenli önizleme")
    p.add_argument("--root", default=".")
    p.add_argument("--service", choices=("off", "local"), default="off")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--source-approval")
    args = p.parse_args()
    try:
        root = Path(args.root).resolve()
        approval = root / args.source_approval if args.source_approval else None
        result = express(root, service=args.service == "local",
                         limit=args.limit, approval=approval)
        print("Sonuç:", result["status"])
        return 3 if result["status"] == "CEVIRI_ENGELLERI_INCELENMELI" else 0
    except (RuntimeError, OSError, ValueError, UnicodeError, KeyError) as exc:
        print("[ENGEL] Master-first Express:", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
