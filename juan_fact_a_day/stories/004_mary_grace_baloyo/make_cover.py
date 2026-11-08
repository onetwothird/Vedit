"""Custom Reel cover (thumbnail.jpg, 1080x1920) for 004_mary_grace_baloyo: full-bleed portrait + headline + mascot.

    python make_cover.py

story.json has no "thumbnail" block on purpose, so BUILD.bat / make_thumbnail.py won't overwrite this cover.
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "scripts"))
import cards  # noqa: E402

cards.brand_for(HERE / "story.json")
from cards import W, H, font, parse_marked, wrap, draw_lines, sticker, background  # noqa: E402

img = background().convert("RGB")
navy = Image.new("RGB", (1, 1), cards.NAVY).getpixel((0, 0))

# ---- portrait: full width, top of frame, fading into the navy background
PH = 1180
p = Image.open(HERE / "photos/baloyo_cadet.jpg").convert("L")
p = ImageOps.autocontrast(p, cutoff=1)
p = ImageOps.fit(p, (W, PH), Image.LANCZOS, centering=(0.5, 0.18)).convert("RGB")
p = ImageEnhance.Contrast(p).enhance(1.12)
fade = Image.new("L", (W, PH), 255)
fd = ImageDraw.Draw(fade)
start = int(PH * 0.55)
for y in range(start, PH):
    fd.line((0, y, W, y), fill=round(255 * (1 - (y - start) / (PH - start)) ** 1.6))
img.paste(p, (0, 0), fade)
# soft dark vignette at the very top so the sticker/brand read on the photo
top = Image.new("L", (W, 260), 0)
for y in range(260):
    ImageDraw.Draw(top).line((0, y, W, y), fill=round(150 * (1 - y / 260)))
img.paste(Image.new("RGB", (W, 260), navy), (0, 0), top)

d = ImageDraw.Draw(img)

# ---- brand line, top-left
fb = font("ExtraBold", 44)
d.text((52, 64), "Juan Fact", font=fb, fill=cards.WHITE)
d.text((52 + d.textlength("Juan Fact ", font=fb), 64), "a Day", font=fb, fill=cards.YELLOW)

# ---- sticker, over the photo
st = sticker("MEDAL OF VALOR")
img.paste(st, (W - st.width - 40, 760), st)  # stamped across the uniform, clear of her face

# ---- badge
y = 960
f = font("ExtraBold", 60)
label = "KAUNA-UNAHANG BABAE"
tw = d.textlength(label, font=f)
x0, x1 = (W - tw) / 2 - 46, (W + tw) / 2 + 46
d.rounded_rectangle((x0, y + 10, x1, y + 112), radius=52, fill=cards.YELLOW_DARK)
d.rounded_rectangle((x0, y, x1, y + 102), radius=52, fill=cards.YELLOW)
d.text((W / 2, y + 51), label, font=f, fill=cards.NAVY, anchor="mm")
y += 150

# ---- headline
size = 140
fh = font("ExtraBold", size)
y = draw_lines(d, wrap(parse_marked("Hindi siya *nag-eject.*"), fh, W - 80, d), fh, y, round(size * 1.08), shadow=10) + 48

# ---- sub (left of the mascot)
fs = font("SemiBold", 54)
for line in ("Para iligtas ang", "200+ pamilya."):
    d.text((60, y + 4), line, font=fs, fill=cards.SHADOW)
    d.text((60, y), line, font=fs, fill=cards.WHITE)
    y += 68
fn = font("ExtraBold", 40)
d.text((60, y + 30), "1Lt. Mary Grace Baloyo", font=fn, fill=cards.YELLOW)
d.text((60, y + 82), "Philippine Air Force · 2001", font=font("SemiBold", 36), fill=cards.SOFT)

# ---- mascot, bottom-right
m = Image.open(cards.B["mascot"]).convert("RGBA")
mh = 430
m = m.resize((round(m.width * mh / m.height), mh), Image.LANCZOS)
img.paste(m, (W - m.width + 20, H - mh), m)

out = HERE / "thumbnail.jpg"
img.save(out, quality=95)
print("cover:", out)
