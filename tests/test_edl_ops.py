"""Pure EDL edit operations: cut/restore/trim words, caption fixes, section patches, kept ranges."""
import json
from copy import deepcopy

import pytest

from common import load_json, save_json
from edl import Timeline
from edl_ops import clip_edl, cut_words, fix_word, kept_ranges, patch_section, restore_words, trim
from highlights import find_highlights

E = {"clips": [{"src": "main", "words": [0, 20]}]}


def test_cut_middle_splits_clip():
    assert cut_words(E, 5, 7)["clips"] == [{"src": "main", "words": [0, 4]}, {"src": "main", "words": [8, 20]}]


def test_cut_edges_and_keeps_other_keys():
    e = {"clips": [{"src": "main", "words": [0, 20], "zoom": 1.1, "crop_x": 0.4}]}
    out = cut_words(e, 0, 3)["clips"]
    assert out == [{"src": "main", "words": [4, 20], "zoom": 1.1, "crop_x": 0.4}]
    out = cut_words(e, 18, 99)["clips"]
    assert out == [{"src": "main", "words": [0, 17], "zoom": 1.1, "crop_x": 0.4}]


def test_cut_whole_clip_removes_it():
    e = {"clips": [{"src": "main", "words": [0, 4]}, {"src": "main", "words": [6, 9]}]}
    assert cut_words(e, 0, 4)["clips"] == [{"src": "main", "words": [6, 9]}]
    assert cut_words(e, 0, 20)["clips"] == []


def test_cut_outside_is_noop():
    assert cut_words(E, 30, 40) == E


def test_time_clips_pass_through():
    t = {"src": "main", "in": 1.0, "out": 4.0}
    e = {"clips": [t, {"src": "main", "words": [0, 10]}]}
    assert cut_words(e, 0, 10)["clips"] == [t]
    assert trim(e, 3, 4)["clips"] == [t, {"src": "main", "words": [3, 4]}]
    assert restore_words(e, 20, 25)["clips"][0] == t


def test_restore_merges():
    assert restore_words(cut_words(E, 5, 7), 5, 7)["clips"] == E["clips"]


def test_restore_merge_keeps_neighbour_keys():
    e = {"clips": [{"src": "main", "words": [0, 4], "zoom": 1.1}, {"src": "main", "words": [8, 20], "zoom": 1.2}]}
    assert restore_words(e, 5, 7)["clips"] == [{"src": "main", "words": [0, 20], "zoom": 1.1}]


def test_restore_non_adjacent_inserts_in_source_order():
    e = {"clips": [{"src": "main", "words": [0, 4]}, {"src": "main", "words": [20, 30]}]}
    assert restore_words(e, 10, 12)["clips"] == [{"src": "main", "words": [0, 4]}, {"src": "main", "words": [10, 12]},
                                                  {"src": "main", "words": [20, 30]}]
    assert restore_words(e, 40, 42)["clips"][-1] == {"src": "main", "words": [40, 42]}
    assert restore_words(e, 6, 8)["clips"][0] == {"src": "main", "words": [0, 4]}


def test_restore_overlap_does_not_duplicate():
    e = {"clips": [{"src": "main", "words": [0, 6]}]}
    assert restore_words(e, 4, 9)["clips"] == [{"src": "main", "words": [0, 9]}]


def test_restore_into_empty_edl():
    assert restore_words({"clips": []}, 3, 5)["clips"] == [{"src": "main", "words": [3, 5]}]


def test_trim_drops_outside_words():
    e = {"clips": [{"src": "main", "words": [0, 4]}, {"src": "main", "words": [8, 20]}, {"src": "main", "words": [25, 30]}]}
    assert trim(e, 3, 10)["clips"] == [{"src": "main", "words": [3, 4]}, {"src": "main", "words": [8, 10]}]


@pytest.mark.parametrize("op,args", [(cut_words, (1, 2)), (restore_words, (30, 32)), (trim, (2, 5)),
                                     (fix_word, ("a", "b")), (patch_section, ("framing", {"crop_x": 0.2}))])
