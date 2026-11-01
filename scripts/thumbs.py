#!/usr/bin/env python3
"""Thumbnail candidates: finds the sharpest, best-lit frames with a clear face (if OpenCV is installed),
spread across the video, and optionally builds a ready 1280x720 thumbnail with bold title text.

  python thumbs.py VIDEO [--count 4] [--samples 80]
  python thumbs.py VIDEO --title "I QUIT MY JOB" [--pick 2] [--color "#FFD400"] [--aspect 16:9|9:16]
Outputs into <video>.edit/thumbs/: candidate_N.jpg (full res), candidates_sheet.jpg (Read this), thumbnail.jpg
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import die, ffprobe, need, run, ts, workdir_for  # noqa: E402

try:
    import numpy as np
except ImportError:
    die("numpy required: pip install numpy")


_INFO: dict = {}


def gray(video: str, t: float, w: int = 320) -> "np.ndarray | None":
    info = _INFO.setdefault(video, ffprobe(video))
    h = int(info["height"] * w / info["width"]) // 2 * 2
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", video, "-frames:v", "1",
                          "-vf", f"scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                         capture_output=True).stdout
    return np.frombuffer(raw[: w * h], np.uint8).reshape(h, w).astype(np.float32) if len(raw) >= w * h else None


def score(img: "np.ndarray", det) -> tuple[float, dict]:
    lap = (img[1:-1, 1:-1] * 4 - img[:-2, 1:-1] - img[2:, 1:-1] - img[1:-1, :-2] - img[1:-1, 2:])
    sharp = float(lap.var())
    mean = float(img.mean())
    contrast = float(img.std())
    expo = 1.0 - min(1.0, abs(mean - 120) / 120)
    face = 0.0
    if det is not None:
        faces = det.detectMultiScale(img.astype(np.uint8), 1.1, 6, minSize=(img.shape[1] // 14,) * 2)
        if len(faces):
            fw = max(f[2] for f in faces)
            face = min(1.0, fw / (img.shape[1] * 0.25))  # bigger face = better thumbnail
    s = np.log1p(sharp) * 1.0 + expo * 2.0 + contrast / 40 + face * 3.0
    return float(s), {"sharp": round(sharp, 1), "exposure": round(expo, 2), "face": round(face, 2)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--count", type=int, default=4)
    ap.add_argument("--samples", type=int, default=80)
    ap.add_argument("--title", help="text for the finished thumbnail (keep it 2-5 words)")
    ap.add_argument("--pick", type=int, default=1, help="which candidate to use for the thumbnail")
    ap.add_argument("--color", default="#FFFFFF", help="title color")
    ap.add_argument("--font", default="Arial")
    ap.add_argument("--aspect", default="16:9")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    need("ffmpeg")

    from reframe import _detector
    _, det = _detector()
    dur = ffprobe(a.video)["duration"]
    outdir = Path(a.out) if a.out else workdir_for(a.video) / "thumbs"
    outdir.mkdir(parents=True, exist_ok=True)

    if not a.title or not list(outdir.glob("candidate_*.jpg")):
        scored = []
        for k in range(a.samples):
            t = dur * (k + 0.5) / a.samples
            g = gray(a.video, t)
            if g is None or g.mean() < 15:
                continue
            sc, parts = score(g, det)
            scored.append((sc, t, parts))
            print(f"\r[thumbs] scanning {k + 1}/{a.samples}", end="", file=sys.stderr)
        print(file=sys.stderr)
        scored.sort(reverse=True)
        picks, gap = [], dur / (a.count * 2 + 1)
        for sc, t, parts in scored:  # keep variety: candidates far apart in time
            if all(abs(t - p[1]) > gap for p in picks):
                picks.append((sc, t, parts))
            if len(picks) == a.count:
                break
        for old in outdir.glob("candidate_*.jpg"):
            old.unlink()
        for i, (sc, t, parts) in enumerate(picks, 1):
            run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", a.video, "-frames:v", "1", "-q:v", "2",
                 str(outdir / f"candidate_{i}.jpg")])
            print(f"candidate_{i}.jpg  at {ts(t)}  score={sc:.2f} {parts}")
        from frames import sheets
        sh = sheets(a.video, [p[1] for p in picks], outdir, 480, 2, False, "candidates_sheet")
        print(f"sheet: {sh[0]}")

    if a.title:
        src = outdir / f"candidate_{a.pick}.jpg"
        if not src.exists():
            die(f"{src} not found — run without --title first")
        W, H = (1280, 720) if a.aspect == "16:9" else (1080, 1920)
        text = a.title.upper().replace("'", "’").replace(":", "\\:")
        fs = int(H * (0.16 if a.aspect == "16:9" else 0.075))
        fs = min(fs, int(W * 1.6 / max(1, len(text))))  # shrink long titles to fit
        c = a.color.lstrip("#")
        vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},eq=contrast=1.12:saturation=1.25,"
              f"drawbox=x=0:y=ih*0.62:w=iw:h=ih*0.38:color=black@0.45:t=fill,"
              f"drawtext=font='{a.font}':text='{text}':fontsize={fs}:fontcolor=0x{c}:borderw={max(4, fs // 12)}:"
              f"bordercolor=black:x=(w-text_w)/2:y=h*0.80-text_h/2")
        out = outdir / "thumbnail.jpg"
        run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vf", vf, "-frames:v", "1", "-q:v", "2", str(out)])
        print(f"thumbnail: {out}")


if __name__ == "__main__":
    main()
