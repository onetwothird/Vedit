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
"""
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
FONTS = ROOT.parent / "fonts"
BG = ROOT / "brand" / "card_background.png"
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


def render(spec, out):
    img = Image.open(BG).convert("RGB").resize((W, H))
    d = ImageDraw.Draw(img)
    y = 250
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
        y = draw_lines(d, lines, f, y, 62, color=SOFT, shadow=0) + 40
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
                render(sc["card"], sp.parent / sc["image"])


if __name__ == "__main__":
    main()
