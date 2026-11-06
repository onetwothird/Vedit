#!/usr/bin/env python3
"""Motion-graphics animation for a flat 'fact card' image.

Given the card and the clean shared background (same template, no content), the card's content is split into
horizontal element bands (badge, headline, illustration, mascot...). Each element pops in with a springy
overshoot (staggered), bobs/breathes while on screen, pulses on every narration beat, and snaps out at the end.
The background keeps a slow continuous drift across scenes (pass the scene's global start time), so hard cuts
between scenes look like one continuous piece.

  python animate_card.py CARD BG OUT.mp4 --duration 6.2 [--beats 0,2.1,4.0] [--t0 0] [--fps 30]

Used automatically by story.py for scenes with "animate": true.
"""
from __future__ import annotations

import argparse
import math
import subprocess

import numpy as np
from PIL import Image

try:
    import cv2  # type: ignore
except ImportError:  # pragma: no cover
    cv2 = None


def ease_out_back(p: float, s: float = 1.9) -> float:
    p = min(max(p, 0.0), 1.0) - 1
    return 1 + (s + 1) * p ** 3 + s * p ** 2


def ease_out_cubic(p: float) -> float:
    p = min(max(p, 0.0), 1.0)
    return 1 - (1 - p) ** 3


def ease_in_cubic(p: float) -> float:
    p = min(max(p, 0.0), 1.0)
    return p ** 3


def _resize(img: np.ndarray, w: int, h: int) -> np.ndarray:
    if cv2 is not None:
        return cv2.resize(img, (w, h), interpolation=cv2.INTER_LINEAR)
    return np.asarray(Image.fromarray(img).resize((w, h), Image.BILINEAR))