def test_ops_are_pure(op, args):
    e = deepcopy(E)
    e["captions"] = {"replace": {"x": "y"}}
    snap = deepcopy(e)
    out = op(e, *args)
    assert e == snap
    assert out is not e and out["clips"] is not e["clips"]


def test_fix_word_adds_replace():
    assert fix_word(E, "cloud code", "Claude Code")["captions"]["replace"] == {"cloud code": "Claude Code"}
    e = fix_word(fix_word(E, "a", "b"), "c", "d")
    assert e["captions"]["replace"] == {"a": "b", "c": "d"}


def test_patch_section_shallow_merge():
    e = {"clips": [], "captions": {"style": "pop", "color": "#fff"}}
    out = patch_section(e, "captions", {"style": "box"})
    assert out["captions"] == {"style": "box", "color": "#fff"}
    assert patch_section(E, "audio", {"target_lufs": -16})["audio"] == {"target_lufs": -16}
    assert patch_section(E, "color", {"saturation": 1.1})["color"] == {"saturation": 1.1}


def test_patch_section_framing_maps_to_clips_and_fit():
    e = {"clips": [{"src": "main", "words": [0, 4]}, {"src": "main", "in": 1, "out": 2}]}
    out = patch_section(e, "framing", {"crop_x": 0.3, "zoom": 1.2, "fit": "blur"})
    assert out["fit"] == "blur"
    assert all(c["crop_x"] == 0.3 and c["zoom"] == 1.2 for c in out["clips"])
    assert "fit" not in out["clips"][0]


def test_patch_section_rejects_unknown():
    with pytest.raises(ValueError):
        patch_section(E, "nope", {})
    with pytest.raises(ValueError):
        patch_section(E, "framing", {"bogus": 1})


def test_kept_ranges_match_timeline(pipeline_workdir):
    wd = pipeline_workdir
    edl = load_json(wd / "edl.json")
    tr = load_json(wd / "transcript.main.json")
    assert any("words" in c for c in edl["clips"])
    tl = Timeline(wd / "edl.json")
    got = kept_ranges(edl, tr)
    assert len(got) == len(tl.clips)
    for (a, b), c in zip(got, tl.clips):
        assert a == pytest.approx(c["tin"], abs=1e-6)
        assert b == pytest.approx(c["tout"], abs=1e-6)


def test_kept_ranges_pad_and_time_clips(pipeline_workdir):
    tr = load_json(pipeline_workdir / "transcript.main.json")
    W = tr["words"]
    e = {"pad": [0, 0], "clips": [{"src": "main", "in": 1.0, "out": 2.5}, {"src": "main", "words": [2, 3]}]}
    got = kept_ranges(e, tr)
    assert got[0] == (1.0, pytest.approx(2.5))   # 1.5 s = exactly 45 frames at the default 30 fps
    assert got[1][0] == max(0.0, W[2]["start"])
    assert got[1][1] == pytest.approx(W[3]["end"], abs=1 / 30)


def test_clip_edl_stays_inside_highlight(en_podcast_wd):
    hs, _ = find_highlights(en_podcast_wd)
    h = hs[0]
    e = clip_edl(en_podcast_wd, h, "shorts", "en", None)
    assert e["clips"] and all("words" in c for c in e["clips"])
    assert all(h["w0"] <= c["words"][0] <= c["words"][1] <= h["w1"] for c in e["clips"])
    assert e["output"]["path"] == "" and e["output"]["aspect"] == "9:16"
    assert e["captions"]["language"] == "en" and e["captions"]["style"] == "pop"
    assert all("crop_x" in c for c in e["clips"])


def test_clip_edl_language_none_and_framing(en_podcast_wd, tmp_path):
    hs, _ = find_highlights(en_podcast_wd)
    h = hs[0]
    assert "language" not in clip_edl(en_podcast_wd, h, "youtube", None, None)["captions"]
    wd = tmp_path / "wd"
    wd.mkdir()
    for f in en_podcast_wd.glob("*.json"):
        (wd / f.name).write_text(f.read_text())
    (wd / "framing.json").write_text(json.dumps({h["id"]: {"crop_x": 0.37, "face": True}}))
    e = clip_edl(wd, h, "shorts", None, None)
    assert all(c["crop_x"] == 0.37 for c in e["clips"])


