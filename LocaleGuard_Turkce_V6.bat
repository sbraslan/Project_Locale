@echo off
chcp 65001 >nul
setlocal EnableExtensions DisableDelayedExpansion
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHONIOENCODING=utf-8"
pushd "%~dp0" 2>nul
if errorlevel 1 (
  echo [ENGEL] Project_Locale klasorune erisilemedi.
  exit /b 2
)
where python >nul 2>nul
if errorlevel 1 (
  echo [ENGEL] Python bulunamadi.
  popd
  exit /b 2
)
if not exist "tools\locale_guard\turkce_menu_v6.py" (
  echo [ENGEL] Turkce menu modulu bulunamadi.
  popd
  exit /b 2
)
python -B -m tools.locale_guard.turkce_menu_v6
set "LG_KOD=%errorlevel%"
popd
endlocal & exit /b %LG_KOD%
