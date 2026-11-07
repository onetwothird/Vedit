"""Render the 'card' specs inside a story.json into card images (same look as the hand-made cards).

    python tools/make_cards.py stories/004_xxx/story.json      # one story
    python tools/make_cards.py                                  # every story

A scene with a "card" block gets its "image" file (re)drawn:
  {"image": "cards/5.jpg",
   "card": {"badge": "AT ALAM MO RIN BA?",           # yellow pill on top
            "headline": "Umabot pa sa *kalawakan!*",  # *stars* = yellow highlight
            "big": "1985",                            # optional huge yellow text (numbers, names)
            "sub": "Sakay ng Space Shuttle Discovery",# optional small line(s)
            "art": "yoyo_red", "art_scale": 0.8,      # optional picture: assets/art/<name>.png, or "stars3"
            "mascot": true}}                          # Juan peeks from the bottom-right
Scenes without "card" keep their existing hand-made image.

Darker looks (e.g. mysteries) - optional card keys:
  "bg": "dark"                      # night version of the brand background
  "photo": "photos/x.jpg"           # a real photo (path relative to the story folder)
  "photo_mode": "full" | "frame"    # full = graded full-screen backdrop; frame = framed picture under the text
  "photo_focus": [0.5, 0.5]         # which part of the photo to keep when cropping (full mode)
  "credit": "Photo: ... (CC BY 2.0)"  # small static line at the bottom (licence attribution)
These cards also write "_plate_<name>.jpg" (the same card without its content). Point the scene's
"background" at it so the animation pops the text in over the photo.
"""
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parent.parent
FONTS = ROOT.parent / "fonts"
BG = ROOT / "brand" / "card_background_v2.png"  # header sits lower (clear of the TikTok top bar)
CONTENT_TOP = 395  # first line of card content, just under the header
ART = ROOT / "assets" / "art"
W, H = 1080, 1920
RED_FALLBACK = "#D7263D"
YELLOW, YELLOW_DARK, NAVY, SHADOW, WHITE, SOFT = "#FFC72C", "#C9961A", "#0F1A3C", "#0B1A50", "#FFFFFF", "#DCE6FF"
SAFE_BOTTOM = 1390  # below this the subtitles live
MAX_W = 920


def font(weight, size):
    return ImageFont.truetype(str(FONTS / f"Poppins-{weight}.ttf"), size)


def parse_marked(text):
    """'Ang *YO-YO* ay' -> [(word, highlighted)]"""
    out, hi = [], False
    for tok in text.split():
        start = tok.startswith("*")
        if start:
            hi = True
            tok = tok[1:]
        end = tok.endswith("*") or tok.rstrip("!?.,").endswith("*")
        if end:
            tok = tok.replace("*", "")
        out.append((tok, hi))
        if end:
            hi = False
    return out


def wrap(words, f, max_w, d):
    lines, cur = [], []
    for w in words:
        test = " ".join(x for x, _ in cur + [w])
        if cur and d.textlength(test, font=f) > max_w:
            lines.append(cur)
            cur = []
        cur.append(w)
    if cur:
        lines.append(cur)
    return lines


def draw_lines(d, lines, f, y, line_h, color=WHITE, shadow=7):
    for ln in lines:
        total = d.textlength(" ".join(w for w, _ in ln), font=f)
        x = (W - total) / 2
        sp = d.textlength(" ", font=f)
        for w, hi in ln:
            if shadow:
                d.text((x, y + shadow), w, font=f, fill=SHADOW)
            d.text((x, y), w, font=f, fill=YELLOW if hi else color)
            x += d.textlength(w, font=f) + sp
        y += line_h
    return y


