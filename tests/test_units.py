from captions import ass_time, srt_time
from common import filter_path, hex_to_ass, parse_time, ts
from render import atempo_chain
from transcribe import build


def test_time_helpers():
    assert parse_time("1:02.5") == 62.5
    assert parse_time("01:00:01") == 3601
    assert parse_time(3) == 3.0
    assert ts(62.5) == "1:02.50"
    assert ass_time(3661.25) == "1:01:01.25"
    assert srt_time(1.5) == "00:00:01,500"


def test_colors_and_paths():
    assert hex_to_ass("#FFD400") == "&H0000D4FF"
    assert filter_path("C:/Users/me/look.cube") == "C\\:/Users/me/look.cube"


def test_atempo_chain_splits_extremes():
    assert atempo_chain(1.0) == []
    assert atempo_chain(3.0)[0] == "atempo=2.0"
    prod = 1.0
    for f in atempo_chain(0.25):
        v = float(f.split("=")[1])
        assert 0.5 <= v <= 2.0
        prod *= v
    assert abs(prod - 0.25) < 1e-6


def _words(text, start=0.0):
    out = []
    for i, w in enumerate(text.split()):
        out.append({"start": start + i * 0.3, "end": start + i * 0.3 + 0.25, "text": w, "p": 0.9})
    return out


def test_fillers_and_retakes_detected():
    w = _words("Um so the first step is easy.", 0) + _words("So the first step is really easy.", 5)
    d = build("x.mp4", w, "en", "main", 10)
    assert d["words"][0]["filler"] == "hard"
    assert d["words"][1]["filler"] == "soft"
    assert any("retake_of_later" in s for s in d["sentences"])
