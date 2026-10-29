#!/usr/bin/env python3
"""LocaleGuard: 3 seçenekli, onay isteyen Türkçe yönetim menüsü (V6)."""
from __future__ import annotations

import ast
import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

from tools.locale_guard.locale_guard import load_config

HEDEF_DAL = "automation/locale-unified-master-first-20261029"
HEDEF_DEPO = "sbraslan/Project_Locale"
GITHUB_ADRESLERI = {
    "https://github.com/" + HEDEF_DEPO,
    "https://github.com/" + HEDEF_DEPO + ".git",
    "git@github.com:" + HEDEF_DEPO + ".git",
    "ssh://git@github.com/" + HEDEF_DEPO + ".git",
}
KURULACAKLAR = (
    "LocaleGuard_Turkce_V6.bat",
    "tools/locale_guard/turkce_menu_v6.py",
    "tools/locale_guard/tests/test_turkce_menu_v6.py",
    "tools/locale_guard/incremental_scan_v1.py",
    "tools/locale_guard/tests/test_incremental_scan_v1.py",
    "tools/locale_guard/translation_queue_preview_v1.py",
    "tools/locale_guard/tests/test_translation_queue_preview_v1.py",
    "tools/locale_guard/master_tr_live_apply_v1.py",
    "tools/locale_guard/tests/test_master_tr_live_apply_v1.py",
    "tools/locale_guard/runtime_readiness_v1.py",
    "tools/locale_guard/unified_translation_contract_v1.py",
    "tools/locale_guard/unified_master_first_plan_v1.py",
    "tools/locale_guard/unified_master_tr_stage_v1.py",
    "tools/locale_guard/unified_targets_stage_v1.py",
    "tools/locale_guard/tests/test_unified_translation_contract_v1.py",
    "tools/locale_guard/tests/test_unified_master_first_plan_v1.py",
    "tools/locale_guard/tests/test_unified_master_tr_stage_v1.py",
    "tools/locale_guard/tests/test_unified_targets_stage_v1.py",
)
DURUM_ADLARI = {
    "BASELINE_REQUIRED": "Onaylı başlangıç referansı bulunmayan dosyalar",
    "EXTRA_FILE_REVIEW": "Hedefteki fazla dosyalar",
    "EXTRA_KEY_REVIEW": "Hedefteki fazla anahtarlar",
    "FILE_ENCODING_REVIEW": "Kodlama incelemesi gereken dosyalar",
    "FREEFORM_OR_UNREGISTERED_REVIEW": "Özel biçimli ve henüz tanımlanmamış dosyalar",
    "MISSING_FILE_TRANSLATION": "Eksik hedef dosyalar",
    "MISSING_KEY_TRANSLATION": "Eksik çeviri anahtarları",
    "SCHEMA_TRANSLATION_DISABLED": "Çevirisi özellikle kapalı yapısal dosyalar",
    "SPECIAL_FILE_PRESERVED": "Baytları korunan özel dosyalar",
    "SOURCE_CHANGED_RETRANSLATION": "Master değişikliği nedeniyle yeniden çevrilecekler",
    "PARSER_SHAPE_REVIEW": "Dosya şeması incelemesi gerekenler",
}
# Yerel çalışmada daha önce doğrulanmış özgün Git blob kimlikleri.
# Bunun dışındaki değişmiş dosyalar otomatik kurulumla ezilmez.
BILINEN_ESKI_BLOBLAR = {
    "tools/locale_guard/turkce_menu_v6.py": {
        "1737f2763a5e26c67065b0ef93e730b8cbf33b2c",
    },
    "tools/locale_guard/tests/test_turkce_menu_v6.py": {
        "dfe9d1c1c574ef3d79dcbcd984d7adf9931db951",
    },
    "tools/locale_guard/incremental_scan_v1.py": {
        "570b7c27df40fda4fb9f2fa296fc427487362a37",
        "237848db59cb3c3375e3d0756df3d63d9d632377",
    },
    "tools/locale_guard/tests/test_incremental_scan_v1.py": {
        "35960513dc3ad1f1ca768f5d757aec4423854eb1",
        "9f8eda6a69f1a051ec2e14a3b0043730fb5d985e",
    },
}

