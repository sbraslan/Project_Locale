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
    echo [BLOCK] Verified rehearsal module is missing.
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
    echo [BLOCK] Python not available on PATH.
    popd
    exit /b 2
)
python -B -c "import sys; assert sys.version_info >= (3, 10)"
if errorlevel 1 (
    echo [BLOCK] Python 3.10+ is required.
    popd
    exit /b 2
)

:menu
cls
echo ======================================================================
echo PROJECT_LOCALE SAFE MAIN MENU V2  -  2026-10-29
echo ======================================================================
echo CANONICAL MASTER: master/          LIVE TARGET: tr/
echo VERIFIED 2026-10-29 PREVIEW: 316 cells / 46 files / 1 HOLD
echo EARLIER STAGING V2: 262 cells / 46 files (separate workflow)
echo LIVE APPLY: LOCKED                CLIENT/BINARY: UNTOUCHED
echo ======================================================================
echo [1] Verify 316-cell master-to-TR preview (read-only)
echo [2] Save verified 316-cell checkpoint (.locale_guard only)
echo [3] Run all local Locale Guard unit tests
echo [4] Rehearse 316-cell test application (sandbox only)
echo [5] Verify earlier 262-cell Staging V2 + differential audit
echo [6] Canonical EN Stage-1 scope dry-run (report only)
echo [7] FULL SAFE PREFLIGHT (tests + both pipelines + Stage-1)
echo [8] Show Git status (read-only)
echo [9] Show output locations and next activation steps
echo [A] Activation readiness status (NO live apply)
echo [Q] Exit
echo.
choice /C 123456789AQ /N /M "Select: "
if errorlevel 11 goto end
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
echo [BLOCK] Local Staging V2 validator or differential audit is missing.
echo [BLOCK] No files were changed. Do not substitute older results.
cmd /c exit /b 2
goto after

:canonical
if not exist "tools\locale_guard\canonical_backfill_stage1_dry_run.py" goto missing_canonical
python -B -m tools.locale_guard.canonical_backfill_stage1_dry_run --report .locale_guard/safe_menu_canonical_stage1_v2.json --diff .locale_guard/safe_menu_canonical_stage1_v2.diff
goto after

:missing_canonical
echo [BLOCK] Canonical Stage-1 dry-run module is missing.
cmd /c exit /b 2
goto after

:full
echo.
echo [1/5] Locale Guard tests...
python -B -m unittest discover -s tools\locale_guard\tests -v
if errorlevel 1 goto after
echo.
echo [2/5] Latest 316-cell merge preview integrity...
python -B -m tools.locale_guard.master_tr_rehearsal status --root .
if errorlevel 1 goto after
if not exist "tools\locale_guard\master_tr_stage_validate_v2.py" goto missing_full
if not exist "tools\locale_guard\master_tr_stage_diff_audit_v1.py" goto missing_full
echo.
echo [3/5] Earlier Staging V2 integrity...
python -B -m tools.locale_guard.master_tr_stage_validate_v2
if errorlevel 1 goto after
echo.
echo [4/5] Earlier Staging V1-V2 independent difference audit...
python -B -m tools.locale_guard.master_tr_stage_diff_audit_v1
if errorlevel 1 goto after
if not exist "tools\locale_guard\canonical_backfill_stage1_dry_run.py" goto missing_full
echo.
echo [5/5] Canonical EN Stage-1 (report only)...
python -B -m tools.locale_guard.canonical_backfill_stage1_dry_run --report .locale_guard/safe_menu_canonical_stage1_v2.json --diff .locale_guard/safe_menu_canonical_stage1_v2.diff
if errorlevel 1 goto after
echo.
echo [OK] ALL FIVE PREFLIGHT CHECKS PASSED. LIVE APPLY REMAINS LOCKED.
cmd /c exit /b 0
goto after

:missing_full
echo [BLOCK] One or more local preflight scripts are missing.
echo [BLOCK] No fallback or live apply was attempted.
cmd /c exit /b 2
goto after

:gitstatus
git status --short --branch
goto after

:paths
echo.
echo Latest 316-cell preview:
echo   .locale_guard\master_tr_merge_preview_v1
echo Latest 316-cell semantic candidate:
echo   .locale_guard\staging_master_tr_v3_semantic_candidate
echo Checkpoints:
echo   .locale_guard\checkpoints
echo Sandbox rehearsal:
echo   .locale_guard\test_apply_master_tr_aa529ec168d1\tr
echo Earlier 262-cell Staging V2:
echo   .locale_guard\staging_master_tr_v2
echo Canonical Stage-1 report:
echo   .locale_guard\safe_menu_canonical_stage1_v2.json
echo.
echo Planned order: 3 tests, 1 verify, 2 checkpoint, 4 sandbox, 7 full check.
echo Note: One dragon_alchemy cell is excluded and needs semantic approval.
echo No menu option writes to live master/ or tr/.
cmd /c exit /b 0
goto after

:readiness
echo.
echo [CHECK] Latest 316-cell preview must remain SHA256-identical...
python -B -m tools.locale_guard.master_tr_rehearsal status --root .
if errorlevel 1 goto after
echo.
echo [BLOCK] LIVE ACTIVATION IS NOT YET AUTHORIZED BY THIS MENU.
echo 316 reviewed technical changes are test-ready, not deployed.
echo 1 excluded dragon_alchemy cell must be handled explicitly.
echo Run [4] sandbox and [7] full safe preflight.
echo For a future live apply: require explicit approval, backup, independent
echo pre-write hash checks, rollback, and post-write verification.
cmd /c exit /b 0
goto after

:after
if errorlevel 1 (
    echo.
    echo [BLOCK] Command failed. No automatic retry or live apply.
) else (
    echo.
    echo [OK] Command finished. No live apply.
)
echo.
pause
goto menu

:end
popd
endlocal
exit /b 0
