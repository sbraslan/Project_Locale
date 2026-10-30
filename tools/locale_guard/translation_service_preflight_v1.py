#!/usr/bin/env python3
"""LibreTranslate yerel servis preflight ve güvenli tek-sefer başlatma.

Asla pip install / update yapmaz. 127.0.0.1 dışına bağlanmaz.
Başarılı /languages + gerçek çeviri kontrolü olmadan çağıran motor çeviriye geçemez.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import socket
import subprocess
import sys
import sysconfig
import time
import urllib.error
import urllib.request
from pathlib import Path

URL = "http://127.0.0.1:5000"
PYTHON_ONLY_CODES = {"de", "en", "tr"}
DEFAULT_CODES = ("de", "en", "tr")
PROBE = (
    ("de", "tr", "Beende die Mission.", "Görevi tamamla"),
    ("tr", "en", "Görevi tamamla.", "Complete the mission"),
    ("en", "tr", "Complete the mission.", "Görevi tamamla"),
)
NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))
LOG_NAME = "libretranslate_server.log"


class ServicePreflightError(RuntimeError):
    """Tek hata: çeviri sisteminin başlangıç koşulu başarısız."""


class ServiceNotRunning(ServicePreflightError):
    pass


def _fetch_json(path: str, payload: dict | None = None, timeout: float = 4) -> object:
    data = None if payload is None else json.dumps(
        payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        URL + path, data=data,
        method="GET" if data is None else "POST",
        headers={"Content-Type": "application/json; charset=utf-8",
                 "Accept": "application/json"},
    )
    with NO_PROXY.open(request, timeout=timeout) as response:
        if response.status != 200:
            raise ServicePreflightError("Yerel servis HTTP " + str(response.status))
        if int(response.headers.get("Content-Length", "0") or "0") > 262144:
            raise ServicePreflightError("Yerel servis cevap sınırını aştı")
        raw = response.read(262145)
        if len(raw) > 262144:
            raise ServicePreflightError("Yerel servis cevap sınırını aştı")
    return json.loads(raw.decode("utf-8", "strict"))


def local_port_listens() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 5000), timeout=1):
            return True
    except OSError:
        return False


def check_ready(required: tuple[str, ...] = DEFAULT_CODES,
                *, check_translation: bool = True) -> dict:
    """Sunucuya erişimi, dil kodlarını ve gerçek UTF-8 çeviriyi doğrula."""
    try:
        data = _fetch_json("/languages")
    except (urllib.error.URLError, OSError, ValueError, UnicodeError) as exc:
        raise ServicePreflightError(
            "127.0.0.1:5000 /languages erişilemiyor: " + str(exc)
        ) from exc
    if not isinstance(data, list):
        raise ServicePreflightError("5000 portunda LibreTranslate /languages biçimi yok")
    codes = {item.get("code") for item in data if isinstance(item, dict)}
    missing = sorted(set(required) - codes)
    if missing:
        raise ServicePreflightError("Yerel sunucuda eksik dil modelleri: " + ",".join(missing))

    if check_translation:
        pairs = list(PROBE)
        extra = set(required) - PYTHON_ONLY_CODES
        if extra:
            # Hedef aşamada her dilin EN köprüsü çalışıyor mu doğrula.
            pairs.extend(("en", code, "Welcome adventurer.", "")
                         for code in sorted(extra))
            pairs.extend((code, "en", "Welcome adventurer.", "")
                         for code in sorted(extra))
        for source, target, sample, _ in pairs:
            try:
                response = _fetch_json("/translate", {
                    "q": sample, "source": source, "target": target,
                    "format": "text"}, timeout=30)
            except (urllib.error.HTTPError, urllib.error.URLError,
                    ValueError, UnicodeError, OSError) as exc:
                raise ServicePreflightError(
                    source + " -> " + target + " model çevirisi hazır değil: "
                    + str(exc)) from exc
            text = response.get("translatedText") if isinstance(response, dict) else None
            if not isinstance(text, str) or not text.strip():
                raise ServicePreflightError(
                    source + " -> " + target + " boş/geçersiz yanıt döndürdü")
            # Konsol gösterimi değil, gerçek UTF-8 Python string'i kontrol edilir.
            if "\ufffd" in text or "\x00" in text or "Ã¶" in text or "Ã¼" in text:
                raise ServicePreflightError(
                    source + " -> " + target + " gerçek UTF-8 çevirisi bozuk")
            if sample != "Welcome adventurer." and text == sample:
                raise ServicePreflightError(
                    source + " -> " + target + " beklenen deneme metnini çevirmedi")
    return {"status": "HAZIR", "languages": sorted(codes),
            "required": list(required), "translation_probe": check_translation,
            "url": URL}


def _python_entrypoints() -> list[Path]:
    candidates = []
    scripts = Path(sysconfig.get_path("scripts"))
    candidates += [scripts / "libretranslate.exe", scripts / "libretranslate",
                   Path(sys.executable).parent / "Scripts/libretranslate.exe",
                   Path(sys.executable).parent / "libretranslate.exe"]
    if os.name == "nt":
        appdata = Path(os.environ.get("APPDATA", "C:/nonexistent"))
        local = Path(os.environ.get("LOCALAPPDATA", "C:/nonexistent"))
        for version in ("Python39", "Python310", "Python311", "Python312",
                        "Python313", "Python314", "Python315"):
            candidates += [appdata / "Python" / version / "Scripts/libretranslate.exe"]
        for version in ("Python39", "Python310", "Python311", "Python312",
                        "Python313", "Python314", "Python315"):
            candidates += [local / "Programs/Python" / version / "Scripts/libretranslate.exe"]
    return candidates


def discover_start_command() -> list[str] | None:
    """Yalnız var olan kurulu ve sabit LibreTranslate komutunu kullan."""
    override = os.environ.get("METIN2_LIBRETRANSLATE_EXE", "").strip()
    if override:
        f = Path(override).expanduser()
        if not f.is_file() or f.is_symlink() or f.name.lower() not in {
            "libretranslate.exe", "libretranslate"
        }:
            raise ServicePreflightError(
                "METIN2_LIBRETRANSLATE_EXE kurulu LibreTranslate dosyasını göstermiyor")
        return [str(f.resolve())]

    candidates = _python_entrypoints()
    found = shutil.which("libretranslate")
    if found:
        candidates.insert(0, Path(found))
    for candidate in candidates:
        if candidate.is_file() and not candidate.is_symlink():
            return [str(candidate.resolve())]
    return None


def _launch(root: Path, command: list[str],
            required: tuple[str, ...]) -> subprocess.Popen:
    guard = root / ".locale_guard"
    if not guard.is_dir() or guard.is_symlink():
        raise ServicePreflightError(".locale_guard rapor klasörü güvenli değil")
    srv_dir = guard / "translation_service"
    if srv_dir.is_symlink():
        raise ServicePreflightError("Çeviri servis log klasörü sembolik bağlantı")
    srv_dir.mkdir(exist_ok=True)
    logfile = srv_dir / LOG_NAME
    if logfile.is_symlink():
        raise ServicePreflightError("Çeviri servis logu sembolik bağlantı")
    startup_args = command + [
        "--host", "127.0.0.1", "--port", "5000",
        "--load-only", ",".join(required),
    ]
    creationflags = 0
    if os.name == "nt":
        creationflags = (getattr(subprocess, "DETACHED_PROCESS", 0)
                         | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    environment = dict(os.environ)
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    # Sadece tek log dosyasına ekleme: önceki kayıtlar korunur.
    with logfile.open("ab") as writer:
        writer.write(("\n--- LibreTranslate startup UTC "
                      + dt.datetime.now(dt.timezone.utc).isoformat()
                      + " ---\n").encode("ascii"))
        writer.flush()
        proc = subprocess.Popen(
            startup_args, stdin=subprocess.DEVNULL,
            stdout=writer, stderr=subprocess.STDOUT,
            close_fds=True, cwd=str(root), env=environment,
            creationflags=creationflags,
        )
    return proc


def ensure_service(root: Path, *, required: tuple[str, ...] = DEFAULT_CODES,
                   autostart: bool = True, timeout_seconds: int = 120) -> dict:
    """Önce reuse, yoksa yerel kurulumdan start; tek preflight hatası."""
    if not required or any(
        code not in {"ar","cs","de","da","en","es","fr","el","hu","it",
                     "nl","pl","pt","ro","ru","tr"} for code in required
    ):
        raise ServicePreflightError("Desteklenmeyen dil kodları")
    if local_port_listens():
        # Port açık ama LibreTranslate yanlışsa var olan sunucuyu kapatma.
        try:
            status = check_ready(required)
        except ServicePreflightError as exc:
            raise ServicePreflightError(
                "5000 portunda servis var ama hazır değil. "
                "Çalışan servisi durdurmadan önce modeli/portu inceleyin: "
                + str(exc)) from exc
        print("[OK] LibreTranslate zaten açık; yeniden başlatılmadı.")
        return {**status, "action": "MEVCUT_SERVIS_KULLANILDI"}

    if not autostart:
        raise ServicePreflightError(
            "LibreTranslate kapalı ve otomatik başlatma devre dışı")
    cmd = discover_start_command()
    if cmd is None:
        raise ServicePreflightError(
            "LibreTranslate kapalı ve bu Windows/Python ortamında "
            "'libretranslate' başlatıcısı bulunamadı. Bir kerelik kurulum: "
            "python -m pip install libretranslate; ardından tekrar Express çalıştırın. "
            "Başka Python ortamına kuruluysa METIN2_LIBRETRANSLATE_EXE "
            "değişkenine o ortamın libretranslate.exe yolunu verin. "
            "Docker zorunlu değil. Otomatik paket kurulumu yapılmadı."
        )
    print("[BİLGİ] LibreTranslate kapalı; kurulu başlatıcıdan açılıyor:", cmd[0])
    proc = _launch(root, cmd, required)
    begin = time.monotonic()
    last_failure = ""
    while time.monotonic() - begin < timeout_seconds:
        code = proc.poll()
        if code is not None:
            raise ServicePreflightError(
                "LibreTranslate başlatıldı ama kapandı (exit="
                + str(code) + "). Kayıt: .locale_guard/translation_service/"
                + LOG_NAME)
        if local_port_listens():
            try:
                status = check_ready(required)
                print("[OK] LibreTranslate hazır, çeviri testleri geçti.")
                return {**status, "action": "OTOMATIK_BASLATILDI",
                        "pid": proc.pid}
            except ServicePreflightError as exc:
                last_failure = str(exc)
        time.sleep(2)
    raise ServicePreflightError(
        "LibreTranslate başlatıldı ancak model/çeviri doğrulaması "
        + str(timeout_seconds) + " saniyede tamamlanmadı. "
        + ("Son kontrol: " + last_failure + ". " if last_failure else "")
        + "Kayıt: .locale_guard/translation_service/" + LOG_NAME
        + " | Canlı locale dosyaları değiştirilmedi."
    )
