@echo off
title vedit - Make my video
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo.
  echo  vedit is not installed yet. Double-click 1_INSTALL.bat first.
  echo.
  pause
  exit /b 1
)

rem easy.py asks the questions, accepts a dragged-in video and keeps the window open at the end.
python easy.py %*
