@echo off
REM Double-click: builds every story.  Drag a story folder onto this file: builds just that one.
cd /d "%~dp0"
where ffmpeg >nul 2>nul || (echo FFmpeg not found. Run:  winget install Gyan.FFmpeg   then reopen this window. & pause & exit /b 1)
python -m pip install --quiet edge-tts numpy pillow opencv-python faster-whisper
if "%~1"=="" (python build.py) else (python build.py "%~1")
echo.
start "" "%~dp0stories"
pause
