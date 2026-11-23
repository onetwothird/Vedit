"""Studio local engine server: auth/origin, projects, analyze/export job wiring, clip edits, restart, serve."""
import dataclasses
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="needs the studio extra")
import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import models  # noqa: E402
import vedit  # noqa: E402
from common import load_json, save_json, workdir_for  # noqa: E402
from conftest import SCRIPTS  # noqa: E402
from highlights import find_highlights  # noqa: E402
from studio_jobs import Job  # noqa: E402
from studio_projects import create_project, load_project, output_dir, output_name, save_project  # noqa: E402
from studio_server import create_app  # noqa: E402

TOKEN = "t-" + "studio" + "-test"
KEY = "sk-" + "fake" + "-" + "x9Q2" * 4  # never a real key; built from pieces


class FakeQueue:
    """Records submissions; tests drive job state through emit()."""

    def __init__(self):
        self.on_event = None
        self.submits = []
        self.jobs = {}
        self.shut = False

    @property
    def steps(self):
        return self.submits[-1]["steps"]

    def submit(self, project_id, kind, steps, env=None):
        job = Job(id=f"j{len(self.submits) + 1}", project_id=project_id, kind=kind)
        self.submits.append({"project_id": project_id, "kind": kind, "steps": list(steps), "env": dict(env or {})})
        self.jobs[job.id] = job
        self.on_event(dataclasses.replace(job))
        return dataclasses.replace(job)

    def emit(self, job_id, **fields):
        job = self.jobs[job_id]
        for k, v in fields.items():
            setattr(job, k, v)
        self.on_event(dataclasses.replace(job))

    def finish(self, job_id):
        self.emit(job_id, state="running", stage="x", pct=50.0)
        self.emit(job_id, state="done", pct=100.0)

    def get(self, job_id):
        return dataclasses.replace(self.jobs[job_id])

    def cancel(self, job_id):
        job = self.jobs[job_id]
        if job.state in ("queued", "running"):
            self.emit(job_id, state="cancelled")

    def shutdown(self):
        self.shut = True


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    monkeypatch.setenv("VEDIT_HOME", str(h))
    return h


def installed(monkeypatch, *names):
    st = {n: {"installed": n in names, "size_mb": m["size_mb"]} for n, m in models.MODELS.items()}
    monkeypatch.setattr(models, "status", lambda: st)


@pytest.fixture
def fake_queue():
    return FakeQueue()


