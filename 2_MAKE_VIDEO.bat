@echo off
title vedit - Make my video
cd /d "%~dp0"

if "%~1"=="" (
  echo.
  echo  How to use: DRAG your video file and DROP it on 2_MAKE_VIDEO.bat
  echo  ^(You can drop several videos at once.^)
  echo.
  pause
  exit /b
)

where python >nul 2>nul
if errorlevel 1 (
  echo  vedit is not installed yet. Double-click 1_INSTALL.bat first.
  pause
  exit /b 1
)

echo.
echo  ============================================
echo    STEP 1: Where will you post it?
echo  ============================================
echo    1 = Reels / TikTok / Shorts  (vertical, tall)
echo    2 = YouTube                   (wide)
echo    3 = Podcast / long talk       (wide, gentle cuts)
echo    4 = Captions only, no cutting
echo.
choice /c 1234 /n /m "  Type 1, 2, 3 or 4: "
if errorlevel 4 (set PRESET=clean) else if errorlevel 3 (set PRESET=podcast) else if errorlevel 2 (set PRESET=youtube) else (set PRESET=reels)

echo.
echo  ============================================
echo    STEP 2: What language do you speak in the video?
echo  ============================================
echo    1 = English
echo    2 = Filipino / Tagalog
echo    3 = Taglish (mixed)  - slower but more accurate
echo.
choice /c 123 /n /m "  Type 1, 2 or 3: "
if errorlevel 3 (set LANG=tl& set MODEL=medium) else if errorlevel 2 (set LANG=tl& set MODEL=small) else (set LANG=en& set MODEL=small)

echo.
echo  Working on it... This can take a few minutes. Don't close this window.
if "%MODEL%"=="medium" echo  (First time with Taglish: it downloads a 1.5 GB language file once.)
echo.
python scripts\vedit.py auto %* --preset %PRESET% --language %LANG% --model %MODEL%
if errorlevel 1 (
  echo.
  echo  Something went wrong. See "Problems?" in START_HERE.md
  pause
  exit /b 1
)

echo.
echo  ============================================
echo    DONE! Your new video is next to the original,
echo    named  ..._%PRESET%.mp4
echo    WATCH IT and check the captions before posting.
echo  ============================================
explorer /select,"%~dpn1_%PRESET%.mp4"
pause
