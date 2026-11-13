@echo off
title vedit - Install
cd /d "%~dp0"
echo.
echo  ============================================
echo    vedit installer  (run this ONCE)
echo  ============================================
echo.
set NEED_RESTART=0

where python >nul 2>nul
if errorlevel 1 (
  echo  [1/3] Installing Python... ^(a window may ask "Yes" - click Yes^)
  winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
  set NEED_RESTART=1
) else (
  echo  [1/3] Python is already installed. OK
)

where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo  [2/3] Installing FFmpeg ^(the video engine^)...
  winget install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements
  set NEED_RESTART=1
) else (
  echo  [2/3] FFmpeg is already installed. OK
)

if "%NEED_RESTART%"=="1" (
  echo.
  echo  ------------------------------------------------------------
  echo   Almost done! Close this window, then double-click
  echo   1_INSTALL.bat ONE MORE TIME to finish.
  echo  ------------------------------------------------------------
  pause
  exit /b
)

echo  [3/3] Installing the vedit parts ^(takes a few minutes^)...
python -m pip install --upgrade pip >nul 2>nul
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo  Something went wrong while installing. Check your internet and run this again.
  pause
  exit /b 1
)

echo.
echo  Checking everything...
python scripts\vedit.py doctor
echo.
echo  ============================================
echo    DONE!  Now drag a video onto 2_MAKE_VIDEO.bat
echo  ============================================
pause
