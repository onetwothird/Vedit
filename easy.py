#!/usr/bin/env python3
"""vedit made easy: answer two questions, get a finished video.

Works the same on Windows, Mac and Linux, and inside VS Code (open this file, press the Run button).

  python easy.py                      # asks you to drag a video into the window
  python easy.py myvideo.mp4          # or give the video(s) directly
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

PLATFORMS = [  # (menu text, preset)
    ("Reels / TikTok / Shorts  (vertical, tall)", "reels"),
    ("YouTube                  (wide)", "youtube"),
    ("Podcast / long talk      (wide, gentle cuts)", "podcast"),
    ("Captions only, no cutting", "clean"),
]
LANGUAGES = [  # (menu text, language code, whisper model)
    ("English", "en", "small"),
    # large-v3: on a real Taglish video it fixed 7 words medium got wrong (naintindihan, hindi rin, kotse...)
    ("Filipino / Tagalog", "tl", "large-v3"),
    ("Taglish (mixed English + Filipino)", "tl", "large-v3"),
]
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm", ".mts", ".3gp"}


def say(text: str = "") -> None:
    print(" " + text if text else "", flush=True)


def banner(title: str) -> None:
    say()
    say("=" * 44)
    say("  " + title)
    say("=" * 44)


def ask_choice(title: str, options: list[str]) -> int:
    banner(title)
    for i, text in enumerate(options, 1):
        say(f"  {i} = {text}")
    while True:
        say()
        ans = input(f"  Type a number (1-{len(options)}) and press Enter: ").strip()
        if ans.isdigit() and 1 <= int(ans) <= len(options):
            return int(ans) - 1
        say(f"  Please type just a number from 1 to {len(options)}.")


def split_paths(text: str) -> list[str]:
    """Turn what a drag-and-drop pastes into file paths, on any platform.

    Windows pastes "C:\\My Videos\\a.mp4" (quotes only when there are spaces).
    Mac pastes /Users/me/My\\ Videos/a.mp4 (spaces escaped) or '/Users/me/a.mp4'.
    """
    text = text.replace("﻿", "").strip()
    if not text:
        return []
    if os.name == "nt":
        return [a or b for a, b in re.findall(r'"([^"]+)"|(\S+)', text)]
    try:
        return shlex.split(text)
    except ValueError:
        return [text.strip("'\"")]


def ask_videos() -> list[Path]:
    banner("Which video?")
    say("  Drag your video file INTO THIS WINDOW, then press Enter.")
    say("  (You can drag several videos at once.)")
    while True:
        say()
        videos, bad = check_videos(split_paths(input("  Video: ")))
        if videos and not bad:
            return videos
        for b in bad:
            say(f"  Can't find a video here: {b}")
        say("  Try dragging the file in again.")


def check_videos(paths: list[str]) -> tuple[list[Path], list[str]]:
    good, bad = [], []
    for p in paths:
        f = Path(p).expanduser()
        if f.is_file() and f.suffix.lower() in VIDEO_EXTS:
            good.append(f.resolve())
        else:
            bad.append(p)
    return good, bad


def missing_parts() -> list[str]:
    missing = []
    if not shutil.which("ffmpeg"):
        missing.append("FFmpeg (the video engine)")
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        try:
            import whisper  # noqa: F401
        except ImportError:
            missing.append("the caption engine (faster-whisper)")
    return missing


def install_help() -> None:
    say("  vedit is not fully installed yet. Missing: " + ", ".join(missing_parts()))
    say()
    if os.name == "nt":
        say("  Fix: double-click 1_INSTALL.bat, then try again.")
    elif sys.platform == "darwin":
        say("  Fix: open Terminal and run these two lines:")
        say("    brew install ffmpeg")
        say(f'    python3 -m pip install -r "{HERE / "requirements.txt"}"')
    else:
        say("  Fix: open a terminal and run these two lines:")
        say("    sudo apt install ffmpeg      (or your system's package manager)")
        say(f'    python3 -m pip install -r "{HERE / "requirements.txt"}"')


def show_in_folder(path: Path) -> None:
    try:
        if os.name == "nt":
            subprocess.Popen(["explorer", "/select,", str(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path.parent)])
    except OSError:
        pass


def run(argv: list[str]) -> int:
    say("vedit: cut the pauses, add captions, get a finished video.")
    if missing_parts():
        install_help()
        return 1

    if argv:
        videos, bad = check_videos(argv)
        for b in bad:
            say(f"  Skipping, not a video file: {b}")
        if not videos:
            videos = ask_videos()
    else:
        videos = ask_videos()

    preset = PLATFORMS[ask_choice("Where will you post it?", [t for t, _ in PLATFORMS])][1]
    _, lang, model = LANGUAGES[ask_choice("What language do you speak in the video?", [t for t, *_ in LANGUAGES])]

    say()
    say("Working on it... This can take a few minutes. Don't close this window.")
    if model in ("medium", "large-v3"):
        say("(The first time, it downloads a big language file once. Use good internet.)")
    say()
    cmd = [sys.executable, str(HERE / "scripts" / "vedit.py"), "auto", *map(str, videos),
           "--preset", preset, "--language", lang, "--model", model]
    if subprocess.call(cmd, cwd=str(HERE)) != 0:
        say()
        say('Something went wrong. Scroll up to read the error, and see "Problems?" in START_HERE.md')
        return 1

    outputs = [v.with_name(f"{v.stem}_{preset}.mp4") for v in videos]
    banner("DONE!")
    for o in outputs:
        say(f"  {o}")
    say()
    say("  Your new video is next to the original. WATCH IT and check the")
    say("  captions before posting.")
    if outputs[0].exists():
        show_in_folder(outputs[0])
    return 0


def main() -> None:
    try:
        code = run(sys.argv[1:])
    except (KeyboardInterrupt, EOFError):
        say()
        say("Stopped.")
        code = 1
    if sys.stdin.isatty():  # keep a double-clicked window open so people can read it
        say()
        try:
            input(" Press Enter to close this window...")
        except (KeyboardInterrupt, EOFError):
            pass
    sys.exit(code)


if __name__ == "__main__":
    main()
