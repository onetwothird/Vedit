#!/usr/bin/env python3
"""Find where the subject is, for reframing 16:9 footage to 9:16 / 1:1 / 4:5.

  python reframe.py VIDEO --start 10 --end 25     -> prints crop_x (0..1, horizontal center of the subject)

render.py calls auto_crop_x() for clips with "crop_x": "auto". Uses OpenCV face detection
(pip install opencv-python). If unavailable, falls back to 0.5 — then pick crop_x yourself from
`frames.py VIDEO --grid` (each yellow line = 0.1).
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import ffprobe  # noqa: E402

_warned = False


def _detector():
    try:
        import cv2  # type: ignore
        import numpy  # noqa: F401
    except ImportError:
        return None, None
    base = getattr(getattr(cv2, "data", None), "haarcascades", "") or ""
    for name in ("haarcascade_frontalface_default.xml", "haarcascade_frontalface_alt2.xml"):
        p = Path(base) / name
        if p.exists():
            return cv2, cv2.CascadeClassifier(str(p))
    return cv2, None


def auto_crop_x(video: str, start: float, end: float, samples: int = 7) -> float:
    global _warned
    cv2, det = _detector()
    if det is None:
        if not _warned:
            print("[reframe] OpenCV face detector unavailable -> crop_x=0.5 (pip install opencv-python)",
                  file=sys.stderr)
            _warned = True
        return 0.5
    import numpy as np
    info = ffprobe(video)
    w, h = info["width"], info["height"]
    sw = 640
    sh = int(h * sw / w) // 2 * 2
    xs = []
    for k in range(samples):
        t = start + (end - start) * (k + 0.5) / samples
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", video, "-frames:v", "1",
                              "-vf", f"scale={sw}:{sh}", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                             capture_output=True).stdout
        if len(raw) < sw * sh:
            continue
        img = np.frombuffer(raw[: sw * sh], np.uint8).reshape(sh, sw)
        faces = det.detectMultiScale(img, scaleFactor=1.1, minNeighbors=6, minSize=(sw // 20, sw // 20))
        if len(faces):
            x, y, fw, fh = max(faces, key=lambda f: f[2] * f[3])
            xs.append((x + fw / 2) / sw)
    return round(float(np.median(xs)), 3) if xs else 0.5


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--start", type=float, default=0)
    ap.add_argument("--end", type=float, default=None)
    a = ap.parse_args()
    end = a.end or ffprobe(a.video)["duration"]
    print(auto_crop_x(a.video, a.start, end))


if __name__ == "__main__":
    main()
