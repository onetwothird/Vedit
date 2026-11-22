"""caption_events() hands the live preview the same groups/timings that build() turns into ASS."""
import re
from pathlib import Path

from captions import build, caption_events
from edl import Timeline

GOLDEN = Path(__file__).parent / "fixtures" / "golden_captions_16x9.ass"


def captions_ass(workdir):
    return build(Timeline(workdir / "edl.json"))[0]


def parse_ass_dialogue_starts(ass):
    """Start (0.01s) of each caption group: layer-0 events, skipping pop's follow-on per-word events."""
    starts = []
    for m in re.finditer(r"^Dialogue: 0,(\d+):(\d\d):(\d\d)\.(\d\d),.*$", ass, re.M):
        if "fad(0,0)" in m.group(0):
            continue
        h, mi, sec, cs = (int(x) for x in m.groups())
        starts.append(round(h * 3600 + mi * 60 + sec + cs / 100, 2))
    return starts


def test_build_output_unchanged(pipeline_workdir):
    assert captions_ass(pipeline_workdir) == GOLDEN.read_text(encoding="utf-8")


def test_events_match_ass_timing(pipeline_workdir):
    ev = caption_events(Timeline(pipeline_workdir / "edl.json"))
    ass_starts = parse_ass_dialogue_starts(captions_ass(pipeline_workdir))
    assert ev["events"]
    assert [round(e["start"], 2) for e in ev["events"]] == ass_starts[: len(ev["events"])]


def test_events_shape(pipeline_workdir):
    ev = caption_events(Timeline(pipeline_workdir / "edl.json"))
    assert ev["size_frac"] == round(1080 * 0.056) / 1080 and ev["color"] == "#FFFFFF"
    for e in ev["events"]:
        assert e["end"] > e["start"] and e["words"]
        assert all({"text", "start", "end"} <= set(w) for w in e["words"])


def test_events_vertical_lower_position(pipeline_workdir_9x16):
    ev = caption_events(Timeline(pipeline_workdir_9x16 / "edl.json"))
    assert ev["baseline_frac"] == 0.24 and ev["style"] == "pop" and ev["uppercase"] is True
