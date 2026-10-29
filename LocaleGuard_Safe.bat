@echo off
chcp 65001 >nul
setlocal EnableExtensions DisableDelayedExpansion
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHONIOENCODING=utf-8"

pushd "%~dp0" 2>nul
if errorlevel 1 (
    echo [ENGEL] Project_Locale klasörüne girilemedi.
    exit /b 2
)
if not exist "tools\locale_guard\master_tr_rehearsal.py" (
    echo [ENGEL] Doğrulanmış prova modülü bulunamadı.
    popd
    exit /b 2
)
if not exist "tools\locale_guard\config.json" (
    echo [ENGEL] Locale Guard yapılandırması bulunamadı.
    popd
    exit /b 2
)
where python >nul 2>nul
if errorlevel 1 (
    echo [ENGEL] Python sistem yolunda bulunamadı.
    popd
    exit /b 2
)
python -B -c "import sys; assert sys.version_info >= (3, 10)"
if errorlevel 1 (
    echo [ENGEL] Python 3.10 veya üzeri gereklidir.
    popd
    exit /b 2
)

:menu
cls
echo ======================================================================
echo PROJECT_LOCALE - GÜVENLİ ANA MENÜ V4  -  2026-10-29
echo ======================================================================
echo KANONİK KAYNAK: master/             CANLI HEDEF: tr/
echo SON ÖNİZLEME: 316 hücre / 46 dosya / 1 bekleyen onay
echo ÖNCEKİ STAGING V2: 262 hücre / 46 dosya (ayrı işlem)
echo CANLI UYGULAMA: KİLİTLİ             İSTEMCİ/BINARY: DEĞİŞMEDİ
echo ======================================================================
echo [1] 316 hücrelik Master - TR önizlemesini doğrula (salt okunur)
echo [2] Doğrulanan 316 hücrelik kontrol noktasını kaydet
echo [3] Tüm yerel Locale Guard birim testlerini çalıştır
echo [4] 316 hücrelik uygulamayı test alanında prova et
echo [5] Önceki 262 hücrelik Staging V2 ve fark denetimi
echo [6] Kanonik EN Aşama-1 kapsam denemesi (yalnız rapor)
echo [7] TAM GÜVENLİ ÖN KONTROL (testler + iki hat + Aşama-1)
echo [8] Git durumunu göster (salt okunur)
echo [9] Çıktı konumları ve sonraki etkinleştirme adımları
echo [D] Artımlı tarama: 16 dilde eksik/fazla/değişen kayıtlar
echo [H] Etkinleştirme hazırlığı (canlı uygulama YOK)
echo [C] Çıkış
echo.
choice /C 123456789HDC /N /M "Seçiminiz: "
if errorlevel 12 goto end
if errorlevel 11 goto delta
if errorlevel 10 goto readiness
if errorlevel 9 goto paths
if errorlevel 8 goto gitstatus
if errorlevel 7 goto full
if errorlevel 6 goto canonical
if errorlevel 5 goto verify_v2
if errorlevel 4 goto rehearsal
if errorlevel 3 goto tests
if errorlevel 2 goto checkpoint
if errorlevel 1 goto status
goto menu

:status
python -B -m tools.locale_guard.master_tr_rehearsal status --root .
goto after

:checkpoint
python -B -m tools.locale_guard.master_tr_rehearsal checkpoint --root .
goto after

:tests
python -B -m unittest discover -s tools\locale_guard\tests -v
goto after

:rehearsal
python -B -m tools.locale_guard.master_tr_rehearsal rehearse --root .
goto after

:verify_v2
if not exist "tools\locale_guard\master_tr_stage_validate_v2.py" goto missing_v2
if not exist "tools\locale_guard\master_tr_stage_diff_audit_v1.py" goto missing_v2
python -B -m tools.locale_guard.master_tr_stage_validate_v2
if errorlevel 1 goto after
python -B -m tools.locale_guard.master_tr_stage_diff_audit_v1
goto after

:missing_v2
echo [ENGEL] Yerel Staging V2 doğrulayıcısı veya fark denetimi bulunamadı.
echo [ENGEL] Dosyalar değişmedi. Eski sonuçları geçerli saymayın.
cmd /c exit /b 2
goto after

:canonical
if not exist "tools\locale_guard\canonical_backfill_stage1_dry_run.py" goto missing_canonical
python -B -m tools.locale_guard.canonical_backfill_stage1_dry_run --report .locale_guard/safe_menu_canonical_stage1_v2.json --diff .locale_guard/safe_menu_canonical_stage1_v2.diff
goto after

