"""Cloud projects from uploads, analyse with the monthly limit, refunds, busy guards."""
import json
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="needs the studio extra")
jwt = pytest.importorskip("jwt", reason="needs the cloud extra")
from fastapi.testclient import TestClient  # noqa: E402

import studio_server  # noqa: E402
from cloud_config import CloudConfig  # noqa: E402
import cloud_storage as cs  # noqa: E402
from cloud_usage import Busy, MemoryUsage  # noqa: E402  (the module name the engine itself imports)
from studio_projects import USER_HOME  # noqa: E402
from studio_server import create_app  # noqa: E402
from test_cloud_auth import SECRET, UID_A, UID_B, auth, make_token  # noqa: E402

A = auth(make_token(UID_A))
B = auth(make_token(UID_B))
BUSY = "Vedit is busy, try again later"


class FakeRunner:
    """Records every step with the home it ran under; the exit code is mutable between runs."""

    def __init__(self):
        self.code = 0
        self.block = False
        self.calls, self.homes, self.cancelled = [], [], []
        self.gates: dict[str, threading.Event] = {}

    def run(self, job_id, stage, argv, env, on_line, cancelled):
        self.calls.append((job_id, stage, argv, env))
        self.homes.append(USER_HOME.get())
        if stage == "highlights":
            src = Path(argv[2])
            (src.parent / (src.stem + ".edit") / "highlights.json").write_text(
                json.dumps({"highlights": [], "reason": "none"}), encoding="utf-8")
        if self.block:
            self.gates.setdefault(job_id, threading.Event()).wait(5)
        return self.code, ["boom"] if self.code else []

    def cancel(self, job_id):
        self.cancelled.append(job_id)
        self.gates.setdefault(job_id, threading.Event()).set()

    def release_all(self):
        for ev in self.gates.values():
            ev.set()


def fake_probe(path):
    """The file's bytes say what it is: video:600 lasts 600 s, anything else is not a video."""
    data = Path(path).read_bytes()
    if not data.startswith(b"video:"):
        raise RuntimeError("ffprobe: invalid data")
    return {"path": str(path), "duration": float(data.split(b":")[1]), "has_video": True, "has_audio": True}


@pytest.fixture(autouse=True)
def _probe(monkeypatch):
    monkeypatch.setattr(studio_server, "cached_probe", fake_probe)


@pytest.fixture
def storage():
    return cs.MemoryStorage()


@pytest.fixture
def usage():
    return MemoryUsage(default_limit=3600)


@pytest.fixture
def runner():
    return FakeRunner()


@pytest.fixture
def cfg(tmp_path, storage, usage, runner):
    return CloudConfig(data_root=tmp_path / "data", origins=("https://web.example.test",),
                       supabase_url="https://x.supabase.test", jwt_secret=SECRET, storage=storage, usage=usage,
                       runner_factory=lambda: runner)


@pytest.fixture
def client(cfg, runner):
    with TestClient(create_app("desktop-token", cloud=cfg)) as c:
        yield c
        runner.release_all()


def home_of(cfg, uid=UID_A):
    return cfg.data_root / "users" / uid


def upload(client, storage, data=b"video:600", headers=A, name="clip.mp4", kind="video", ctype="video/mp4"):
    r = client.post("/uploads", headers=headers,
                    json={"kind": kind, "name": name, "size": len(data), "content_type": ctype})
    assert r.status_code == 200, r.text
    key = r.json()["upload_key"]
    storage.objects[key] = data
    return key


def make_project(client, storage, data=b"video:600", headers=A, language="tl", **kw):
    key = upload(client, storage, data, headers, **kw)
    return client.post("/projects", headers=headers,
                       json={"upload_key": key, "language": language, "platform": "tiktok", "type": "clip"})


def wait_state(client, job_id, headers=A):
    end = time.time() + 5
    while time.time() < end:
        j = client.get(f"/jobs/{job_id}", headers=headers).json()
        if j["state"] in ("done", "failed", "cancelled"):
            client.app.state.engine.wait_idle()
            return j
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def analyze(client, pid, headers=A):
    return client.post(f"/projects/{pid}/analyze", headers=headers, json={})


def used(usage, uid=UID_A):
    return usage.status(uid)["seconds_used"]


