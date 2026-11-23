"""The Edit screen's tools, through a real export: speed, colour, text and b-roll layers are applied with the same
patch ops the app sends, render.py draws the clip, and the frames are read back to check each tool landed where and
when the preview shows it."""
import json
import shutil
import subprocess

import numpy as np
import pytest

from conftest import probe, run_script
from edl import Timeline
from edl_ops import patch_section

SPEED = 1.5


def frame(path, t, w, h):
    """The RGB frame at output time t (accurate seek), as an h x w x 3 array."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ss", f"{t:.3f}", "-frames:v", "1",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(h, w, 3).astype(int)


def solid_png(path, rgb, size=(320, 320)):
    from PIL import Image
    Image.new("RGB", size, rgb).save(path)


@pytest.fixture(scope="module")
def renders(pipeline_workdir_9x16, tmp_path_factory):
    d = tmp_path_factory.mktemp("tools")
    for f in pipeline_workdir_9x16.glob("*.json"):
        shutil.copy(f, d / f.name)
    solid_png(d / "red.png", (255, 0, 0))
    solid_png(d / "blue.png", (0, 0, 255), (640, 360))
    base = json.loads((d / "edl.json").read_text())
    base["output"] = {**base["output"], "path": str(d / "base.mp4")}
    base["captions"] = {**(base.get("captions") or {}), "style": "off", "srt": False}
    # the same ops the app sends: Speed and Color tools
    base = patch_section(base, "framing", {"speed": SPEED})
    base = patch_section(base, "color", {"saturation": 0, "contrast": 1.1, "sharpen": True})
    (d / "base.json").write_text(json.dumps(base))
    tl = Timeline(d / "base.json")
    assert len(tl.clips) >= 3, "the test needs three clips, with cuts between them"
    c0, c1, c2 = tl.clips[0], tl.clips[1], tl.clips[2]
    # three layers in three separate moments, in source time like the timeline places them
    text_src = c0["tin"] + 0.3
    pip_src = c1["tin"] + 0.2
    full_src = c2["tin"] + 0.3
    layers = [
        {"id": "t", "type": "text", "at": {"src": "main", "t": text_src}, "duration": 1.0, "text": "Wait for it", "position": "top"},
        {"id": "p", "type": "image", "at": {"src": "main", "t": pip_src}, "duration": 1.0, "path": "red.png", "mode": "pip", "position": "top-right"},
        {"id": "f", "type": "image", "at": {"src": "main", "t": full_src}, "duration": 0.6, "path": "blue.png", "mode": "full"},
    ]
    edit = patch_section(base, "overlays", {"items": layers})
    edit["output"] = {**edit["output"], "path": str(d / "edit.mp4")}
    (d / "edit.json").write_text(json.dumps(edit))
    run_script("render.py", d / "base.json")
    run_script("render.py", d / "edit.json")
    v = next(s for s in probe(d / "edit.mp4")["streams"] if s["codec_type"] == "video")
    return {"d": d, "tl": tl, "edit": Timeline(d / "edit.json"), "src": (text_src, pip_src, full_src),
            "w": int(v["width"]), "h": int(v["height"])}


def out_time(tl, src_t):
    """Output time of a source moment, worked out like the preview does (preview/playback.ts toOutputTime)."""
    t = 0.0
    for c in tl.clips:
        if c["tin"] <= src_t < c["tout"]:
            return t + (src_t - c["tin"]) / c["speed"]
        t += c["dur"]
    return None


def test_speed_shortens_the_clip(renders):
    tl, d = renders["tl"], renders["d"]
    src_len = sum(c["tout"] - c["tin"] for c in tl.clips)
    got = float(probe(d / "edit.mp4")["format"]["duration"])
    assert got == pytest.approx(src_len / SPEED, abs=0.15)
    assert all(c["speed"] == SPEED for c in tl.clips)


def test_layers_land_where_the_preview_puts_them(renders):
    tl = renders["edit"]
    for src_t in renders["src"]:
        assert tl.anchor({"src": "main", "t": src_t}) == pytest.approx(out_time(tl, src_t), abs=1 / 30)


def test_saturation_zero_is_grey(renders):
    f = frame(renders["d"] / "base.mp4", 0.5, renders["w"], renders["h"])
    assert np.abs(f[..., 0] - f[..., 1]).mean() < 4 and np.abs(f[..., 1] - f[..., 2]).mean() < 4


def test_text_shows_at_the_top_only_while_it_lasts(renders):
    d, w, h, tl = renders["d"], renders["w"], renders["h"], renders["edit"]
    at = tl.anchor({"src": "main", "t": renders["src"][0]})
    band = (slice(int(h * 0.10), int(h * 0.20)), slice(int(w * 0.2), int(w * 0.55)))  # left of the corner b-roll
    rest = (slice(int(h * 0.4), int(h * 0.9)), slice(0, w))

    def diff(t, region):
        return np.abs(frame(d / "edit.mp4", t, w, h)[region] - frame(d / "base.mp4", t, w, h)[region]).mean()

    assert diff(at + 0.5, band) > 20  # the text and its box
    assert diff(at + 0.5, rest) < 3  # nothing else changed
    assert diff(at + 1.15, band) < 3  # gone after its 1 s


def test_corner_broll_sits_in_the_top_right_with_the_export_margin(renders):
    d, w, h, tl = renders["d"], renders["w"], renders["h"], renders["edit"]
    at = tl.anchor({"src": "main", "t": renders["src"][1]})
    f = frame(d / "edit.mp4", at + 0.5, w, h)
    m, pw = round(w * 0.04), round(w * 0.35)
    inner = f[m + 4:m + pw - 4, w - m - pw + 4:w - m - 4]
    assert inner[..., 0].mean() > 200 and inner[..., 1].mean() < 60 and inner[..., 2].mean() < 60  # red, not greyed
    assert f[h // 2, w // 2, 0] < 200 or f[h // 2, w // 2, 1] > 60  # the middle is still the video


def test_full_screen_broll_covers_the_frame(renders):
    d, w, h, tl = renders["d"], renders["w"], renders["h"], renders["edit"]
    at = tl.anchor({"src": "main", "t": renders["src"][2]})
    f = frame(d / "edit.mp4", at + 0.3, w, h)
    assert f[..., 2].mean() > 200 and f[..., 0].mean() < 50


def test_long_text_wraps_inside_the_frame():
    from render import wrap_text
    one = "This is a much longer line of text that would not fit across a vertical video"
    lines = wrap_text(one, 54, 1080 * 0.88 - 36).split("\n")
    assert len(lines) > 1 and " ".join(lines) == one
    from PIL import ImageFont
    from render import TEXT_FONT
    font = ImageFont.truetype(str(TEXT_FONT), 54)
    assert all(font.getlength(line) <= 1080 * 0.88 - 36 for line in lines)


def test_text_is_drawn_once(renders):
    """captions.py draws hand-written text overlays; the editor's layers (with an id) are render.py's alone."""
    import captions
    ass, _, _ = captions.build(renders["edit"])
    assert "Wait for it" not in ass
