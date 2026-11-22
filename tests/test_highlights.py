"""Offline clip finding: candidate windows, scoring, selection, titles."""
import random
import shutil
import time
from pathlib import Path

import pytest

from common import load_json, save_json
from conftest import ff, run_script
from highlights import WEIGHTS, energy_profile, find_highlights, make_title
from transcribe import build

FIX = Path(__file__).resolve().parent / "fixtures" / "transcripts"


def _wd_from(tmp_path_factory, name):
    d = tmp_path_factory.mktemp(name)
    shutil.copy(FIX / f"{name}.json", d / "transcript.main.json")
    shutil.copy(FIX / f"{name}.analysis.json", d / "analysis.main.json")
    return d


def _synthetic_wd(d, lines_and_gaps, duration=None):
    """lines_and_gaps: [(text, words_per_second, gap_after)] -> transcript + analysis with no source video."""
    words, sil, t = [], [], 0.0
    for text, rate, gap in lines_and_gaps:
        toks = text.split()
        step = 1 / rate
        for k, w in enumerate(toks):
            words.append({"start": t + k * step, "end": t + (k + 0.85) * step, "text": w, "p": 0.9})
        t += len(toks) * step
        if gap >= 0.35:
            sil.append({"start": round(t, 3), "end": round(t + gap, 3)})
        t += gap
    data = build(str(d / "missing.mp4"), words, "en", "main", duration or t)
    save_json(data, d / "transcript.main.json")
    save_json({"name": "main", "probe": {"duration": duration or t}, "silences": sil}, d / "analysis.main.json")
    return d


@pytest.fixture(scope="module")
def en_podcast_wd(tmp_path_factory):
    return _wd_from(tmp_path_factory, "en_podcast")


@pytest.fixture(scope="module")
def taglish_wd(tmp_path_factory):
    return _wd_from(tmp_path_factory, "taglish_vlog")


@pytest.fixture(scope="module")
def music_only_wd(tmp_path_factory):
    return _wd_from(tmp_path_factory, "music_only")


SHORT_TALK = [
    ("Quick tip for today before you head out.", 2.5, 0.5),
    ("Always back up your footage twice.", 2.5, 0.5),
    ("One copy on a drive and one in the cloud.", 2.5, 0.4),
    ("That's it, see you tomorrow.", 2.5, 0.0),
]


@pytest.fixture(scope="module")
def short_wd(tmp_path_factory):
    return _synthetic_wd(tmp_path_factory.mktemp("short"), SHORT_TALK, duration=15.0)


@pytest.fixture(scope="module")
def short_speech_long_video_wd(tmp_path_factory):
    return _synthetic_wd(tmp_path_factory.mktemp("shortlong"), SHORT_TALK, duration=120.0)


VOCAB = ("we went to the market and talked about money and work and family and the new place "
         "then everyone laughed because the plan changed again at the last minute").split()
HOOKS = ["Nobody believes this part", "Why does this keep happening?", "The worst day was", "Grabe talaga",
         "Here is the secret", "Paano ba"]


@pytest.fixture(scope="module")
def long_wd(tmp_path_factory):
    rng = random.Random(11)
    rows, t = [], 0.0
    while t < 3 * 3600:
        n = rng.randint(6, 18)
        text = " ".join(rng.choice(VOCAB) for _ in range(n)) + "."
        if rng.random() < 0.03:
            text = rng.choice(HOOKS) + " " + text
        if rng.random() < 0.2:
            text = "um " + text
        rate = rng.uniform(2.0, 3.6)
        gap = 3.0 if rng.random() < 0.04 else rng.uniform(0.2, 0.9)
        rows.append((text, rate, gap))
        t += len(text.split()) / rate + gap
    return _synthetic_wd(tmp_path_factory.mktemp("long"), rows)


def last_word_index(wd):
    return len(load_json(wd / "transcript.main.json")["words"]) - 1


# --- brief tests --------------------------------------------------------------------------------------------

def test_windows_respect_bounds(en_podcast_wd):
    hs, why = find_highlights(en_podcast_wd)
    assert why is None and 1 <= len(hs) <= 12
    for h in hs:
        assert 20 <= h["end"] - h["start"] <= 90 and 0 <= h["score"] <= 100


def test_no_overlap_sorted_by_score(en_podcast_wd):
    hs, _ = find_highlights(en_podcast_wd)
    assert [h["score"] for h in hs] == sorted((h["score"] for h in hs), reverse=True)
    spans = sorted((h["start"], h["end"]) for h in hs)
    assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:]))


def test_starts_and_ends_on_sentences(en_podcast_wd):
    tr = load_json(en_podcast_wd / "transcript.main.json")
    starts = {s["w0"] for s in tr["sentences"]}
    ends = {s["w1"] for s in tr["sentences"]}
    assert all(h["w0"] in starts and h["w1"] in ends for h in find_highlights(en_podcast_wd)[0])


def test_taglish_hook_words_score_higher(taglish_wd):
    hs, _ = find_highlights(taglish_wd)
    assert hs[0]["title"].lower().startswith("alam mo ba")      # fixture puts the hook in one window


def test_music_only_returns_reason(music_only_wd):
    assert find_highlights(music_only_wd) == ([], "no_speech")


def test_short_video_is_one_clip(short_wd):                      # 14 s of speech in a 15 s video
    hs, _ = find_highlights(short_wd)
    assert len(hs) == 1 and hs[0]["start"] == 0 and hs[0]["w1"] == last_word_index(short_wd)
    assert hs[0]["end"] == 15.0 and hs[0]["w0"] == 0


