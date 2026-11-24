"""Stories (photos or clips + a script -> a narrated video) and batches (many videos analysed, then exported)."""
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import studio_stories as stories
from studio_server import create_app
from test_studio_server import TOKEN, FakeQueue, installed  # noqa: F401
from test_studio_whole import video  # noqa: F401  (a talking video with its transcript already made)

AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("VEDIT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "user"))


@pytest.fixture
def fq():
    return FakeQueue()


@pytest.fixture
def app(home, fq, monkeypatch):
    installed(monkeypatch, "small", "large-v3")
    a = create_app(TOKEN, fq)
    fq.on_event = a.state.engine.on_event
    yield a
    a.state.engine.close()


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def photos(tmp_path):
    d = tmp_path / "photos"
    d.mkdir()
    out = []
    for i, color in enumerate([(200, 40, 40), (40, 160, 90)]):
        p = d / f"p{i}.jpg"
        Image.new("RGB", (800, 600), color).save(p)
        out.append(str(p))
    return out


def new_story(client, title="Lost wallet"):
    r = client.post("/stories", headers=AUTH, json={"title": title})
    assert r.status_code == 200, r.text
    return r.json()


def test_new_story_has_defaults_and_is_listed(client):
    s = new_story(client)
    assert stories.is_story_id(s["id"])
    assert (s["title"], s["aspect"], s["voice"], s["captions"], s["scenes"]) == ("Lost wallet", "9:16", "en-US-AndrewNeural", "pop", [])
    assert [x["id"] for x in client.get("/stories", headers=AUTH).json()] == [s["id"]]
    assert client.get(f"/stories/{s['id']}", headers=AUTH).json()["title"] == "Lost wallet"
    voices = client.get("/stories/voices", headers=AUTH).json()
    assert {"id": "fil-PH-BlessicaNeural", "name": "Blessica (Filipino)"} in voices


def test_save_checks_every_field(client, photos):
    sid = new_story(client)["id"]
    ok = client.put(f"/stories/{sid}", headers=AUTH, json={
        "aspect": "16:9", "voice": "en-GB-SoniaNeural", "captions": "none",
        "scenes": [{"media": photos[0], "text": "One.\nTwo."}, {"media": "C:/x/clip.MOV", "keep_audio": True}]})
    assert ok.status_code == 200, ok.text
    doc = ok.json()
    assert [sc["kind"] for sc in doc["scenes"]] == ["image", "video"]
    assert doc["scenes"][1]["keep_audio"] is True and doc["scenes"][0]["keep_audio"] is False
    for bad in ({"aspect": "4:3"}, {"voice": "xx-Robot"}, {"captions": "fancy"}, {"music": "C:/a.txt"},
                {"music_volume": 2}, {"scenes": [{"media": "C:/a/notes.txt"}]}, {"scenes": [{"text": "no media"}]},
                {"title": "x" * 81}, {"scenes": [{"media": photos[0], "motion": "spin"}]}):
        r = client.put(f"/stories/{sid}", headers=AUTH, json=bad)
        assert r.status_code == 400, (bad, r.text)
    assert client.get(f"/stories/{sid}", headers=AUTH).json()["aspect"] == "16:9"  # failed saves change nothing


def test_unknown_and_unsafe_ids_are_not_found(client):
    for sid in ("s000000000000", "../etc", "nope"):
        assert client.get(f"/stories/{sid}", headers=AUTH).status_code == 404


def test_story_json_has_one_narrator_and_a_line_per_caption(tmp_path, photos):
    doc = {**stories.default_doc("s" + "a" * 12, "T"), "music": "C:/m/bgm.mp3", "music_volume": 0.2, "scenes": [
        {"media": photos[0], "kind": "image", "text": "Maria was late.\n\n  Where is my wallet? ", "motion": "in", "keep_audio": False},
        {"media": photos[1], "kind": "image", "text": "", "motion": "auto", "keep_audio": False},
        {"media": "C:/v/me.mp4", "kind": "video", "text": "ignored", "motion": "auto", "keep_audio": True},
        {"media": "C:/v/beach.mp4", "kind": "video", "text": "The sea.", "motion": "auto", "keep_audio": False}]}
    s = stories.to_story_json(doc, tmp_path / "T.mp4")
    assert s["voices"] == {"narrator": {"engine": "edge", "voice": "en-US-AndrewNeural", "color": "#FFFFFF"}}
    assert s["output"] == {"path": str(tmp_path / "T.mp4"), "aspect": "9:16"}
    assert s["music"] == {"path": "C:/m/bgm.mp3", "volume": 0.2}
    a, b, c, d = s["scenes"]
    assert a["lines"] == [{"speaker": "narrator", "text": "Maria was late."}, {"speaker": "narrator", "text": "Where is my wallet?"}]
    assert a["motion"] == "in" and b["duration"] == stories.SILENT_SECONDS and "lines" not in b
    assert c == {"video": "C:/v/me.mp4", "keep_audio": True, "transition": "fade"}
    assert d["lines"] == [{"speaker": "narrator", "text": "The sea."}] and "motion" not in d
    assert stories.to_story_json({**doc, "captions": "none"}, tmp_path / "x.mp4")["captions"] == {"enabled": False}


def test_build_runs_voices_then_render_and_remembers_the_video(client, app, fq, photos, tmp_path):
    sid = new_story(client, "My: story?")["id"]
    assert client.post(f"/stories/{sid}/build", headers=AUTH, json={}).status_code == 400  # no scenes yet
    client.put(f"/stories/{sid}", headers=AUTH, json={"scenes": [{"media": photos[0], "text": "Hello."}]})
    out_dir = tmp_path / "out"
    job = client.post(f"/stories/{sid}/build", headers=AUTH, json={"out_dir": str(out_dir)}).json()
    sub = fq.submits[-1]
    assert (sub["kind"], sub["project_id"]) == ("story", sid)
    assert [st[0] for st in sub["steps"]] == ["voices", "render"]
    story_path = stories.story_dir(sid) / "story.json"
    assert sub["steps"][1][1][-2:] == ["build", str(story_path)]
    built = json.loads(story_path.read_text(encoding="utf-8"))
    out = out_dir / "My story.mp4"
    assert built["output"]["path"] == str(out)
    out.write_bytes(b"video")  # what render.py would have written
    fq.finish(job["id"])
    app.state.engine.wait_idle()
    done = client.get(f"/jobs/{job['id']}", headers=AUTH).json()
    assert done["result"] == {"files": [str(out)]}
    doc = client.get(f"/stories/{sid}", headers=AUTH).json()
    assert doc["output"] == str(out) and doc["last_job"]["state"] == "done" and "building" not in doc


def test_a_failed_build_keeps_the_last_video(client, app, fq, photos, tmp_path):
    sid = new_story(client)["id"]
    client.put(f"/stories/{sid}", headers=AUTH, json={"scenes": [{"media": photos[0], "text": "Hi."}]})
    job = client.post(f"/stories/{sid}/build", headers=AUTH, json={"out_dir": str(tmp_path)}).json()
    fq.emit(job["id"], state="failed", error="no internet for the voice")
    app.state.engine.wait_idle()
    doc = client.get(f"/stories/{sid}", headers=AUTH).json()
    assert doc["output"] is None and doc["last_job"]["error"] == "no internet for the voice"
    assert client.get(f"/jobs/{job['id']}", headers=AUTH).json()["result"] == {"files": []}


def test_build_says_which_photo_is_missing(client, photos, tmp_path):
    sid = new_story(client)["id"]
    client.put(f"/stories/{sid}", headers=AUTH, json={"scenes": [{"media": photos[0]}, {"media": str(tmp_path / "gone.jpg")}]})
    r = client.post(f"/stories/{sid}/build", headers=AUTH, json={})
    assert r.status_code == 400 and "scene 2: gone.jpg" in r.json()["detail"]


def test_only_the_storys_own_files_are_served(client, photos, tmp_path):
    sid = new_story(client)["id"]
    client.put(f"/stories/{sid}", headers=AUTH, json={"scenes": [{"media": photos[0]}]})
    assert client.get(f"/stories/{sid}/file", headers=AUTH, params={"path": photos[0]}).status_code == 200
    assert client.get(f"/stories/{sid}/file", headers=AUTH, params={"path": photos[1]}).status_code == 404


def test_default_folder_is_videos_vedit(client, fq, photos, tmp_path):
    sid = new_story(client, "")["id"]
    client.put(f"/stories/{sid}", headers=AUTH, json={"aspect": "1:1", "scenes": [{"media": photos[0]}]})
    client.post(f"/stories/{sid}/build", headers=AUTH, json={})
    built = json.loads((stories.story_dir(sid) / "story.json").read_text(encoding="utf-8"))
    assert built["output"]["path"].endswith(str(tmp_path / "user" / "Videos" / "Vedit" / "Story (1x1).mp4")[-30:])


def test_batch_analyses_each_video_then_exports_it(client, app, fq, video, tmp_path):  # noqa: F811
    out = tmp_path / "exports"
    r = client.post("/batches", headers=AUTH, json={"sources": [str(video)], "type": "vlog", "language": "en",
                                                     "platform": "youtube", "formats": ["16:9"], "out_dir": str(out)})
    assert r.status_code == 200, r.text
    b = r.json()
    (item,) = b["items"]
    assert fq.submits[-1]["kind"] == "analyze" and item["export"] is None
    fq.finish(item["analyze"])
    app.state.engine.wait_idle()
    sub = fq.submits[-1]
    assert sub["kind"] == "export" and sub["project_id"] == item["project"]["id"]
    again = client.get(f"/batches/{b['id']}", headers=AUTH).json()["items"][0]
    assert again["export"] == f"j{len(fq.submits)}" and again["error"] is None
    # analysing the same project again never exports twice
    n = len(fq.submits)
    job = client.post(f"/projects/{item['project']['id']}/analyze", headers=AUTH, json={}).json()
    fq.finish(job["id"])
    app.state.engine.wait_idle()
    assert len(fq.submits) == n + 1


def test_batch_without_export_only_analyses(client, app, fq, video):  # noqa: F811
    b = client.post("/batches", headers=AUTH, json={"sources": [str(video)], "type": "reel", "language": "en",
                                                     "platform": "tiktok", "export": False}).json()
    fq.finish(b["items"][0]["analyze"])
    app.state.engine.wait_idle()
    assert [s["kind"] for s in fq.submits] == ["analyze"]


def test_batch_checks_its_input(client, video, tmp_path):  # noqa: F811
    base = {"type": "clip", "language": "en", "platform": "reels"}
    for body, code in (({**base, "sources": []}, 422), ({**base, "sources": [str(tmp_path / "nope.mp4")]}, 400),
                       ({**base, "sources": [str(video), str(video)]}, 400),
                       ({**base, "sources": [str(video)], "platform": "youtube"}, 400),
                       ({**base, "sources": [str(video)], "formats": ["2:1"]}, 400)):
        assert client.post("/batches", headers=AUTH, json=body).status_code == code, body
    assert client.get("/batches/b000000000000", headers=AUTH).status_code == 404
    assert client.get("/batches/..%2Fx", headers=AUTH).status_code == 404
