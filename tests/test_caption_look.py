"""Caption look settings the editor exposes: font, size, outline, bold. The burned-in ASS and the preview events agree."""
import re

import pytest
from captions import SINGLE_WEIGHT, build, caption_events
from conftest import _variant
from edl import Timeline

CAP = re.compile(r"^Style: Cap,([^,]*),(\d+),(?:[^,]*,){4}(-?\d),(?:[^,]*,){8}([\d.]+),", re.M)
TITLE = re.compile(r"^Style: Title,[^,]*,(\d+),", re.M)


def cap_style(ass):
    font, size, bold, outline = CAP.search(ass).groups()
    return font, int(size), int(bold), float(outline)


def look(base, factory, name, captions):
    tl = Timeline(_variant(base, factory, name, captions) / "edl.json")
    return build(tl)[0], caption_events(tl)


@pytest.fixture(scope="session")
def default_look(pipeline_workdir_9x16, tmp_path_factory):
    return look(pipeline_workdir_9x16, tmp_path_factory, "lookdef", {"style": "pop"})


def test_scale_grows_the_caption_but_not_the_titles(pipeline_workdir_9x16, tmp_path_factory, default_look):
    ass, ev = look(pipeline_workdir_9x16, tmp_path_factory, "lookbig", {"style": "pop", "scale": 150})
    base_ass, base_ev = default_look
    assert cap_style(ass)[1] == round(cap_style(base_ass)[1] * 1.5)
    assert ev["size_frac"] == pytest.approx(base_ev["size_frac"] * 1.5, rel=0.02)
    assert TITLE.search(ass).group(1) == TITLE.search(base_ass).group(1)


def test_scale_out_of_range_is_clamped(pipeline_workdir_9x16, tmp_path_factory, default_look):
    _, ev = look(pipeline_workdir_9x16, tmp_path_factory, "looktiny", {"style": "pop", "scale": 1})
    assert ev["size_frac"] == pytest.approx(default_look[1]["size_frac"] * 0.4, rel=0.05)
    _, ev = look(pipeline_workdir_9x16, tmp_path_factory, "lookjunk", {"style": "pop", "scale": "huge"})
    assert ev["size_frac"] == pytest.approx(default_look[1]["size_frac"])


def test_single_weight_fonts_are_not_fake_bolded(pipeline_workdir_9x16, tmp_path_factory, default_look):
    assert "Anton" in SINGLE_WEIGHT
    ass, ev = look(pipeline_workdir_9x16, tmp_path_factory, "lookanton", {"style": "pop", "font": "Anton"})
    assert cap_style(ass)[0] == "Anton" and cap_style(ass)[2] == 0 and ev["bold"] is False
    assert cap_style(default_look[0])[2] == -1 and default_look[1]["bold"] is True


def test_outline_can_be_turned_off_and_a_comma_cannot_break_the_style(pipeline_workdir_9x16, tmp_path_factory):
    ass, ev = look(pipeline_workdir_9x16, tmp_path_factory, "lookline",
                   {"style": "pop", "outline_scale": 0, "font": "Bad,Font"})
    font, _, _, outline = cap_style(ass)
    assert outline == 0 and ev["outline_frac"] == 0
    assert font == "Bad Font"


@pytest.mark.parametrize("n", [1, 2, 3, 5])
def test_words_at_a_time_is_exact(pipeline_workdir_9x16, tmp_path_factory, n):
    _, ev = look(pipeline_workdir_9x16, tmp_path_factory, f"lookw{n}", {"style": "pop", "max_words": n})
    sizes = [len(e["words"]) for e in ev["events"]]
    assert max(sizes) == n
    assert sum(sizes) == len([w for e in ev["events"] for w in e["words"]])


def test_words_at_a_time_matches_the_burned_in_groups(pipeline_workdir_9x16, tmp_path_factory):
    ass, ev = look(pipeline_workdir_9x16, tmp_path_factory, "lookw2ass", {"style": "clean", "max_words": 2})
    lines = [ln for ln in ass.splitlines() if ln.startswith("Dialogue: 0,")]
    assert len(lines) == len(ev["events"]) and all(len(e["words"]) <= 2 for e in ev["events"])