def test_clip_edl_silence_mode_cuts_by_time(tmp_path):
    save_json({"name": "main", "probe": {"path": "x.mp4", "duration": 60.0, "fps": 30},
               "silences": [{"start": 10.0, "end": 12.0}, {"start": 40.0, "end": 41.0}]}, tmp_path / "analysis.main.json")
    h = {"id": "h1", "start": 5.0, "end": 30.0, "w0": 0, "w1": 0, "score": 1, "title": "t", "features": {}}
    e = clip_edl(tmp_path, h, "youtube", None, None)
    assert all("in" in c for c in e["clips"])
    assert e["clips"][0]["in"] == 5.0 and e["clips"][-1]["out"] == 30.0
    assert all(5.0 <= c["in"] < c["out"] <= 30.0 for c in e["clips"])


@pytest.mark.parametrize("w0,w1", [(7, 5), (-1, 3), (2, -1)])
def test_invalid_ranges_raise(w0, w1):
    for op in (cut_words, restore_words, trim):
        with pytest.raises(ValueError):
            op(E, w0, w1)


def test_kept_ranges_raise_value_error_not_systemexit():
    tr = {"words": [{"start": 0.0, "end": 0.5}, {"start": 0.6, "end": 1.0}], "duration": 2.0}
    for e in ({"clips": [{"words": [0, 5]}]}, {"clips": [{"in": 3, "out": 2}]}):
        with pytest.raises(ValueError):
            kept_ranges(e, tr)
    with pytest.raises(ValueError):
        kept_ranges({"clips": [{"words": [0, 1]}]}, {})


def test_kept_ranges_snap_to_frames_with_speed():
    tr = {"words": [], "duration": 10.0}
    got = kept_ranges({"output": {"fps": 10}, "clips": [{"in": 0, "out": 1.04, "speed": 2.0}]}, tr)
    assert got == [(0.0, pytest.approx(1.0))]   # 1.04/2*10 = 5.2 -> 5 frames -> 0.5 s * 2


def test_clip_edl_no_clips_raises(tmp_path):
    save_json({"name": "main", "probe": {"path": "x.mp4", "duration": 60.0, "fps": 30},
               "silences": [{"start": 4.0, "end": 50.0}]}, tmp_path / "analysis.main.json")
    h = {"id": "h1", "start": 10.0, "end": 30.0, "w0": 0, "w1": 0, "score": 1, "title": "t", "features": {}}
    with pytest.raises(ValueError):
        clip_edl(tmp_path, h, "youtube", None, None)


def test_fix_word_same_removes_key():
    e = fix_word({"clips": [], "captions": {"replace": {"teh": "the", "x": "y"}}}, "teh", "teh")
    assert e["captions"]["replace"] == {"x": "y"}
    assert fix_word({"clips": []}, "a", "a")["captions"]["replace"] == {}  # absent key is fine


@pytest.mark.parametrize("section", ["captions", "audio", "color"])
def test_patch_section_null_removes_key(section):
    e = {"clips": [], section: {"a": 1, "b": 2}}
    out = patch_section(e, section, {"a": None, "c": None, "b": 3})
    assert out[section] == {"b": 3}
    assert patch_section({"clips": []}, section, {"a": None})[section] == {}


def test_patch_section_framing_null_removes_keys():
    e = {"fit": "blur", "clips": [{"words": [0, 1], "crop_x": 0.3, "zoom": 1.2}, {"in": 1, "out": 2, "crop_x": 0.5}]}
    out = patch_section(e, "framing", {"crop_x": None, "zoom": None, "fit": None})
    assert "fit" not in out
    assert all("crop_x" not in c and "zoom" not in c for c in out["clips"])
    assert out["clips"][0]["words"] == [0, 1] and "fit" not in patch_section({"clips": []}, "framing", {"fit": None})
