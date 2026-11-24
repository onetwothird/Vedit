"""The b-roll matcher: file-name words against the words a clip plays, and the optional AI pass."""
import json

import pytest

import broll


def W(*spec, step=0.5):
    """Words `step` seconds apart: W("I", "love", "Manila") -> transcript words."""
    return [{"text": t, "start": k * step, "end": k * step + 0.4} for k, t in enumerate(spec)]


def media(tmp_path, *names):
    for n in names:
        f = tmp_path / n
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"x")
    return tmp_path


@pytest.mark.parametrize("word,expected", [
    ("Beaches,", "beach"), ("cities", "city"), ("Café", "cafe"), ("glass", "glass"), ("bus", "bus"),
    ("boxes", "box"), ("dogs", "dog"), ("Manila.", "manila"),
])
def test_norm(word, expected):
    assert broll.norm(word) == expected


def test_name_words_drop_camera_prefixes_dates_and_particles():
    assert broll.name_words("IMG_20240501_beach-sunset.JPG") == ["beach", "sunset"]
    assert broll.name_words("mangoTree 4k final.mp4") == ["mango", "tree"]
    assert broll.name_words("mga_jeepney sa Manila.mov") == ["jeepney", "manila"]
    assert broll.name_words("DSC0001.jpg") == []


def test_list_media_only_photos_and_videos_two_levels_deep(tmp_path):
    media(tmp_path, "a.jpg", "b.MP4", "notes.txt", ".hidden.png", "sub/c.png", "sub/deeper/d.webp",
          "sub/deeper/deepest/e.jpg", ".git/f.png")
    names = sorted(p.name for p in broll.list_media(tmp_path))
    assert names == ["a.jpg", "b.MP4", "c.png", "d.webp"]
    assert len(broll.list_media(tmp_path, max_files=2)) == 2


def test_clip_words_keep_only_played_words_without_hard_fillers():
    tr = {"words": W("so", "um", "the", "beach", "was", "cold")}
    tr["words"][1]["filler"] = "hard"
    got = broll.clip_words(tr, [(0.0, 1.0), (2.0, 2.9)])
    assert [w["text"] for w in got] == ["so", "was", "cold"]
    assert [w["i"] for w in got] == [0, 4, 5]


def test_match_names_places_each_file_at_its_strongest_moment(tmp_path):
    folder = media(tmp_path, "beach.jpg", "beach-sunset.jpg", "jeepney.mp4", "office.png")
    words = broll.clip_words({"words": W("We", "went", "to", "the", "beach", "then", "watched", "the", "sunset",
                                         "on", "the", "beach", "and", "rode", "jeepneys", "home")}, [(0, 99)])
    got = {s["name"]: s for s in broll.match_names(words, broll.list_media(folder), [(0, 99)])}
    assert set(got) == {"beach.jpg", "beach-sunset.jpg", "jeepney.mp4"}  # nobody says "office"
    assert got["beach-sunset.jpg"]["score"] > got["beach.jpg"]["score"]  # both name words said together
    assert got["beach-sunset.jpg"]["t"] == pytest.approx(4 * 0.5)  # first moment both words are near
    assert got["beach-sunset.jpg"]["word"] == "beach sunset"
    assert got["jeepney.mp4"] == {**got["jeepney.mp4"], "type": "video", "t": 7.0, "source": "name"}
    assert got["jeepney.mp4"]["path"] == str(folder / "jeepney.mp4")


def test_suggestion_length_stops_at_the_end_of_its_kept_range(tmp_path):
    folder = media(tmp_path, "cake.jpg", "dog.jpg")
    words = broll.clip_words({"words": W("a", "cake", "and", "a", "dog")}, [(0, 1.5), (1.5, 2.4)])
    got = {s["name"]: s for s in broll.match_names(words, broll.list_media(folder), [(0, 1.5), (1.5, 2.4)])}
    assert got["cake.jpg"]["duration"] == 1.0  # 0.5 -> 1.5
    assert got["dog.jpg"]["duration"] == broll.MIN_LENGTH  # 2.0 -> 2.4 is shorter than the floor


