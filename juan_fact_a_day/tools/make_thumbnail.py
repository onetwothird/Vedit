"""Make the Reel cover photo (thumbnail.jpg, 1080x1920) from the "thumbnail" block of a story.json.

    python tools/make_thumbnail.py stories/001_7641_na_isla/story.json   # one story
    python tools/make_thumbnail.py                                       # every story

  "thumbnail": {"badge": "ALAM MO BA?",           # yellow pill
                "big": "7,641",                   # huge yellow hook (number/name), optional
                "headline": "na isla ang *Pilipinas!*",   # *stars* = yellow
                "sticker": "HINDI NA 7,107!",     # tilted red label, optional
                "art": "islands_map"}             # assets/art/<name>.png, optional (left of Juan)
The hook text sits in the middle so it still reads when Facebook crops the cover to a square in the grid.
"""
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageOps

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_cards import (ART, BG, MAX_W, NAVY, RED_FALLBACK, SHADOW, WHITE, YELLOW, YELLOW_DARK,  # noqa: E402
                        builtin_art, draw_lines, font, parse_marked, wrap)

ROOT = Path(__file__).resolve().parent.parent
W, H = 1080, 1920
RED = RED_FALLBACK


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
    d.rounded_rectangle((0, 0, w - 1, h - 1), radius=22, fill="#0B1A50", outline="#3A5BB8", width=5)
    hr = w * 0.22
    d.ellipse((w / 2 - hr, h * 0.16, w / 2 + hr, h * 0.16 + 2 * hr), fill="#2A4597")
    d.rounded_rectangle((w * 0.14, h * 0.56, w * 0.86, h + 40), radius=int(w * 0.3), fill="#2A4597")
    return im


def draw_lineup(img, items, y, base):
    """A row of people cards: {"cross": true, "label": "PATAY"} silhouettes or {"photo": "...", "label": ...}."""
    n = len(items)
    gap = 18
    w = (W - 60 - gap * (n - 1)) // n
    h = round(w * 1.55)
    d = ImageDraw.Draw(img)
    f = font("ExtraBold", 38)
    for i, it in enumerate(items):
        x = 30 + i * (w + gap)
        if it.get("photo"):
            p = ImageOps.grayscale(Image.open(base / it["photo"])).convert("RGB")
            p = ImageOps.fit(p, (w - 16, h - 16), Image.LANCZOS, centering=(0.5, 0.35))
            dim = it.get("dim")
            if dim:  # remembered, not highlighted: darker photo, thin muted frame
                p = Image.blend(p, Image.new("RGB", p.size, "#0B1A50"), 0.35)
            card = Image.new("RGBA", (w, h), "#3A5BB8" if dim else YELLOW)
            card.paste(p, (8, 8))
            m = Image.new("L", (w, h), 0)
            ImageDraw.Draw(m).rounded_rectangle((0, 0, w - 1, h - 1), radius=22, fill=255)
            if not dim:
                glow = Image.new("RGBA", (w + 60, h + 60), (0, 0, 0, 0))
                ImageDraw.Draw(glow).rounded_rectangle((20, 20, w + 40, h + 40), radius=30, fill=(255, 199, 44, 170))
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
            d.text((x + w / 2, y + h + 34), it["label"], font=fl, fill=col if col != RED else "#FF5A6E", anchor="mm")
    return y + h + 70


def render(spec, out, base=None):
    base = Path(base) if base else out.parent
    img = Image.open(BG).convert("RGB").resize((W, H))
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
    if spec.get("mascot") is False:
        out.parent.mkdir(parents=True, exist_ok=True)
        img.save(out, quality=95)
        print("thumbnail:", out)
        return
    # Juan, big, bottom-right; the art bottom-left
    m = Image.open(ROOT / "brand" / "juan_mascot.png").convert("RGBA")
    mh = spec.get("mascot_height", 640)
    m = m.resize((round(m.width * mh / m.height), mh), Image.LANCZOS)
    my = max(y + 10, H - mh - 20)
    if spec.get("art"):
        art = builtin_art(spec["art"]) or Image.open(ART / f"{spec['art']}.png").convert("RGBA")
        s = min(spec.get("art_scale", 1.0) * 620 / art.width, (H - my - 60) / art.height, 1.6)
        art = art.resize((round(art.width * s), round(art.height * s)), Image.LANCZOS)
        img.paste(art, (40, H - art.height - 60), art)
    img.paste(m, (W - m.width + 15, my), m)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, quality=95)
    print("thumbnail:", out)


def main():
    paths = [Path(p) for p in sys.argv[1:]] or sorted((ROOT / "stories").glob("*/story.json"))
    for sp in paths:
        story = json.loads(sp.read_text(encoding="utf-8"))
        if story.get("thumbnail"):
            render(story["thumbnail"], sp.parent / "thumbnail.jpg")


if __name__ == "__main__":
    main()
