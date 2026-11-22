"""Preview proxies and filmstrips: when a proxy is needed, upright output, caching, sprite metadata."""
import math
import os
import subprocess

import pytest

from common import ffmpeg_bin, load_json
from proxy import filmstrip, make_proxy, needs_proxy, probe, rotation, video_stream

MP4 = "mov,mp4,m4a,3gp,3g2,mj2"


def _info(vcodec, acodec, fmt, dur, rot):
    v = {"codec_type": "video", "codec_name": vcodec, "width": 1920, "height": 1080}
    if rot:
        v["side_data_list"] = [{"side_data_type": "Display Matrix", "rotation": rot}]
    streams = [v] + ([{"codec_type": "audio", "codec_name": acodec}] if acodec else [])
    return {"streams": streams, "format": {"format_name": fmt, "duration": str(dur)}}


@pytest.mark.parametrize("vcodec,acodec,fmt,dur,rot,expected", [
    ("h264", "aac", MP4, 600, 0, False),
    ("hevc", "aac", MP4, 60, 0, True),
    ("h264", "aac", MP4, 600, 90, True),
    ("h264", "ac3", "mpegts", 60, 0, True),
    ("h264", "aac", MP4, 1801, 0, True),
    ("h264", None, MP4, 60, 0, False),
])
def test_needs_proxy(vcodec, acodec, fmt, dur, rot, expected):
    assert needs_proxy(_info(vcodec, acodec, fmt, dur, rot)) is expected


def test_rotation_normalised_and_tag_fallback():
    assert rotation({"side_data_list": [{"rotation": -90}]}) == 270
    assert rotation({"tags": {"rotate": "180"}}) == 180
    assert rotation({}) == 0


def _clip(path, dur=2, size="320x180", rot=0):
    ff = ffmpeg_bin("ffmpeg")
    plain = path.with_name("plain_" + path.name) if rot else path
    subprocess.run([ff, "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc=size={size}:rate=10:duration={dur}",
                    "-f", "lavfi", "-i", f"sine=duration={dur}", "-c:v", "libx264", "-c:a", "aac", "-shortest",
                    str(plain)], check=True)
    if rot:  # ffmpeg 7+ drops the legacy rotate tag; -display_rotation on a copy writes a real displaymatrix
        subprocess.run([ff, "-y", "-v", "error", "-display_rotation", str(rot), "-i", str(plain), "-c", "copy",
                        str(path)], check=True)
    return path


def test_rotated_phone_clip_proxy_is_upright(tmp_path):
    src = _clip(tmp_path / "phone.mp4", rot=90)
    p = make_proxy(src, tmp_path / "wd")
    v = video_stream(probe(p))
    assert (v["width"], v["height"]) == (540, 960) and rotation(v) == 0


def test_compliant_source_returned_as_is(tmp_path):
    src = _clip(tmp_path / "ok.mp4")
    assert make_proxy(src, tmp_path / "wd") == src


def test_proxy_cached(tmp_path):
    src = _clip(tmp_path / "phone.mp4", rot=90)
    wd = tmp_path / "wd"
    p = make_proxy(src, wd)
    assert set(load_json(wd / "proxy.json")) == {"src", "size", "mtime"}
    stamp = os.stat(p).st_mtime_ns
    assert make_proxy(src, wd) == p
    assert os.stat(p).st_mtime_ns == stamp
    assert not (wd / "proxy.part.mp4").exists()


def test_filmstrip_metadata(tmp_path):
    src = _clip(tmp_path / "a.mp4", dur=5)
    out = filmstrip(src, tmp_path)
    meta = load_json(tmp_path / "filmstrip.json")
    assert out.exists()
    assert meta["count"] == math.ceil(float(probe(src)["format"]["duration"]) / 2) == 3
    assert meta["every"] == 2.0 and meta["height"] == 96 and meta["width"] == 170
    sprite = video_stream(probe(out))
    assert (sprite["width"], sprite["height"]) == (meta["width"] * meta["count"], 96)
