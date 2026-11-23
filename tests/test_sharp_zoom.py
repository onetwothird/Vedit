"""Automatic punch-ins never soften the picture: on a source with no pixels to spare at the output shape they are left
out, on a larger one they zoom only as far as its pixels allow. A zoom you chose yourself is kept as it is."""
import json
import subprocess

import numpy as np
import pytest

from common import ffmpeg_bin
from edl import Timeline
from edl_ops import patch_section


def video(path, size):
    subprocess.run([ffmpeg_bin("ffmpeg"), "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=25:d=2",
                    "-f", "lavfi", "-i", "sine=d=2", "-c:v", "libx264", "-c:a", "aac", "-shortest", str(path)],
                   check=True)
    return path


@pytest.fixture(scope="module")
def sources(tmp_path_factory):
    d = tmp_path_factory.mktemp("zoom")
    return {"720p": video(d / "hd.mp4", "1280x720"), "4k": video(d / "uhd.mp4", "3840x2160"), "dir": d}


def tl_for(sources, key, clips, name="edl.json"):
    d = sources["dir"]
    edl = {"sources": {"main": str(sources[key])}, "output": {"aspect": "9:16", "path": str(d / f"{key}.mp4")},
           "captions": {"style": "off", "srt": False}, "clips": clips}
    (d / name).write_text(json.dumps(edl))
    return Timeline(d / name)


JUMP = [{"in": 0, "out": 0.6}, {"in": 0.6, "out": 1.2, "zoom": 1.12}, {"in": 1.2, "out": 1.8}]


def test_no_punch_in_when_the_crop_is_already_enlarged(sources):
    tl = tl_for(sources, "720p", JUMP)  # 9:16 from 1280x720 keeps a 405 px strip, shown at 720 px wide
    assert tl.sharp_zoom("main") == 1.0
    assert [c["zoom"] for c in tl.clips if "zoom" in c] == [1.0]
    assert tl.clips[1]["zoom_asked"] == 1.12


def test_punch_in_is_capped_where_a_big_source_runs_out_of_pixels(sources):
    tl = tl_for(sources, "4k", [JUMP[0], {**JUMP[1], "zoom": 1.2}, JUMP[2]])
    assert tl.sharp_zoom("main") == pytest.approx(1215 / 1080)
    assert tl.clips[1]["zoom"] == pytest.approx(1.125, abs=1e-3)
    assert tl_for(sources, "4k", JUMP).clips[1]["zoom"] == 1.12  # within the spare pixels: kept


def test_marked_punch_ins_and_chosen_zooms(sources):
    marked = [JUMP[0], {**JUMP[1], "punchin": True}, {**JUMP[2], "zoom": 1.12, "punchin": False}]
    tl = tl_for(sources, "720p", marked)
    assert [tl.clips[1]["zoom"], tl.clips[2]["zoom"]] == [1.0, 1.12]
    every = [{**c, "zoom": 1.12} for c in JUMP]  # the Framing slider sets the same zoom on every clip
    assert {c["zoom"] for c in tl_for(sources, "720p", every).clips} == {1.12}


def test_the_framing_slider_marks_its_zoom_as_chosen():
    e = patch_section({"clips": [{"in": 0, "out": 1}, {"in": 1, "out": 2, "zoom": 1.12, "punchin": True}]},
                      "framing", {"zoom": 1.2})
    assert [(c["zoom"], c["punchin"]) for c in e["clips"]] == [(1.2, False), (1.2, False)]


def test_render_keeps_every_cut_equally_sharp(sources, tmp_path):
    """The punch-in clip of a 720p source renders with the same crop as its neighbours, so detail does not drop."""
    from render import video_chain
    tl = tl_for(sources, "720p", JUMP)
    crop = lambda c: next(p for p in video_chain(tl, c).split(",") if p.startswith("crop="))  # noqa: E731
    assert crop(tl.clips[0]) == crop(tl.clips[1]) == crop(tl.clips[2])


def sharpness(path, t, w, h):
    raw = subprocess.run([ffmpeg_bin("ffmpeg"), "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
                          "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, check=True).stdout
    g = np.frombuffer(raw, np.uint8).reshape(h, w).astype(float)
    return np.abs(np.diff(g, axis=1)).mean() + np.abs(np.diff(g, axis=0)).mean()


def test_export_is_close_to_the_source_detail(sources):
    """An export of a 1:1 crop (no enlargement) keeps nearly all of the source's edge detail after both encodes."""
    from conftest import run_script
    d = sources["dir"]
    edl = {"sources": {"main": str(sources["4k"])},
           "output": {"width": 1080, "height": 1920, "fps": 25, "path": str(d / "sharp.mp4")},
           "captions": {"style": "off", "srt": False}, "clips": [{"in": 0, "out": 1.5}]}
    (d / "sharp.json").write_text(json.dumps(edl))
    run_script("render.py", d / "sharp.json")
    subprocess.run([ffmpeg_bin("ffmpeg"), "-y", "-v", "error", "-ss", "0.8", "-i", str(sources["4k"]), "-frames:v", "1",
                    "-vf", "crop=1215:2160,scale=1080:1920:flags=lanczos", "-f", "rawvideo", "-pix_fmt", "gray",
                    str(d / "ref.gray")], check=True)
    g = np.fromfile(d / "ref.gray", np.uint8).reshape(1920, 1080).astype(float)
    want = np.abs(np.diff(g, axis=1)).mean() + np.abs(np.diff(g, axis=0)).mean()
    assert sharpness(d / "sharp.mp4", 0.8, 1080, 1920) > 0.9 * want
