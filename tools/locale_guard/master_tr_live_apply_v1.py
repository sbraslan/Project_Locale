#!/usr/bin/env python3
"""316 hücrelik önizleme için insan onaylı, geri alınabilir Master/TR işlemi.

Asla otomatik onaylamaz, çeviri üretmez, Git push/commit yapmaz.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path

from tools.locale_guard.locale_guard import decode_text
from tools.locale_guard.master_tr_rehearsal import (
    EXCLUDED, PREVIEW_SHA, CANDIDATE_SHA, PACK_SHA,
    digest, read_safe, relative_path, split_eol, validate,
)

ACK_REVIEW = "ONAYLIYORUM 316"
ACK_APPLY = "UYGULA 316"
ACK_ROLLBACK = "GERI AL"
REVIEW_REL = ".locale_guard/master_tr_inceleme_" + PREVIEW_SHA[:12] + ".tsv"
APPROVAL_REL = ".locale_guard/master_tr_onay_" + PREVIEW_SHA[:12] + ".json"
TRANSACTIONS_REL = ".locale_guard/master_tr_canli_islemler"
LOCK_REL = ".locale_guard/master_tr_islem_kilidi.lock"


def kontrol(kosul: bool, mesaj: str) -> None:
    if not kosul:
        raise RuntimeError("[ENGEL] " + mesaj)


def kaydet_atomik(yol: Path, veri: bytes) -> None:
    """Tek dosyalık atomik değişiklik, geçici dosya ve fsync."""
    gecici = yol.with_name("." + yol.name + "." + uuid.uuid4().hex + ".tmp")
    kontrol(not yol.is_symlink(), "Sembolik bağlantıya yazma reddedildi: " + str(yol))
    try:
        with gecici.open("xb") as f:
            f.write(veri)
            f.flush()
            os.fsync(f.fileno())
        os.replace(gecici, yol)
    finally:
        if gecici.exists():
            gecici.unlink()


def json_bayt(deger: dict) -> bytes:
    return (json.dumps(deger, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def depo_kontrol(kok: Path) -> Path:
    kontrol(kok.name == "Project_Locale", "Project_Locale deposu dışında işlem yasak")
    guard = kok / ".locale_guard"
    kontrol(guard.is_dir() and not guard.is_symlink(), ".locale_guard dizini güvenilir değil")
    return guard


def dosya_yolu(kok: Path, dil: str, goreli: str) -> Path:
    kontrol(dil in ("master", "tr"), "Yalnız master ve tr desteklenir")
    return kok / dil / relative_path(goreli)


def rapor_baytlari(kok: Path, onizleme: dict[str, bytes]) -> bytes:
    """Kullanıcının 316 hücreyi içerikleriyle incelemesi için TSV raporu."""
    satirlar = ["Dosya\tSatır\tSütun\tÖnce\tSonra\n"]
    adet = 0
    for goreli, yeni_bayt in sorted(onizleme.items()):
        once = read_safe(kok, dosya_yolu(kok, "tr", goreli))
        eski, _, _ = decode_text(
            once, language="tr", legacy_encodings=("cp1254",))
        yeni, _, _ = decode_text(
            yeni_bayt, language="tr", legacy_encodings=("cp1254",))
        eski_satirlar = eski.splitlines(keepends=True)
        yeni_satirlar = yeni.splitlines(keepends=True)
        kontrol(len(eski_satirlar) == len(yeni_satirlar),
                "İnceleme satır sayısı değişti: " + goreli)
        for sira, (a, b) in enumerate(zip(eski_satirlar, yeni_satirlar), 1):
            aa = split_eol(a)[0].split("\t")
            bb = split_eol(b)[0].split("\t")
            kontrol(len(aa) == len(bb), "İnceleme sütun sayısı değişti")
            for sutun, (eski_hucre, yeni_hucre) in enumerate(zip(aa, bb)):
                if eski_hucre == yeni_hucre:
                    continue
                kontrol((goreli, sira, sutun) != EXCLUDED,
                        "Onaysız Dragon Alchemy hücresi incelemeye girmiş")
                satirlar.append(
                    f"{goreli}\t{sira}\t{sutun}\t"
                    + json.dumps(eski_hucre, ensure_ascii=False)
                    + "\t" + json.dumps(yeni_hucre, ensure_ascii=False) + "\n"
                )
                adet += 1
    kontrol(adet == 316, "İnceleme listesi 316 hücre değil: " + str(adet))
    return "".join(satirlar).encode("utf-8")


def inceleme_olustur(kok: Path, summary: dict, previews: dict[str, bytes]) -> Path:
    depo_kontrol(kok)
    kontrol(summary["planned_change_count"] == 316 and
            summary["excluded_unapproved_count"] == 1, "Beklenen kapsam farklı")
    hedef = kok / REVIEW_REL
    veri = rapor_baytlari(kok, previews)
    if hedef.exists():
        kontrol(read_safe(kok, hedef) == veri, "Mevcut inceleme raporu farklı")
    else:
        with hedef.open("xb") as f:
            f.write(veri)
            f.flush()
            os.fsync(f.fileno())
    return hedef


def onay_kaydi(kok: Path, summary: dict, previews: dict[str, bytes]) -> dict:
    kontrol(summary["preview_manifest_sha256"] == PREVIEW_SHA and
            summary["candidate_manifest_sha256"] == CANDIDATE_SHA,
            "Önizleme manifesti sürümü farklı")
    return {
        "surum": 1,
        "durum": "KULLANICI_TARAFINDAN_ONAYLANDI",
        "onaylanan_hucre": 316,
        "haric_tutulan_hucre": 1,
        "hedef_klasorler": ["tr", "master"],
        "onizleme_sha256": PREVIEW_SHA,
        "aday_sha256": CANDIDATE_SHA,
        "inceleme_sha256": digest(rapor_baytlari(kok, previews)),
        "dosyalar": [
            {"yol": rel, "yeni_sha256": digest(raw),
             "eski_tr_sha256": digest(read_safe(kok, dosya_yolu(kok, "tr", rel))),
             "eski_master_sha256": digest(read_safe(kok, dosya_yolu(kok, "master", rel)))}
            for rel, raw in sorted(previews.items())
        ],
        "kullanici_onay_sozu": ACK_REVIEW,
        "otomatik_onay": False,
    }


def onayla(kok: Path, summary: dict, previews: dict[str, bytes],
           input_fn=input) -> bool:
    inceleme = kok / REVIEW_REL
    kontrol(inceleme.is_file(), "Önce 316 hücrelik inceleme raporunu oluşturun")
    rapor = rapor_baytlari(kok, previews)
    kontrol(read_safe(kok, inceleme) == rapor, "İnceleme raporu güncel değil")
    print("İnceleme dosyası:", inceleme.relative_to(kok))
    print("316 değişikliğin anlamını ve etkilerini incelemeden onay vermeyin.")
    print("1 Dragon Alchemy hücresi işleme alınmayacak.")
    if input_fn("316 hücre için anlamsal onay (ONAYLIYORUM 316): ").strip() != ACK_REVIEW:
        print("[İPTAL] Kullanıcı onaylamadı.")
        return False
    veri = json_bayt(onay_kaydi(kok, summary, previews))
    yol = kok / APPROVAL_REL
    if yol.exists():
        kontrol(read_safe(kok, yol) == veri, "Mevcut onay sürümü farklı; müdahale yasak")
    else:
        with yol.open("xb") as f:
            f.write(veri)
            f.flush()
            os.fsync(f.fileno())
    print("[ONAY] İnceleme kimliğine bağlı 316 hücre onayı kaydedildi.")
    return True


def onay_dogrula(kok: Path, summary: dict, previews: dict[str, bytes]) -> dict:
    veri = onay_kaydi(kok, summary, previews)
    yol = kok / APPROVAL_REL
    kontrol(yol.is_file(), "316 hücre için ayrıca kullanıcı onayı gerekli")
    kontrol(read_safe(kok, yol) == json_bayt(veri),
            "Onay kaydı veya dosya kimlikleri değişmiş")
    return veri


def hedefleri_hazirla(kok: Path, previews: dict[str, bytes]) -> list[dict]:
    """Çift hedef: TR ve kaynak Master aynı doğrulanmış yeni baytlara terfi eder."""
    sonuc = []
    for rel, new in sorted(previews.items()):
        for lang in ("tr", "master"):
            path = dosya_yolu(kok, lang, rel)
            old = read_safe(kok, path)
            # Master/TR kodlamaları kaynakta ayrışmışsa otomatik çift yazma güvensizdir.
            kontrol(old == read_safe(kok, dosya_yolu(kok, "tr", rel)),
                    "Master/TR ham bayt uyumsuz; çift yazma durduruldu: " + rel)
            sonuc.append({
                "dil": lang, "dosya": rel,
                "eski_sha256": digest(old), "yeni_sha256": digest(new),
                "eski": old, "yeni": new, "hedef": path,
            })
    kontrol(len(sonuc) == 92, "46 dosya x 2 hedef beklendi")
    return sonuc


@contextmanager
def islem_kilidi(kok: Path):
    lock = kok / LOCK_REL
    kontrol(not lock.exists() and not lock.is_symlink(),
            "Önceki işlem kilidi mevcut; manuel inceleme gerekli")
    with lock.open("xb") as f:
        f.write(("PID: " + str(os.getpid()) + "\n").encode("ascii"))
        f.flush()
        os.fsync(f.fileno())
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


def islem_kaydet(yol: Path, journal: dict) -> None:
    kaydet_atomik(yol, json_bayt(journal))


def islem_klasoru(kok: Path) -> Path:
    base = kok / TRANSACTIONS_REL
    kontrol(not base.is_symlink(), "İşlem klasörü sembolik bağlantı")
    base.mkdir(parents=True, exist_ok=True)
    for folder in base.iterdir():
        if not folder.is_dir() or folder.is_symlink():
            raise RuntimeError("İşlem dizininde beklenmeyen kayıt")
        journal = folder / "islem.json"
        if not journal.exists():
            raise RuntimeError("Tamamlanmamış işlem klasörü mevcut: " + folder.name)
        durum = json.loads(read_safe(kok, journal).decode("utf-8")).get("durum")
        kontrol(durum in ("UYGULANDI", "GERI_ALINDI"),
                "Tamamlanmamış işlem var; yeni uygulama durduruldu: " + folder.name)
    ad = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    folder = base / (ad + "_" + uuid.uuid4().hex[:8])
    folder.mkdir(mode=0o700)
    return folder


def geri_yukle(kok: Path, folder: Path, journal: dict,
               *, force_expected: bool = True) -> None:
    """Yeni SHA veya eski SHA dışında bir dosya değiştirilmez."""
    kontrol(len(journal.get("dosyalar", [])) == 92,
            "Geri alma kayıt kapsamı beklenenden farklı")
    plan = []
    # Önce *bütün* hedefleri ve yedekleri kontrol et: sonradan yapılmış
    # kullanıcı düzenlemeleri kısmi geri alma ile ezilemez.
    for item in reversed(journal["dosyalar"]):
        path = dosya_yolu(kok, item["dil"], item["dosya"])
        backup = folder / "yedekler" / item["dil"] / relative_path(item["dosya"])
        original = read_safe(kok, backup, item["eski_sha256"])
        current = read_safe(kok, path)
        if force_expected:
            kontrol(digest(current) in (item["eski_sha256"], item["yeni_sha256"]),
                    "Geri alma sırasında beklenmeyen dosya değişikliği: " + str(path))
        plan.append((path, original))
    for path, original in plan:
        if read_safe(kok, path) != original:
            kaydet_atomik(path, original)
        kontrol(read_safe(kok, path) == original, "Geri alma doğrulaması başarısız")
    journal["durum"] = "GERI_ALINDI"
    islem_kaydet(folder / "islem.json", journal)


def uygula(kok: Path, summary: dict, previews: dict[str, bytes],
           input_fn=input, write_fn=kaydet_atomik) -> bool:
    depo_kontrol(kok)
    onay_dogrula(kok, summary, previews)
    hedefler = hedefleri_hazirla(kok, previews)
    print("Uygulama kapsamı: 316 hücre / 46 dosya / master + tr = 92 dosya")
    print("Bir Dragon Alchemy hücresi hariç; diğer dil klasörleri değiştirilmez.")
    if input_fn("Son canlı onay (UYGULA 316): ").strip() != ACK_APPLY:
        print("[İPTAL] Canlı uygulama yapılmadı.")
        return False
    with islem_kilidi(kok):
        # Son anda değişmiş dosya uygulamaya girmeyecek.
        for item in hedefler:
            kontrol(digest(read_safe(kok, item["hedef"])) == item["eski_sha256"],
                    "Uygulama başlamadan kaynak değişti: " + item["dosya"])
        folder = islem_klasoru(kok)
        journal = {
            "surum": 1, "durum": "HAZIRLANDI",
            "onay_sha256": digest(read_safe(kok, kok / APPROVAL_REL)),
            "onizleme_sha256": PREVIEW_SHA,
            "dosyalar": [
                {key: item[key] for key in ("dil", "dosya", "eski_sha256", "yeni_sha256")}
                for item in hedefler
            ],
        }
        backup_root = folder / "yedekler"
        backup_root.mkdir()
        # Bütün yedekler yazılıp hash doğrulanmadan canlı yazma başlamaz.
        for item in hedefler:
            backup = backup_root / item["dil"] / relative_path(item["dosya"])
            backup.parent.mkdir(parents=True, exist_ok=True)
            with backup.open("xb") as f:
                f.write(item["eski"])
                f.flush()
                os.fsync(f.fileno())
            kontrol(read_safe(kok, backup) == item["eski"], "Yedek doğrulanamadı")
        islem_kaydet(folder / "islem.json", journal)
        try:
            journal["durum"] = "UYGULANIYOR"
            islem_kaydet(folder / "islem.json", journal)
            for item in hedefler:
                write_fn(item["hedef"], item["yeni"])
                kontrol(digest(read_safe(kok, item["hedef"])) == item["yeni_sha256"],
                        "Canlı SHA uyuşmazlığı: " + item["dosya"])
            for item in hedefler:
                kontrol(read_safe(kok, item["hedef"]) == item["yeni"],
                        "Uygulama sonrası uyuşmazlık")
            kontrol(all(
                read_safe(kok, dosya_yolu(kok, "master", rel))
                == read_safe(kok, dosya_yolu(kok, "tr", rel))
                for rel in previews
            ), "Master/TR senkronluğu bozuldu")
            journal["durum"] = "UYGULANDI"
            islem_kaydet(folder / "islem.json", journal)
            print("[BAŞARILI] Master ve TR dosyaları uygulandı.")
            print("Yedek/journal:", folder.relative_to(kok))
            print("Client ve Binary değiştirilmedi; runtime testleri ayrıca yapılmalı.")
            return True
        except BaseException:
            try:
                geri_yukle(kok, folder, journal)
                print("[GERİ ALINDI] Hata nedeniyle tüm hedefler eski sürüme döndü.")
            except Exception as rollback_error:
                journal["durum"] = "KURTARMA_GEREKIYOR"
                islem_kaydet(folder / "islem.json", journal)
                print("[KRİTİK] Otomatik geri alma başarısız:", rollback_error)
                print("İşlem klasörü:", folder.relative_to(kok))
            raise


def son_islem(kok: Path) -> Path:
    base = kok / TRANSACTIONS_REL
    kontrol(base.is_dir() and not base.is_symlink(), "Henüz işlem kaydı yok")
    folders = sorted((p for p in base.iterdir() if p.is_dir()), reverse=True)
    kontrol(bool(folders), "Geri alınacak işlem yok")
    return folders[0]


def geri_al(kok: Path, input_fn=input) -> bool:
    depo_kontrol(kok)
    folder = son_islem(kok)
    journal_path = folder / "islem.json"
    journal = json.loads(read_safe(kok, journal_path).decode("utf-8"))
    kontrol(journal["durum"] in ("UYGULANDI", "KURTARMA_GEREKIYOR", "UYGULANIYOR"),
            "Bu işlem geri alınabilir durumda değil")
    kontrol(journal.get("onizleme_sha256") == PREVIEW_SHA
            and len(journal.get("dosyalar", [])) == 92,
            "İşlem kaydı beklenen kapsamda değil")
    print("Geri alınacak işlem:", folder.name)
    if input_fn("Geri alma onayı (GERI AL): ").strip() != ACK_ROLLBACK:
        print("[İPTAL] Geri alma yapılmadı.")
        return False
    with islem_kilidi(kok):
        geri_yukle(kok, folder, journal)
    print("[TAMAM] 46 master + 46 TR dosyası yedekten geri alındı.")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="316 hücre için güvenli canlı uygulama motoru")
    ap.add_argument("islem", choices=("incele", "onayla", "uygula", "geri-al"))
    ap.add_argument("--root", default=".")
    args = ap.parse_args()
    kok = Path(args.root).resolve()
    try:
        depo_kontrol(kok)
        if args.islem == "geri-al":
            return 0 if geri_al(kok) else 1
        summary, previews = validate(kok)
        if args.islem == "incele":
            output = inceleme_olustur(kok, summary, previews)
            print("[HAZIR] 316 hücrenin önce/sonra inceleme listesi:", output.relative_to(kok))
            print("Onaysız 1 hücre hariç tutulmuştur.")
            return 0
        if args.islem == "onayla":
            return 0 if onayla(kok, summary, previews) else 1
        if args.islem == "uygula":
            return 0 if uygula(kok, summary, previews) else 1
    except (RuntimeError, OSError, KeyError, ValueError, UnicodeError) as exc:
        print("[ENGEL]", exc)
        print("Kontrol kayıtları ve mevcut dosyalarınızı inceleyin.")
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
