#!/usr/bin/env python3
"""16 dili tarayan, yalnız ONAYLI Türkçe paketi canlıya alabilen Express V1.

ÖNEMLİ: Eksik çevirileri üretmez, 15 dili hazırmış gibi göstermez.
Tek giriş komutu, ancak canlı yazma için ayrı insan onayları gerekir.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

from tools.locale_guard.incremental_scan_v1 import collect
from tools.locale_guard.locale_guard import load_config
from tools.locale_guard.translation_queue_preview_v1 import build_queue
from tools.locale_guard.master_tr_rehearsal import validate, rehearse, create_checkpoint
from tools.locale_guard.master_tr_live_apply_v1 import (
    APPROVAL_REL, depo_kontrol, geri_yukle, inceleme_olustur,
    islem_kilidi, onay_dogrula, onayla, son_islem, uygula,
)
from tools.locale_guard.runtime_readiness_v1 import runtime_kontrol

FULL_LIMITS = (
    "FILE_ENCODING_REVIEW", "MISSING_KEY_TRANSLATION",
    "MISSING_FILE_TRANSLATION", "EXTRA_KEY_REVIEW", "EXTRA_FILE_REVIEW",
    "BASELINE_REQUIRED", "FREEFORM_OR_UNREGISTERED_REVIEW",
    "PARSER_SHAPE_REVIEW", "SOURCE_CHANGED_RETRANSLATION",
)
STEPS = (
    "Birim testleri",
    "16 dilde eksik/fazla/değişen taraması",
    "Çeviri kuyruğu ve engel raporu",
    "Master/TR teknik doğrulaması",
    "316 hücrelik izole uygulama provası",
    "316 hücrenin insan tarafından incelenmesi",
    "Yedekli onaylı uygulama",
    "TR runtime bütünlük doğrulaması",
    "16 dilin uygulama sonrası durumu",
)


def sor(metin: str, beklenen: str, input_fn=input) -> bool:
    print("\n" + metin)
    print("Devam etmek için", repr(beklenen), "yazın; vazgeçmek için Enter.")
    try:
        value = input_fn("Onay: ").strip()
    except (KeyboardInterrupt, EOFError):
        print("[İPTAL] Kullanıcı işlemi iptal etti.")
        return False
    if value != beklenen:
        print("[İPTAL] Tam onay sözcüğü girilmedi.")
        return False
    return True


def run_tests(root: Path) -> bool:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(
        [sys.executable, "-B", "-m", "unittest", "discover",
         "-s", "tools/locale_guard/tests", "-q"],
        cwd=root, capture_output=True, check=False,
        encoding="utf-8", errors="replace", env=env,
    )
    print("Birim testleri:", "BAŞARILI" if proc.returncode == 0 else "BAŞARISIZ")
    if proc.returncode:
        print(proc.stdout[-4000:])
        print(proc.stderr[-4000:])
    else:
        lines = (proc.stderr or proc.stdout).splitlines()
        for line in lines:
            if line.strip().startswith(("Ran ", "OK", "FAILED")):
                print(" -", line.strip().replace("Ran ", "Çalıştırılan test: "))
    return proc.returncode == 0


def root_check(root: Path) -> None:
    depo_kontrol(root)
    for name in ("master", "tr", "tools/locale_guard/tests"):
        path = root / name
        if not path.is_dir() or path.is_symlink():
            raise RuntimeError("Gerekli klasör geçersiz: " + name)


def save_json(path: Path, data: dict) -> None:
    if path.is_symlink():
        raise RuntimeError("Express raporu sembolik bağlantılı yola yazılmaz")
    raw = (json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    with path.open("xb") as file:
        file.write(raw)
        file.flush()
        os.fsync(file.fileno())


def make_run(root: Path) -> Path:
    guard = root / ".locale_guard"
    base = guard / "express_islemler"
    if base.is_symlink():
        raise RuntimeError("Express günlük klasörü sembolik bağlantılı")
    base.mkdir(parents=True, exist_ok=True)
    key = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%S_%f") + "_" + uuid.uuid4().hex[:8]
    result = base / key
    result.mkdir(mode=0o700)
    return result


def summarize_scan(scan: dict, queue: dict) -> dict:
    counts = scan["counts"]
    issues = {key: counts.get(key, 0) for key in FULL_LIMITS if counts.get(key, 0)}
    return {
        "dil_sayisi": len(scan["languages"]),
        "eksik_anahtar": counts.get("MISSING_KEY_TRANSLATION", 0),
        "ceviri_adayi_hucre": queue["translation_cells"],
        "anlamsal_onayli_hucre": 0,
        "ceviri_servisi_bagli": False,
        "tam_16_dil_senkron": not bool(issues),
        "tum_dilleri_engelleyen_bulgular": issues,
        "bilinen_kodlama_istisnalari": [
            {"dil": i["language"], "dosya": i["path"], "sebep": i.get("reason", "")}
            for i in scan["items"] if i["status"] == "FILE_ENCODING_REVIEW"
        ],
    }


def has_ready_316(root: Path) -> bool:
    try:
        folder = son_islem(root)
        entry = json.loads((folder / "islem.json").read_text(encoding="utf-8"))
        return entry.get("durum") == "UYGULANDI"
    except (RuntimeError, OSError, KeyError, ValueError):
        return False


def rollback_if_needed(root: Path) -> bool:
    """Yalnız motor tarafından uygulanmış EN SON işlem geri alınır."""
    try:
        folder = son_islem(root)
        journal = json.loads((folder / "islem.json").read_text(encoding="utf-8"))
        if journal.get("durum") != "UYGULANDI":
            return False
        with islem_kilidi(root):
            geri_yukle(root, folder, journal)
        print("[GÜVENLİK] Runtime SHA kontrolü başarısızdı; eski baytlar geri yüklendi.")
        return True
    except Exception as exc:
        print("[KRİTİK] Otomatik geri alma tamamlanamadı:", exc)
        print("Yedekleri ve işlem kaydını koruyun; yeniden uygulama yapmayın.")
        return False


def prepare(root: Path, folder: Path) -> tuple[dict, dict]:
    print("\n[1/9] Bütün birim ve regresyon testleri çalıştırılıyor.")
    if not run_tests(root):
        raise RuntimeError("Güvenlik testleri başarısız; canlı işlem başlamadı.")

    config = load_config(root / "tools/locale_guard/config.json")
    baseline_path = root / ".locale_guard/last_synced_master_v1.json"
    baseline = (json.loads(baseline_path.read_text(encoding="utf-8-sig"))
                if baseline_path.is_file() else None)

    print("\n[2/9] Master ve 16 dilin mevcut dosyaları taranıyor.")
    scan = collect(root, config, baseline=baseline)
    save_json(folder / "butun_diller_tarama.json", scan)
    print("Bulgu sayısı:", scan["action_count"])
    for label, count in sorted(scan["counts"].items()):
        print("  ", label, ":", count)

    print("\n[3/9] Eksik kayıtlar için çeviri kuyruğu hazırlanıyor.")
    queue = build_queue(root, config, baseline=baseline)
    save_json(folder / "ceviri_kuyrugu.json", queue)
    print("Çeviri adayı hücre:", queue["translation_cells"])
    print("Çeviri üretilmedi; onaysız kayıtlar canlıya uygulanmayacak.")
    state = summarize_scan(scan, queue)
    save_json(folder / "kapsam_ve_engeller.json", state)
    print("Tam 16 dil senkron:", "EVET" if state["tam_16_dil_senkron"] else "HAYIR")
    if state["bilinen_kodlama_istisnalari"]:
        for item in state["bilinen_kodlama_istisnalari"][:5]:
            print("  Kodlama incelemesi:", item["dil"] + "/" + item["dosya"])
    return scan, state


def tr_express(root: Path, folder: Path, input_fn=input) -> bool:
    """Eski 316 önizleme paketini bütün denetim ve onaylarıyla yürüt."""
    if has_ready_316(root):
        print("\n[4-7/9] Önceden uygulanmış paket bulundu; yeniden yazma yok.")
        ready = runtime_kontrol(root)
        save_json(folder / "turkce_runtime.json", ready)
        print("[8/9] Türkçe runtime SHA kontrolü başarılı. 92 dosya doğrulandı.")
        return True

    print("\n[4/9] 316 hücrelik önizlemenin SHA-256 doğrulaması.")
    summary, previews = validate(root)
    print("Doğrulandı: 316 hücre / 46 dosya, 1 hücre hariç.")

    print("\n[5/9] Tam 316 hücrelik izole uygulama provası.")
    test_folder = rehearse(root, summary, previews)
    create_checkpoint(root, summary)
    print("İzole prova:", test_folder.relative_to(root))

    print("\n[6/9] Önce/sonra inceleme dosyası hazırlanıyor.")
    review = inceleme_olustur(root, summary, previews)
    print("İnceleme dosyası:", review.relative_to(root))
    print("Bu dosya 316 hücrenin eski ve yeni metnini içerir.")
    print("Lütfen dosyayı açıp metinlerin anlamını kontrol edin.")
    print("Bir Dragon Alchemy hücresi bu uygulamaya dahil DEĞİL.")

    if not sor(
        "16 dilin eksikleri bitmiş DEĞİL. Yalnız 316 hücrelik Türkçe paketi "
        "Master + TR için denemeye devam etmek istiyor musunuz?",
        "SADECE TR", input_fn,
    ):
        print("[BEKLİYOR] Tarama ve çeviri kuyruğu hazır; canlı dosyalar değişmedi.")
        return False

    try:
        onay_dogrula(root, summary, previews)
        print("316 hücrelik eski içerik onayı mevcut ve bayt kimlikleriyle eşleşiyor.")
    except RuntimeError:
        print("Anlamsal onay henüz mevcut değil.")
        if not onayla(root, summary, previews, input_fn):
            print("[BEKLİYOR] Kullanıcı metinleri onaylamadı. Yazma yapılmadı.")
            return False

    print("\n[7/9] Master ve Türkçe dosyalar için yedekli uygulama.")
    if not uygula(root, summary, previews, input_fn):
        print("[BEKLİYOR] Son UYGULA 316 onayı verilmedi.")
        return False

    print("\n[8/9] Gerçek hedeflerin runtime ön koşul denetimi.")
    try:
        ready = runtime_kontrol(root)
        save_json(folder / "turkce_runtime.json", ready)
        print("[BAŞARILI] 92 dosya SHA-256; Master/TR içerikleri eşit.")
        return True
    except (RuntimeError, OSError, ValueError, KeyError) as exc:
        print("[ENGEL] Runtime bütünlük denetimi başarısız:", exc)
        rollback_if_needed(root)
        raise


def execute(root: Path, input_fn=input) -> int:
    """Bir menü seçimi ile çalışır; kişisel onaylar ayrı alınır."""
    root_check(root)
    print("=" * 72)
    print("PROJECT_LOCALE — EXPRESS TARAYICI / ONAYLI UYGULAMA / TR RUNTIME")
    print("=" * 72)
    print("Kaynak: master/  |  Dil sayısı: 16")
    print("Tüm diller taranır; eksik metinler çevrilmiş gibi gösterilmez.")
    print("Canlı yazma sadece insan tarafından onaylanmış 316 TR hücresi içindir.")
    print("İstemci/Binary kopyalama ve oyun açma bu komutun kapsamında değildir.")
    if not sor("EXPRESS bütün kontrolleri ve raporlamayı başlatsın mı?",
               "EXPRESS", input_fn):
        return 1

    folder = make_run(root)
    print("İşlem kaydı:", folder.relative_to(root))
    state = {
        "durum": "BASLADI",
        "tum_diller_uygulandi": False,
        "istemci_ve_binary_degisti": False,
        "oyun_ici_test_baslatildi": False,
        "islem_klasoru": str(folder.relative_to(root)),
    }
    try:
        scan, scope = prepare(root, folder)
        state["tarama_bulgusu"] = scan["action_count"]
        state["ceviri_adayi"] = scope["ceviri_adayi_hucre"]
        state["tum_diller_uygulandi"] = False

        result = tr_express(root, folder, input_fn)
        state["tr_runtime_hazir"] = result
        state["durum"] = "TR_RUNTIME_HAZIR_DIGER_DILLER_BEKLIYOR" if result else "ONAY_BEKLIYOR"

        print("\n[9/9] Express sonuç özeti")
        print(" - 16 dil taraması ve çeviri kuyruğu: HAZIR")
        print(" - 316 hücre Türkçe uygulama:", "UYGULANDI" if result else "ONAY BEKLİYOR")
        print(" - Türkçe runtime ön kontrolü:", "BAŞARILI" if result else "BAŞLATILMADI")
        print(" - Diğer 15 dil ve istemci paketleri: TAMAMLANMADI")
        print(" - Oyun içi runtime: HENÜZ BAŞLATILMADI")
        return 0 if result else 3
    except Exception as exc:
        state["durum"] = "GUVENLIK_ENGELI"
        state["hata"] = str(exc)
        print("\n[ENGEL] EXPRESS güvenlik nedeniyle durduruldu:", exc)
        print("Sorun çözülmeden tekrar uygulama yapmayın.")
        return 2
    finally:
        save_json(folder / "sonuc.json", state)


def main() -> int:
    p = argparse.ArgumentParser(description="Tek komutla 16 dil tarama ve onaylı TR uygulama")
    p.add_argument("--root", default=".")
    args = p.parse_args()
    return execute(Path(args.root).resolve())


if __name__ == "__main__":
    raise SystemExit(main())
