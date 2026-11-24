"""Build the engine Vedit Studio ships: PyInstaller (one folder, not one file) plus FFmpeg.

    python packaging/build_engine.py [--ffmpeg-dir DIR]

Writes app/src-tauri/engine/ (vedit-engine.exe and its _internal/ folder) and app/src-tauri/ffmpeg/ (ffmpeg.exe,
ffprobe.exe and FFmpeg's license). Both are git-ignored; tauri.conf.json bundles them as resources, so the
installed app has <install dir>/engine/vedit-engine.exe and <install dir>/bin/ffmpeg.exe.

One folder, not one file: every job step runs `vedit-engine run <script>` as a new process, and a one-file build
would unpack the whole engine to a temp folder on each start.

The scripts are run with runpy, so PyInstaller cannot see their imports by itself: each one is passed as a hidden
import (which collects the libraries they use) and also copied as source next to them in _internal/, where both
the frozen modules and engine_entry.py say their __file__ is. The scripts find fonts and templates one folder up
(`Path(__file__).parent.parent / "fonts"`), so those go in engine/ itself, beside _internal/.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
TAURI = ROOT / "app" / "src-tauri"
WORK = ROOT / "build" / "engine"
# never needed by the engine; some get pulled in by optional imports in its libraries
EXCLUDE = ["tkinter", "matplotlib", "pytest", "IPython", "torch", "tensorflow", "keras", "jax", "h5py", "grpc",
           "transformers", "sklearn", "scipy", "pandas", "pygments", "jinja2"]


def find_ffmpeg_dir(given: str | None) -> Path:
    if given:
        return Path(given)
    found = shutil.which("ffmpeg")
    if not found:
        sys.exit("ffmpeg not found: pass --ffmpeg-dir with the folder that holds ffmpeg.exe and ffprobe.exe")
    return Path(found).resolve().parent


def build_engine() -> Path:
    modules = sorted(p.stem for p in SCRIPTS.glob("*.py") if p.stem not in ("__init__", "engine_entry"))
    sep = ";" if sys.platform == "win32" else ":"
    args = [
        sys.executable, "-m", "PyInstaller", str(SCRIPTS / "engine_entry.py"),
        "--name", "vedit-engine", "--onedir", "--console", "--noconfirm", "--clean",
        "--distpath", str(WORK / "dist"), "--workpath", str(WORK / "work"), "--specpath", str(WORK),
        "--paths", str(SCRIPTS),
        "--add-data", f"{SCRIPTS / '*.py'}{sep}.",
        # libraries that load parts of themselves at run time
        "--collect-submodules", "uvicorn",
        "--collect-data", "faster_whisper",
        "--collect-binaries", "ctranslate2",
        "--collect-data", "edge_tts",
    ]
    for m in EXCLUDE:
        args += ["--exclude-module", m]
    for m in modules:
        args += ["--hidden-import", m]
    subprocess.run(args, check=True, cwd=ROOT)
    return WORK / "dist" / "vedit-engine"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--ffmpeg-dir", help="folder with ffmpeg.exe and ffprobe.exe (default: the one on PATH)")
    a = ap.parse_args()

    built = build_engine()
    engine = TAURI / "engine"
    shutil.rmtree(engine, ignore_errors=True)
    shutil.copytree(built, engine)
    for folder in ("fonts", "templates"):
        shutil.copytree(ROOT / folder, engine / folder)

    src = find_ffmpeg_dir(a.ffmpeg_dir)
    ffmpeg = TAURI / "ffmpeg"
    shutil.rmtree(ffmpeg, ignore_errors=True)
    ffmpeg.mkdir(parents=True)
    for name in ("ffmpeg.exe", "ffprobe.exe"):
        if not (src / name).is_file():
            sys.exit(f"{name} not found in {src}")
        shutil.copy2(src / name, ffmpeg / name)
    for lic in (src / "LICENSE", src.parent / "LICENSE", src.parent / "LICENSE.txt"):
        if lic.is_file():
            shutil.copy2(lic, ffmpeg / "FFMPEG-LICENSE.txt")
            break
    (ffmpeg / "FFMPEG-SOURCE.txt").write_text(
        "FFmpeg is a separate program shipped next to Vedit Studio, under its own license (FFMPEG-LICENSE.txt).\n"
        "Source code: https://ffmpeg.org/download.html\n"
        f"These binaries came from: {src}\n", encoding="utf-8")
    print(f"engine -> {engine}\nffmpeg -> {ffmpeg}")


if __name__ == "__main__":
    main()
