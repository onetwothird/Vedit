"""Crossfade and whip transitions between kept segments: the edl key, the patch op the app sends, the render plan and
filter graph, and a real render whose length, sound and untouched frames match a hard-cut render of the same edit."""
import json
import shutil
import subprocess
from types import SimpleNamespace

import numpy as np
import pytest

from conftest import probe, run_script
from edl import TRANSITION_DURATION, Timeline, transition_of
from edl_ops import patch_section

# ---------- the edl key ----------


def test_no_transition_is_a_hard_cut():
    assert transition_of({}) == {"type": "none", "duration": 0.0}
    assert transition_of({"transition": {"type": "none", "duration": 0.5}}) == {"type": "none", "duration": 0.0}


def test_transition_type_alone_takes_the_default_duration():
    assert transition_of({"transition": {"type": "crossfade"}}) == {"type": "crossfade", "duration": TRANSITION_DURATION}
    assert transition_of({"transition": "whip"}) == {"type": "whip", "duration": TRANSITION_DURATION}


@pytest.mark.parametrize("bad", [{"type": "wipe"}, {"type": "whip", "duration": 0}, {"type": "whip", "duration": 3},
                                 {"type": "crossfade", "duration": True}, {"type": "crossfade", "duration": "0.4"}, 7])
def test_bad_transitions_are_refused(bad):
    with pytest.raises(ValueError):
        transition_of({"transition": bad})


# ---------- the patch op ----------

EDL = {"sources": {"main": "x.mp4"}, "clips": [{"words": [0, 3]}, {"words": [6, 9]}]}


def test_patch_sets_and_clears_the_transition():
    e = patch_section(EDL, "transition", {"type": "crossfade", "duration": 0.5})
    assert e["transition"] == {"type": "crossfade", "duration": 0.5}
    assert "transition" not in EDL  # the input is not changed
    e = patch_section(e, "transition", {"type": "whip"})
    assert e["transition"] == {"type": "whip", "duration": 0.5}  # the duration is kept when only the type changes
    e = patch_section(e, "transition", {"type": None, "duration": None})  # the undo of setting both on a fresh edl
    assert "transition" not in e


@pytest.mark.parametrize("patch", [{"type": "dissolve"}, {"type": "whip", "duration": 9}, {"style": "fast"},
                                   {"duration": -1}])
def test_patch_refuses_bad_transitions(patch):
    with pytest.raises(ValueError):
        patch_section(EDL, "transition", patch)


def test_studio_patch_op_accepts_the_transition_section():
    from studio_server import _apply_op
    e = _apply_op(dict(EDL), {"op": "patch", "section": "transition", "patch": {"type": "whip", "duration": 0.3}})
    assert e["transition"] == {"type": "whip", "duration": 0.3}


# ---------- the render plan and filter graph ----------


def fake_tl(durs, kind="crossfade", duration=0.4, fps=30, src_in=(5.0,)):
    clips, t = [], 0.0
    for i, d in enumerate(durs):
        vin = src_in[i] if i < len(src_in) else 5.0 + i * 10
        clips.append({"index": i, "src": "main", "audio_alias": "main", "dur": d, "speed": 1.0, "t0": t, "t1": t + d,
                      "vin": vin, "ain": vin, "tin": vin, "tout": vin + d})
        t += d
    info = {"width": 1920, "height": 1080, "has_audio": True, "duration": 600}
    return SimpleNamespace(clips=clips, duration=t, transition={"type": kind, "duration": duration},
                           out={"width": 1080, "height": 1920, "fps": fps}, edl={}, dir=None,
                           sources={"main": {"path": "x.mp4", "info": info, "is_image": False, "synced": False}})