def builtin_art(name):
    """Simple vector art drawn in code (no image file needed)."""
    if name == "stars3":
        import math
        im = Image.new("RGBA", (900, 300), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        for cx in (150, 450, 750):
            pts = [(cx + (r := 130 if k % 2 == 0 else 52) * math.sin(k * math.pi / 5),
                    165 - r * math.cos(k * math.pi / 5)) for k in range(10)]
            d.polygon([(x, y + 10) for x, y in pts], fill=SHADOW)
            d.polygon(pts, fill=YELLOW)
        return im
    return None


def _header_layer(bg):
    """The 'Juan Fact a Day' header from the brand background, cut out as RGBA (so it can sit on any plate)."""
    top = bg.crop((0, 0, W, 380))
    smooth = top.filter(ImageFilter.GaussianBlur(28))
    diff = ImageOps.grayscale(Image.fromarray(
        np.abs(np.asarray(top, "int16") - np.asarray(smooth, "int16")).clip(0, 255).astype("uint8")))
    mask = diff.point(lambda v: 255 if v > 45 else int(v * 255 / 45) if v > 18 else 0).filter(ImageFilter.MaxFilter(3))
    layer = top.convert("RGBA")  # header band (rows 0-380)
    layer.putalpha(mask)
    return layer


def _cover(photo, focus=(0.5, 0.5)):
    s = max(W / photo.width, H / photo.height)
    im = photo.resize((round(photo.width * s), round(photo.height * s)), Image.LANCZOS)
    x = round((im.width - W) * focus[0])
    y = round((im.height - H) * focus[1])
    return im.crop((x, y, x + W, y + H))


def _shade(img, top=(0, 430, 0.85), bottom=(1300, H, 0.88), vignette=0.55):
    """Darken top/bottom (header + subtitle room) and the edges so text stays readable on photos."""
    shade = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(shade)
    for y in range(H):
        a = 0.0
        if y < top[1]:
            a = top[2] * (1 - y / top[1])
        if y > bottom[0]:
            a = max(a, bottom[2] * (y - bottom[0]) / (bottom[1] - bottom[0]))
        d.line((0, y, W, y), fill=int(a * 255))
    vig = Image.new("L", (W, H), 0)
    ImageDraw.Draw(vig).ellipse((-W * 0.35, -H * 0.2, W * 1.35, H * 1.2), fill=255)
    vig = ImageOps.invert(vig.filter(ImageFilter.GaussianBlur(220))).point(lambda v: int(v * vignette))
    mask = Image.fromarray(np.maximum(np.asarray(shade), np.asarray(vig)))
    return Image.composite(Image.new("RGB", (W, H), "#03060f"), img, mask)


def make_plate(spec, base):
    """Background for a card: the brand template, or a night/photo version of it (+ credit line)."""
    bg = Image.open(BG).convert("RGB").resize((W, H))
    dark = spec.get("bg") == "dark"
    photo = spec.get("photo")
    if not (dark or photo or spec.get("credit")):
        return bg, False
    if photo and spec.get("photo_mode", "full") == "full":
        p = Image.open(base / photo).convert("RGB")
        p = _cover(p, spec.get("photo_focus", (0.5, 0.5)))
        p = ImageEnhance.Color(p).enhance(0.45)
        p = ImageEnhance.Brightness(p).enhance(0.62)
        p = Image.blend(p, Image.new("RGB", (W, H), "#0a1a4a"), 0.22)
        plate = _shade(p)
    elif dark:
        plate = ImageEnhance.Brightness(ImageEnhance.Color(bg).enhance(0.55)).enhance(0.42)
        plate = _shade(plate, top=(0, 300, 0.5), bottom=(1400, H, 0.6), vignette=0.5)
    else:  # brand background as-is (framed photos sit on top of it)
        plate = bg.copy()
    if dark or (photo and spec.get("photo_mode", "full") == "full"):
        plate.paste(_header_layer(bg), (0, 0), _header_layer(bg))
        plate.paste(bg.crop((0, H - 17, W, H)), (0, H - 17))  # flag stripe
    if spec.get("credit"):
        d = ImageDraw.Draw(plate)
        f = font("SemiBold", 24)
        d.text((W / 2, H - 46), spec["credit"], font=f, fill="#AEB9D6", anchor="mm")
    return plate, True


def framed(photo_path, max_w, max_h):
    """Photo with a white border + soft shadow, as RGBA."""
    p = Image.open(photo_path).convert("RGB")
    s = min(max_w / p.width, max_h / p.height)
    p = p.resize((max(1, round(p.width * s)) - 24, max(1, round(p.height * s)) - 24), Image.LANCZOS)
    b = 12
    im = Image.new("RGBA", (p.width + 2 * b + 30, p.height + 2 * b + 30), (0, 0, 0, 0))
    sh = Image.new("L", im.size, 0)
    ImageDraw.Draw(sh).rounded_rectangle((18, 22, p.width + 2 * b + 18, p.height + 2 * b + 22), radius=18, fill=170)
    im.paste((3, 6, 15, 255), (0, 0), sh.filter(ImageFilter.GaussianBlur(9)))
    ImageDraw.Draw(im).rounded_rectangle((6, 6, p.width + 2 * b + 6, p.height + 2 * b + 6), radius=16, fill="#F4F1E8")
    im.paste(p, (b + 6, b + 6))
    return im


def render(spec, out, base=None):
    base = Path(base) if base else out.parent.parent
    img, custom = make_plate(spec, base)
    if custom:
        img.save(out.with_name(f"_plate_{out.stem}.jpg"), quality=94)
        img = img.copy()
    d = ImageDraw.Draw(img)
    y = spec.get("top", CONTENT_TOP)
    if spec.get("badge"):
        f = font("ExtraBold", 50)
        tw = d.textlength(spec["badge"], font=f)
        x0, x1 = (W - tw) / 2 - 40, (W + tw) / 2 + 40
        d.rounded_rectangle((x0, y + 8, x1, y + 100), radius=46, fill=YELLOW_DARK)
        d.rounded_rectangle((x0, y, x1, y + 92), radius=46, fill=YELLOW)
        d.text((W / 2, y + 46), spec["badge"], font=f, fill=NAVY, anchor="mm")
        y += 145
    if spec.get("big"):
        size = spec.get("big_size", 250)
        f = font("ExtraBold", size)
        while d.textlength(spec["big"], font=f) > MAX_W and size > 80:
            size -= 8
            f = font("ExtraBold", size)
        top = d.textbbox((0, 0), spec["big"], font=f)[1]
        d.text((W / 2 + 0, y - top + 10), spec["big"], font=f, fill=SHADOW, anchor="ma")
        d.text((W / 2, y - top), spec["big"], font=f, fill=YELLOW, anchor="ma")
        y += d.textbbox((0, 0), spec["big"], font=f)[3] - top + 40
    if spec.get("headline"):
        size = spec.get("headline_size", 84)
        f = font("ExtraBold", size)
        lines = wrap(parse_marked(spec["headline"]), f, MAX_W, d)
        y = draw_lines(d, lines, f, y, round(size * 1.16)) + 30
    if spec.get("sub"):
        f = font("SemiBold", spec.get("sub_size", 46))
        lines = wrap(parse_marked(spec["sub"]), f, MAX_W - 40, d)
        y = draw_lines(d, lines, f, y, 62, color=SOFT, shadow=spec.get("sub_shadow", 0)) + 40
    if spec.get("photo") and spec.get("photo_mode", "full") == "frame":
        room_h = SAFE_BOTTOM - y - 10
        fr = framed(base / spec["photo"], round(spec.get("photo_w", 900)), max(room_h, 200))
        img.paste(fr, ((W - fr.width) // 2, y + max(0, (room_h - fr.height) // 2)), fr)
    if spec.get("lineup"):  # row of people photos, same look as the thumbnail lineup
        from make_thumbnail import draw_lineup
        y = draw_lineup(img, spec["lineup"], y + 30, base)
    if spec.get("art"):
        art = builtin_art(spec["art"]) or Image.open(ART / f"{spec['art']}.png").convert("RGBA")
        room_h = SAFE_BOTTOM - y - 20
        s = min(spec.get("art_scale", 1.0), 860 / art.width, max(room_h, 120) / art.height)
        art = art.resize((max(1, round(art.width * s)), max(1, round(art.height * s))), Image.LANCZOS)
        ay = y + max(0, (room_h - art.height) // 2) if spec.get("art_center", True) else y
        img.paste(art, ((W - art.width) // 2, ay), art)
    if spec.get("mascot"):
        m = Image.open(ART / "mascot_peek.png").convert("RGBA")
        img.paste(m, (W - m.width - 20, H - m.height - 17), m)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, quality=94)
    print("card:", out)


def main():
    paths = [Path(p) for p in sys.argv[1:]] or sorted((ROOT / "stories").glob("*/story.json"))
    for sp in paths:
        story = json.loads(sp.read_text(encoding="utf-8"))
        for sc in story["scenes"]:
            if sc.get("card"):
                render(sc["card"], sp.parent / sc["image"], sp.parent)


if __name__ == "__main__":
    main()
