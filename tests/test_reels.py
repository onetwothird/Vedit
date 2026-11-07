"""Fact Reels kit: brand loading, cards, and a story with raw video scenes (no internet / Whisper needed)."""
import json
import shutil
from pathlib import Path

import pytest
from conftest import SCRIPTS, ff, probe, run_script

TEMPLATE = SCRIPTS.parent / "templates" / "fact_reels"


def test_brand_defaults_and_lookup(tmp_path):
    import brand
    (tmp_path / "kit" / "stories" / "a").mkdir(parents=True)
    (tmp_path / "kit" / "brand.json").write_text(json.dumps({"name": "Test", "colors": {"accent": "#00FF00"}}))
    bp = brand.find_brand(tmp_path / "kit" / "stories" / "a")
    assert bp == (tmp_path / "kit" / "brand.json").resolve()
    b = brand.load(bp)
    assert b["colors"]["accent"] == "#00FF00" and b["colors"]["shadow"] == "#0B1A50"  # override + default
    assert b["header_bottom"] == 200 and b["background"].name == "card_background.png"
    assert brand.find_brand(tmp_path) is None


def test_generated_background_and_cards(tmp_path):
    from PIL import Image
    kit = tmp_path / "kit"
    shutil.copytree(TEMPLATE, kit)
    sp = kit / "_new_story_template" / "story.json"
    run_script("cards.py", "cards", sp)
    run_script("cards.py", "thumbnail", sp)
    assert Image.open(kit / "brand" / "card_background.png").size == (1080, 1920)
    for name in ("01.jpg", "02.jpg", "03.jpg"):  # image scene + 2 video scenes (card_image)
        assert Image.open(kit / "_new_story_template" / "cards" / name).size == (1080, 1920)
    assert (kit / "_new_story_template" / "thumbnail.jpg").exists()


def test_reels_new_and_story(tmp_path):
    run_script("reels.py", "new", tmp_path / "ch")
    run_script("reels.py", "story", tmp_path / "ch", "005_test")
    story = json.loads((tmp_path / "ch" / "stories" / "005_test" / "story.json").read_text(encoding="utf-8"))
    assert story["output"]["path"] == "005_test.mp4"
    assert (tmp_path / "ch" / "stories" / "005_test" / "clips").is_dir()


def test_story_with_raw_video_scenes(tmp_path, ffmpeg_ok, monkeypatch):
    """image card + short silent clip with a card (loops) + talking clip with its own audio and captions."""
    import story as st
    kit = tmp_path / "kit"
    shutil.copytree(TEMPLATE, kit)
    d = kit / "stories" / "t"
    (d / "clips").mkdir(parents=True)
    ff("-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=1.5", "-pix_fmt", "yuv420p", str(d / "clips" / "broll.mp4"))
    ff("-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=3", "-f", "lavfi", "-i", "sine=f=300:d=3",
       "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(d / "clips" / "talk.mp4"))
    story = {
        "extends": "../../channel.json", "title": "t", "output": {"path": "t.mp4", "aspect": "9:16"},
        "scenes": [
            {"image": "cards/1.jpg", "duration": 1.0, "card": {"badge": "HI", "headline": "One *two*"}},
            {"video": "clips/broll.mp4", "duration": 2.5, "card_image": "cards/2.jpg",
             "card": {"badge": "VIDEO", "headline": "Over the *clip*"}},
            {"video": "clips/talk.mp4", "keep_audio": True, "card_image": "cards/3.jpg", "card_hold": 1.0,
             "card": {"headline": "Talking"}},
        ],
    }
    sp = d / "story.json"
    sp.write_text(json.dumps(story))
    run_script("cards.py", "cards", sp)
    fake = [{"start": 0.2, "end": 0.6, "text": "hello", "p": 1.0, "filler": None},
            {"start": 0.7, "end": 1.2, "text": "world", "p": 1.0, "filler": None}]
    monkeypatch.setattr(st, "transcribe_window", lambda *a, **k: fake)
    loaded = st.load_story(sp)
    build = d / "story.build"
    build.mkdir()
    assert st.synth_all(loaded, d, build) == []  # nothing to narrate
    edl = st.build_edl(loaded, d, build)
    talk = edl["sources"]["vid2"]
    assert isinstance(talk, dict) and Path(talk["transcript"]).exists()  # talking clip carries its captions
    assert [c.get("mute") for c in edl["clips"]] == [True, True, None]  # card + b-roll silent, talking clip keeps audio
    from common import save_json
    save_json(edl, build / "edl.json")
    run_script("render.py", build / "edl.json", "--preview")
    info = probe(d / "t_preview.mp4")  # --preview renders at half size
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (int(v["width"]), int(v["height"])) == (540, 960)
    assert float(info["format"]["duration"]) == pytest.approx(6.5, abs=0.2)
    assert "world" in (d / "t_preview.srt").read_text(encoding="utf-8").lower()