KAYIT_ACIKLAMALARI = {
    "test": "Tüm birim ve regresyon testleri",
    "audit": "Ana güvenlik ve yapısal bütünlük denetimi",
    "preview": "316 hücrelik Türkçe önizleme doğrulaması",
    "stage": "Eski 262 hücrelik staging doğrulaması",
    "diff": "Eski staging sürümleri arasındaki fark denetimi",
    "english": "İngilizce kanonik kapsam denemesi",
    "scan": "16 dilde artımlı değişiklik taraması",
    "queue": "Eksik çeviriler için güvenli iş kuyruğu",
    "runtime": "Uygulama sonrası Türkçe runtime SHA-256 denetimi",
    "unified": "200 Master TXT ve 16 dil Master-öncelikli güvenli Express taraması",
    "master_stage": "Master EN/DE metinleri için Türkçe staging provası",
    "checkpoint": "316 hücrelik kontrol noktası",
    "rehearse": "316 hücrelik izole uygulama provası",
}


def satir():
    print("=" * 65)


def onay_iste(islem: str, parola: str = "EVET", cevap=None) -> bool:
    """Kullanıcı izin vermedikçe işlem çağrılmaz."""
    print("\nYapılacak işlem:", islem)
    print("Devam etmek için", repr(parola), "yazın. Vazgeçmek için Enter'a basın.")
    try:
        giris = (cevap or input)("Onay: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nİşlem iptal edildi.")
        return False
    if giris != parola:
        print("İşlem iptal edildi; herhangi bir komut başlatılmadı.")
        return False
    return True


def secim_al(gecerli: set[str]) -> str:
    try:
        secim = input("\nSeçiminiz: ").strip().upper()
    except (EOFError, KeyboardInterrupt):
        return "0"
    if secim not in gecerli:
        print("Geçersiz seçim. Gösterilen seçeneklerden birini kullanın.")
        return ""
    return secim


def guvenli_yol(kok: Path, goreli: str) -> Path:
    saf = PurePosixPath(goreli)
    if (not goreli or saf.is_absolute() or saf.as_posix() != goreli
            or ".." in saf.parts or "\\" in goreli or ":" in goreli):
        raise RuntimeError("Güvensiz göreli yol reddedildi.")
    hedef = kok / Path(*saf.parts)
    for yol in (hedef, *hedef.parents):
        if yol == kok.parent:
            break
        if yol.is_symlink():
            raise RuntimeError("Sembolik bağlantılı dosya veya klasör reddedildi: " + goreli)
    if not hedef.resolve().is_relative_to(kok.resolve()):
        raise RuntimeError("Proje dizini dışındaki yol reddedildi.")
    return hedef