def test_pick_skips_overlaps_used_files_and_returns_time_order():
    s = lambda name, t, d=3.0: {"path": f"/m/{name}", "t": t, "duration": d}  # noqa: E731
    cands = [s("a.jpg", 10), s("b.jpg", 11), s("a.jpg", 20), s("c.jpg", 2), s("d.jpg", 30), s("e.jpg", 40)]
    got = broll.pick(cands, taken=[(29, 31)], used_paths=["/m/e.jpg"])
    assert [(x["path"], x["t"]) for x in got] == [("/m/c.jpg", 2), ("/m/a.jpg", 10)]
    assert len(broll.pick([s(f"{k}.jpg", k * 5) for k in range(30)], limit=4)) == 4


class FakeHttp:
    """Stands in for httpx.Client: records the request, answers with `text` as the AI's reply."""

    def __init__(self, text, status=200):
        self.text, self.status, self.sent = text, status, None

    def post(self, url, **kw):
        self.sent = {"url": url, **kw}
        reply = self

        class R:
            status_code = reply.status

            def raise_for_status(self):
                if reply.status >= 400:
                    raise RuntimeError("http error")

            def json(self):
                return {"content": [{"type": "text", "text": reply.text}]}
        return R()


def test_suggest_merges_ai_picks_first_then_name_matches(tmp_path):
    folder = media(tmp_path / "pics", "shore.jpg", "dog.jpg", "cat.jpg")
    tr = {"words": W("we", "walked", "along", "the", "sea", "with", "my", "dog", "and", "a", "big", "cat", step=1.0)}
    reply = json.dumps({"broll": [
        {"t": 4.1, "file": "shore.jpg", "word": "the sea", "why": "the sea is a shore"},
        {"t": 6.8, "file": "DOG.JPG", "word": "dog"},
        {"t": 1.0, "file": "../../etc/passwd"},  # not a listed file
        {"t": 999, "file": "cat.jpg"},  # outside the clip
        {"file": "cat.jpg"},  # no time
    ]})
    http = FakeHttp(reply)
    out = broll.suggest(tr, [(0, 99)], folder, ai={"provider": "anthropic", "api_key": "sk-test"}, http=http)
    assert out["ai"] == "ok" and out["files"] == 3
    got = [(s["name"], s["t"], s["source"]) for s in out["suggestions"]]
    assert got == [("shore.jpg", 4.0, "ai"), ("dog.jpg", 7.0, "ai"), ("cat.jpg", 11.0, "name")]
    assert out["suggestions"][0]["why"] == "the sea is a shore"
    assert "score" not in out["suggestions"][0]
    body = json.dumps(http.sent["json"])
    assert "shore.jpg" in body and str(folder) not in body and "pics" not in body  # names only, never the folder
    assert http.sent["json"]["system"] == broll.AI_PROMPT


def test_suggest_keeps_name_matches_when_the_ai_fails(tmp_path):
    folder = media(tmp_path, "dog.jpg")
    tr = {"words": W("my", "dog")}
    for http in (FakeHttp("not json"), FakeHttp("{}", status=401)):
        out = broll.suggest(tr, [(0, 9)], folder, ai={"provider": "anthropic", "api_key": "sk-test"}, http=http)
        assert out["ai"] == broll.AI_FAILED
        assert [s["name"] for s in out["suggestions"]] == ["dog.jpg"]


def test_suggest_without_a_key_never_calls_out_and_respects_existing_broll(tmp_path):
    folder = media(tmp_path, "dog.jpg", "cat.jpg", "bird.jpg")
    tr = {"words": W("dog", "x", "x", "x", "x", "x", "x", "x", "cat", "x", "x", "x", "x", "x", "x", "x", "bird")}
    existing = [{"type": "image", "path": str(folder / "dog.jpg"), "at": {"src": "main", "t": 0.0}, "duration": 1},
                {"type": "video", "path": str(folder / "x.mp4"), "at": {"src": "main", "t": 7.5}, "duration": 2},
                {"type": "text", "text": "hi", "at": {"src": "main", "t": 4.0}, "duration": 3}]
    out = broll.suggest(tr, [(0, 99)], folder, overlays=existing, ai={"provider": "anthropic", "api_key": ""})
    assert out["ai"] is None
    assert [s["name"] for s in out["suggestions"]] == ["cat.jpg"]  # dog is used; bird overlaps the video