# ---- creating a project from an upload ------------------------------------------------------------------------

def test_project_from_upload(client, storage, usage, cfg):
    r = make_project(client, storage)
    assert r.status_code == 200, r.text
    src = Path(r.json()["source"])
    assert src.is_file() and src.name == "source.mp4" and src.parent.parent == home_of(cfg) / "work"
    assert storage.objects == {}
    assert list((home_of(cfg) / "uploads").glob("*")) == []
    assert used(usage) == 0  # nothing is charged at creation
    assert r.json()["last_activity"]


def test_source_path_is_refused_in_cloud(client, tmp_path):
    f = tmp_path / "a.mp4"
    f.write_bytes(b"video:60")
    r = client.post("/projects", headers=A, json={"source": str(f), "language": "en", "platform": "tiktok"})
    assert r.status_code == 400
    assert client.post("/projects", headers=A, json={"language": "en", "platform": "tiktok"}).status_code == 400


def test_other_users_or_non_video_key_is_404(client, storage):
    key = upload(client, storage, headers=A)
    body = {"upload_key": key, "language": "en", "platform": "tiktok"}
    assert client.post("/projects", headers=B, json=body).status_code == 404
    assert key in storage.objects  # the owner can still use it
    img = upload(client, storage, b"png", kind="image", name="a.png", ctype="image/png")
    assert client.post("/projects", headers=A, json={**body, "upload_key": img}).status_code == 404
    assert img in storage.objects
    assert client.post("/projects", headers=A, json={**body, "upload_key": "nope"}).status_code == 404


def assert_nothing_left(cfg, storage):
    assert storage.objects == {}
    home = home_of(cfg)
    assert list((home / "uploads").glob("*")) == []
    assert not (home / "work").exists() or list((home / "work").iterdir()) == []


def test_over_remaining_is_400_and_cleaned(client, storage, cfg):
    cfg.usage = MemoryUsage(default_limit=300)  # 5 min left
    r = make_project(client, storage, b"video:600")
    assert r.status_code == 400
    assert r.json()["detail"] == "This video is 10 min; you have 5 min left this month"
    assert_nothing_left(cfg, storage)


def test_partial_minutes_round_up_and_down(client, storage, cfg):
    cfg.usage = MemoryUsage(default_limit=359)
    r = make_project(client, storage, b"video:601")
    assert r.json()["detail"] == "This video is 11 min; you have 5 min left this month"


def test_over_an_hour_is_400(client, storage, cfg):
    r = make_project(client, storage, b"video:3601")
    assert r.status_code == 400 and r.json()["detail"] == "Videos can be up to 60 minutes"
    assert_nothing_left(cfg, storage)
    assert make_project(client, storage, b"video:3600").status_code == 200  # exactly an hour is fine


def test_not_a_video_is_400_and_cleaned(client, storage, cfg, usage):
    r = make_project(client, storage, b"just text")
    assert r.status_code == 400 and r.json()["detail"] == "This file is not a video"
    assert_nothing_left(cfg, storage)
    assert used(usage) == 0


# ---- analyse ---------------------------------------------------------------------------------------------------

def test_analyze_charges_duration_and_runs_cloud_steps(client, storage, usage, runner, cfg):
    r0 = make_project(client, storage, b"video:600").json()
    pid = r0["id"]
    r = analyze(client, pid)
    assert r.status_code == 200, r.text
    assert wait_state(client, r.json()["id"])["state"] == "done"
    assert used(usage) == 600
    assert [c[1] for c in runner.calls] == ["proxy", "analyze", "highlights", "framing"]  # no model step
    argv = runner.calls[1][2]
    assert argv[argv.index("--model") + 1] == "large-v3"  # tl on the GPU plan
    assert set(runner.homes) == {home_of(cfg)}  # every step ran as the user who submitted it
    pfile = Path(r0["source"]).parent / "source.edit" / "project.json"
    assert json.loads(pfile.read_text())["last_activity"]


def test_failed_job_is_refunded_and_can_be_retried(client, storage, usage, runner):
    pid = make_project(client, storage, b"video:600").json()["id"]
    runner.code = 1
    j = analyze(client, pid).json()
    assert wait_state(client, j["id"])["state"] == "failed"
    assert used(usage) == 0
    runner.code = 0
    j2 = analyze(client, pid).json()
    assert j2["id"] != j["id"]
    assert wait_state(client, j2["id"])["state"] == "done"
    assert used(usage) == 600  # the refunded id was not reused


