import json
import shutil
import subprocess

import pytest
from conftest import probe, run_script


def dur(p):
    return float(probe(p)["format"]["duration"])


def test_analyze_finds_silences(talking):
    a = json.loads((talking / "raw.edit" / "analysis.main.json").read_text(encoding="utf-8"))
    assert len(a["silences"]) >= 4
    assert (talking / "raw.edit" / "transcript.main.txt").read_text(encoding="utf-8").count("{Um,}") == 1


def test_autocut_drops_filler_and_flubbed_take(talking):
    run_script("autocut.py", talking / "raw.edit", "--preset", "youtube", "--out", talking / "raw.edit" / "edl.t.json")
    edl = json.loads((talking / "raw.edit" / "edl.t.json").read_text())
    tr = json.loads((talking / "raw.edit" / "transcript.main.json").read_text())
    kept = {i for c in edl["clips"] for i in range(c["words"][0], c["words"][1] + 1)}
    um = next(w["i"] for w in tr["words"] if w["text"].lower().startswith("um"))
    retake = next(s for s in tr["sentences"] if "retake_of_later" in s)
    assert um not in kept
    assert retake["w0"] not in kept


def test_render_preview_and_formats(talking):
    edl = talking / "raw.edit" / "edl.r.json"
    run_script("autocut.py", talking / "raw.edit", "--preset", "youtube", "--out", edl)
    e = json.loads(edl.read_text())
    e["output"]["path"] = str(talking / "out.mp4")
    e["chapters"] = [{"at": {"word": ["main", 0]}, "title": "Intro"}]
    edl.write_text(json.dumps(e))
    run_script("render.py", edl, "--preview", "--formats", "9:16")
    main, vert = talking / "out_preview.mp4", talking / "out_9x16_preview.mp4"
    assert main.exists() and vert.exists()
    assert 5 < dur(main) < 12
    v = next(s for s in probe(vert)["streams"] if s["codec_type"] == "video")
    assert int(v["height"]) > int(v["width"])
    assert (talking / "out_preview.srt").read_text().strip()
    assert (talking / "out_preview.chapters.txt").read_text().startswith("00:00 Intro")


def test_auto_one_command(talking):
    r = run_script("auto.py", talking / "raw.mp4", "--preset", "shorts", "--preview")
    assert "summary" in r.stdout
    out = talking / "raw_shorts_preview.mp4"
    assert out.exists()
    v = next(s for s in probe(out)["streams"] if s["codec_type"] == "video")
    assert int(v["height"]) > int(v["width"])


def test_multicam_sync(talking):
    cam2 = talking / "cam2.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(talking / "raw.mp4"), "-af", "adelay=1300|1300",
                    "-c:v", "copy", str(cam2)], check=True)
    r = run_script("multicam.py", "sync", talking / "raw.mp4", cam2)
    off = float(r.stdout.split("offset=")[1].split("s")[0])
    assert abs(off - 1.3) < 0.05


def _has_system_voice():
    filt = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True).stdout
    return " flite " in filt or shutil.which("espeak-ng") or shutil.which("espeak") or shutil.which("say")


@pytest.mark.skipif(not _has_system_voice(), reason="no offline TTS voice available")
def test_story_mode(tmp_path, ffmpeg_ok):
    from PIL import Image
    Image.new("RGB", (1200, 800), (40, 90, 160)).save(tmp_path / "p.jpg")
    story = {"title": "t", "output": {"path": "t.mp4", "aspect": "9:16"},
             "voices": {"n": {"engine": "system", "color": "#FFD400"}},
             "scenes": [{"image": "p.jpg", "title": "Hi",
                         "lines": [{"speaker": "n", "text": "This is a test."},
                                   {"speaker": "n", "text": "Close up now.", "focus": [0.3, 0.4], "shot": "close"}]},
                        {"image": "p.jpg", "duration": 1.0, "transition": "fade"}]}
    (tmp_path / "s.json").write_text(json.dumps(story))
    run_script("story.py", "build", tmp_path / "s.json", "--preview")
    out = tmp_path / "t_preview.mp4"
    assert out.exists() and dur(out) > 2
