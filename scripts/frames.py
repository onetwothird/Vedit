#!/usr/bin/env python3
"""Contact sheets so Claude can SEE the video. Each sheet = up to 12 timestamped frames.

Usage:
  python frames.py VIDEO --every 15                 # one frame every 15s across the whole video
  python frames.py VIDEO --start 60 --end 90 --count 8
  python frames.py VIDEO --at 12.5 47 1:03.2       # specific moments
  python frames.py VIDEO --every 30 --grid          # 10% vertical grid lines -> pick crop_x for 9:16 reframing
Then Read the printed .jpg paths.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import subprocess
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import ffprobe, need, parse_time, run, ts, workdir_for  # noqa: E402

PER_SHEET = 12
# Bundled font for the time labels: without an explicit fontfile, some Windows FFmpeg builds
# need a fontconfig file and crash.
_fonts = sorted((Path(__file__).resolve().parent.parent / "fonts").glob("Poppins-*.ttf"))
_FONT = (":fontfile='" + _fonts[0].as_posix().replace(":", "\\:") + "'") if _fonts else ""


def grab(video: str, t: float, out: Path, width: int, grid: bool):
    label = ts(t).replace(":", r"\:")
    vf = [f"scale={width}:-2"]
    if grid:
        vf.append("drawgrid=w=iw/10:h=ih:t=2:c=yellow@0.6")
    base = ",".join(vf)
    txt = (f",drawtext=text='{label}':x=8:y=8:fontsize={max(14, width // 22)}:fontcolor=white:"
           f"box=1:boxcolor=black@0.6:boxborderw=4{_FONT}")
    cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", video, "-frames:v", "1", "-q:v", "3"]
    if subprocess.run(cmd + ["-vf", base + txt, str(out)], capture_output=True).returncode != 0:
        run(cmd + ["-vf", base, str(out)])  # drawtext unusable here -> frame without the time label


def sheets(video: str, times: list[float], outdir: Path, width: int, cols: int, grid: bool, tag: str) -> list[Path]:
    need("ffmpeg")
    outdir.mkdir(parents=True, exist_ok=True)
    made = []
    for si in range(0, len(times), PER_SHEET):
        chunk = times[si:si + PER_SHEET]
        with tempfile.TemporaryDirectory() as td:
            for k, t in enumerate(chunk):
                grab(video, t, Path(td) / f"f{k:03d}.jpg", width, grid)
            n = len(chunk)
            c = min(cols, n)
            r = (n + c - 1) // c
            # pad the sequence so tile emits exactly one image
            for k in range(n, r * c):
                shutil.copy(Path(td) / "f000.jpg", Path(td) / f"f{k:03d}.jpg")
            out = outdir / f"{tag}_{si // PER_SHEET + 1:02d}_{ts(chunk[0]).replace(':', '-')}.jpg"
            run(["ffmpeg", "-y", "-v", "error", "-framerate", "1", "-i", str(Path(td) / "f%03d.jpg"),
                 "-vf", f"tile={c}x{r}:padding=4:color=black", "-frames:v", "1", "-q:v", "3", str(out)])
            made.append(out)
    return made


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--every", type=float, help="seconds between frames")
    ap.add_argument("--at", nargs="+", help="explicit timestamps")
    ap.add_argument("--start", default="0")
    ap.add_argument("--end", default=None)
    ap.add_argument("--count", type=int, default=12)
    ap.add_argument("--width", type=int, default=480, help="width of each thumbnail")
    ap.add_argument("--cols", type=int, default=4)
    ap.add_argument("--grid", action="store_true", help="overlay 10%% vertical grid (for choosing crop_x)")
    ap.add_argument("--out", default=None, help="output dir (default <video>.edit/frames)")
    ap.add_argument("--tag", default="sheet")
    a = ap.parse_args()

    dur = ffprobe(a.video)["duration"]
    if a.at:
        times = [parse_time(x) for x in a.at]
    else:
        s = parse_time(a.start)
        e = min(parse_time(a.end), dur) if a.end else dur
        e = max(s, e - 0.05)
        if a.every:
            times, t = [], s + min(a.every / 2, (e - s) / 2)
            while t < e:
                times.append(t)
                t += a.every
        else:
            n = max(1, a.count)
            times = [s + (e - s) * (k + 0.5) / n for k in range(n)]
    times = [min(max(0.0, t), max(0.0, dur - 0.05)) for t in times]
    outdir = Path(a.out) if a.out else workdir_for(a.video) / "frames"
    for p in sheets(a.video, times, outdir, a.width, a.cols, a.grid, a.tag):
        print(p)


if __name__ == "__main__":
    main()