def test_cancelled_job_is_not_refunded(client, storage, usage, runner):
    pid = make_project(client, storage, b"video:600").json()["id"]
    runner.block = True
    j = analyze(client, pid).json()
    assert client.post(f"/jobs/{j['id']}/cancel", headers=A).status_code == 200
    assert wait_state(client, j["id"])["state"] == "cancelled"
    assert used(usage) == 600


def test_over_limit_on_analyze_is_400(client, storage, cfg):
    cfg.usage = usage = MemoryUsage(default_limit=700)
    pid = make_project(client, storage, b"video:600").json()["id"]
    assert wait_state(client, analyze(client, pid).json()["id"])["state"] == "done"
    r = analyze(client, pid)  # re-analysis charges again: 100 s left
    assert r.status_code == 400 and r.json()["detail"] == "You have 1 min left this month"
    assert used(usage) == 600


class BusyUsage(MemoryUsage):
    def charge(self, uid, job_id, seconds):
        raise Busy()


def test_global_budget_spent_is_503(client, storage, cfg):
    pid = make_project(client, storage).json()["id"]
    cfg.usage = BusyUsage()
    r = analyze(client, pid)
    assert r.status_code == 503 and r.json()["detail"] == BUSY


def test_paused_is_503_and_charges_nothing(client, storage, usage, cfg):
    pid = make_project(client, storage).json()["id"]
    cfg.paused = True
    r = analyze(client, pid)
    assert r.status_code == 503 and r.json()["detail"] == BUSY
    assert used(usage) == 0


def test_third_job_and_same_project_are_409(client, storage, usage, runner):
    p1, p2, p3 = (make_project(client, storage).json()["id"] for _ in range(3))
    runner.block = True
    assert analyze(client, p1).status_code == 200
    assert analyze(client, p1).status_code == 409  # this project is already being analysed
    assert analyze(client, p2).status_code == 200
    assert analyze(client, p3).status_code == 409  # two jobs already
    assert used(usage) == 1200  # the refused ones were not charged
    pb = make_project(client, storage, headers=B).json()["id"]  # another user is not affected by A's jobs
    assert analyze(client, pb, headers=B).status_code == 200


def test_submit_failure_refunds(client, storage, usage, monkeypatch):
    pid = make_project(client, storage).json()["id"]

    def boom(*a, **k):
        raise RuntimeError("queue down")

    monkeypatch.setattr(client.app.state.engine.queue, "submit", boom)
    with pytest.raises(RuntimeError):  # the test client re-raises what the server answers with a 500
        analyze(client, pid)
    assert used(usage) == 0


# ---- project file ----------------------------------------------------------------------------------------------

def test_desktop_project_file_has_no_last_activity(tmp_path, monkeypatch):
    from studio_projects import create_project

    monkeypatch.setenv("VEDIT_HOME", str(tmp_path / "home"))
    src = tmp_path / "v.mp4"
    src.write_bytes(b"x")
    p = create_project(src, "en", "tiktok")
    doc = json.loads((tmp_path / "v.edit" / "project.json").read_text())
    assert "last_activity" not in doc and p.last_activity is None


def test_guards_hold_while_the_dispatcher_lags(client, storage, usage, runner, monkeypatch):
    """Back-to-back requests (a double click) must see each other's jobs even before the dispatcher has."""
    engine = client.app.state.engine
    gate = threading.Event()
    real = engine._handle
    monkeypatch.setattr(engine, "_handle", lambda job: (gate.wait(10), real(job))[1])
    try:
        p1, p2, p3 = (make_project(client, storage).json()["id"] for _ in range(3))
        runner.block = True
        assert analyze(client, p1).status_code == 200
        assert analyze(client, p1).status_code == 409  # same project, dispatcher still blocked
        assert used(usage) == 600  # charged once
        assert analyze(client, p2).status_code == 200
        assert analyze(client, p3).status_code == 409  # the 2-job cap
        assert used(usage) == 1200
    finally:
        gate.set()
        runner.release_all()
