"""caption_events() hands the live preview the same groups/timings that build() turns into ASS."""
import json
import re
from pathlib import Path

import pytest
from captions import build, caption_events
from edl import Timeline

FIXTURES = Path(__file__).parent / "fixtures"
DIALOGUE = re.compile(r"^Dialogue: 0,(\d+):(\d\d):(\d\d)\.(\d\d),(\d+):(\d\d):(\d\d)\.(\d\d),.*$", re.M)


def captions_ass(workdir):
    return build(Timeline(workdir / "edl.json"))[0]


def _t(h, m, s, cs):
    return int(h) * 3600 + int(m) * 60 + int(s) + int(cs) / 100


def ass_events(ass):
    """[(start, end, line)] of every layer-0 (caption) Dialogue line."""
    return [(_t(*m.group(1, 2, 3, 4)), _t(*m.group(5, 6, 7, 8)), m.group(0)) for m in DIALOGUE.finditer(ass)]


def parse_ass_dialogue_starts(ass):
    """Start of each caption group: pop group-first events carry the pop-in tag; clean has one event per group."""
    pop = "fscx90" in ass
    return [round(s, 2) for s, _, line in ass_events(ass)
            if (not pop) or "fscx90" in line]


@pytest.mark.parametrize("fixture, golden", [
    ("pipeline_workdir", "16x9"), ("pipeline_workdir_9x16", "9x16_pop"),
    ("pipeline_workdir_box", "box"), ("pipeline_workdir_speakers", "speakers")])
def test_build_output_unchanged(request, fixture, golden):
    """ASS + SRT captured from build() before the caption_events refactor (f5f838f)."""
    ass, srt, _ = build(Timeline(request.getfixturevalue(fixture) / "edl.json"))
    assert ass == (FIXTURES / f"golden_captions_{golden}.ass").read_text(encoding="utf-8")
    want = json.loads((FIXTURES / f"golden_captions_{golden}.srt.json").read_text(encoding="utf-8"))
    assert [list(x) for x in srt] == want


def test_events_match_ass_timing(pipeline_workdir):
    ev = caption_events(Timeline(pipeline_workdir / "edl.json"))
    ass_starts = parse_ass_dialogue_starts(captions_ass(pipeline_workdir))
    assert ev["events"]
    assert [round(e["start"], 2) for e in ev["events"]] == ass_starts


@pytest.mark.parametrize("fixture", ["pipeline_workdir_9x16", "pipeline_workdir_box", "pipeline_workdir_speakers"])
def test_pop_group_starts_match_ass(request, fixture):
    wd = request.getfixturevalue(fixture)
    ev = caption_events(Timeline(wd / "edl.json"))
    assert [round(e["start"], 2) for e in ev["events"]] == parse_ass_dialogue_starts(captions_ass(wd))


@pytest.mark.parametrize("fixture", ["pipeline_workdir_9x16", "pipeline_workdir_box", "pipeline_workdir_speakers"])
def test_pop_highlight_windows_match_ass(request, fixture):
    wd = request.getfixturevalue(fixture)
    ev = caption_events(Timeline(wd / "edl.json"))
    wins = [(w["hl_start"], w["hl_end"]) for e in ev["events"] for w in e["words"] if w["hl_end"] > w["hl_start"]]
    ass = ass_events(captions_ass(wd))
    assert wins and len(wins) == len(ass)
    for (hs, he), (s, e, _) in zip(wins, ass):
        assert abs(hs - s) <= 0.01 and abs(he - e) <= 0.01
    for e in ev["events"]:  # raw word times stay raw; hl windows tile the group
        assert e["words"][0]["hl_start"] == e["start"] and e["words"][-1]["hl_end"] == e["end"]


def test_events_shape(pipeline_workdir):
    tl = Timeline(pipeline_workdir / "edl.json")
    ev = caption_events(tl)
    H = tl.out["height"]
    assert ev["size_frac"] == round(H * 0.056) / H and ev["color"] == "#FFFFFF"
    assert ev["highlight"] == "#FFD400" and ev["outline_color"] == "#000000" and ev["shadow"] == 1
    assert ev["back_color"] is None and ev["back_alpha"] is None and ev["speaker_colors"] == {}
    for e in ev["events"]:
        assert e["end"] > e["start"] and e["words"]
        for w in e["words"]:
            assert {"text", "start", "end", "hl_start", "hl_end", "speaker"} <= set(w)
            assert w["speaker"] is None


def test_events_vertical_size(pipeline_workdir_9x16):
    tl = Timeline(pipeline_workdir_9x16 / "edl.json")
    W, H = tl.out["width"], tl.out["height"]
    assert H > W
    assert caption_events(tl)["size_frac"] == round(W * 0.085) / H


def test_events_box_and_speakers(pipeline_workdir_box, pipeline_workdir_speakers):
    box = caption_events(Timeline(pipeline_workdir_box / "edl.json"))
    assert box["back_color"] == "#000000" and abs(box["back_alpha"] - (1 - 0x70 / 255)) < 1e-3
    assert box["shadow"] == 0
    spk = caption_events(Timeline(pipeline_workdir_speakers / "edl.json"))
    assert spk["speaker_colors"] == {"maria": "#FF6FB5", "narrator": "#FFD400"}
    assert {w["speaker"] for e in spk["events"] for w in e["words"]} == {"maria", "narrator"}


def test_events_vertical_lower_position(pipeline_workdir_9x16):
    ev = caption_events(Timeline(pipeline_workdir_9x16 / "edl.json"))
    assert ev["baseline_frac"] == 0.24 and ev["style"] == "pop" and ev["uppercase"] is True


def _with_intro(wd, tmp_path, seconds):
    """Copy of the workdir whose edl opens with a still-image intro of `seconds`."""
    import shutil
    import subprocess
    d = tmp_path / "wd_intro"
    shutil.copytree(wd, d, ignore=shutil.ignore_patterns("*.mp4", "*.ass", "*.srt"))
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=640x360", "-frames:v", "1",
                    str(d / "intro.png")], check=True)
    e = json.loads((d / "edl.json").read_text())
    e["intro"] = {"path": "intro.png", "duration": seconds}
    (d / "edl.json").write_text(json.dumps(e))
    return d


def test_lead_in_is_the_intro_length_and_captions_start_after_it(pipeline_workdir, tmp_path):
    """The preview skips bumpers, so it subtracts lead_in from caption times: those times include the intro."""
    plain = caption_events(Timeline(pipeline_workdir / "edl.json"))
    assert plain["lead_in"] == 0
    d = _with_intro(pipeline_workdir, tmp_path, 2.0)
    ev = caption_events(Timeline(d / "edl.json"))
    assert ev["lead_in"] == pytest.approx(2.0, abs=1 / 30)
    assert len(ev["events"]) == len(plain["events"])
    for a, b in zip(plain["events"], ev["events"]):
        assert b["start"] - a["start"] == pytest.approx(ev["lead_in"], abs=1e-6)
