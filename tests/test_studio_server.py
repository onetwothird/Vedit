"""Studio local engine server: auth/origin, projects, analyze/export job wiring, clip edits, restart, serve."""
import dataclasses
import json
import os
import shutil
import subprocess
import sys
import threading

import pytest

pytest.importorskip("fastapi", reason="needs the studio extra")
import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

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


@pytest.fixture
def fake_queue():
    return FakeQueue()


@pytest.fixture
def app(home, fake_queue):
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
    assert isinstance(d["ffmpeg"], bool) and isinstance(d["libass"], bool) and d["models"] == {}


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
    assert fake_queue.submits[-1]["env"] == {}


def test_analyze_done_builds_clips(client, auth, analyzed_project):
    p = client.get(f"/projects/{analyzed_project['id']}", headers=auth).json()
    hs = analyzed_project["highlights"]
    assert hs and [c["highlight"]["id"] for c in p["clips"]] == [h["id"] for h in hs]
    assert all(c["kept"] and (analyzed_project["wd"] / c["edl_file"]).is_file() for c in p["clips"])
    assert p["last_job"]["state"] == "done"
    assert p["last_job"]["result"] == {"clips": len(hs), "reason": None}


def test_clip_edl_error_skips_that_highlight(client, auth, app, fake_queue, analyzed_project):
    wd = analyzed_project["wd"]
    hl = load_json(wd / "highlights.json")
    bad = {**hl["highlights"][0], "id": "h99", "w0": 10**6, "w1": 10**6 + 1}  # no speech in range -> ValueError
    hl["highlights"].append(bad)
    save_json(hl, wd / "highlights.json")
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
    assert client.get(f"/projects/{pid}/clips/nope", headers=auth).status_code == 404


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
    trimmed = client.patch(url, headers=auth, json={"op": "trim", "w0": a + 1, "w1": b}).json()
    assert trimmed["edl"]["clips"][0]["words"][0] == a + 1
    restored = client.patch(url, headers=auth, json={"op": "restore", "w0": a, "w1": a}).json()
    assert restored["edl"]["clips"][0]["words"][0] == a
    assert client.patch(url, headers=auth, json={"op": "cut", "w0": 5, "w1": 2}).status_code == 400
    assert client.patch(url, headers=auth, json={"op": "explode"}).status_code == 400
    assert client.patch(url, headers=auth, json={"op": "cut"}).status_code == 400
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
    assert [s[0] for s in steps] == ["render", "render"] and [s[2] for s in steps] == [0.5, 0.5]
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
    normal = client.post(f"/projects/{pid}/export", headers=auth,
                         json={"clips": [h["id"]], "formats": ["9:16"], "quality": "normal"})
    assert normal.status_code == 200 and "--crf" not in fake_queue.steps[0][1]
    assert client.post(f"/projects/{pid}/export", headers=auth,
                       json={"clips": ["nope"], "formats": ["9:16"]}).status_code == 404
    assert client.post(f"/projects/{pid}/export", headers=auth,
                       json={"clips": [h["id"]], "formats": []}).status_code == 400


def test_cancel_job(client, auth, fake_queue, analyzed_project):
    job = client.post(f"/projects/{analyzed_project['id']}/analyze", headers=auth, json={}).json()
    r = client.post(f"/jobs/{job['id']}/cancel", headers=auth)
    assert r.status_code == 200 and r.json()["state"] == "cancelled"
    assert client.post("/jobs/nope/cancel", headers=auth).status_code == 404


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
    assert sub["env"] == {"VEDIT_AI_KEY": KEY}
    hl = sub["steps"][2][1]
    assert hl[hl.index("--ai") + 1] == "anthropic" and hl[hl.index("--ai-model") + 1] == "m1"
    assert not any(KEY in a for s in sub["steps"] for a in s[1])
    assert KEY not in json.dumps(job) and not any(KEY in s for s in seen) and KEY not in json.dumps(msgs)
    assert load_project(pid).last_job["state"] == "done"
    assert not any(KEY in p.read_text(errors="ignore") for p in all_files(tmp_path))


def test_events_websocket(client, auth, app, fake_queue, analyzed_project):
    with client.websocket_connect(f"/events?token={TOKEN}") as ws:
        job = client.post(f"/projects/{analyzed_project['id']}/analyze", headers=auth, json={}).json()
        app.state.engine.wait_idle()
        msg = ws.receive_json()
        assert msg["type"] == "job" and msg["job"]["id"] == job["id"] and msg["job"]["state"] == "queued"
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/events?token=wrong") as ws:
            ws.receive_json()
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/events?token={TOKEN}", headers={"Origin": "https://evil.example"}) as ws:
            ws.receive_json()


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
    env = {**os.environ, "VEDIT_HOME": str(tmp_path / "home"), "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.Popen([sys.executable, str(SCRIPTS / "engine_entry.py"), "serve", "--port", "0", "--token",
                             TOKEN], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
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
