"""karaoke and emoji caption styles: the ASS they burn in and the events the live preview draws."""
import re

import pytest
from captions import EMOJI, build, caption_events, emoji_for
from common import hex_to_ass
from conftest import _variant
from edl import Timeline

LINE = re.compile(r"^Dialogue: 0,(\d+):(\d\d):(\d\d)\.(\d\d),(\d+):(\d\d):(\d\d)\.(\d\d),Cap,,0,0,0,,(.*)$", re.M)
SYL = re.compile(r"\\(kf|k)(\d+)")


def _t(h, m, s, cs):
    return int(h) * 3600 + int(m) * 60 + int(s) + int(cs) / 100


def caption_lines(ass):
    return [(_t(*m.group(1, 2, 3, 4)), _t(*m.group(5, 6, 7, 8)), m.group(9)) for m in LINE.finditer(ass)]


@pytest.fixture(scope="session")
def karaoke_wd(pipeline_workdir_9x16, tmp_path_factory):
    return _variant(pipeline_workdir_9x16, tmp_path_factory, "wdkara", {"style": "karaoke", "highlight": "#00E5FF"})


@pytest.fixture(scope="session")
def karaoke_speakers_wd(pipeline_workdir_9x16, tmp_path_factory):
    return _variant(pipeline_workdir_9x16, tmp_path_factory, "wdkaraspk", {
        "style": "karaoke", "speaker_colors": {"maria": "#ff6fb5", "narrator": "#ffd400"}}, speakers=True)


@pytest.fixture(scope="session")
def emoji_wd(pipeline_workdir_9x16, tmp_path_factory):
    return _variant(pipeline_workdir_9x16, tmp_path_factory, "wdemoji", {"style": "emoji", "emoji_font": "Segoe UI Emoji"})


# ---- karaoke ------------------------------------------------------------------------------------------------------

def test_karaoke_is_one_line_per_group_with_the_whole_text(karaoke_wd):
    tl = Timeline(karaoke_wd / "edl.json")
    ass, srt, _ = build(tl)
    ev = caption_events(tl)
    lines = caption_lines(ass)
    shown = [(g, x) for g, x in zip(ev["events"], srt) if g["end"] > g["start"]]  # a cut retake has no screen time
    assert ev["style"] == "karaoke" and ev["uppercase"] is True
    assert len(lines) == len(shown) and len(srt) == len(ev["events"])
    for (s, e, text), (group, (_, _, plain)) in zip(lines, shown):
        assert s == pytest.approx(group["start"], abs=0.01) and e == pytest.approx(group["end"], abs=0.01)
        assert re.sub(r"\{[^}]*\}", "", text) == plain  # every word is on screen for the whole group
        assert "fscx" not in text  # no pop: the words fill, they don't jump


def test_karaoke_fills_each_word_while_it_is_spoken(karaoke_wd):
    tl = Timeline(karaoke_wd / "edl.json")
    ev = caption_events(tl)
    col, hi = hex_to_ass("#FFFFFF"), hex_to_ass("#00E5FF")
    shown = [g for g in ev["events"] if g["end"] > g["start"]]
    for (_, _, text), group in zip(caption_lines(build(tl)[0]), shown, strict=True):
        # filled = highlight (primary), waiting = base colour (secondary)
        assert text.startswith(r"{\1c" + hi + r"\2c" + col)
        assert text.count(r"\kf") == len(group["words"])
        cursor, k = 0, 0  # centiseconds from the group start, word index
        for kind, n in SYL.findall(text):
            if kind == "k":  # a pause before the next word
                cursor += int(n)
                continue
            w = group["words"][k]
            assert cursor == pytest.approx((w["start"] - group["start"]) * 100, abs=1.01)
            cursor += int(n)
            assert cursor == pytest.approx((min(w["end"], group["end"]) - group["start"]) * 100, abs=1.01) \
                or int(n) == 1  # a word too short to time still gets one centisecond
            k += 1
        assert k == len(group["words"])
        assert cursor <= round((group["end"] - group["start"]) * 100) + 1


def test_karaoke_uses_speaker_colours(karaoke_speakers_wd):
    ass = build(Timeline(karaoke_speakers_wd / "edl.json"))[0]
    texts = [x[2] for x in caption_lines(ass)]
    assert any(r"\1c" + hex_to_ass("#ff6fb5") in t for t in texts)
    assert any(r"\1c" + hex_to_ass("#ffd400") in t for t in texts)


# ---- emoji --------------------------------------------------------------------------------------------------------

def test_emoji_keywords_in_english_and_filipino():
    assert emoji_for("Hello,") == "👋" and emoji_for("THANKS!") == "🙏"
    assert emoji_for("salamat") == "🙏" and emoji_for("Galing!") == "🔥" and emoji_for("mahal") == "❤️"
    assert emoji_for("everyone") is None and emoji_for("") is None
    assert all(k == k.lower() and re.fullmatch(r"[\w']+", k) for k in EMOJI)  # norm_word spellings


def test_emoji_burns_in_after_keywords(emoji_wd):
    tl = Timeline(emoji_wd / "edl.json")
    ass, srt, _ = build(tl)
    texts = [x[2] for x in caption_lines(ass)]
    tag = r"{\fnSegoe UI Emoji}👋{\fnMontserrat ExtraBold}"
    assert any("HELLO" in t and tag in t for t in texts)
    assert all(t.index(tag) > t.index("HELLO") for t in texts if tag in t)
    assert any("🙏" in t for t in texts) and any("👀" in t for t in texts)
    # still pop captions: one event per active word, the active word highlighted and scaled
    assert any(r"\fscx90" in t for t in texts) and any(r"\fscx108" in t for t in texts)
    assert srt[0][2] == "HELLO 👋 EVERYONE,"


def test_emoji_events_for_the_preview(emoji_wd, pipeline_workdir_9x16):
    ev = caption_events(Timeline(emoji_wd / "edl.json"))
    words = [w for e in ev["events"] for w in e["words"]]
    assert ev["style"] == "emoji" and ev["uppercase"] is True
    got = {w["text"].lower().strip(",."): w["emoji"] for w in words if "emoji" in w}
    assert got.items() >= {"hello": "👋", "thanks": "🙏"}.items()
    assert all("emoji" not in w for w in words if emoji_for(w["text"]) is None)
    pop = caption_events(Timeline(pipeline_workdir_9x16 / "edl.json"))
    assert not any("emoji" in w for e in pop["events"] for w in e["words"])  # only the emoji style adds them
