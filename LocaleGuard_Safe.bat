@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHONIOENCODING=utf-8"

pushd "%~dp0" 2>nul
if errorlevel 1 (
    echo [BLOCK] Cannot enter Project_Locale directory.
    exit /b 2
)
if not exist "tools\locale_guard\master_tr_rehearsal.py" (
    echo [BLOCK] Safe rehearsal module is missing.
    popd
    exit /b 2
)
if not exist "tools\locale_guard\config.json" (
    echo [BLOCK] Locale Guard config is missing.
    popd
    exit /b 2
)
where python >nul 2>nul
if errorlevel 1 (
    echo [BLOCK] python is not available on PATH.
    popd
    exit /b 2
)
python -B -c "import sys; assert sys.version_info >= (3, 10)"
if errorlevel 1 (
    echo [BLOCK] Python 3.10+ required.
    popd
    exit /b 2
)

:menu
cls
echo ===============================================================
echo PROJECT_LOCALE SAFE MAIN MENU  -  MASTER IS SOURCE OF TRUTH
echo ===============================================================
echo Source: master/            Target: tr/
echo LIVE APPLY: DISABLED       CLIENT/BINARY: UNTOUCHED
echo.
echo [1] Verify checkpoint inputs and show status (read only)
echo [2] Save verified progress checkpoint (.locale_guard only)
echo [3] Run Locale Guard unit tests
echo [4] Run isolated 46-file TR test application (no live write)
echo [5] Show repository Git status (read only)
echo [6] Show existing test outputs / checkpoint locations
echo [Q] Exit
echo.
choice /C 123456Q /N /M "Select: "
if errorlevel 7 goto end
if errorlevel 6 goto paths
if errorlevel 5 goto gitstatus
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

:gitstatus
git status --short --branch
goto after

:paths
echo Candidate:
echo   .locale_guard\staging_master_tr_v3_semantic_candidate
echo Input preview:
echo   .locale_guard\master_tr_merge_preview_v1
echo Checkpoints:
echo   .locale_guard\checkpoints
echo Isolated test files:
echo   .locale_guard\test_apply_master_tr_aa529ec168d1\tr
echo Note: these are ignored local outputs, not a live deployment.
cmd /c exit /b 0
goto after

:after
if errorlevel 1 (
    echo.
    echo [BLOCK] Command failed. No automatic retry or live apply.
) else (
    echo.
    echo [OK] Command finished.
)
echo.
pause
goto menu

:end
popd
endlocal
exit /b 0
