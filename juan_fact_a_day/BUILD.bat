@echo off
REM Juan Fact a Day - build videos.
REM   Double-click         -> builds EVERY story in the "stories" folder
REM   Drag a story folder  -> builds only that story
REM Each finished video is saved inside its own story folder (e.g. stories\001_7641_na_isla\001_7641_na_isla.mp4)
cd /d "%~dp0"
where ffmpeg >nul 2>nul || (echo FFmpeg not found. Run:  winget install Gyan.FFmpeg   then reopen this window. & pause & exit /b 1)
python -m pip install --quiet edge-tts numpy pillow opencv-python
if not exist assets\sfx\whoosh.wav python tools\make_sfx.py
if not "%~1"=="" (
  python tools\make_cards.py "%~1\story.json"
  python tools\make_thumbnail.py "%~1\story.json"
  python "%~dp0..\scripts\story.py" build "%~1\story.json"
  goto done
)
python tools\make_cards.py
python tools\make_thumbnail.py
for /d %%d in (stories\*) do (
  if exist "%%d\story.json" (
    echo.
    echo === Building %%d ===
    python "%~dp0..\scripts\story.py" build "%%d\story.json"
  )
)
:done
echo.
echo DONE! Each video is inside its story folder.
start "" "%~dp0stories"
pause
