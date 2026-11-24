"""Vlog and Quick Reel projects: the whole video as one edit. They live beside a clip project of the same video
(sharing its transcript) without touching it, analyse without the highlight search, and end with one clip that
covers every word, cut with the preset (16:9 for a vlog, vertical for a reel)."""
import pytest
from fastapi.testclient import TestClient

from common import load_json, save_json, workdir_for
from highlights import find_highlights
from studio_projects import list_recent, project_file, project_id
from test_studio_server import TOKEN, FakeQueue, installed  # noqa: F401  (fixtures below reuse the server's)
from studio_server import create_app


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("VEDIT_HOME", str(tmp_path / "home"))


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


AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def video(talking, tmp_path):
    import shutil
    d = tmp_path / "videos"
    d.mkdir()
    v = d / "my vlog.mp4"
    shutil.copy(talking / "raw.mp4", v)
    wd = workdir_for(v)
    tr = load_json(talking / "pristine.edit" / "transcript.main.json")
    tr["source"] = str(v)
    save_json(tr, wd / "transcript.main.json")
    an = load_json(talking / "pristine.edit" / "analysis.main.json")
    an["probe"]["path"] = str(v)
    save_json(an, wd / "analysis.main.json")
    return v


def new(client, video, kind, platform):
    r = client.post("/projects", headers=AUTH, json={"source": str(video), "language": "en", "platform": platform,
                                                     "type": kind})
    assert r.status_code == 200, r.text
    return r.json()


def analyse(client, app, fq, pid):
    job = client.post(f"/projects/{pid}/analyze", headers=AUTH, json={}).json()
    fq.finish(job["id"])
    app.state.engine.wait_idle()
    return client.get(f"/jobs/{job['id']}", headers=AUTH).json()


def test_types_have_their_own_ids_and_files(client, video):
    clip = new(client, video, "clip", "reels")
    vlog = new(client, video, "vlog", "youtube")
    reel = new(client, video, "reel", "shorts")
    assert len({clip["id"], vlog["id"], reel["id"]}) == 3
    assert clip["id"] == project_id(video)  # existing clip projects keep their id
    assert (vlog["type"], reel["type"]) == ("vlog", "reel")
    wd = workdir_for(video)
    assert all((wd / project_file(k)).is_file() for k in ("clip", "vlog", "reel"))
    assert {p.id for p in list_recent()} == {clip["id"], vlog["id"], reel["id"]}
    assert client.get(f"/projects/{vlog['id']}", headers=AUTH).json()["type"] == "vlog"


def test_platform_must_fit_the_type(client, video):
    for kind, platform in (("vlog", "reels"), ("clip", "youtube"), ("reel", "youtube")):
        r = client.post("/projects", headers=AUTH, json={"source": str(video), "language": "en", "platform": platform,
                                                         "type": kind})
        assert r.status_code in (400, 422), (kind, platform)


def test_whole_video_analysis_skips_the_highlight_search(client, fq, video):
    pid = new(client, video, "vlog", "youtube")["id"]
    client.post(f"/projects/{pid}/analyze", headers=AUTH, json={})
    assert [s[0] for s in fq.steps] == ["proxy", "analyze"]
    assert sum(s[2] for s in fq.steps) == pytest.approx(1.0)


@pytest.mark.parametrize("kind,platform,aspect", [("vlog", "youtube", "16:9"), ("reel", "shorts", "9:16")])
def test_one_clip_covers_the_whole_video(client, app, fq, video, kind, platform, aspect):
    pid = new(client, video, kind, platform)["id"]
    job = analyse(client, app, fq, pid)
    assert job["state"] == "done" and job["result"]["clips"] == 1
    p = client.get(f"/projects/{pid}", headers=AUTH).json()
    (c,) = p["clips"]
    words = load_json(workdir_for(video) / "transcript.main.json")["words"]
    assert c["highlight"]["id"] == "h01" and c["highlight"]["title"] == "my vlog" and c["kept"]
    assert (c["highlight"]["w0"], c["highlight"]["w1"]) == (0, len(words) - 1)
    assert c["edl_file"] == f"{kind}-h01.edl.json"
    edl = load_json(workdir_for(video) / c["edl_file"])
    assert edl["output"].get("aspect") == aspect
    assert edl["captions"]["style"] == ("clean" if kind == "vlog" else "pop")
    view = client.get(f"/projects/{pid}/clips/h01", headers=AUTH).json()
    assert view["kept_ranges"] and len(view["zooms"]) == len(edl["clips"])


def test_reanalysing_keeps_the_edit(client, app, fq, video):
    pid = new(client, video, "reel", "reels")["id"]
    analyse(client, app, fq, pid)
    wd = workdir_for(video)
    r = client.patch(f"/projects/{pid}/clips/h01", headers=AUTH, json={"op": "cut", "w0": 2, "w1": 3})
    assert r.status_code == 200
    before = (wd / "reel-h01.edl.json").read_text(encoding="utf-8")
    assert analyse(client, app, fq, pid)["result"]["new"] == 0
    assert (wd / "reel-h01.edl.json").read_text(encoding="utf-8") == before


def test_a_whole_video_project_leaves_the_clip_project_alone(client, app, fq, video):
    clip = new(client, video, "clip", "reels")["id"]
    job = client.post(f"/projects/{clip}/analyze", headers=AUTH, json={}).json()
    wd = workdir_for(video)
    hs, why = find_highlights(wd)
    save_json({"source": str(video), "highlights": hs, "reason": why}, wd / "highlights.json")
    fq.finish(job["id"])
    app.state.engine.wait_idle()
    clips_before = client.get(f"/projects/{clip}", headers=AUTH).json()["clips"]
    vlog = new(client, video, "vlog", "youtube")["id"]
    analyse(client, app, fq, vlog)
    assert client.get(f"/projects/{clip}", headers=AUTH).json()["clips"] == clips_before
    assert len(client.get(f"/projects/{vlog}", headers=AUTH).json()["clips"]) == 1


def test_a_video_without_speech_fails_with_a_clear_message(client, app, fq, video):
    wd = workdir_for(video)
    tr = load_json(wd / "transcript.main.json")
    save_json({**tr, "words": [], "sentences": []}, wd / "transcript.main.json")
    pid = new(client, video, "vlog", "youtube")["id"]
    job = analyse(client, app, fq, pid)
    assert job["state"] == "failed" and "no speech" in job["error"]