:missing_canonical
echo [ENGEL] Kanonik Aşama-1 deneme modülü bulunamadı.
cmd /c exit /b 2
goto after

:full
echo.
echo [1/5] Locale Guard testleri çalışıyor...
python -B -m unittest discover -s tools\locale_guard\tests -v
if errorlevel 1 goto after
echo.
echo [2/5] Son 316 hücrelik birleştirme önizlemesi doğrulanıyor...
python -B -m tools.locale_guard.master_tr_rehearsal status --root .
if errorlevel 1 goto after
if not exist "tools\locale_guard\master_tr_stage_validate_v2.py" goto missing_full
if not exist "tools\locale_guard\master_tr_stage_diff_audit_v1.py" goto missing_full
echo.
echo [3/5] Önceki Staging V2 bütünlüğü doğrulanıyor...
python -B -m tools.locale_guard.master_tr_stage_validate_v2
if errorlevel 1 goto after
echo.
echo [4/5] Önceki Staging V1-V2 bağımsız fark denetimi...
python -B -m tools.locale_guard.master_tr_stage_diff_audit_v1
if errorlevel 1 goto after
if not exist "tools\locale_guard\canonical_backfill_stage1_dry_run.py" goto missing_full
echo.
echo [5/5] Kanonik EN Aşama-1 (yalnız rapor)...
python -B -m tools.locale_guard.canonical_backfill_stage1_dry_run --report .locale_guard/safe_menu_canonical_stage1_v2.json --diff .locale_guard/safe_menu_canonical_stage1_v2.diff
if errorlevel 1 goto after
echo.
echo [TAMAM] Beş ön kontrolün tamamı başarılı. Canlı uygulama kilitli.
cmd /c exit /b 0
goto after

:missing_full
echo [ENGEL] Bir veya daha fazla yerel ön kontrol dosyası bulunamadı.
echo [ENGEL] Yedek yöntem veya canlı uygulama denenmedi.
cmd /c exit /b 2
goto after

:gitstatus
git status --short --branch
goto after

:paths
echo.
echo Son 316 hücrelik önizleme:
echo   .locale_guard\master_tr_merge_preview_v1
echo Son 316 hücrelik anlamsal aday:
echo   .locale_guard\staging_master_tr_v3_semantic_candidate
echo Kontrol noktaları:
echo   .locale_guard\checkpoints
echo Test alanındaki prova:
echo   .locale_guard\test_apply_master_tr_aa529ec168d1\tr
echo Önceki 262 hücrelik Staging V2:
echo   .locale_guard\staging_master_tr_v2
echo 16 dil artımlı tarama raporu:
echo   .locale_guard\incremental_scan_v1.json
echo Kanonik Aşama-1 raporu:
echo   .locale_guard\safe_menu_canonical_stage1_v2.json
echo.
echo Önerilen sıra: D tarama, 3 test, 1 doğrulama, 2 kayıt, 4 prova, 7 tam kontrol.
echo Not: Bir dragon_alchemy hücresi dışlandı; anlamsal onay gerekiyor.
echo Hiçbir seçenek canlı master/ veya tr/ dosyalarına yazmaz.
cmd /c exit /b 0
goto after

:delta
if not exist "tools\locale_guard\incremental_scan_v1.py" goto missing_delta
python -B -m tools.locale_guard.incremental_scan_v1 --root .
goto after

:missing_delta
echo [ENGEL] Artımlı tarama modülü bulunamadı; dosya değiştirilmedi.
cmd /c exit /b 2
goto after

:readiness
echo.
echo [KONTROL] Son 316 hücrelik önizlemenin SHA256 değeri değişmemeli...
python -B -m tools.locale_guard.master_tr_rehearsal status --root .
if errorlevel 1 goto after
echo.
echo [ENGEL] BU MENÜDEN CANLI ETKİNLEŞTİRMEYE İZİN VERİLMİYOR.
echo 316 teknik değişiklik test için hazır, canlıya uygulanmadı.
echo Dışlanan 1 dragon_alchemy hücresi ayrı incelenmelidir.
echo Önce [4] test alanı provasını, sonra [7] tam ön kontrolü çalıştırın.
echo Canlı uygulama için açık onay, yedekleme, bağımsız kaynak hash
echo kontrolleri, geri alma ve işlem sonrası doğrulama zorunludur.
cmd /c exit /b 0
goto after

:after
if errorlevel 1 (
    echo.
    echo [ENGEL] Komut başarısız. Otomatik tekrar veya canlı uygulama yok.
) else (
    echo.
    echo [TAMAM] Komut tamamlandı. Canlı uygulama yapılmadı.
)
echo.
pause
goto menu

:end
popd
endlocal
exit /b 0