def test_plan_puts_half_the_transition_each_side_of_every_cut():
    from render import plan_transitions
    tl = fake_tl([2.0, 3.0, 1.5])
    cuts = plan_transitions(tl)
    assert [(x["after"], x["at"]) for x in cuts] == [(0, 2.0), (1, 5.0)]
    assert all(x["half"] == pytest.approx(0.2) for x in cuts)
    c0, c1, c2 = tl.clips
    assert (c0["head"], c0["tail"]) == (0.0, pytest.approx(0.2))
    assert (c1["head"], c1["tail"]) == (pytest.approx(0.2), pytest.approx(0.2))
    assert (c2["head"], c2["tail"]) == (pytest.approx(0.2), 0.0)


def test_plan_never_outlasts_a_short_clip_and_snaps_to_frames():
    from render import plan_transitions
    tl = fake_tl([2.0, 0.2, 2.0], duration=1.0)
    cuts = plan_transitions(tl)
    assert [x["half"] for x in cuts] == [pytest.approx(3 / 30)] * 2  # half of the 0.2 s clip, in whole frames
    tl = fake_tl([2.0, 1 / 30, 2.0])
    assert plan_transitions(tl) == []  # under one frame each side: hard cuts


def test_plan_for_no_transition_adds_no_handles():
    from render import plan_transitions
    tl = fake_tl([2.0, 3.0], kind="none", duration=0.0)
    assert plan_transitions(tl) == []
    assert all(c["head"] == c["tail"] == 0 for c in tl.clips)


def test_segment_reads_its_handles_from_the_source():
    from render import plan_transitions, seg_cmd
    tl = fake_tl([2.0, 3.0], src_in=(5.0, 20.0))
    plan_transitions(tl)
    cmd = seg_cmd(tl, tl.clips[1], "out.mkv", preview=True)
    assert cmd[cmd.index("-ss") + 1] == "19.8000"  # 0.2 s before the in point
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "tpad=start_mode" not in fc  # the source has the whole head
    assert "tpad=stop_mode=clone:stop_duration=0.0000" not in fc and "trim=duration=3.2000" in fc
    assert "adelay=delays=9600S:all=1" in fc and f"apad=whole_len={round(3.2 * 48000)}" in fc
    assert cmd[-2:] == ["3.2000", "out.mkv"]


def test_a_head_before_the_source_starts_holds_the_first_frame():
    from render import plan_transitions, seg_cmd
    tl = fake_tl([2.0, 3.0], src_in=(5.0, 0.05))
    plan_transitions(tl)
    cmd = seg_cmd(tl, tl.clips[1], "out.mkv", preview=True)
    assert cmd[cmd.index("-ss") + 1] == "0.0000"
    assert "tpad=start_mode=clone:start_duration=0.1500" in cmd[cmd.index("-filter_complex") + 1]


def test_hard_cut_segments_are_unchanged():
    """No transition: the segment command is what it was before transitions existed (so the cache stays valid)."""
    from render import plan_transitions, seg_cmd
    tl = fake_tl([2.0, 3.0], kind="none", duration=0.0)
    plain = [seg_cmd(tl, c, "o.mkv", preview=False) for c in tl.clips]
    plan_transitions(tl)
    assert [seg_cmd(tl, c, "o.mkv", preview=False) for c in tl.clips] == plain
    fc = plain[1][plain[1].index("-filter_complex") + 1]
    assert "tpad" not in fc and "adelay" not in fc and plain[1][plain[1].index("-ss") + 1] == "15.0000"


def test_crossfade_graph_overlaps_the_pictures_and_butts_the_sound():
    from render import join_graph, plan_transitions
    tl = fake_tl([2.0, 3.0, 1.5])
    g = join_graph(tl, plan_transitions(tl))
    assert "[s0][s1]xfade=transition=fade:duration=0.400000:offset=1.800000[x1]" in g
    assert "[x1][s2]xfade=transition=fade:duration=0.400000:offset=4.800000[x2]" in g
    assert "[x2]null[vjoin]" in g
    assert "[1:a]atrim=start_sample=9600:end_sample=153600,asetpts=PTS-STARTPTS[a1]" in g
    assert "[a0][a1][a2]concat=n=3:v=0:a=1[ajoin]" in g