@pytest.fixture
def app(home, fake_queue, monkeypatch):
    installed(monkeypatch, "small", "large-v3")  # default: no model step; tests override
    a = create_app(TOKEN, fake_queue)
    fake_queue.on_event = a.state.engine.on_event
    yield a
    a.state.engine.close()


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def auth():
    return {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def tiny_video(talking, tmp_path):
    d = tmp_path / "Mga Video"
    d.mkdir()
    v = d / "talk.mp4"
    shutil.copy(talking / "raw.mp4", v)
    return v


@pytest.fixture
def analyzed_project(client, auth, app, fake_queue, tiny_video, talking):
    """tiny_video with the pristine transcript/analysis in its workdir, analyzed through a faked job."""
    wd = workdir_for(tiny_video)
    tr = load_json(talking / "pristine.edit" / "transcript.main.json")
    tr["source"] = str(tiny_video)
    save_json(tr, wd / "transcript.main.json")
    an = load_json(talking / "pristine.edit" / "analysis.main.json")
    an["probe"]["path"] = str(tiny_video)
    save_json(an, wd / "analysis.main.json")
    pid = client.post("/projects", headers=auth, json={"source": str(tiny_video), "language": "en",
                                                       "platform": "reels"}).json()["id"]
    job = client.post(f"/projects/{pid}/analyze", headers=auth, json={}).json()
    hs, why = find_highlights(wd)
    save_json({"source": str(tiny_video), "highlights": hs, "reason": why}, wd / "highlights.json")
    fake_queue.finish(job["id"])
    app.state.engine.wait_idle()
    return {"id": pid, "wd": wd, "job": job["id"], "highlights": hs}


def all_files(root):
    return [p for p in root.rglob("*") if p.is_file()]


# ---- auth and origin ------------------------------------------------------------------------------------------

def test_requires_token(client):
    assert client.get("/health").status_code == 401
    assert client.get("/health", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_rejects_foreign_origin(client, auth):
    assert client.get("/health", headers={**auth, "Origin": "https://evil.example"}).status_code == 403


def test_health_and_allowed_origins(client, auth):
    for origin in (None, "tauri://localhost", "http://tauri.localhost", "http://localhost:5173"):
        r = client.get("/health", headers={**auth, **({"Origin": origin} if origin else {})})
        assert r.status_code == 200
        assert r.json() == {"ok": True, "version": vedit.__version__}


def test_doctor_shape(client, auth):
    d = client.get("/doctor", headers=auth).json()
    assert isinstance(d["ffmpeg"], bool) and isinstance(d["libass"], bool) and set(d["models"]) == {"small", "large-v3"}


# ---- projects -------------------------------------------------------------------------------------------------

def test_create_and_get_project(client, auth, tiny_video):
    r = client.post("/projects", headers=auth, json={"source": str(tiny_video), "language": "taglish",
                                                     "platform": "tiktok"})
    assert r.status_code == 200
    p = r.json()
    assert p["source"] == str(tiny_video.resolve()) and p["language"] == "taglish" and p["clips"] == []
    assert client.get(f"/projects/{p['id']}", headers=auth).json() == p
    assert [x["id"] for x in client.get("/projects/recent", headers=auth).json()] == [p["id"]]
    assert client.get("/projects/nope", headers=auth).status_code == 404
    bad = client.post("/projects", headers=auth, json={"source": str(tiny_video.parent / "missing.mp4"),
                                                       "language": "en", "platform": "reels"})
    assert bad.status_code == 400


def test_analyze_submits_expected_steps(client, auth, fake_queue, tiny_video):
    pid = client.post("/projects", headers=auth, json={"source": str(tiny_video), "language": "tl",
                                                       "platform": "shorts"}).json()["id"]
    job = client.post(f"/projects/{pid}/analyze", headers=auth, json={}).json()
    assert job["kind"] == "analyze" and job["project_id"] == pid
    assert [s[0] for s in fake_queue.steps] == ["proxy", "analyze", "highlights", "framing"]
    assert [s[2] for s in fake_queue.steps] == [0.1, 0.6, 0.15, 0.15]
    assert sum(s[2] for s in fake_queue.steps) == pytest.approx(1.0)
    analyze = fake_queue.steps[1][1]
    assert analyze[analyze.index("--language") + 1] == "tl" and analyze[analyze.index("--model") + 1] == "large-v3"
    assert "--framing" in fake_queue.steps[3][1] and "--ai" not in fake_queue.steps[2][1]
    assert all(str(tiny_video.resolve()) in s[1] for s in fake_queue.steps)
    assert fake_queue.submits[-1]["env"] == {} and all(len(s) == 3 for s in fake_queue.steps)


def test_analyze_prepends_model_step_when_missing(client, auth, fake_queue, tiny_video, monkeypatch):
    installed(monkeypatch, "small")  # tagalog needs large-v3
    pid = client.post("/projects", headers=auth, json={"source": str(tiny_video), "language": "tl",
                                                       "platform": "shorts"}).json()["id"]
    client.post(f"/projects/{pid}/analyze", headers=auth, json={})
    steps = fake_queue.steps
    assert [s[0] for s in steps] == ["model", "proxy", "analyze", "highlights", "framing"]
    assert steps[0][1][-3:] == ["models.py", "download", "large-v3"] or steps[0][1][-2:] == ["download", "large-v3"]
    assert [s[2] for s in steps] == pytest.approx([0.3, 0.07, 0.42, 0.105, 0.105])
    assert sum(s[2] for s in steps) == pytest.approx(1.0)


def test_analyze_without_body(client, auth, fake_queue, tiny_video):
    pid = client.post("/projects", headers=auth, json={"source": str(tiny_video), "language": "en",
                                                       "platform": "reels"}).json()["id"]
    assert client.post(f"/projects/{pid}/analyze", headers=auth).status_code == 200
    assert [s[0] for s in fake_queue.steps] == ["proxy", "analyze", "highlights", "framing"]


def test_analyze_max_clips_goes_to_highlights_step(client, auth, fake_queue, tiny_video):
    pid = client.post("/projects", headers=auth, json={"source": str(tiny_video), "language": "en",
                                                       "platform": "reels"}).json()["id"]
    assert client.post(f"/projects/{pid}/analyze", headers=auth, json={"max_clips": 18}).status_code == 200
    hl = fake_queue.steps[2][1]
    assert hl[hl.index("--max") + 1] == "18"
    client.post(f"/projects/{pid}/analyze", headers=auth, json={})
    assert "--max" not in fake_queue.steps[2][1]
    client.post(f"/projects/{pid}/analyze", headers=auth, json={"max_clips": None})
    assert "--max" not in fake_queue.steps[2][1]


@pytest.mark.parametrize("bad", [0, -1, 61, 1000])
def test_analyze_max_clips_out_of_range_is_422(client, auth, fake_queue, tiny_video, bad):
    pid = client.post("/projects", headers=auth, json={"source": str(tiny_video), "language": "en",
                                                       "platform": "reels"}).json()["id"]
    r = client.post(f"/projects/{pid}/analyze", headers=auth, json={"max_clips": bad})
    assert r.status_code == 422 and r.json()["code"] == "bad_request"
    assert not fake_queue.submits or fake_queue.submits[-1].get("kind") != "analyze"


@pytest.mark.parametrize("ok", [1, 60])
def test_analyze_max_clips_bounds_accepted(client, auth, fake_queue, tiny_video, ok):
    pid = client.post("/projects", headers=auth, json={"source": str(tiny_video), "language": "en",
                                                       "platform": "reels"}).json()["id"]
    assert client.post(f"/projects/{pid}/analyze", headers=auth, json={"max_clips": ok}).status_code == 200


def test_filmstrip_returns_meta_and_image_path(client, auth, analyzed_project):
    wd = analyzed_project["wd"]
    meta = {"every": 2.0, "height": 96, "width": 54, "count": 10, "cols": 10, "rows": 1}
    save_json(meta, wd / "filmstrip.json")
    (wd / "filmstrip.jpg").write_bytes(b"jpg")
    r = client.get(f"/projects/{analyzed_project['id']}/filmstrip", headers=auth)
    assert r.status_code == 200
    assert r.json() == {**meta, "image": str(wd / "filmstrip.jpg")}


def test_filmstrip_missing_is_404_no_filmstrip(client, auth, analyzed_project):
    wd = analyzed_project["wd"]
    pid = analyzed_project["id"]
    for f in ("filmstrip.json", "filmstrip.jpg"):
        (wd / f).unlink(missing_ok=True)
    r = client.get(f"/projects/{pid}/filmstrip", headers=auth)
    assert r.status_code == 404 and r.json()["code"] == "no_filmstrip"
    save_json({"every": 2.0, "height": 96, "width": 54, "count": 1, "cols": 1, "rows": 1}, wd / "filmstrip.json")
    r = client.get(f"/projects/{pid}/filmstrip", headers=auth)  # json without the image
    assert r.status_code == 404 and r.json()["code"] == "no_filmstrip"
    (wd / "filmstrip.json").unlink()
    (wd / "filmstrip.jpg").write_bytes(b"jpg")  # image without the json
    assert client.get(f"/projects/{pid}/filmstrip", headers=auth).json()["code"] == "no_filmstrip"
    assert client.get("/projects/nope/filmstrip", headers=auth).json()["code"] == "not_found"


def test_analyze_done_builds_clips(client, auth, analyzed_project):
    p = client.get(f"/projects/{analyzed_project['id']}", headers=auth).json()
    hs = analyzed_project["highlights"]
    assert hs and [c["highlight"]["id"] for c in p["clips"]] == [h["id"] for h in hs]
    assert all(c["kept"] and (analyzed_project["wd"] / c["edl_file"]).is_file() for c in p["clips"])
    assert p["last_job"]["state"] == "done"
    assert p["last_job"]["result"] == {"clips": len(hs), "new": len(hs), "reason": None}
    wd = analyzed_project["wd"]
    assert p["settings"] == {"workdir": str(wd), "proxy": str(Path(p["source"])),  # no proxy needed: the source
                             "filmstrip": str(wd / "filmstrip.json")}


def test_settings_point_at_proxy_when_one_was_built(client, auth, app, fake_queue, analyzed_project):
    wd, pid = analyzed_project["wd"], analyzed_project["id"]
    src = client.get(f"/projects/{pid}", headers=auth).json()["source"]
    (wd / "proxy.mp4").write_bytes(b"")
    save_json({"src": src, "size": 1, "mtime": 1}, wd / "proxy.json")
    job = client.post(f"/projects/{pid}/analyze", headers=auth, json={}).json()
    fake_queue.finish(job["id"])
    app.state.engine.wait_idle()
    assert client.get(f"/projects/{pid}", headers=auth).json()["settings"]["proxy"] == str(wd / "proxy.mp4")


def test_reanalyze_keeps_edits_and_adds_new_ids(client, auth, app, fake_queue, analyzed_project):
    wd, pid = analyzed_project["wd"], analyzed_project["id"]
    hid = analyzed_project["highlights"][0]["id"]
    url = f"/projects/{pid}/clips/{hid}"
    a, _ = client.get(url, headers=auth).json()["edl"]["clips"][0]["words"]
    edited = client.patch(url, headers=auth, json={"op": "cut", "w0": a, "w1": a}).json()["edl"]
    # shrink the kept clip's highlight so a later stretch of speech is free for a new highlight
    proj = load_project(pid)
    tr = load_json(wd / "transcript.main.json")
    sents = tr["sentences"]
    proj.clips[0].highlight = {**proj.clips[0].highlight, "start": 0.0, "end": sents[1]["end"]}
    save_project(proj)
    kept_before = dataclasses.asdict(proj.clips[0])
    later = {"id": hid, "start": sents[3]["start"], "end": sents[-1]["end"], "w0": sents[3]["w0"],
             "w1": sents[-1]["w1"], "score": 50, "title": "Later bit", "features": {}}
    same = {**analyzed_project["highlights"][0], "id": "h05"}  # overlaps the kept clip by > 50%: dropped
    save_json({"source": "x", "highlights": [same, later], "reason": None}, wd / "highlights.json")
    job = client.post(f"/projects/{pid}/analyze", headers=auth, json={}).json()
    fake_queue.finish(job["id"])
    app.state.engine.wait_idle()
    p = client.get(f"/projects/{pid}", headers=auth).json()
    assert p["clips"][0] == kept_before                       # highlight and file name untouched
    assert load_json(wd / f"clip-{hid}.edl.json") == edited   # the user's cut survived
    assert [c["highlight"]["id"] for c in p["clips"]] == [hid, "h02"]
    assert p["clips"][1]["highlight"]["title"] == "Later bit" and (wd / "clip-h02.edl.json").is_file()
    assert p["last_job"]["result"] == {"clips": 2, "new": 1, "reason": None}


def test_unsafe_highlight_ids_never_become_files(client, auth, app, fake_queue, analyzed_project):
    wd, pid = analyzed_project["wd"], analyzed_project["id"]
    for f in wd.glob("clip-*.edl.json"):
        f.unlink()  # nothing preserved, so every highlight is considered
    h = {**analyzed_project["highlights"][0], "id": "../../x"}
    save_json({"source": "x", "highlights": [h], "reason": None}, wd / "highlights.json")
    job = client.post(f"/projects/{pid}/analyze", headers=auth, json={}).json()
    fake_queue.finish(job["id"])
    app.state.engine.wait_idle()
    assert load_project(pid).clips == []
    assert not list(wd.parent.parent.rglob("x.edl.json"))


def test_clip_edl_error_skips_that_highlight(client, auth, app, fake_queue, analyzed_project):
    wd = analyzed_project["wd"]
    hl = load_json(wd / "highlights.json")
    bad = {**hl["highlights"][0], "id": "h99", "w0": 10**6, "w1": 10**6 + 1}  # no speech in range -> ValueError
    hl["highlights"].append(bad)
    save_json(hl, wd / "highlights.json")
    (wd / "clip-h01.edl.json").unlink()  # not preserved, so both highlights are rebuilt
    job = client.post(f"/projects/{analyzed_project['id']}/analyze", headers=auth, json={}).json()
    fake_queue.finish(job["id"])
    app.state.engine.wait_idle()
    p = client.get(f"/projects/{analyzed_project['id']}", headers=auth).json()
    assert "h99" not in [c["highlight"]["id"] for c in p["clips"]]
    assert p["last_job"]["state"] == "done" and p["last_job"]["result"]["clips"] == len(hl["highlights"]) - 1


def test_transcript_and_clip_view(client, auth, analyzed_project):
    pid, hid = analyzed_project["id"], analyzed_project["highlights"][0]["id"]
    tr = client.get(f"/projects/{pid}/transcript", headers=auth).json()
    assert tr["words"]
    v = client.get(f"/projects/{pid}/clips/{hid}", headers=auth).json()
    assert v["edl"]["clips"] and v["kept_ranges"] and all(e > s for s, e in v["kept_ranges"])
    assert "events" in v["captions"]
    r = client.get(f"/projects/{pid}/clips/nope", headers=auth)
    assert r.status_code == 404 and r.json()["code"] == "not_found" and isinstance(r.json()["detail"], str)
    (analyzed_project["wd"] / f"clip-{hid}.edl.json").unlink()
    r = client.get(f"/projects/{pid}/clips/{hid}", headers=auth)
    assert r.status_code == 404 and r.json()["code"] == "clip_missing"


def test_patch_cut_returns_new_ranges(client, auth, analyzed_project):
    pid, hid = analyzed_project["id"], analyzed_project["highlights"][0]["id"]
    before = client.get(f"/projects/{pid}/clips/{hid}", headers=auth).json()
    a, b = next(c["words"] for c in before["edl"]["clips"] if c["words"][1] - c["words"][0] >= 4)
    r = client.patch(f"/projects/{pid}/clips/{hid}", headers=auth, json={"op": "cut", "w0": a + 1, "w1": a + 3})
    assert r.status_code == 200
    after = r.json()
    W = client.get(f"/projects/{pid}/transcript", headers=auth).json()["words"]
    total = lambda rs: sum(e - s for s, e in rs)  # noqa: E731
    span = W[a + 3]["end"] - W[a + 1]["start"]
    assert total(after["kept_ranges"]) < total(before["kept_ranges"]) - span / 2
    mid = (W[a + 2]["start"] + W[a + 2]["end"]) / 2
    assert not any(s <= mid <= e for s, e in after["kept_ranges"])
    saved = load_json(analyzed_project["wd"] / f"clip-{hid}.edl.json")
    assert saved == after["edl"]
    assert client.get(f"/projects/{pid}/clips/{hid}", headers=auth).json() == after


def test_patch_other_ops_and_errors(client, auth, analyzed_project):
    pid, hid = analyzed_project["id"], analyzed_project["highlights"][0]["id"]
    url = f"/projects/{pid}/clips/{hid}"
    e = client.patch(url, headers=auth, json={"op": "fix_word", "wrong": "FFmpeg", "right": "ffmpeg"}).json()["edl"]
    assert e["captions"]["replace"] == {"FFmpeg": "ffmpeg"}
    e = client.patch(url, headers=auth, json={"op": "patch", "section": "framing", "patch": {"crop_x": 0.3}}).json()
    assert all(c["crop_x"] == 0.3 for c in e["edl"]["clips"])
    a, b = e["edl"]["clips"][0]["words"]
    trimmed = client.patch(url, headers=auth, json={"op": "trim", "w_first": a + 1, "w_last": b}).json()
    assert trimmed["edl"]["clips"][0]["words"][0] == a + 1
    restored = client.patch(url, headers=auth, json={"op": "restore", "w0": a, "w1": a}).json()
    assert restored["edl"]["clips"][0]["words"][0] == a
    assert client.patch(url, headers=auth, json={"op": "cut", "w0": 5, "w1": 2}).status_code == 400
    assert client.patch(url, headers=auth, json={"op": "explode"}).status_code == 400
    assert client.patch(url, headers=auth, json={"op": "cut"}).status_code == 400
    assert client.patch(url, headers=auth, json={"op": "trim", "w0": a, "w1": b}).status_code == 400
    assert client.patch(url, headers=auth, json={"op": "patch", "section": "bogus", "patch": {}}).status_code == 400


def test_put_kept(client, auth, analyzed_project):
    pid, hid = analyzed_project["id"], analyzed_project["highlights"][0]["id"]
    p = client.put(f"/projects/{pid}/clips/{hid}/kept", headers=auth, json={"kept": False}).json()
    assert next(c for c in p["clips"] if c["highlight"]["id"] == hid)["kept"] is False
    assert load_project(pid).clips[0].kept is False


def test_export_steps_paths_and_result(client, auth, app, fake_queue, analyzed_project, tiny_video):
    pid = analyzed_project["id"]
    h = analyzed_project["highlights"][0]
    r = client.post(f"/projects/{pid}/export", headers=auth,
                    json={"clips": [h["id"]], "formats": ["9:16", "1:1"], "quality": "high"})
    assert r.status_code == 200
    job = r.json()
    steps = fake_queue.steps
    assert [s[0] for s in steps] == ["render 1/2", "render 2/2"] and [s[2] for s in steps] == [0.5, 0.5]
    assert [(x["stage"], x["hid"], x["aspect"]) for x in job["result"]["renders"]] == [
        ("render 1/2", h["id"], "9:16"), ("render 2/2", h["id"], "1:1")]
    temps = [Path(s[1][s[1].index("--crf") - 1]) for s in steps]
    assert all(s[1][s[1].index("--crf") + 1] == "18" for s in steps)
    outs = [output_dir(tiny_video.resolve()) / output_name(1, h["title"], a) for a in ("9:16", "1:1")]
    for (_, argv, _), aspect, out in zip(steps, ("9:16", "1:1"), outs):
        e = load_json(argv[argv.index("--crf") - 1])
        assert e["output"]["aspect"] == aspect and e["output"]["path"] == str(out)
    for out in outs:  # pretend render ran
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"")
        out.with_suffix(".srt").write_text("1\n", encoding="utf-8")
    fake_queue.finish(job["id"])
    app.state.engine.wait_idle()
    last = client.get(f"/projects/{pid}", headers=auth).json()["last_job"]
    assert last["state"] == "done"
    assert last["result"]["files"] == [str(p) for o in outs for p in (o, o.with_suffix(".srt"))]
    assert "failed" not in last["result"] and not any(t.exists() for t in temps)
    normal = client.post(f"/projects/{pid}/export", headers=auth,
                         json={"clips": [h["id"]], "formats": ["9:16"], "quality": "normal"})
    assert normal.status_code == 200 and "--crf" not in fake_queue.steps[0][1]
    assert client.post(f"/projects/{pid}/export", headers=auth,
                       json={"clips": ["nope"], "formats": ["9:16"]}).status_code == 404
    assert client.post(f"/projects/{pid}/export", headers=auth,
                       json={"clips": [h["id"]], "formats": []}).status_code == 400
    dup = client.post(f"/projects/{pid}/export", headers=auth, json={"clips": [h["id"], h["id"]], "formats": ["9:16"]})
    assert dup.status_code == 400 and dup.json()["code"] == "bad_request"


def test_export_options_folder_srt_thumbnail_and_numbering(client, auth, fake_queue, analyzed_project, tmp_path):
    pid, h = analyzed_project["id"], analyzed_project["highlights"][0]
    dest = tmp_path / "Mga Video ñ"
    r = client.post(f"/projects/{pid}/export", headers=auth,
                    json={"clips": [h["id"]], "formats": ["9:16"], "out_dir": str(dest), "srt": False,
                          "thumbnail": True, "first_number": 3})
    assert r.status_code == 200
    job = r.json()
    out = dest / output_name(3, h["title"], "9:16")
    assert job["result"]["renders"][0]["path"] == str(out)
    steps = fake_queue.steps
    assert [s[0] for s in steps] == ["render 1/1", "thumb 1/1"] and sum(s[2] for s in steps) == pytest.approx(1.0)
    assert steps[1][1][-1] == str(out.with_suffix(".jpg"))
    assert load_json(steps[0][1][-1])["captions"]["srt"] is False
    bad = client.post(f"/projects/{pid}/export", headers=auth,
                      json={"clips": [h["id"]], "out_dir": "relative/folder"})
    assert bad.status_code == 400


def test_export_out_dir_is_checked_and_created(client, auth, fake_queue, analyzed_project, tmp_path):
    pid, h = analyzed_project["id"], analyzed_project["highlights"][0]

    def post(folder):
        return client.post(f"/projects/{pid}/export", headers=auth, json={"clips": [h["id"]], "out_dir": str(folder)})

    for bad in (str(tmp_path / "a" / ".." / "b"), r"\\server\share\x", "//server/share/x", r"\\?\C:\x", "relative"):
        r = client.post(f"/projects/{pid}/export", headers=auth, json={"clips": [h["id"]], "out_dir": bad})
        assert r.status_code == 400, bad
    afile = tmp_path / "file.txt"
    afile.write_text("x")
    assert post(afile).status_code == 400
    fresh = tmp_path / "new" / "Mga Bidyo ñ"
    assert not fresh.exists()
    assert post(fresh).status_code == 200 and fresh.is_dir()


def test_thumbnail_failure_keeps_the_video_and_is_a_warning(client, auth, app, fake_queue, analyzed_project, tmp_path):
    pid, h = analyzed_project["id"], analyzed_project["highlights"][0]
    job = client.post(f"/projects/{pid}/export", headers=auth,
                      json={"clips": [h["id"]], "formats": ["9:16", "1:1"], "thumbnail": True, "out_dir": str(tmp_path)}).json()
    assert [s[0] for s in fake_queue.steps] == ["render 1/2", "thumb 1/2", "render 2/2", "thumb 2/2"]
    outs = [tmp_path / output_name(1, h["title"], a) for a in ("9:16", "1:1")]
    for o in outs:
        o.write_bytes(b"")
    outs[0].with_suffix(".jpg").write_bytes(b"")  # only the second clip's thumbnail is missing
    fake_queue.finish(job["id"])
    app.state.engine.wait_idle()
    last = client.get(f"/jobs/{job['id']}", headers=auth).json()
    assert last["state"] == "done" and "failed" not in last["result"]
    assert str(outs[1]) in last["result"]["files"] and str(outs[0].with_suffix(".jpg")) in last["result"]["files"]
    assert last["result"]["warnings"] == [{"hid": h["id"], "aspect": "1:1", "kind": "thumbnail"}]


def test_cancel_during_thumbnail_keeps_that_clips_video(client, auth, app, fake_queue, analyzed_project, tmp_path):
    pid, h = analyzed_project["id"], analyzed_project["highlights"][0]
    job = client.post(f"/projects/{pid}/export", headers=auth,
                      json={"clips": [h["id"]], "formats": ["9:16", "1:1"], "thumbnail": True, "out_dir": str(tmp_path)}).json()
    first = tmp_path / output_name(1, h["title"], "9:16")
    first.write_bytes(b"")
    fake_queue.emit(job["id"], state="running", stage="thumb 1/2")
    fake_queue.emit(job["id"], state="cancelled")
    app.state.engine.wait_idle()
    got = client.get(f"/jobs/{job['id']}", headers=auth).json()
    assert got["result"]["files"] == [str(first)] and "failed" not in got["result"]


def test_thumb_frame_time():
    from thumb_frame import frame_time
    assert frame_time(10) == 0.5 and frame_time(0.4) == 0.2 and frame_time(0) == 0.0


def test_jobs_list_knows_every_active_export_of_a_project(client, auth, app, fake_queue, analyzed_project, tmp_path):
    pid, h = analyzed_project["id"], analyzed_project["highlights"][0]
    ids = [client.post(f"/projects/{pid}/export", headers=auth,
                       json={"clips": [h["id"]], "out_dir": str(tmp_path), "first_number": n}).json()["id"] for n in (1, 2)]
    app.state.engine.wait_idle()
    got = client.get(f"/jobs?project={pid}&kind=export", headers=auth).json()  # what a reloaded app asks for
    assert sorted(j["id"] for j in got) == sorted(ids) and all(j["result"]["renders"] for j in got)
    assert client.get("/jobs?project=nope", headers=auth).json() == []
    assert client.get(f"/jobs?project={pid}&kind=analyze", headers=auth).json() == []
    fake_queue.finish(ids[0])
    app.state.engine.wait_idle()
    left = client.get(f"/jobs?project={pid}", headers=auth).json()
    assert [j["id"] for j in left] == [ids[1]]
    assert client.get("/jobs").status_code == 401


def test_thumb_frame_never_fails_and_clears_a_stale_jpg(tmp_path):
    import subprocess, sys
    script = Path(__file__).resolve().parent.parent / "scripts" / "thumb_frame.py"
    stale = tmp_path / "a.jpg"
    stale.write_bytes(b"old")
    r = subprocess.run([sys.executable, str(script), str(tmp_path / "missing.mp4"), str(stale)], capture_output=True)
    assert r.returncode == 0 and not stale.exists()
    assert subprocess.run([sys.executable, str(script)], capture_output=True).returncode == 0  # even bad arguments


def test_export_removes_a_stale_thumbnail_before_the_job_runs(client, auth, fake_queue, analyzed_project, tmp_path):
    pid, h = analyzed_project["id"], analyzed_project["highlights"][0]
    stale = tmp_path / output_name(1, h["title"], "9:16")
    stale.with_suffix(".jpg").write_bytes(b"old")
    client.post(f"/projects/{pid}/export", headers=auth,
                json={"clips": [h["id"]], "thumbnail": True, "out_dir": str(tmp_path)})
    assert not stale.with_suffix(".jpg").exists()


@pytest.mark.parametrize("end_state", ["failed", "cancelled"])
def test_export_failure_reports_finished_files(client, auth, app, fake_queue, analyzed_project, tiny_video,
                                              end_state):
    pid, h = analyzed_project["id"], analyzed_project["highlights"][0]
    job = client.post(f"/projects/{pid}/export", headers=auth,
                      json={"clips": [h["id"]], "formats": ["9:16", "1:1"]}).json()
    temps = [Path(s[1][-1]) for s in fake_queue.steps]
    assert all(t.is_file() for t in temps)
    first = output_dir(tiny_video.resolve()) / output_name(1, h["title"], "9:16")
    first.parent.mkdir(parents=True, exist_ok=True)
    first.write_bytes(b"")  # render 1/2 finished
    fake_queue.emit(job["id"], state="running", stage="render 1/2")
    fake_queue.emit(job["id"], stage="render 2/2", pct=50.0)
    fake_queue.emit(job["id"], state=end_state, error="boom" if end_state == "failed" else None)
    app.state.engine.wait_idle()
    got = client.get(f"/jobs/{job['id']}", headers=auth).json()
    assert got["state"] == end_state and got["result"]["files"] == [str(first)]
    if end_state == "failed":
        assert got["result"]["failed"] == {"hid": h["id"], "aspect": "1:1"}
    else:
        assert "failed" not in got["result"]
    assert load_project(pid).last_job == got
    assert not any(t.exists() for t in temps)


def test_post_processing_failure_is_consistent(client, auth, app, fake_queue, analyzed_project):
    pid = analyzed_project["id"]
    (analyzed_project["wd"] / "highlights.json").unlink()  # analyze "succeeded" but left nothing to build from
    with client.websocket_connect(f"/events?token={TOKEN}") as ws:
        job = client.post(f"/projects/{pid}/analyze", headers=auth, json={}).json()
        fake_queue.finish(job["id"])
        app.state.engine.wait_idle()
        msgs = [ws.receive_json() for _ in range(3)]
    assert msgs[-1]["job"]["state"] == "failed" and "post-processing failed" in msgs[-1]["job"]["error"]
    got = client.get(f"/jobs/{job['id']}", headers=auth).json()
    assert got == msgs[-1]["job"] == load_project(pid).last_job
    assert client.post(f"/jobs/{job['id']}/cancel", headers=auth).json() == got


def test_cancel_job(client, auth, fake_queue, analyzed_project):
    job = client.post(f"/projects/{analyzed_project['id']}/analyze", headers=auth, json={}).json()
    r = client.post(f"/jobs/{job['id']}/cancel", headers=auth)
    assert r.status_code == 200 and r.json()["state"] == "cancelled"
    assert client.post("/jobs/nope/cancel", headers=auth).status_code == 404
    r = client.get("/jobs/nope", headers=auth)
    assert r.status_code == 404 and r.json()["code"] == "not_found"
    assert client.get(f"/jobs/{job['id']}", headers=auth).json()["state"] == "cancelled"


def test_ai_key_never_persisted(client, auth, app, fake_queue, analyzed_project, tmp_path):
    pid = analyzed_project["id"]
    seen = []
    orig = fake_queue.on_event
    fake_queue.on_event = lambda j: (seen.append(json.dumps(dataclasses.asdict(j))), orig(j))
    with client.websocket_connect(f"/events?token={TOKEN}") as ws:
        job = client.post(f"/projects/{pid}/analyze", headers=auth,
                          json={"ai": {"provider": "anthropic", "api_key": KEY, "model": "m1"}}).json()
        fake_queue.finish(job["id"])
        app.state.engine.wait_idle()
        msgs = [ws.receive_json() for _ in range(3)]
    sub = fake_queue.submits[-1]
    assert sub["env"] == {}  # never job-wide: only the highlights step gets the key
    assert sub["steps"][2][3] == {"VEDIT_AI_KEY": KEY}
    assert all(len(s) == 3 for k, s in enumerate(sub["steps"]) if k != 2)
    hl = sub["steps"][2][1]
    assert hl[hl.index("--ai") + 1] == "anthropic" and hl[hl.index("--ai-model") + 1] == "m1"
    assert not any(KEY in a for s in sub["steps"] for a in s[1])
    assert KEY not in json.dumps(job) and not any(KEY in s for s in seen) and KEY not in json.dumps(msgs)
    assert load_project(pid).last_job["state"] == "done"
    assert not any(KEY in p.read_text(errors="ignore") for p in all_files(tmp_path))


def ws_receive(ws, timeout=5.0):
    """receive_json with a deadline: a stuck server fails the test instead of hanging it."""
    box = {}

    def go():
        try:
            box["msg"] = ws.receive_json()
        except BaseException as e:  # noqa: BLE001 - re-raised in the test thread
            box["err"] = e

    t = threading.Thread(target=go, daemon=True)
    t.start()
    t.join(timeout)
    assert not t.is_alive(), f"no websocket message within {timeout}s"
    if "err" in box:
        raise box["err"]
    return box["msg"]


def wait_clients(engine, n, timeout=5.0):
    deadline = time.monotonic() + timeout
    while len(engine._clients) < n:
        assert time.monotonic() < deadline, "server never registered the websocket client"
        time.sleep(0.01)


def test_events_websocket(client, auth, app, fake_queue, analyzed_project):
    engine = app.state.engine
    with client.websocket_connect(f"/events?token={TOKEN}") as ws:
        wait_clients(engine, 1)  # registered before the event is triggered
        job = client.post(f"/projects/{analyzed_project['id']}/analyze", headers=auth, json={}).json()
        engine.wait_idle()
        msg = ws_receive(ws)
        assert msg["type"] == "job" and msg["job"]["id"] == job["id"] and msg["job"]["state"] == "queued"
    with client.websocket_connect(f"/events?token={TOKEN}") as ws:  # reconnect: snapshot of what is active
        msg = ws_receive(ws)
        assert msg["job"]["id"] == job["id"] and msg["job"]["state"] == "queued"
    fake_queue.emit(job["id"], state="cancelled")
    app.state.engine.wait_idle()
    assert app.state.engine.active_jobs() == []
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/events?token=wrong") as ws:
            ws.receive_json()
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/events?token={TOKEN}", headers={"Origin": "https://evil.example"}) as ws:
            ws.receive_json()


def test_ws_queue_drops_oldest_when_full():
    import asyncio

    from studio_server import WS_QUEUE_MAX, _offer
    q = asyncio.Queue(maxsize=WS_QUEUE_MAX)
    for i in range(WS_QUEUE_MAX + 5):
        _offer(q, {"n": i})
    assert q.qsize() == WS_QUEUE_MAX and q.get_nowait() == {"n": 5}


def test_error_bodies_have_codes(client, auth):
    r = client.get("/health")
    assert r.status_code == 401 and r.json() == {"detail": "missing or wrong token", "code": "unauthorized"}
    r = client.get("/health", headers={**auth, "Origin": "https://evil.example"})
    assert r.json()["code"] == "forbidden"
    r = client.get("/projects/nope", headers=auth)
    assert r.json()["code"] == "not_found" and isinstance(r.json()["detail"], str)
    r = client.post("/projects", headers=auth, json={"source": "x", "language": "xx", "platform": "reels"})
    assert r.status_code == 422 and r.json()["code"] == "bad_request" and isinstance(r.json()["detail"], str)
    assert client.get("/no-such-route", headers=auth).json()["code"] == "not_found"


@pytest.mark.parametrize("token", ["", "   "])
def test_create_app_rejects_empty_token(home, token):
    with pytest.raises(ValueError):
        create_app(token, FakeQueue())


# ---- restart and serve ----------------------------------------------------------------------------------------

def test_interrupted_job_marked_failed_on_start(tmp_path, monkeypatch):
    monkeypatch.setenv("VEDIT_HOME", str(tmp_path / "home"))
    states = {}
    for name, state in (("a", "running"), ("b", "queued"), ("c", "done")):
        v = tmp_path / f"{name}.mp4"
        v.write_bytes(b"")
        p = create_project(v, "en", "reels")
        p.last_job = {"id": name, "project_id": p.id, "kind": "analyze", "state": state, "stage": "", "pct": 0.0,
                      "message": "", "error": None, "result": None}
        save_project(p)
        states[p.id] = state
    app = create_app(TOKEN, FakeQueue())
    try:
        for pid, state in states.items():
            job = load_project(pid).last_job
            if state == "done":
                assert job["state"] == "done" and job["error"] is None
            else:
                assert job["state"] == "failed" and job["error"] == "interrupted"
    finally:
        app.state.engine.close()


def test_serve_prints_ready_line(tmp_path):
    env = {**os.environ, "VEDIT_HOME": str(tmp_path / "home"), "PYTHONIOENCODING": "utf-8",
           "VEDIT_ENGINE_TOKEN": TOKEN}
    proc = subprocess.Popen([sys.executable, str(SCRIPTS / "engine_entry.py"), "serve", "--port", "0"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    lines = []
    reader = threading.Thread(target=lambda: lines.append(proc.stdout.readline()), daemon=True)
    reader.start()
    try:
        reader.join(timeout=10)
        assert lines and lines[0].startswith("@@ready "), (lines, proc.poll())
        port = json.loads(lines[0][len("@@ready "):])["port"]
        assert port > 0
        r = httpx.get(f"http://127.0.0.1:{port}/health", headers={"Authorization": f"Bearer {TOKEN}"}, timeout=5)
        assert r.status_code == 200 and r.json()["ok"] is True
    finally:
        proc.kill()
        proc.wait(timeout=10)
        out = proc.stdout.read()
        proc.stdout.close()
        proc.stderr.close()
    assert out.strip() == ""  # exactly one stdout line


@pytest.mark.parametrize("value", [None, "", "  "])
def test_serve_requires_token_env(tmp_path, value):
    env = {k: v for k, v in os.environ.items() if k != "VEDIT_ENGINE_TOKEN"}
    env.update({"VEDIT_HOME": str(tmp_path / "home"), **({"VEDIT_ENGINE_TOKEN": value} if value is not None else {})})
    r = subprocess.run([sys.executable, str(SCRIPTS / "engine_entry.py"), "serve", "--port", "0"],
                       capture_output=True, text=True, env=env, timeout=30)
    assert r.returncode == 2 and "VEDIT_ENGINE_TOKEN" in r.stderr and r.stdout == ""
    r = subprocess.run([sys.executable, str(SCRIPTS / "engine_entry.py"), "serve", "--token", TOKEN],
                       capture_output=True, text=True, env=env, timeout=30)
    assert r.returncode == 2 and "--token" in r.stderr  # argv token is not accepted


def test_unexpected_keyerror_is_500(app, auth, monkeypatch):
    import studio_server

    def boom(*_a, **_k):
        raise KeyError("internal")
    monkeypatch.setattr(studio_server, "list_recent", boom)
    r = TestClient(app, raise_server_exceptions=False).get("/projects/recent", headers=auth)
    assert r.status_code == 500 and r.json() == {"detail": "internal error", "code": "internal"}


def test_clip_edits_probe_source_once(client, auth, analyzed_project, monkeypatch):
    import studio_server
    studio_server._probe_cache.clear()
    calls = []
    real = studio_server.ffprobe
    monkeypatch.setattr(studio_server, "ffprobe", lambda path: (calls.append(path), real(path))[1])
    import edl
    monkeypatch.setattr(edl, "ffprobe", lambda path: pytest.fail("Timeline probed without the cache"))
    pid, hid = analyzed_project["id"], analyzed_project["highlights"][0]["id"]
    url = f"/projects/{pid}/clips/{hid}"
    a, _ = client.get(url, headers=auth).json()["edl"]["clips"][0]["words"]
    assert client.patch(url, headers=auth, json={"op": "cut", "w0": a, "w1": a}).status_code == 200
    assert client.patch(url, headers=auth, json={"op": "restore", "w0": a, "w1": a}).status_code == 200
    assert len(calls) <= 1


# ---- engine CLIs added for the server -------------------------------------------------------------------------

def test_proxy_cli_writes_filmstrip(tiny_video):
    from conftest import run_script
    run_script("proxy.py", tiny_video)
    meta = load_json(workdir_for(tiny_video) / "filmstrip.json")
    assert meta["count"] >= 1 and (workdir_for(tiny_video) / "filmstrip.jpg").is_file()


def test_highlights_framing_cli(tiny_video, talking):
    from conftest import run_script
    wd = workdir_for(tiny_video)
    hs = [{"id": "h01", "start": 0.5, "end": 3.0}, {"id": "h02", "start": 4.0, "end": 6.0}]
    save_json({"source": str(tiny_video), "highlights": hs, "reason": None}, wd / "highlights.json")
    run_script("highlights.py", tiny_video, "--framing")
    fr = load_json(wd / "framing.json")
    assert set(fr) == {"h01", "h02"}
    assert all(isinstance(v["crop_x"], float) and 0 <= v["crop_x"] <= 1 and isinstance(v["face"], bool)
               for v in fr.values())


def test_render_crf_flag(pipeline_workdir, tmp_path, monkeypatch):
    import render
    wd = tmp_path / "wd"
    shutil.copytree(pipeline_workdir, wd)
    seen = []
    monkeypatch.setattr(render, "render_segments", lambda tl, *a, **k: [])
    monkeypatch.setattr(render, "final_pass", lambda tl, segs, ass, out, preview: seen.append(tl.out["crf"]))
    for extra in ([], ["--crf", "18"]):
        monkeypatch.setattr(sys, "argv", ["render.py", str(wd / "edl.json"), *extra])
        render.main()
    assert seen == [20, 18]


# ---- models ---------------------------------------------------------------------------------------------------

def test_models_status_route(client, auth, monkeypatch):
    installed(monkeypatch, "small")
    r = client.get("/models", headers=auth).json()
    assert r == {"small": {"installed": True, "size_mb": 484}, "large-v3": {"installed": False, "size_mb": 3090}}


def test_model_download_submits_model_job(client, auth, fake_queue):
    job = client.post("/models/large-v3/download", headers=auth).json()
    assert job["kind"] == "model"
    assert [s[0] for s in fake_queue.steps] == ["model"] and fake_queue.steps[0][2] == 1.0
    assert fake_queue.steps[0][1][-2:] == ["download", "large-v3"]


def test_model_unknown_name_404(client, auth):
    for r in (client.post("/models/huge/download", headers=auth), client.delete("/models/huge", headers=auth)):
        assert r.status_code == 404 and r.json()["code"] == "not_found"


def test_model_delete_and_busy(client, auth, app, fake_queue, tmp_path, monkeypatch):
    monkeypatch.undo()  # real status()/delete against a temp folder
    monkeypatch.setenv("VEDIT_MODELS", str(tmp_path / "m"))
    monkeypatch.setenv("VEDIT_HOME", str(tmp_path / "home"))
    (tmp_path / "m" / "small").mkdir(parents=True)
    for f in models.REQUIRED_FILES:
        (tmp_path / "m" / "small" / f).write_bytes(b"x")
    models._mark_complete("small")
    job = client.post("/models/large-v3/download", headers=auth).json()
    app.state.engine.wait_idle()
    busy = client.delete("/models/small", headers=auth)
    assert busy.status_code == 409 and busy.json()["code"] == "busy" and (tmp_path / "m" / "small").exists()
    fake_queue.finish(job["id"])
    app.state.engine.wait_idle()
    r = client.delete("/models/small", headers=auth)
    assert r.status_code == 200 and r.json()["small"]["installed"] is False and not (tmp_path / "m" / "small").exists()


def test_patch_null_and_same_fix_remove_keys_over_http(client, auth, analyzed_project):
    pid, hid = analyzed_project["id"], analyzed_project["highlights"][0]["id"]
    url = f"/projects/{pid}/clips/{hid}"
    sent = client.patch(url, headers=auth, json={"op": "patch", "section": "captions", "patch": {"zz_key": 5}})
    assert sent.json()["edl"]["captions"]["zz_key"] == 5
    r = client.patch(url, headers=auth, json={"op": "patch", "section": "captions", "patch": {"zz_key": None}})
    assert r.status_code == 200 and "zz_key" not in r.json()["edl"]["captions"]
    assert "zz_key" not in client.get(url, headers=auth).json()["edl"]["captions"]  # persisted
    client.patch(url, headers=auth, json={"op": "fix_word", "wrong": "FFmpeg", "right": "ffmpeg"})
    r = client.patch(url, headers=auth, json={"op": "fix_word", "wrong": "FFmpeg", "right": "FFmpeg"})
    assert r.status_code == 200 and "FFmpeg" not in r.json()["edl"]["captions"]["replace"]


def test_patch_output_aspect_over_http(client, auth, analyzed_project):
    pid, hid = analyzed_project["id"], analyzed_project["highlights"][0]["id"]
    url = f"/projects/{pid}/clips/{hid}"
    before = client.get(url, headers=auth).json()["edl"]["output"].get("aspect")
    r = client.patch(url, headers=auth, json={"op": "patch", "section": "output", "patch": {"aspect": "1:1"}})
    assert r.status_code == 200 and r.json()["edl"]["output"]["aspect"] == "1:1"
    assert client.get(url, headers=auth).json()["edl"]["output"]["aspect"] == "1:1"  # persisted
    bad = client.patch(url, headers=auth, json={"op": "patch", "section": "output", "patch": {"aspect": "2:1"}})
    assert bad.status_code == 400
    bad = client.patch(url, headers=auth, json={"op": "patch", "section": "output", "patch": {"path": "C:/x.mp4"}})
    assert bad.status_code == 400
    assert client.get(url, headers=auth).json()["edl"]["output"]["aspect"] == "1:1"  # a refused patch changes nothing
    r = client.patch(url, headers=auth, json={"op": "patch", "section": "output", "patch": {"aspect": None}})
    assert r.status_code == 200 and "aspect" not in r.json()["edl"]["output"]
    if before is not None:
        client.patch(url, headers=auth, json={"op": "patch", "section": "output", "patch": {"aspect": before}})


# ---- AI key test and brand kit --------------------------------------------------------------------------------

def test_ai_test_reports_ok_and_never_echoes_the_key(client, auth, monkeypatch):
    import studio_server
    seen = {}

    def fake_get(url, headers):
        seen["url"], seen["headers"] = url, headers
        return 200

    monkeypatch.setattr(studio_server, "_http_get", fake_get)
    r = client.post("/ai/test", headers=auth, json={"provider": "anthropic", "api_key": "sk-ant-secret"})
    assert r.json() == {"ok": True}
    assert "anthropic.com" in seen["url"] and seen["headers"]["x-api-key"] == "sk-ant-secret"
    assert "sk-ant-secret" not in r.text
    client.post("/ai/test", headers=auth, json={"provider": "openai", "api_key": "sk-x"})
    assert seen["headers"]["Authorization"] == "Bearer sk-x"


@pytest.mark.parametrize("status,error", [(401, "rejected"), (403, "rejected"), (500, "status_500")])
def test_ai_test_failures(client, auth, monkeypatch, status, error):
    import studio_server
    monkeypatch.setattr(studio_server, "_http_get", lambda url, headers: status)
    r = client.post("/ai/test", headers=auth, json={"provider": "openai", "api_key": "sk-bad"})
    assert r.json() == {"ok": False, "error": error}


def test_ai_test_network_error_hides_the_key(client, auth, monkeypatch):
    import studio_server

    def boom(url, headers):
        raise OSError("failed with header sk-leak")

    monkeypatch.setattr(studio_server, "_http_get", boom)
    r = client.post("/ai/test", headers=auth, json={"provider": "openai", "api_key": "sk-leak"})
    assert r.json() == {"ok": False, "error": "network"}
    assert "sk-leak" not in r.text
    assert client.post("/ai/test", headers=auth, json={"provider": "openai", "api_key": " "}).json()["error"] == "empty"
    assert client.post("/ai/test", headers=auth, json={"provider": "nope", "api_key": "k"}).status_code == 422


def test_brand_round_trip(client, auth, home):
    empty = client.get("/brand", headers=auth).json()
    assert empty == {k: "" for k in ("font", "caption_color", "highlight", "logo", "intro", "outro", "music")}
    kit = {**empty, "font": "Poppins", "caption_color": "#ffffff", "highlight": "#ffc72c", "logo": "C:/b/logo.png"}
    assert client.put("/brand", headers=auth, json=kit).json() == kit
    assert client.get("/brand", headers=auth).json() == kit
    saved = json.loads((home / "brand.json").read_text(encoding="utf-8"))
    assert saved == {"font": "Poppins", "caption_color": "#ffffff", "highlight": "#ffc72c", "logo": "C:/b/logo.png"}


def test_brand_put_keeps_keys_settings_does_not_edit(client, auth, home):
    home.mkdir(parents=True, exist_ok=True)
    (home / "brand.json").write_text(json.dumps({"font": "Old", "caption_style": "pop", "uppercase": True,
                                                 "replace": {"a": "b"}}), encoding="utf-8")
    body = {"font": "New", "caption_color": "", "highlight": "", "logo": "", "intro": "", "outro": "", "music": ""}
    client.put("/brand", headers=auth, json=body)
    saved = json.loads((home / "brand.json").read_text(encoding="utf-8"))
    assert saved == {"font": "New", "caption_style": "pop", "uppercase": True, "replace": {"a": "b"}}


@pytest.mark.parametrize("text", ["{not json", "[1, 2]", ""])
def test_bad_brand_file_is_treated_as_empty(client, auth, home, text):
    import studio_server
    home.mkdir(parents=True, exist_ok=True)
    (home / "brand.json").write_text(text, encoding="utf-8")
    r = client.get("/brand", headers=auth)
    assert r.status_code == 200 and set(r.json().values()) == {""}
    assert studio_server.usable_brand_file() is None
    assert client.put("/brand", headers=auth, json={**r.json(), "font": "Fix"}).status_code == 200
    assert studio_server.usable_brand_file() is not None


# ---- media for the preview: a <video> or <img> cannot send headers, so these take ?token= ----

def test_media_streams_the_video_with_range_and_query_token(client, analyzed_project, tiny_video):
    pid = analyzed_project["id"]
    assert client.get(f"/projects/{pid}/media").status_code == 401
    assert client.get(f"/projects/{pid}/media?token=wrong").status_code == 401
    r = client.get(f"/projects/{pid}/media?token={TOKEN}", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and r.headers["content-type"] == "video/mp4"
    assert r.content == tiny_video.read_bytes()[:100]
    full = client.get(f"/projects/{pid}/media?token={TOKEN}")
    assert full.status_code == 200 and len(full.content) == tiny_video.stat().st_size


def test_query_token_is_only_for_media_routes(client, analyzed_project):
    assert client.get(f"/projects/{analyzed_project['id']}?token={TOKEN}").status_code == 401
    assert client.post(f"/projects/{analyzed_project['id']}/media?token={TOKEN}").status_code == 401


def test_filmstrip_image_streams(client, analyzed_project):
    wd = analyzed_project["wd"]
    (wd / "filmstrip.jpg").write_bytes(b"\xff\xd8jpeg")
    r = client.get(f"/projects/{analyzed_project['id']}/filmstrip.jpg?token={TOKEN}")
    assert r.status_code == 200 and r.content == b"\xff\xd8jpeg" and r.headers["content-type"] == "image/jpeg"


def test_file_serves_only_broll_a_clip_uses(client, auth, analyzed_project, tmp_path):
    pid, hid = analyzed_project["id"], analyzed_project["highlights"][0]["id"]
    shot, other = tmp_path / "shot.png", tmp_path / "secret.png"
    shot.write_bytes(b"png-bytes")
    other.write_bytes(b"nope")
    url = lambda p: f"/file?path={p}&token={TOKEN}"  # noqa: E731
    assert client.get(url(shot)).status_code == 404  # not used by any clip yet
    item = {"id": "b", "type": "image", "at": {"src": "main", "t": 1}, "duration": 1, "path": str(shot), "mode": "full"}
    assert client.patch(f"/projects/{pid}/clips/{hid}", headers=auth,
                        json={"op": "patch", "section": "overlays", "patch": {"items": [item]}}).status_code == 200
    r = client.get(url(shot))
    assert r.status_code == 200 and r.content == b"png-bytes"
    assert client.get(url(other)).status_code == 404
    assert client.get(url(analyzed_project["wd"] / "transcript.main.json")).status_code == 404
