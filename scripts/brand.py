#!/usr/bin/env python3
"""Brand kit for Fact Reels: one brand.json gives every card, thumbnail and video the same look.

  python brand.py path/to/brand.json        # (re)generate the card background if it is missing

brand.json (paths are relative to brand.json; every key is optional):
{
  "name": "My Fact Channel",              # printed in the header of the generated background
  "tagline": "Isang fact araw-araw",      # small line under the name (generated background only)
  "background": "brand/card_background.png",   # 1080x1920 empty card; generated if the file is missing
  "colors": {"accent": "#FFC72C", "accent_dark": "#C9961A", "badge_text": "#0F1A3C", "shadow": "#0B1A50",
             "text": "#FFFFFF", "soft": "#DCE6FF", "sticker": "#D7263D",
             "bg_top": "#16307A", "bg_bottom": "#0A1640"},
  "fonts": {"ExtraBold": "../../fonts/Poppins-ExtraBold.ttf", "SemiBold": "../../fonts/Poppins-SemiBold.ttf"},
  "art_dir": "assets/art",                # pictures you name in a card's "art"
  "mascot_peek": "assets/art/mascot_peek.png",   # small character for "mascot": true on cards (optional)
  "mascot": "brand/mascot.png",           # big character on the thumbnail (optional)
  "header_overlay": "brand/header.png",   # transparent PNG of just the logo/header (1080 wide, top-aligned), used on photo cards (optional)
  "sfx_dir": "assets/sfx",                # whoosh/ding/pop are generated here
  "content_top": 395,                     # y where card content starts (just under your header)
  "header_bottom": 360                    # rows above this are the static header (never animated)
}
A story finds its brand.json by looking in its own folder, then each parent folder.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
W, H = 1080, 1920

DEFAULT_COLORS = {
    "accent": "#FFC72C", "accent_dark": "#C9961A", "badge_text": "#0F1A3C", "shadow": "#0B1A50",
    "text": "#FFFFFF", "soft": "#DCE6FF", "sticker": "#D7263D", "sticker_text": "#FF5A6E",
    "night": "#0a1a4a", "shade": "#03060f", "credit": "#AEB9D6", "frame": "#F4F1E8",
    "panel": "#2A4597", "panel_edge": "#3A5BB8", "bg_top": "#16307A", "bg_bottom": "#0A1640",
}


def find_brand(start: Path) -> Path | None:
    """brand.json in `start` (a story folder or file) or any parent folder."""
    p = Path(start).resolve()
    for d in [p] + list(p.parents):
        if d.is_dir() and (d / "brand.json").exists():
            return d / "brand.json"
    return None


def load(path: Path | None) -> dict:
    """-> brand dict with absolute paths and every default filled in."""
    raw = json.loads(Path(path).read_text(encoding="utf-8")) if path else {}
    base = Path(path).resolve().parent if path else REPO
    rel = lambda v: (base / v).resolve() if v else None  # noqa: E731
    fonts = {"ExtraBold": REPO / "fonts" / "Poppins-ExtraBold.ttf",
             "SemiBold": REPO / "fonts" / "Poppins-SemiBold.ttf",
             "Bold": REPO / "fonts" / "Poppins-Bold.ttf"}
    fonts.update({k: rel(v) for k, v in (raw.get("fonts") or {}).items()})
    b = {
        "file": Path(path).resolve() if path else None,
        "dir": base,
        "name": raw.get("name", "My Fact Channel"),
        "tagline": raw.get("tagline", ""),
        "background": rel(raw.get("background", "brand/card_background.png")),
        "colors": {**DEFAULT_COLORS, **(raw.get("colors") or {})},
        "fonts": fonts,
        "art_dir": rel(raw.get("art_dir", "assets/art")),
        "mascot_peek": rel(raw.get("mascot_peek")),
        "mascot": rel(raw.get("mascot")),
        "header_overlay": rel(raw.get("header_overlay")),
        "sfx_dir": rel(raw.get("sfx_dir", "assets/sfx")),
        "content_top": int(raw.get("content_top", 395)),
        "header_bottom": int(raw.get("header_bottom", 200)),
    }
    return b


def ensure_background(b: dict) -> Path:
    """Generate a clean card background (gradient + name header + accent stripe) when the file is missing."""
    p = b["background"]
    if p and p.exists():
        return p
    from PIL import Image, ImageDraw, ImageFont
    c = b["colors"]
    top, bot = _rgb(c["bg_top"]), _rgb(c["bg_bottom"])
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / (H - 1)
        d.line((0, y, W, y), fill=tuple(round(a + (z - a) * t) for a, z in zip(top, bot)))
    glow = Image.new("L", (W, H), 0)  # soft light behind the content area
    ImageDraw.Draw(glow).ellipse((-200, 380, W + 200, 1500), fill=60)
    from PIL import ImageFilter
    img = Image.composite(Image.new("RGB", (W, H), _mix(top, (255, 255, 255), 0.25)), img,
                          glow.filter(ImageFilter.GaussianBlur(160)))
    d = ImageDraw.Draw(img)
    fname = ImageFont.truetype(str(b["fonts"]["ExtraBold"]), 64)
    size = 64
    while d.textlength(b["name"], font=fname) > W - 160 and size > 30:
        size -= 4
        fname = ImageFont.truetype(str(b["fonts"]["ExtraBold"]), size)
    y = 215 if b["tagline"] else 245
    d.text((W / 2, y + 6), b["name"], font=fname, fill=c["shadow"], anchor="mm")
    d.text((W / 2, y), b["name"], font=fname, fill=c["text"], anchor="mm")
    if b["tagline"]:
        ft = ImageFont.truetype(str(b["fonts"]["SemiBold"]), 34)
        d.text((W / 2, y + 62), b["tagline"], font=ft, fill=c["accent"], anchor="mm")
    d.rounded_rectangle((W / 2 - 70, 330, W / 2 + 70, 338), radius=4, fill=c["accent"])
    d.rectangle((0, H - 17, W, H), fill=c["accent"])
    p.parent.mkdir(parents=True, exist_ok=True)
    img.save(p)
    print(f"[brand] generated background: {p}")
    return p


def _rgb(h: str):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _mix(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


if __name__ == "__main__":
    bp = Path(sys.argv[1]) if len(sys.argv) > 1 else find_brand(Path.cwd())
    print(ensure_background(load(bp)))