def test_short_speech_in_long_video_is_one_padded_clip(short_speech_long_video_wd):
    words = load_json(short_speech_long_video_wd / "transcript.main.json")["words"]
    hs, why = find_highlights(short_speech_long_video_wd)          # ~13 s of speech in a 120 s video
    assert why is None and len(hs) == 1
    h = hs[0]
    assert h["w0"] == 0 and h["w1"] == len(words) - 1
    assert h["start"] <= words[0]["start"] and h["end"] >= words[-1]["end"]
    assert 0 <= h["start"] and h["end"] <= 120.0 and h["end"] - h["start"] >= min(20, 120.0) - 1e-6


def test_padding_is_even_and_clamped():
    from highlights import _padded
    assert _padded(10, 20, 20, 0, 100) == (5, 25)
    assert _padded(2, 12, 20, 0, 100) == (0, 20)
    assert _padded(2, 12, 40, 0, 30) == (0, 30)                    # both sides clamped: the whole video                  # start side clamped, end takes the rest
    assert _padded(90, 98, 20, 0, 100) == (80, 100)               # end side clamped, start takes the rest



def test_three_hour_video_fast_and_spread(long_wd):              # synthetic 3 h transcript, no audio
    t0 = time.time()
    hs, _ = find_highlights(long_wd)
    assert time.time() - t0 < 10 and len(hs) == 12
    assert max(h["start"] for h in hs) - min(h["start"] for h in hs) > 3600


# --- extra coverage -----------------------------------------------------------------------------------------

def test_shape_ids_and_features(en_podcast_wd):
    hs, _ = find_highlights(en_podcast_wd, max_clips=5)
    assert [h["id"] for h in hs] == [f"h{i:02d}" for i in range(1, len(hs) + 1)]
    for h in hs:
        assert set(h["features"]) == set(WEIGHTS)
        assert all(0 <= v <= 1 for v in h["features"].values())
        assert h["features"]["face"] == 0.5 and h["features"]["energy"] == 0.5   # no framing, no audio


def test_no_window_spans_long_silence(en_podcast_wd):
    ana = load_json(en_podcast_wd / "analysis.main.json")
    long_sil = [s for s in ana["silences"] if s["end"] - s["start"] > 2]
    assert long_sil
    for h in find_highlights(en_podcast_wd)[0]:
        assert not any(h["start"] < s["start"] and s["end"] < h["end"] for s in long_sil)


def test_favours_30_to_60_s_clips(en_podcast_wd):
    hs, _ = find_highlights(en_podcast_wd)
    long_ones = [h for h in hs if h["end"] - h["start"] >= 30]
    assert len(long_ones) * 3 >= len(hs), [round(h["end"] - h["start"]) for h in hs]


def test_spacing_between_clips(en_podcast_wd):
    hs, _ = find_highlights(en_podcast_wd, max_clips=3)   # plenty of room: 30 s gap must hold
    spans = sorted((h["start"], h["end"]) for h in hs)
    assert len(hs) == 3 and all(b[0] - a[1] >= 30 for a, b in zip(spans, spans[1:]))


def test_hook_and_completeness_features(taglish_wd):
    hs, _ = find_highlights(taglish_wd)
    assert hs[0]["features"]["hook"] == 1 and all(h["features"]["hook"] == 0 for h in hs[1:])
    assert all(h["features"]["complete"] == 1 for h in hs)


def test_title_rules():
    words = [{"text": t, "filler": f} for t, f in
             [("um,", "hard"), ("so", "soft"), ("THE", None), ("BIGGEST", None), ("mistake", None),
              ("uh", "hard"), ("I", None), ("made", None), ("was", None), ("chasing", None), ("trends", None),
              ("that", None), ("nobody", None), ("in", None), ("my", None), ("audience", None), ("wanted.", None)]]
    title = make_title(words)
    assert title.startswith("The biggest mistake I made")
    assert len(title) <= 48 and "uh" not in title.split() and not title.endswith(" ")
    assert title == "The biggest mistake I made was chasing trends"


def test_face_is_constant_until_framing_runs_before_scoring(tmp_path, taglish_wd):
    for f in taglish_wd.glob("*.json"):
        shutil.copy(f, tmp_path / f.name)
    hs, _ = find_highlights(tmp_path)
    save_json({"highlights": hs}, tmp_path / "highlights.json")   # a stale run plus framing keyed by its ids
    save_json({h["id"]: {"crop_x": 0.5, "face": h["id"] == "h02"} for h in hs}, tmp_path / "framing.json")
    assert all(h["features"]["face"] == 0.5 for h in find_highlights(tmp_path)[0])


def test_energy_profile_and_cache(tmp_path, ffmpeg_ok):
    v = tmp_path / "beep.mp4"
    ff("-f", "lavfi", "-i", "sine=f=440:d=2", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-filter_complex",
       "[1]atrim=0:2[s];[0][s]concat=n=2:v=0:a=1", "-c:a", "aac", str(v))
    prof = energy_profile(v, hop=0.5, wd=tmp_path)
    assert 7 <= len(prof) <= 9
    assert min(prof[:3]) > max(prof[-3:]) + 20
    assert (tmp_path / "energy.main.json").is_file()
    assert energy_profile(v, hop=0.5, wd=tmp_path) == prof


def test_cli_writes_json(taglish_wd):
    r = run_script("highlights.py", taglish_wd, "--max", "3", "--json")
    out = load_json(taglish_wd / "highlights.json")
    assert len(out["highlights"]) == 3 and out["reason"] is None
    assert '"h01"' in r.stdout
    r = run_script("highlights.py", taglish_wd, "--max", "2")
    assert "h01" in r.stdout and "h02" in r.stdout