def komut(kok: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    ortam = dict(os.environ)
    ortam["PYTHONIOENCODING"] = "utf-8"
    ortam["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        args, cwd=kok, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False, env=ortam,
    )


def gunluk_yaz(kok: Path, kimlik: str, args: list[str], sonuc: subprocess.CompletedProcess[str]) -> Path:
    klasor = guvenli_yol(kok, ".locale_guard/menu_logs")
    if klasor.exists() and not klasor.is_dir():
        raise RuntimeError("Günlük klasörü geçersiz.")
    klasor.mkdir(parents=True, exist_ok=True)
    tarih = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    hedef = guvenli_yol(kok, ".locale_guard/menu_logs/" + tarih + "_" + kimlik + ".txt")
    yazilacak = (
        "Komut: " + " ".join(args) + "\n"
        + "Çıkış kodu: " + str(sonuc.returncode) + "\n\n"
        + "Ham teknik çıktı:\n" + sonuc.stdout + "\n" + sonuc.stderr
    )
    with hedef.open("x", encoding="utf-8", newline="\n") as f:
        f.write(yazilacak)
    return hedef.relative_to(kok)


def calistir(kok: Path, kimlik: str, args: list[str]) -> bool:
    print("\nİşleniyor:", KAYIT_ACIKLAMALARI.get(kimlik, kimlik))
    try:
        sonuc = komut(kok, args)
        gunluk = gunluk_yaz(kok, kimlik, args, sonuc)
        if sonuc.returncode == 0:
            print("[BAŞARILI]", KAYIT_ACIKLAMALARI.get(kimlik, kimlik))
        else:
            print("[ENGEL]", KAYIT_ACIKLAMALARI.get(kimlik, kimlik))
            print("Teknik hata ayrıntıları günlük dosyasında. Diğer dosyalara geçilebilir.")
        print("Günlük:", gunluk)
        return sonuc.returncode == 0
    except (OSError, RuntimeError) as hata:
        print("[ENGEL] İşlem güvenli biçimde durduruldu:", hata)
        return False


def modulu_calistir(kok: Path, kimlik: str, modul: str, *args: str) -> bool:
    return calistir(kok, kimlik, [sys.executable, "-B", "-m", modul, *args])


def rapor_damgasi(kok: Path, goreli: str) -> int | None:
    p = guvenli_yol(kok, goreli)
    return p.stat().st_mtime_ns if p.is_file() else None


def guncel_rapor_ozeti(kok: Path, goreli: str, once: int | None,
                      alan: str = "counts") -> None:
    sonra = rapor_damgasi(kok, goreli)
    if sonra is None or sonra == once:
        print("[BİLGİ] Bu işlemde yeni rapor üretilmedi; eski rapor gösterilmiyor.")
        return
    rapor_ozeti(kok, goreli, alan)


def rapor_ozeti(kok: Path, goreli: str, alan: str = "counts") -> None:
    try:
        path = guvenli_yol(kok, goreli)
        rapor = json.loads(path.read_text(encoding="utf-8-sig"))
        print("\nRapor özeti:", goreli)
        for kod, sayi in sorted(rapor.get(alan, {}).items()):
            print(" -", DURUM_ADLARI.get(kod, "Diğer inceleme kaydı"), ":", sayi)
        hatalar = [
            item for item in rapor.get("items", [])
            if item.get("status") == "FILE_ENCODING_REVIEW"
        ]
        for item in hatalar[:5]:
            print(" - Kodlama incelemesi:", item.get("language", "?")
                  + "/" + item.get("path", "?"))
        for key, turkce in (
            ("missing_key_rows", "Eksik anahtar satırı"),
            ("translation_cells", "Çeviri adayı hücre"),
            ("held_cells_or_rows", "Manuel inceleme bekleyenler"),
            ("encoding_or_parser_blockers", "Kodlama veya şema engelleri"),
        ):
            if key in rapor:
                print(" -", turkce + ":", rapor[key])
    except (OSError, ValueError, TypeError) as exc:
        print("Rapor özeti okunamadı; ayrıntı için günlük dosyasına bakın.")


def gerekli_dosya(kok: Path, goreli: str) -> bool:
    return guvenli_yol(kok, goreli).is_file()


def tam_kontrol(kok: Path) -> None:
    if not onay_iste("Tüm güvenlik testleri, önizleme ve 16 dil taraması çalıştırılacak."):
        return
    satir()
    print("TÜM GÜVENLİK KONTROLLERİ")
    satir()
    sonuc = []
    sonuc.append(("Tüm testler", modulu_calistir(
        kok, "test", "unittest", "discover", "-s", "tools/locale_guard/tests", "-q")))
    sonuc.append(("Genel denetim", modulu_calistir(
        kok, "audit", "tools.locale_guard.locale_guard", "audit", "--root", ".")))
    # Canlı uygulama sonrasında eski dry-run kontrolü doğal olarak başarısızdır.
    # Uygulanmış 92 dosya yerine runtime SHA denetimi yapılmalıdır.
    uygulanmis = False
    try:
        from tools.locale_guard.master_tr_live_apply_v1 import son_islem
        folder = son_islem(kok)
        kayit = json.loads((folder / "islem.json").read_text(encoding="utf-8"))
        uygulanmis = kayit.get("durum") == "UYGULANDI"
    except (RuntimeError, OSError, ValueError, ImportError):
        pass
    if uygulanmis:
        sonuc.append(("316 hücrenin canlı SHA kontrolü", modulu_calistir(
            kok, "runtime", "tools.locale_guard.runtime_readiness_v1", "--root", ".")))
    else:
        sonuc.append(("316 hücre", modulu_calistir(
            kok, "preview", "tools.locale_guard.master_tr_rehearsal",
            "status", "--root", ".")))
    eski = [
        ("Staging V2", "tools/locale_guard/master_tr_stage_validate_v2.py",
         "tools.locale_guard.master_tr_stage_validate_v2", "stage"),
        ("Staging farkları", "tools/locale_guard/master_tr_stage_diff_audit_v1.py",
         "tools.locale_guard.master_tr_stage_diff_audit_v1", "diff"),
        ("İngilizce kanonik kontrol", "tools/locale_guard/canonical_backfill_stage1_dry_run.py",
         "tools.locale_guard.canonical_backfill_stage1_dry_run", "english"),
    ]
    for ad, dosya, modul, kimlik in eski:
        if gerekli_dosya(kok, dosya):
            args = ("--report", ".locale_guard/menu_english_stage1_v6.json",
                    "--diff", ".locale_guard/menu_english_stage1_v6.diff") if kimlik == "english" else ()
            sonuc.append((ad, modulu_calistir(kok, kimlik, modul, *args)))
        else:
            print("[EKSİK] Kontrol modülü yerelde bulunamadı:", ad)
            sonuc.append((ad, False))
    rapor = ".locale_guard/incremental_scan_v1.json"
    once = rapor_damgasi(kok, rapor)
    tarama = modulu_calistir(
        kok, "scan", "tools.locale_guard.incremental_scan_v1", "--root", ".")
    sonuc.append(("16 dil taraması", tarama))
    guncel_rapor_ozeti(kok, rapor, once)
    satir()
    print("KONTROL SONUCU")
    for ad, iyi in sonuc:
        print(" -", ad + ":", "BAŞARILI" if iyi else "İNCELEME GEREKİYOR")
    if all(iyi for _, iyi in sonuc):
        print("Tüm otomatik kontroller başarılı; bu, çevirilerin anlamsal onayı değildir.")
    else:
        print("En az bir kontrol tamamlanmadı. Canlı uygulama KİLİTLİ.")


def canli_modul(kok: Path, islem: str) -> bool:
    """İnsan onayı konsoldan alınır; stdout gizlenmez, komut otomatik çalışmaz."""
    args = [
        sys.executable, "-B", "-m",
        "tools.locale_guard.master_tr_live_apply_v1",
        islem, "--root", ".",
    ]
    ortam = dict(os.environ)
    ortam["PYTHONIOENCODING"] = "utf-8"
    ortam["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        result = subprocess.run(args, cwd=kok, env=ortam, check=False)
        if result.returncode != 0:
            print("[ENGEL] İşlem tamamlanmadı. Dosya değiştirilmişse journal kontrol edin.")
        else:
            print("[TAMAM] İşlem başarıyla tamamlandı.")
        return result.returncode == 0
    except OSError as hata:
        print("[ENGEL] Türkçe uygulama modülü başlatılamadı:", hata)
        return False


def islem_menusu(kok: Path) -> None:
    while True:
        satir()
        print("2 - DİL İŞLEMLERİ VE UYGULAMA")
        satir()
        print("[1] 16 dilde fark taraması ve çeviri kuyruğunu hazırla")
        print("[2] Güvenlik testleri ve 316 hücreyi izole prova et")
        print("[3] 316 hücreyi önce/sonra raporunda incele")
        print("[4] İncelenen 316 hücreye anlamsal onay ver")
        print("[5] Eski 316 canlı uygulama (yeni Master-first akışında KİLİTLİ)")
        print("[6] Eski 316 paketinin runtime SHA denetimi (tüm sistem DEĞİL)")
        print("[7] Son eski 316 uygulamasını yedekten geri al")
        print("[8] Master-first EXPRESS: Tüm TXT ve 16 dil farklarını tara")
        print("[9] Master yabancı metinlerini güvenli staging'e çevir (pilot)")
        print("[G] Geri   [M] Ana menü   [0] Çıkış")
        secim = secim_al(set("123456789GM0"))
        if secim in ("G", "M"):
            return
        if secim == "0":
            raise SystemExit(0)
        if secim == "1":
            if onay_iste("16 dil taraması yapılacak ve ayrı çeviri kuyruğu hazırlanacak."):
                report = ".locale_guard/incremental_scan_v1.json"
                once = rapor_damgasi(kok, report)
                modulu_calistir(kok, "scan",
                    "tools.locale_guard.incremental_scan_v1", "--root", ".")
                guncel_rapor_ozeti(kok, report, once)
                tarih = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
                kuyruk = ".locale_guard/translation_queue_" + tarih + ".json"
                modulu_calistir(kok, "queue",
                    "tools.locale_guard.translation_queue_preview_v1",
                    "--root", ".", "--output", kuyruk)
                if gerekli_dosya(kok, kuyruk):
                    rapor_ozeti(kok, kuyruk, "scan_counts")
        elif secim == "2":
            if onay_iste("Bütün birim testleri ve 316 hücrelik izole prova yapılacak."):
                if modulu_calistir(kok, "test", "unittest", "discover",
                                  "-s", "tools/locale_guard/tests", "-q"):
                    if modulu_calistir(kok, "preview",
                                      "tools.locale_guard.master_tr_rehearsal",
                                      "status", "--root", "."):
                        if modulu_calistir(kok, "rehearse",
                                          "tools.locale_guard.master_tr_rehearsal",
                                          "rehearse", "--root", "."):
                            modulu_calistir(kok, "checkpoint",
                                           "tools.locale_guard.master_tr_rehearsal",
                                           "checkpoint", "--root", ".")
        elif secim == "3":
            if onay_iste("316 hücrelik önce/sonra inceleme raporu oluşturulacak."):
                canli_modul(kok, "incele")
                print("Dosyayı açıp 316 metin değişikliğini inceleyin.")
        elif secim == "4":
            if onay_iste("İnceleme raporuna bağlı anlamsal kullanıcı onayı istenecek."):
                canli_modul(kok, "onayla")
        elif secim == "5":
            print("[KİLİTLİ] Eski 316 hücrelik paket, bütün Master'ın Türkçe")
            print("tamamlandığı yeni sistemde canlı uygulanmayacak.")
            print("Bu seçim hiçbir dosyaya yazmaz.")
        elif secim == "6":
            if onay_iste("Uygulanan 92 dosyanın SHA-256 kimlikleri denetlenecek."):
                modulu_calistir(kok, "runtime",
                               "tools.locale_guard.runtime_readiness_v1",
                               "--root", ".")
        elif secim == "7":
            if onay_iste("Son işlem yedekten geri alınabilir; ayrıca GERI AL onayı istenecek."):
                canli_modul(kok, "geri-al")
        elif secim == "8":
            if onay_iste("Master ve 16 dilde bütün TXT dosyaları salt-okunur taranacak."):
                modulu_calistir(kok, "unified",
                               "tools.locale_guard.unified_master_first_plan_v1",
                               "--root", ".")
        elif secim == "9":
            if onay_iste("Master EN/DE metinleri yalnız staging alanına çevrilecek."):
                print("Bu işlem yalnız yerel LibreTranslate servisini kullanır.")
                modulu_calistir(kok, "master_stage",
                               "tools.locale_guard.unified_master_tr_stage_v1",
                               "--root", ".", "--service", "local", "--limit", "50")


def git_kontrol(kok: Path) -> None:
    print("\nGitHub kaynak ve dal bilgileri:")
    for ad, args in (
        ("Git depo konumu", ["git", "rev-parse", "--show-toplevel"]),
        ("Çalışılan dal", ["git", "branch", "--show-current"]),
        ("GitHub uzak adresi", ["git", "remote", "get-url", "origin"]),
        ("Yerel değişiklik sayısı", ["git", "status", "--porcelain", "--untracked-files=all"]),
    ):
        sonuc = komut(kok, args)
        if sonuc.returncode != 0:
            print("[ENGEL]", ad, "alınamadı.")
        elif ad == "Yerel değişiklik sayısı":
            print(ad + ":", len(sonuc.stdout.splitlines()))
        else:
            print(ad + ":", sonuc.stdout.strip())


def git_depo_dogrula(kok: Path) -> None:
    p = komut(kok, ["git", "remote", "get-url", "origin"])
    if p.returncode != 0 or p.stdout.strip() not in GITHUB_ADRESLERI:
        raise RuntimeError("GitHub uzak depo adresi beklenen projeyle eşleşmiyor.")
    p = komut(kok, ["git", "rev-parse", "--show-toplevel"])
    if p.returncode != 0 or Path(p.stdout.strip()).resolve() != kok.resolve():
        raise RuntimeError("Git depo kökü Project_Locale klasörüyle eşleşmiyor.")


def git_getir(kok: Path) -> bool:
    try:
        git_depo_dogrula(kok)
    except RuntimeError as exc:
        print("[ENGEL]", exc)
        return False
    return calistir(kok, "GitHub güncellemelerini indirme",
                    ["git", "fetch", "--no-tags", "origin", HEDEF_DAL])


def kurulacak_plan(kok: Path) -> list[tuple[Path, bytes, bytes | None]]:
    """Git'teki kilitli dalın allowlist dosyalarını karşılaştır; yazma yapma."""
    git_depo_dogrula(kok)
    plan = []
    for goreli in KURULACAKLAR:
        hedef = guvenli_yol(kok, goreli)
        raw = subprocess.run(
            ["git", "show", "refs/remotes/origin/" + HEDEF_DAL + ":" + goreli],
            cwd=kok, capture_output=True, check=False,
        )
        if raw.returncode != 0:
            raise RuntimeError("GitHub dalında kurulum dosyası yok: " + goreli)
        veri = raw.stdout
        if goreli.endswith(".py"):
            try:
                ast.parse(veri.decode("utf-8-sig"), filename=goreli)
            except (SyntaxError, UnicodeError) as exc:
                raise RuntimeError("Python kaynak kodu geçersiz: " + goreli) from exc
        elif goreli.endswith(".bat"):
            metin = veri.decode("utf-8-sig")
            if "@echo off" not in metin or "turkce_menu_v6" not in metin:
                raise RuntimeError("Başlatıcı içerik denetimi başarısız.")
            veri = veri.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        if not hedef.parent.is_dir():
            raise RuntimeError("Kurulum üst klasörü yok: " + goreli)
        eski = hedef.read_bytes() if hedef.exists() else None
        if eski != veri:
            if eski is not None:
                # Bilinen eski sürümler dışında el ile değiştirilmiş dosyalar korunur.
                import hashlib
                ham_kimlik = hashlib.sha1(
                    b"blob " + str(len(eski)).encode("ascii") + b"\x00" + eski
                ).hexdigest()
                if ham_kimlik not in BILINEN_ESKI_BLOBLAR.get(goreli, set()):
                    raise RuntimeError(
                        "Yerel dosya bilinmeyen içerikte; üstüne yazılmaz: " + goreli
                    )
            plan.append((hedef, veri, eski))
    return plan


def kurulum_yap(kok: Path) -> None:
    try:
        plan = kurulacak_plan(kok)
        if not plan:
            print("[TAMAM] Menü ve izinli modüller zaten eşit.")
            return
        satir()
        print("Güncellenecek izinli dosyalar:")
        for yol, _, eski in plan:
            print(" -", yol.relative_to(kok), "(yeni)" if eski is None else "(yedekli güncelleme)")
        print("Master ve dil klasörleri kapsam dışında. Git sıfırlama/silme yapılmaz.")
        if not onay_iste("Yukarıdaki dosyaları yedekleyerek kur.", "KUR"):
            return
        yedek_root = guvenli_yol(
            kok, ".locale_guard/kurulum_yedekleri/"
            + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%S_%f"))
        if yedek_root.exists() or yedek_root.is_symlink():
            raise RuntimeError("Yedek klasörü önceden mevcut.")
        # Yazmadan önce bütün dosyaları hazırlayıp hedef değişikliklerini doğrula.
        for hedef, _, eski in plan:
            if eski is not None and hedef.read_bytes() != eski:
                raise RuntimeError("Dosya kurulum sırasında değişti: " + str(hedef))
        yedek_root.mkdir(parents=True)
        for hedef, _, eski in plan:
            if eski is not None:
                yedek = yedek_root / hedef.relative_to(kok)
                yedek.parent.mkdir(parents=True, exist_ok=True)
                with yedek.open("xb") as f:
                    f.write(eski)
        uygulanmis = []
        try:
            for hedef, yeni, eski in plan:
                fd, gecici = tempfile.mkstemp(prefix=".locale_", dir=hedef.parent)
                try:
                    with os.fdopen(fd, "wb") as f:
                        f.write(yeni)
                        f.flush()
                        os.fsync(f.fileno())
                    os.replace(gecici, hedef)
                finally:
                    if os.path.exists(gecici):
                        os.unlink(gecici)
                uygulanmis.append((hedef, eski))
                if hedef.read_bytes() != yeni:
                    raise RuntimeError("Kurulum sonrası dosya doğrulaması başarısız.")
        except BaseException:
            for hedef, eski in reversed(uygulanmis):
                if eski is None:
                    hedef.unlink(missing_ok=True)
                else:
                    hedef.write_bytes(eski)
            raise
        print("[BAŞARILI] İzinli kod dosyaları kuruldu; yedek:", yedek_root.relative_to(kok))
        print("Güncel menüyü yeniden açmanız önerilir.")
    except (RuntimeError, OSError, ValueError) as exc:
        print("[ENGEL] Güvenli kurulum yapılamadı:", exc)


def git_esitle(kok: Path) -> None:
    try:
        git_depo_dogrula(kok)
        dal = komut(kok, ["git", "branch", "--show-current"])
        if dal.returncode != 0 or dal.stdout.strip() != HEDEF_DAL:
            raise RuntimeError("Hızlı birleştirme yalnız proje geliştirme dalında yapılabilir.")
        durum = komut(kok, ["git", "status", "--porcelain", "--untracked-files=all"])
        if durum.returncode != 0 or durum.stdout.strip():
            raise RuntimeError("Yerel dosya değişiklikleri var; güvenli birleştirme engellendi.")
        ancestor = komut(kok, ["git", "merge-base", "--is-ancestor", "HEAD",
                               "refs/remotes/origin/" + HEDEF_DAL])
        if ancestor.returncode != 0:
            raise RuntimeError(
                "GitHub dalı yerel dalın ilerisi değil; güvenli birleştirme reddedildi."
            )
        fark = komut(kok, ["git", "diff", "--name-only", "HEAD",
                           "refs/remotes/origin/" + HEDEF_DAL])
        if fark.returncode != 0:
            raise RuntimeError("GitHub fark listesi alınamadı.")
        config = load_config(kok / "tools/locale_guard/config.json")
        korunacak = set(config["languages"]) | {config["master_directory"]}
        riskli = [a for a in fark.stdout.splitlines()
                  if a.split("/", 1)[0] in korunacak]
        if riskli:
            raise RuntimeError("GitHub farkı dil/master dosyaları içeriyor; otomatik eşitleme kapalı.")
        if not fark.stdout.strip():
            print("[TAMAM] Yerel dal GitHub ile eşit.")
            return
        print("Eşitlenecek kod dosyası:", len(fark.stdout.splitlines()))
        if not onay_iste("Yerel dalı yalnız ileri sararak eşitle.", "ESITLE"):
            return
        calistir(kok, "GitHub dalını güvenli ileri sarma",
                 ["git", "merge", "--ff-only", "refs/remotes/origin/" + HEDEF_DAL])
    except (RuntimeError, OSError, ValueError) as exc:
        print("[ENGEL] Eşitleme iptal edildi:", exc)


def github_menusu(kok: Path) -> None:
    while True:
        satir()
        print("3 - GİTHUB KURULUM VE EŞİTLEME")
        satir()
        print("[1] GitHub bağlantısını ve yerel dalı incele")
        print("[2] GitHub'dan güncellemeleri indir (dosyalara uygulama yok)")
        print("[3] Yalnız izinli menü/modülleri kur veya güncelle")
        print("[4] Temiz çalışma dalını güvenli şekilde eşitle")
        print("[G] Geri   [M] Ana menü   [0] Çıkış")
        secim = secim_al(set("1234GM0"))
        if secim in ("G", "M"):
            return
        if secim == "0":
            raise SystemExit(0)
        if secim == "1":
            if onay_iste("Git deposunun bağlantısı ve yerel durumu okunacak."):
                git_kontrol(kok)
        elif secim == "2":
            if onay_iste("GitHub'daki hedef daldan güncellemeler indirilecek."):
                git_getir(kok)
        elif secim == "3":
            if onay_iste("İndirilmiş GitHub kodlarıyla kurulum farkları okunacak."):
                kurulum_yap(kok)
        elif secim == "4":
            if onay_iste("GitHub ve çalışma dalı güvenli eşitleme için incelenecek."):
                git_esitle(kok)


def ana_menu(kok: Path) -> None:
    while True:
        print()
        satir()
        print("PROJECT_LOCALE - TÜRKÇE MERKEZİ YÖNETİM V6")
        satir()
        print("[1] KONTROL  - Tüm güvenlik ve bütünlük kontrolleri")
        print("[2] İŞLEM   - Tarama, çeviri kuyruğu, kontrol noktası, prova")
        print("[3] GİTHUB  - Kurulum, güncelleme ve güvenli eşitleme")
        print("[0] ÇIKIŞ")
        print("Master-first Türkçe temizlik ve 16 dil staging çalışmaları devam ediyor.")
        print("Eski 316 paketin canlı uygulaması güvenlik nedeniyle kapalı.")
        secim = secim_al(set("1230"))
        if secim == "0":
            print("Program güvenli şekilde kapatıldı.")
            return
        if secim == "1":
            tam_kontrol(kok)
        elif secim == "2":
            islem_menusu(kok)
        elif secim == "3":
            github_menusu(kok)


def main() -> int:
    try:
        kok = Path(__file__).resolve().parents[2]
        if kok.name != "Project_Locale":
            raise RuntimeError("Menü Project_Locale dizininden açılmalıdır.")
        if not (kok / "tools/locale_guard/config.json").is_file():
            raise RuntimeError("Locale Guard yapılandırması bulunamadı.")
        ana_menu(kok)
        return 0
    except (RuntimeError, OSError, ValueError) as hata:
        print("[ENGEL] Menü güvenli şekilde durduruldu:", hata)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
