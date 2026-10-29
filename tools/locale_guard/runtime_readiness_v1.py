#!/usr/bin/env python3
"""Uygulanmış 316 hücre için TR runtime ön koşul kontrolü (salt okunur)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from tools.locale_guard.master_tr_live_apply_v1 import (
    depo_kontrol, dosya_yolu, son_islem,
)
from tools.locale_guard.master_tr_rehearsal import PREVIEW_SHA, digest, read_safe


def runtime_kontrol(kok: Path) -> dict:
    depo_kontrol(kok)
    folder = son_islem(kok)
    journal = json.loads(read_safe(kok, folder / "islem.json").decode("utf-8"))
    if journal.get("durum") != "UYGULANDI":
        raise RuntimeError("Son işlem uygulanmış durumda değil; runtime provası hazır değil")
    if journal.get("onizleme_sha256") != PREVIEW_SHA:
        raise RuntimeError("Önizleme kimliği uygulama kaydıyla eşleşmiyor")
    items = journal.get("dosyalar", [])
    if len(items) != 92:
        raise RuntimeError("92 dosyalık uygulama kapsamı eksik")
    lookup = {}
    for item in items:
        language, rel, expected = item["dil"], item["dosya"], item["yeni_sha256"]
        key = (language, rel)
        if key in lookup:
            raise RuntimeError("Yinelenen işlem dosyası: " + str(key))
        data = read_safe(kok, dosya_yolu(kok, language, rel), expected)
        lookup[key] = data
    target_names = {rel for lang, rel in lookup if lang == "tr"}
    master_names = {rel for lang, rel in lookup if lang == "master"}
    if len(target_names) != 46 or target_names != master_names:
        raise RuntimeError("Master/TR dosya kapsamı eşleşmiyor")
    for rel in target_names:
        if lookup[("master", rel)] != lookup[("tr", rel)]:
            raise RuntimeError("Master/TR içeriği ayrışmış: " + rel)
    return {
        "durum": "TR_RUNTIME_ON_KONTROL_GECTI",
        "onizleme_sha256": PREVIEW_SHA,
        "islem": folder.name,
        "dogrulanan_dosya": 92,
        "master_tr_esit": True,
        "diger_15_dil_tamamlandi": False,
        "istemciye_kopyalandi": False,
        "oyun_ici_test_yapildi": False,
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Türkçe runtime ön koşul denetimi")
    p.add_argument("--root", default=".")
    args = p.parse_args()
    try:
        result = runtime_kontrol(Path(args.root).resolve())
        print("=" * 66)
        print("TÜRKÇE RUNTIME HAZIRLIK DENETİMİ")
        print("=" * 66)
        print("[BAŞARILI] 46 master + 46 TR dosyası SHA-256 ile doğrulandı.")
        print("[BAŞARILI] Master ve TR birebir eşleşiyor.")
        print("İşlem kayıt numarası:", result["islem"])
        print("DİKKAT: Diğer 15 dilin eksikleri bitmiş sayılmaz.")
        print("DİKKAT: İstemci ve Binary dosyalarına kopyalama yapılmadı.")
        print("SONRAKİ ADIM: Önce yedekli istemci yerelleştirmesi, sonra oyun içi test.")
        return 0
    except (RuntimeError, OSError, ValueError, UnicodeError, KeyError, TypeError) as exc:
        print("[ENGEL] Türkçe runtime ön koşulları sağlanmadı:", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
