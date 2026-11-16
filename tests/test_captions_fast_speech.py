"""Fast speech: captions must stay on screen long enough to read, without joining across pauses or cuts."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from captions import group_words, join_hyphen_pieces, merge_fast  # noqa: E402


def words(spec, clip=0):
    """spec: list of (text, start, end)."""
    return [{"text": t, "ostart": s, "oend": e, "clip": clip} for t, s, e in spec]


def dur(g):
    return g[-1]["oend"] - g[0]["ostart"]


def test_fast_talker_groups_are_merged_until_readable():
    # 8 words in 1.6 s with commas: today this splits into flashes as short as 0.14 s
    w = words([("oh", 0.0, 0.15), ("hindi", 0.15, 0.35), ("na,", 0.35, 0.5), ("maging", 0.5, 0.7),
               ("daily", 0.7, 0.9), ("reality", 0.9, 1.1), ("siya,", 1.1, 1.3), ("hindi", 1.3, 1.44)])
    before = group_words(w, 3, 16)
    assert min(dur(g) for g in before) < 0.4
    after = merge_fast(before, 3, 16, 0.7)
    assert [x["text"] for g in after for x in g] == [x["text"] for x in w]  # nothing lost or reordered
    assert all(dur(g) >= 0.7 for g in after[:-1])
    assert all(len(g) <= 6 for g in after)


def test_no_merge_across_a_pause():
    w = words([("hello", 0.0, 0.2), ("there", 1.5, 1.7)])
    assert len(merge_fast(group_words(w, 3, 16), 3, 16, 0.7)) == 2


def test_no_merge_across_a_cut():
    w = words([("hello", 0.0, 0.2)]) + words([("there", 0.25, 0.45)], clip=1)
    assert len(merge_fast(group_words(w, 3, 16), 3, 16, 0.7)) == 2


def test_merged_caption_never_exceeds_two_lines():
    w = words([("pagkakaintindihan", 0.0, 0.2), ("napakagandang", 0.2, 0.4), ("pinagsasabihan", 0.4, 0.6)])
    for g in merge_fast(group_words(w, 3, 16), 3, 16, 0.7):
        assert sum(len(x["text"]) + 1 for x in g) - 1 <= 32


def test_taglish_hyphen_word_is_glued_back():
    w = words([("hindi,", 0.0, 0.3), ("nag", 0.3, 0.5), ("-agree", 0.5, 0.8), ("ako", 0.8, 1.0)])
    out = join_hyphen_pieces(w)
    assert [x["text"] for x in out] == ["hindi,", "nag-agree", "ako"]
    assert out[1]["ostart"] == 0.3 and out[1]["oend"] == 0.8


def test_hyphen_not_glued_across_sentence_or_cut():
    assert len(join_hyphen_pieces(words([("tapos.", 0, 0.3), ("-ayun", 0.3, 0.5)]))) == 2
    assert len(join_hyphen_pieces(words([("nag", 0, 0.3)]) + words([("-agree", 0.3, 0.5)], clip=1))) == 2


def test_normal_speed_is_unchanged():
    w = words([("one", 0.0, 0.4), ("two", 0.4, 0.8), ("three.", 0.8, 1.2), ("four", 1.3, 1.7), ("five", 1.7, 2.1)])
    before = group_words(w, 3, 16)
    assert merge_fast(before, 3, 16, 0.7) == before