def test_whip_graph_alternates_direction_and_blurs_only_during_the_whip():
    from render import WHIP_BLUR, join_graph, plan_transitions
    tl = fake_tl([2.0, 3.0, 1.5], kind="whip", duration=0.4)
    g = "\n".join(join_graph(tl, plan_transitions(tl)))
    assert "xfade=transition=slideleft:duration=0.400000:offset=1.800000" in g
    assert "xfade=transition=slideright:duration=0.400000:offset=4.800000" in g
    r = round(1080 * WHIP_BLUR)
    assert f"dblur=angle=0:radius={r}:enable='between(t,1.8000,2.2000)+between(t,4.8000,5.2000)'[vjoin]" in g


def test_a_cut_too_short_for_a_transition_is_joined_plainly():
    from render import join_graph, plan_transitions
    tl = fake_tl([2.0, 1 / 30, 2.0])
    g = join_graph(tl, plan_transitions(tl))
    assert "[s0][s1]concat=n=2:v=1:a=0[x1]" in g and "[x1][s2]concat=n=2:v=1:a=0[x2]" in g


# ---------- a real render ----------


def frame(path, t, w, h):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ss", f"{t:.3f}", "-frames:v", "1",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(h, w, 3).astype(int)


def pcm(path):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "s16le", "-ac", "1", "-ar", "16000", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.int16).astype(int)


@pytest.fixture(scope="module")
def renders(pipeline_workdir, tmp_path_factory):
    d = tmp_path_factory.mktemp("transitions")
    for f in pipeline_workdir.glob("*.json"):
        shutil.copy(f, d / f.name)
    base = json.loads((d / "edl.json").read_text())
    base["captions"] = {**(base.get("captions") or {}), "style": "off", "srt": False}
    out = {}
    for kind in ("none", "crossfade", "whip"):
        e = patch_section(base, "transition", {"type": kind, "duration": 0.4})
        e["output"] = {**e["output"], "path": str(d / f"{kind}.mp4")}
        (d / f"{kind}.json").write_text(json.dumps(e))
        run_script("render.py", d / f"{kind}.json")
        out[kind] = d / f"{kind}.mp4"
    tl = Timeline(d / "none.json")
    assert len(tl.clips) >= 3, "the test needs cuts"
    v = next(s for s in probe(out["none"])["streams"] if s["codec_type"] == "video")
    return {"tl": tl, "out": out, "w": int(v["width"]), "h": int(v["height"])}


def test_transitions_keep_the_length(renders):
    want = float(probe(renders["out"]["none"])["format"]["duration"])
    for kind in ("crossfade", "whip"):
        assert float(probe(renders["out"][kind])["format"]["duration"]) == pytest.approx(want, abs=0.05)


def test_transitions_keep_the_sound(renders):
    a = pcm(renders["out"]["none"])
    for kind in ("crossfade", "whip"):
        b = pcm(renders["out"][kind])
        n = min(len(a), len(b))
        assert abs(len(a) - len(b)) < 800
        env = [np.abs(x[:n - n % 800]).reshape(-1, 800).mean(axis=1) for x in (a, b)]  # loudness per 50 ms
        assert np.abs(env[0] - env[1]).max() < 0.05 * env[0].max()  # the same hard-cut audio (to within a sample)


def test_transitions_change_the_picture_only_at_the_cuts(renders):
    tl, out, w, h = renders["tl"], renders["out"], renders["w"], renders["h"]
    hard = out["none"]
    for kind in ("crossfade", "whip"):
        for c in tl.clips[:-1]:
            at = c["t1"]
            if c["dur"] < 0.8 or tl.clips[c["index"] + 1]["dur"] < 0.8:
                continue
            mid = c["t0"] + c["dur"] / 2 if c["dur"] > 1.0 else None
            assert np.abs(frame(out[kind], at - 0.02, w, h) - frame(hard, at - 0.02, w, h)).mean() > 5, (kind, at)
            if mid is not None and mid < at - 0.4 and mid > c["t0"] + 0.4:
                assert np.abs(frame(out[kind], mid, w, h) - frame(hard, mid, w, h)).mean() < 3, (kind, mid)
