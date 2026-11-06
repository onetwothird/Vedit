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

from PIL import Image, ImageDraw

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


def render(spec, out):
    img = Image.open(BG).convert("RGB").resize((W, H))
    d = ImageDraw.Draw(img)
    y = 400
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
    if spec.get("sticker"):
        st = sticker(spec["sticker"])
        img.paste(st, ((W - st.width) // 2, y), st)
        y += st.height
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
