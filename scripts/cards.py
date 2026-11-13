#!/usr/bin/env python3
"""Fact cards + Reel cover (thumbnail) from a story.json, in the look of your brand.json.

  python cards.py cards     path/to/story.json [...]   # draw every scene's "card" into its image file
  python cards.py thumbnail path/to/story.json [...]   # draw thumbnail.jpg from the story's "thumbnail" block

Card keys (scene "card": {...}):
  "badge": "DID YOU KNOW?"             pill on top
  "big": "1985"                        huge accent text (numbers, names)
  "headline": "It reached *space!*"    *stars* = accent color
  "sub": "small explanation"           smaller line(s)
  "art": "rocket", "art_scale": 0.8    picture from brand art_dir (<name>.png), or the built-in "stars3"
  "mascot": true                       brand mascot_peek in the bottom-right
  "bg": "dark"                         night version of the background
  "photo": "photos/x.jpg", "photo_mode": "full" | "frame", "photo_focus": [0.5, 0.5], "credit": "Photo: ..."
  "lineup": [{"photo": "...", "label": "..."}, {"cross": true, "label": "..."}]
Where the card is written: the scene's "image" (photo scenes), or "card_image" (video scenes; default
cards/sceneNN.jpg). Cards with a custom plate (photo/dark/credit) also write _plate_<name>.jpg; point the scene's
"background" at it so only the text animates.

Thumbnail keys: badge, big, headline, sticker (tilted label), art, lineup, mascot (false = none), mascot_height.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

sys.path.insert(0, str(Path(__file__).resolve().parent))
import brand as brandkit  # noqa: E402

W, H = 1080, 1920
SAFE_BOTTOM = 1390  # below this the subtitles live
MAX_W = 920

# set by use_brand(); module-level so the drawing helpers stay simple
B: dict = {}
YELLOW = YELLOW_DARK = NAVY = SHADOW = WHITE = SOFT = RED = "#000000"
C: dict = {}


def use_brand(b: dict):
    global B, C, YELLOW, YELLOW_DARK, NAVY, SHADOW, WHITE, SOFT, RED
    B, C = b, b["colors"]
    YELLOW, YELLOW_DARK, NAVY, SHADOW = C["accent"], C["accent_dark"], C["badge_text"], C["shadow"]
    WHITE, SOFT, RED = C["text"], C["soft"], C["sticker"]


def brand_for(story_path: Path) -> dict:
    b = brandkit.load(brandkit.find_brand(Path(story_path).resolve().parent))
    brandkit.ensure_background(b)
    use_brand(b)
    return b


def font(weight, size):
    p = B["fonts"].get(weight) or B["fonts"]["ExtraBold"]
    return ImageFont.truetype(str(p), size)


def parse_marked(text):
    """'The *YO-YO* is' -> [(word, highlighted)]"""
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


def draw_lines(d, lines, f, y, line_h, color=None, shadow=7):
    color = color or WHITE
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
        im = Image.new("RGBA", (900, 300), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        for cx in (150, 450, 750):
            pts = [(cx + (r := 130 if k % 2 == 0 else 52) * math.sin(k * math.pi / 5),
                    165 - r * math.cos(k * math.pi / 5)) for k in range(10)]
            d.polygon([(x, y + 10) for x, y in pts], fill=SHADOW)
            d.polygon(pts, fill=YELLOW)
        return im
    return None


def load_art(name):
    art = builtin_art(name)
    if art is not None:
        return art
    p = B["art_dir"] / f"{name}.png"
    if not p.exists():
        sys.exit(f"art not found: {p}  (put a transparent PNG there, or remove \"art\")")
    return Image.open(p).convert("RGBA")


def open_photo(path):
    """Open a photo upright (phone photos store their rotation in EXIF)."""
    path = Path(path)
    if not path.exists():
        sys.exit(f"photo not found: {path}")
    return ImageOps.exif_transpose(Image.open(path)).convert("RGB")


def background():
    return Image.open(B["background"]).convert("RGB").resize((W, H))


def _header_layer(bg):
    """The header of the brand background, cut out as RGBA (so it can sit on any plate)."""
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
    return Image.composite(Image.new("RGB", (W, H), C["shade"]), img, mask)


def make_plate(spec, base):
    """Background for a card: the brand template, or a night/photo version of it (+ credit line)."""
    bg = background()
    dark = spec.get("bg") == "dark"
    photo = spec.get("photo")
    if not (dark or photo or spec.get("credit")):
        return bg, False
    if photo and spec.get("photo_mode", "full") == "full":
        p = open_photo(base / photo)
        p = _cover(p, spec.get("photo_focus", (0.5, 0.5)))
        p = ImageEnhance.Color(p).enhance(0.45)
        p = ImageEnhance.Brightness(p).enhance(0.62)
        p = Image.blend(p, Image.new("RGB", (W, H), C["night"]), 0.22)
        plate = _shade(p)
    elif dark:
        plate = ImageEnhance.Brightness(ImageEnhance.Color(bg).enhance(0.55)).enhance(0.42)
        plate = _shade(plate, top=(0, 300, 0.5), bottom=(1400, H, 0.6), vignette=0.5)
    else:  # brand background as-is (framed photos sit on top of it)
        plate = bg.copy()
    if dark or (photo and spec.get("photo_mode", "full") == "full"):
        ho = B.get("header_overlay")
        if ho and ho.exists():  # clean transparent logo from the brand (no leftover background)
            hl = Image.open(ho).convert("RGBA")  # full 1080x1920 overlay or just a header strip, pinned to the top
            hl = hl.resize((W, round(hl.height * W / hl.width)), Image.LANCZOS)
        else:
            hl = _header_layer(bg)
        plate.paste(hl, (0, 0), hl)
        plate.paste(bg.crop((0, H - 17, W, H)), (0, H - 17))  # bottom stripe
    if spec.get("credit"):
        d = ImageDraw.Draw(plate)
        f = font("SemiBold", 24)
        d.text((W / 2, H - 46), spec["credit"], font=f, fill=C["credit"], anchor="mm")
    return plate, True


def framed(photo_path, max_w, max_h):
    """Photo with a white border + soft shadow, as RGBA."""
    p = open_photo(photo_path)
    s = min(max_w / p.width, max_h / p.height)
    p = p.resize((max(1, round(p.width * s)) - 24, max(1, round(p.height * s)) - 24), Image.LANCZOS)
    b = 12
    im = Image.new("RGBA", (p.width + 2 * b + 30, p.height + 2 * b + 30), (0, 0, 0, 0))
    sh = Image.new("L", im.size, 0)
    ImageDraw.Draw(sh).rounded_rectangle((18, 22, p.width + 2 * b + 18, p.height + 2 * b + 22), radius=18, fill=170)
    im.paste((3, 6, 15, 255), (0, 0), sh.filter(ImageFilter.GaussianBlur(9)))
    ImageDraw.Draw(im).rounded_rectangle((6, 6, p.width + 2 * b + 6, p.height + 2 * b + 6), radius=16, fill=C["frame"])
    im.paste(p, (b + 6, b + 6))
    return im


def render_card(spec, out, base):
    out, base = Path(out), Path(base)
    out.parent.mkdir(parents=True, exist_ok=True)
    img, custom = make_plate(spec, base)
    if custom:
        img.save(out.with_name(f"_plate_{out.stem}.jpg"), quality=94)
        img = img.copy()
    d = ImageDraw.Draw(img)
    y = spec.get("top", B["content_top"])
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
        room_h = spec.get("safe_bottom", SAFE_BOTTOM) - y - 10  # per-card override: more room for captions
        fr = framed(base / spec["photo"], round(spec.get("photo_w", 900)), max(room_h, 200))
        img.paste(fr, ((W - fr.width) // 2, y + max(0, (room_h - fr.height) // 2)), fr)
    if spec.get("lineup"):  # row of people photos, same look as the thumbnail lineup
        y = draw_lineup(img, spec["lineup"], y + 30, base)
    if spec.get("art"):
        art = load_art(spec["art"])
        room_h = spec.get("safe_bottom", SAFE_BOTTOM) - y - 20
        s = min(spec.get("art_scale", 1.0), 860 / art.width, max(room_h, 120) / art.height)
        art = art.resize((max(1, round(art.width * s)), max(1, round(art.height * s))), Image.LANCZOS)
        ay = y + max(0, (room_h - art.height) // 2) if spec.get("art_center", True) else y
        img.paste(art, ((W - art.width) // 2, ay), art)
    if spec.get("mascot") and B.get("mascot_peek") and B["mascot_peek"].exists():
        m = Image.open(B["mascot_peek"]).convert("RGBA")
        img.paste(m, (W - m.width - 20, H - m.height - 17), m)
    img.save(out, quality=94)
    print("card:", out)


# ---------------- thumbnail ----------------
def sticker(text):
    f = font("ExtraBold", 64)
    tmp = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    tw = tmp.textlength(text, font=f)
    im = Image.new("RGBA", (int(tw + 110), 150), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((10, 18, im.width - 10, 138), radius=28, fill=SHADOW)
    d.rounded_rectangle((10, 8, im.width - 10, 128), radius=28, fill=RED)
    d.text((im.width / 2, 68), text, font=f, fill=WHITE, anchor="mm")
    return im.rotate(-6, expand=True, resample=Image.BICUBIC)


def silhouette(w, h):
    """Anonymous person shape (head + shoulders) on a dark card."""
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, w - 1, h - 1), radius=22, fill=SHADOW, outline=C["panel_edge"], width=5)
    hr = w * 0.22
    d.ellipse((w / 2 - hr, h * 0.16, w / 2 + hr, h * 0.16 + 2 * hr), fill=C["panel"])
    d.rounded_rectangle((w * 0.14, h * 0.56, w * 0.86, h + 40), radius=int(w * 0.3), fill=C["panel"])
    return im


def draw_lineup(img, items, y, base):
    """A row of people cards: {"cross": true, "label": "..."} silhouettes or {"photo": "...", "label": ...}."""
    n = len(items)
    gap = 18
    w = (W - 60 - gap * (n - 1)) // n
    h = round(w * 1.55)
    d = ImageDraw.Draw(img)
    f = font("ExtraBold", 38)
    acc = brandkit._rgb(YELLOW)
    for i, it in enumerate(items):
        x = 30 + i * (w + gap)
        if it.get("photo"):
            p = ImageOps.grayscale(open_photo(base / it["photo"])).convert("RGB")
            p = ImageOps.fit(p, (w - 16, h - 16), Image.LANCZOS, centering=(0.5, 0.35))
            dim = it.get("dim")
            if dim:  # remembered, not highlighted: darker photo, thin muted frame
                p = Image.blend(p, Image.new("RGB", p.size, SHADOW), 0.35)
            card = Image.new("RGBA", (w, h), C["panel_edge"] if dim else YELLOW)
            card.paste(p, (8, 8))
            m = Image.new("L", (w, h), 0)
            ImageDraw.Draw(m).rounded_rectangle((0, 0, w - 1, h - 1), radius=22, fill=255)
            if not dim:
                glow = Image.new("RGBA", (w + 60, h + 60), (0, 0, 0, 0))
                ImageDraw.Draw(glow).rounded_rectangle((20, 20, w + 40, h + 40), radius=30, fill=(*acc, 170))
                glow = glow.filter(ImageFilter.GaussianBlur(16))
                img.paste(glow, (x - 30, y - 30), glow)
            img.paste(card, (x, y), m)
        else:
            s = silhouette(w, h)
            img.paste(s, (x, y), s)
        if it.get("cross"):
            for a, b in (((x + 14, y + 14), (x + w - 14, y + h - 14)), ((x + w - 14, y + 14), (x + 14, y + h - 14))):
                d.line((a, b), fill=SHADOW, width=20)
                d.line((a, b), fill=RED, width=13)
        if it.get("label"):
            col = RED if (it.get("dim") or not it.get("photo")) else YELLOW
            fl = f
            while d.textlength(it["label"], font=fl) > w + 10 and fl.size > 20:
                fl = font("ExtraBold", fl.size - 2)
            d.text((x + w / 2, y + h + 34 + 4), it["label"], font=fl, fill=SHADOW, anchor="mm")
            d.text((x + w / 2, y + h + 34), it["label"], font=fl, fill=col if col != RED else C["sticker_text"],
                   anchor="mm")
    return y + h + 70


def render_thumbnail(spec, out, base=None):
    out = Path(out)
    base = Path(base) if base else out.parent
    img = background()
    d = ImageDraw.Draw(img)
    y = spec.get("top", 420)
    if spec.get("badge"):
        f = font("ExtraBold", 66)
        tw = d.textlength(spec["badge"], font=f)
        x0, x1 = (W - tw) / 2 - 50, (W + tw) / 2 + 50
        d.rounded_rectangle((x0, y + 10, x1, y + 122), radius=56, fill=YELLOW_DARK)
        d.rounded_rectangle((x0, y, x1, y + 112), radius=56, fill=YELLOW)
        d.text((W / 2, y + 56), spec["badge"], font=f, fill=NAVY, anchor="mm")
        y += 150
    if spec.get("big"):
        size = spec.get("big_size", 330)
        f = font("ExtraBold", size)
        while d.textlength(spec["big"], font=f) > MAX_W + 60 and size > 100:
            size -= 10
            f = font("ExtraBold", size)
        top = d.textbbox((0, 0), spec["big"], font=f)[1]
        d.text((W / 2, y - top + 14), spec["big"], font=f, fill=SHADOW, anchor="ma")
        d.text((W / 2, y - top), spec["big"], font=f, fill=YELLOW, anchor="ma")
        y += d.textbbox((0, 0), spec["big"], font=f)[3] - top + 30
    if spec.get("headline"):
        size = spec.get("headline_size", 100)
        f = font("ExtraBold", size)
        lines = wrap(parse_marked(spec["headline"]), f, MAX_W + 40, d)
        y = draw_lines(d, lines, f, y, round(size * 1.14), shadow=9) + 20
    if spec.get("lineup"):
        y = draw_lineup(img, spec["lineup"], y + 40, base)
    if spec.get("sticker"):
        st = sticker(spec["sticker"])
        img.paste(st, ((W - st.width) // 2, y), st)
        y += st.height
    out.parent.mkdir(parents=True, exist_ok=True)
    mascot = B.get("mascot")
    if spec.get("mascot") is False or not (mascot and mascot.exists()):
        if spec.get("art") and spec.get("mascot") is not False:  # no mascot in this brand: art, centered below
            art = load_art(spec["art"])
            room = H - y - 120
            if room > 120:
                s = min(spec.get("art_scale", 1.0) * 760 / art.width, room / art.height, 1.6)
                art = art.resize((max(1, round(art.width * s)), max(1, round(art.height * s))), Image.LANCZOS)
                img.paste(art, ((W - art.width) // 2, y + (room - art.height) // 2 + 20), art)
        img.save(out, quality=95)
        print("thumbnail:", out)
        return
    # mascot, big, bottom-right; the art bottom-left
    m = Image.open(mascot).convert("RGBA")
    mh = spec.get("mascot_height", 640)
    m = m.resize((round(m.width * mh / m.height), mh), Image.LANCZOS)
    my = max(y + 10, H - mh - 20)
    if spec.get("art"):
        art = load_art(spec["art"])
        s = min(spec.get("art_scale", 1.0) * 620 / art.width, (H - my - 60) / art.height, 1.6)
        art = art.resize((round(art.width * s), round(art.height * s)), Image.LANCZOS)
        img.paste(art, (40, H - art.height - 60), art)
    img.paste(m, (W - m.width + 15, my), m)
    img.save(out, quality=95)
    print("thumbnail:", out)


# ---------------- story helpers ----------------
def card_path(sc: dict, si: int) -> str:
    """Where a scene's card image lives (relative to the story folder)."""
    if sc.get("image"):
        return sc["image"]
    return sc.get("card_image") or f"cards/scene{si:02d}.jpg"


def cards_for_story(sp: Path):
    sp = Path(sp)
    brand_for(sp)
    story = json.loads(sp.read_text(encoding="utf-8"))
    for si, sc in enumerate(story["scenes"]):
        if sc.get("card"):
            render_card(sc["card"], sp.parent / card_path(sc, si), sp.parent)


def thumbnail_for_story(sp: Path):
    sp = Path(sp)
    brand_for(sp)
    story = json.loads(sp.read_text(encoding="utf-8"))
    if story.get("thumbnail"):
        render_thumbnail(story["thumbnail"], sp.parent / "thumbnail.jpg")


def main():
    if len(sys.argv) < 3 or sys.argv[1] not in ("cards", "thumbnail"):
        print(__doc__)
        return 2
    fn = cards_for_story if sys.argv[1] == "cards" else thumbnail_for_story
    for p in sys.argv[2:]:
        fn(Path(p))
    return 0


if __name__ == "__main__":
    sys.exit(main())
