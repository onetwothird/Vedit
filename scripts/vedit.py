#!/usr/bin/env python3
"""vedit — all-in-one AI video editing toolkit (driven by Claude Code).

  vedit doctor                         check ffmpeg / python deps / TTS engines
  vedit auto VIDEO --preset shorts     ONE COMMAND: raw video -> finished video (batch: many files)
  vedit thumbs VIDEO [--title "..."]   thumbnail candidates + ready-made thumbnail
  vedit analyze VIDEO [VIDEO2...]      probe + transcript + silences + scenes + contact sheets
  vedit autocut WORKDIR [opts]         draft edl.json (cut silence, fillers, flubbed takes)
  vedit render EDL [--preview]         render edl.json -> mp4 (+ .srt, chapters) [--formats 9:16,1:1]
  vedit frames VIDEO [opts]            contact sheets to look at any part of a video
  vedit captions EDL                   build captions.ass only
  vedit multicam sync|speakers ...     sync cameras by audio / who-talks-when from separate mics
  vedit reframe VIDEO --start --end    find subject x-position for vertical crops
  vedit tts "text" [opts]              text-to-speech with word timings
  vedit story grid|voices|build STORY  photo + script -> voiced, captioned skit
Run `vedit <command> -h` for options.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

__version__ = "0.2.0"
HERE = Path(__file__).resolve().parent
CMDS = {
    "auto": "auto.py", "thumbs": "thumbs.py", "analyze": "analyze.py", "autocut": "autocut.py",
    "render": "render.py", "frames": "frames.py", "captions": "captions.py", "multicam": "multicam.py",
    "reframe": "reframe.py", "tts": "tts.py", "story": "story.py", "transcribe": "transcribe.py",
}


def doctor():
    ok = True

    def line(name, good, hint=""):
        nonlocal ok
        print(f"  [{'ok' if good else '--'}] {name}" + ("" if good else f"   -> {hint}"))

    print("required")
    for b in ("ffmpeg", "ffprobe"):
        good = bool(shutil.which(b))
        ok &= good
        line(b, good, "macOS: brew install ffmpeg | Ubuntu: sudo apt install ffmpeg | Windows: winget install ffmpeg")
    if shutil.which("ffmpeg"):
        filt = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True).stdout
        for f in ("ass", "loudnorm", "zoompan", "sidechaincompress", "drawtext"):
            good = f" {f} " in filt
            if f in ("ass",):
                ok &= good
            line(f"ffmpeg filter '{f}'", good, "install a full ffmpeg build (with libass/freetype)")
    print("recommended")
    for mod, why in [("faster_whisper", "transcription (pip install faster-whisper)"),
                     ("numpy", "multicam sync/speakers (pip install numpy)"),
                     ("edge_tts", "free natural AI voices (pip install edge-tts)"),
                     ("cv2", "auto face reframing (pip install opencv-python)"),
                     ("PIL", "photo EXIF rotation in story mode (pip install pillow)")]:
        line(mod, importlib.util.find_spec(mod) is not None, why)
    print("optional premium voices")
    line("OPENAI_API_KEY", bool(os.environ.get("OPENAI_API_KEY")), "set it to use OpenAI voices with acting directions")
    line("ELEVENLABS_API_KEY", bool(os.environ.get("ELEVENLABS_API_KEY")), "set it to use ElevenLabs voices")
    print("\nready" if ok else "\nmissing required pieces (see above)")
    return 0 if ok else 1


def main():
    if len(sys.argv) >= 2 and sys.argv[1] in ("--version", "-V"):
        print(f"vedit {__version__}")
        return 0
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd = sys.argv[1]
    if cmd == "doctor":
        return doctor()
    if cmd not in CMDS:
        print(f"unknown command '{cmd}'\n{__doc__}")
        return 2
    return subprocess.call([sys.executable, str(HERE / CMDS[cmd]), *sys.argv[2:]])


if __name__ == "__main__":
    sys.exit(main())