def extract_elements(card: np.ndarray, bg: np.ndarray, keep_top: int = 200, keep_bottom: int = 25,
                     gap: int = 26, min_h: int = 34):
    """-> list of elements {rgba, x, y, w, h, kind} cut out of the card (content = pixels that differ from bg)."""
    H, W = card.shape[:2]
    d = np.abs(card.astype(np.int16) - bg.astype(np.int16)).sum(-1)
    a = np.clip((d - 14) / 36.0, 0, 1)
    a[:keep_top] = 0           # shared header stays part of the background
    a[H - keep_bottom:] = 0    # flag stripe too
    if cv2 is not None:        # drop isolated JPEG-noise specks
        solid = (a > 0.5).astype(np.uint8)
        n, lab, stats, _ = cv2.connectedComponentsWithStats(solid, 8)
        small = np.isin(lab, [i for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] < 25])
        a[small] = 0
        a = np.minimum(a, cv2.dilate((a > 0.5).astype(np.uint8), np.ones((7, 7), np.uint8)).astype(np.float32))
    rows = np.where(a.max(1) > 0.5)[0]
    if not len(rows):
        return []
    bands, s, p = [], rows[0], rows[0]
    for r in rows[1:]:
        if r - p > gap:
            bands.append([s, p])
            s = r
        p = r
    bands.append([s, p])
    merged = []
    for b in bands:  # tiny bands (a wave line, an underline) belong to the element above
        if merged and b[1] - b[0] < min_h:
            merged[-1][1] = b[1]
        else:
            merged.append(b)
    els = []
    for y0, y1 in merged:
        y0, y1 = max(0, y0 - 8), min(H, y1 + 9)
        cols = np.where(a[y0:y1].max(0) > 0.3)[0]
        x0, x1 = max(0, cols[0] - 8), min(W, cols[-1] + 9)
        rgba = np.dstack([card[y0:y1, x0:x1], (a[y0:y1, x0:x1] * 255).astype(np.uint8)])
        els.append({"rgba": rgba, "x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0})
    for i, e in enumerate(els):
        cx = e["x"] + e["w"] / 2
        if e["y"] > H * 0.72 and (cx > W * 0.62 or cx < W * 0.38):
            e["kind"] = "mascot"
        elif i == 0 and e["h"] < 140:
            e["kind"] = "badge"
        elif e["h"] < 420 and i <= 2 and not any(x.get("kind") == "headline" for x in els[:i]):
            e["kind"] = "headline"
        else:
            e["kind"] = "art"
    return els


def blend(frame: np.ndarray, rgba: np.ndarray, x: int, y: int, alpha_mul: float):
    h, w = rgba.shape[:2]
    H, W = frame.shape[:2]
    fx0, fy0, fx1, fy1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
    if fx0 >= fx1 or fy0 >= fy1:
        return
    src = rgba[fy0 - y:fy1 - y, fx0 - x:fx1 - x]
    a = src[..., 3:4].astype(np.float32) * (alpha_mul / 255.0)
    dst = frame[fy0:fy1, fx0:fx1].astype(np.float32)
    frame[fy0:fy1, fx0:fx1] = (src[..., :3] * a + dst * (1 - a)).astype(np.uint8)


def bg_frame(bg: np.ndarray, tg: float, punch: float, center=(540, 565)) -> np.ndarray:
    """Slow continuous drift (global time) + a short zoom punch at each scene start."""
    z = 1.03 + 0.015 * math.sin(tg * 2 * math.pi / 14) + punch
    H, W = bg.shape[:2]
    cx, cy = center
    M = np.float32([[z, 0, cx - z * cx], [0, z, cy - z * cy]])
    if cv2 is not None:
        return cv2.warpAffine(bg, M, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    return np.asarray(Image.fromarray(bg).resize((round(W * z), round(H * z))))[:H, :W].copy()


def render(card_path, bg_path, out, duration: float, beats=(), t0: float = 0.0, fps: int = 30,
           stagger: float = 0.13, outro: float = 0.16, crf: int = 17, ffmpeg: str = "ffmpeg"):
    card = np.asarray(Image.open(card_path).convert("RGB"))
    bg = np.asarray(Image.open(bg_path).convert("RGB"))
    if bg.shape != card.shape:
        bg = np.asarray(Image.fromarray(bg).resize((card.shape[1], card.shape[0]), Image.BICUBIC))
    H, W = card.shape[:2]
    els = extract_elements(card, bg)
    beats = sorted(b for b in beats if 0.15 < b < duration - 0.3)
    order = 0
    for e in els:  # entrance times: badge -> headline -> art (staggered); mascot peeks in a bit later
        if e["kind"] == "mascot":
            e["t_in"] = min(0.55, duration * 0.3)
        else:
            e["t_in"] = 0.04 + stagger * order
            order += 1
    n = max(1, round(duration * fps))
    cmd = [ffmpeg, "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps),
           "-i", "-", "-c:v", "libx264", "-preset", "medium", "-crf", str(crf), "-pix_fmt", "yuv420p",
           "-movflags", "+faststart", str(out)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for f in range(n):
        t = f / fps
        punch = 0.035 * (1 - ease_out_cubic(t / 0.35))
        frame = bg_frame(bg, t0 + t, punch)
        out_p = ease_in_cubic((t - (duration - outro)) / outro) if outro else 0.0
        for i, e in enumerate(els):
            p = (t - e["t_in"]) / 0.42
            if p <= 0:
                continue
            if e["kind"] == "mascot":  # slides up from below + gentle bob
                dy = (1 - ease_out_back(p, 1.4)) * (H - e["y"]) + 7 * math.sin((t - e["t_in"]) * 3.2)
                s, rot_alpha = 1.0, 1.0
            else:
                s = 0.62 + 0.38 * ease_out_back(p)
                dy = (1 - ease_out_cubic(p)) * 70
                rot_alpha = min(1.0, p * 3)
                live = t - e["t_in"]
                if e["kind"] == "art":
                    dy += 9 * math.sin(live * 2.4 + i)
                    s *= 1 + 0.012 * math.sin(live * 1.7 + i)
                else:
                    s *= 1 + 0.006 * math.sin(live * 2.0 + i)
                for b in beats:  # emphasis pulse on each new narration line
                    u = (t - b) / 0.32
                    if 0 <= u <= 1 and e["kind"] in ("headline", "art"):
                        s *= 1 + (0.055 if e["kind"] == "headline" else 0.04) * math.sin(math.pi * u)
            s *= 1 + 0.08 * out_p
            alpha = rot_alpha * (1 - out_p)
            if alpha <= 0.01:
                continue
            w, h = max(1, round(e["w"] * s)), max(1, round(e["h"] * s))
            img = e["rgba"] if (w, h) == (e["w"], e["h"]) else _resize(e["rgba"], w, h)
            cx, cy = e["x"] + e["w"] / 2, e["y"] + e["h"] / 2 + dy
            blend(frame, img, round(cx - w / 2), round(cy - h / 2), alpha)
        proc.stdin.write(frame.tobytes())
    proc.stdin.close()
    if proc.wait() != 0:
        raise RuntimeError(f"ffmpeg failed while animating {card_path}")
    return [{k: e[k] for k in ("kind", "x", "y", "w", "h", "t_in")} for e in els]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("card")
    ap.add_argument("bg")
    ap.add_argument("out")
    ap.add_argument("--duration", type=float, required=True)
    ap.add_argument("--beats", default="")
    ap.add_argument("--t0", type=float, default=0.0)
    ap.add_argument("--fps", type=int, default=30)
    a = ap.parse_args()
    beats = [float(b) for b in a.beats.split(",") if b.strip()]
    for e in render(a.card, a.bg, a.out, a.duration, beats, a.t0, a.fps):
        print(e)


if __name__ == "__main__":
    main()
