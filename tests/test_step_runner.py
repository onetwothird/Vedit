"""Pluggable step runner: the queue talks to any StepRunner; cloud jobs carry their owner's home."""
import threading
import time

import pytest

from studio_jobs import JobQueue
from studio_projects import USER_HOME, use_home


def wait_for(pred, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


class FakeRunner:
    def __init__(self, code=0, tail=(), lines=(), block=False):
        self.code, self.tail, self.lines, self.block = code, list(tail), list(lines), block
        self.gates: dict[str, threading.Event] = {}
        self.cancelled, self.calls, self.homes = [], [], []

    def run(self, job_id, stage, argv, env, on_line, cancelled):
        self.calls.append((job_id, stage, argv, env))
        self.homes.append(USER_HOME.get())
        for line in self.lines:
            on_line(line)
        if self.block:
            self.gates.setdefault(job_id, threading.Event()).wait(5)
        return self.code, self.tail

    def cancel(self, job_id):
        self.cancelled.append(job_id)
        self.gates.setdefault(job_id, threading.Event()).set()


def progress(pct, msg=""):
    return '@@progress {"pct": %s, "message": "%s"}' % (pct, msg)


def test_queue_uses_the_given_runner():
    events = []
    runner = FakeRunner(lines=[progress(50, "half")])
    q = JobQueue(events.append, runner=runner)
    try:
        job = q.submit("p", "analyze", [("a", ["x"], 1.0)])
        assert wait_for(lambda: q.get(job.id).state == "done")
        done = q.get(job.id)
        assert done.pct == 100.0
        assert any(e.message == "half" and e.pct == 50.0 for e in events)
        assert runner.calls[0][1] == "a" and runner.calls[0][2] == ["x"]

        bad = FakeRunner(code=3, tail=["boom"])
        q2 = JobQueue(lambda j: None, runner=bad)
        try:
            j2 = q2.submit("p", "analyze", [("a", ["x"], 1.0)])
            assert wait_for(lambda: q2.get(j2.id).state == "failed")
            assert q2.get(j2.id).error == "boom"
        finally:
            q2.shutdown()
    finally:
        q.shutdown()


def test_python_step_runs_in_process_under_the_home_and_fails_the_job_by_raising(tmp_path):
    runner = FakeRunner()
    q = JobQueue(lambda j: None, runner=runner)
    seen = []
    try:
        with use_home(tmp_path / "u1"):
            ok = q.submit("p", "export", [("a", ["x"], 1.0), ("publish", lambda: seen.append(USER_HOME.get()), 0.0)])
        assert wait_for(lambda: q.get(ok.id).state == "done")
        assert seen == [tmp_path / "u1"] and [c[1] for c in runner.calls] == ["a"]  # never sent to the runner

        def boom():
            raise OSError("r2 down")

        bad = q.submit("p", "export", [("a", ["x"], 1.0), ("publish", boom, 0.0)])
        assert wait_for(lambda: q.get(bad.id).state == "failed")
        assert q.get(bad.id).error == "step 'publish' failed: r2 down"
    finally:
        q.shutdown()


def test_cancel_calls_runner_cancel():
    runner = FakeRunner(block=True)
    q = JobQueue(lambda j: None, runner=runner)
    try:
        job = q.submit("p", "analyze", [("a", ["x"], 1.0)])
        assert wait_for(lambda: q.get(job.id).state == "running" and runner.calls)
        q.cancel(job.id)
        assert runner.cancelled == [job.id]
        assert wait_for(lambda: q.get(job.id).state == "cancelled")
    finally:
        q.shutdown()


def test_job_runs_inside_the_submitters_home(tmp_path):
    runner = FakeRunner()
    q = JobQueue(lambda j: None, runner=runner)
    try:
        with use_home(tmp_path / "x"):
            job = q.submit("p", "analyze", [("a", ["x"], 1.0)])
        plain = q.submit("p", "analyze", [("a", ["x"], 1.0)])
        assert wait_for(lambda: q.get(job.id).state == "done" and q.get(plain.id).state == "done")
        assert runner.homes == [tmp_path / "x", None]
        assert "home" not in vars(q.get(job.id))
    finally:
        q.shutdown()


# ---- per-user jobs in cloud mode -------------------------------------------------------------------------------

jwt = pytest.importorskip("jwt", reason="needs the cloud extra")
pytest.importorskip("fastapi", reason="needs the studio extra")
from fastapi.testclient import TestClient  # noqa: E402

from cloud_config import CloudConfig  # noqa: E402
from studio_server import create_app  # noqa: E402

SECRET = "test-" + "jwt-" + "secret-" + "x" * 32
UID_A = "11111111-1111-4111-8111-111111111111"
UID_B = "22222222-2222-4222-8222-222222222222"


def tok(uid):
    return jwt.encode({"sub": uid, "aud": "authenticated", "exp": int(time.time()) + 3600}, SECRET,
                      algorithm="HS256")


def hdr(uid):
    return {"Authorization": f"Bearer {tok(uid)}"}


@pytest.fixture
def cloud(tmp_path):
    cfg = CloudConfig(data_root=tmp_path / "data", origins=("https://web.example.test",),
                      supabase_url="https://x.supabase.test", jwt_secret=SECRET)
    runner = FakeRunner(block=True)
    sink = []
    q = JobQueue(lambda j: sink[0](j), max_parallel=2, runner=runner)
    app = create_app("t", q, cloud=cfg)
    sink.append(app.state.engine.on_event)
    with TestClient(app) as c:
        yield c, app.state.engine, cfg, runner
    for g in list(runner.gates.values()):
        g.set()


def submit_as(engine, cfg, uid):
    with use_home(cfg.data_root / "users" / uid):
        return engine.submit("nope", "model", [("a", ["x"], 1.0)])


def test_other_users_jobs_are_hidden(cloud):
    c, engine, cfg, runner = cloud
    job = submit_as(engine, cfg, UID_A)
    assert wait_for(lambda: runner.calls)
    assert runner.homes == [cfg.data_root / "users" / UID_A]
    assert [j["id"] for j in c.get("/jobs", headers=hdr(UID_A)).json()] == [job.id]
    assert c.get("/jobs", headers=hdr(UID_B)).json() == []
    assert c.get(f"/jobs/{job.id}", headers=hdr(UID_A)).status_code == 200
    assert c.get(f"/jobs/{job.id}", headers=hdr(UID_B)).status_code == 404
    assert c.post(f"/jobs/{job.id}/cancel", headers=hdr(UID_B)).status_code == 404
    assert runner.cancelled == []
    assert c.post(f"/jobs/{job.id}/cancel", headers=hdr(UID_A)).status_code == 200
    assert runner.cancelled == [job.id]


def test_websocket_only_gets_own_jobs(cloud):
    c, engine, cfg, runner = cloud
    job_a = submit_as(engine, cfg, UID_A)
    assert wait_for(lambda: runner.calls)
    engine.wait_idle()
    with c.websocket_connect(f"/events?token={tok(UID_B)}") as wb, \
            c.websocket_connect(f"/events?token={tok(UID_A)}") as wa:
        first = wa.receive_json()
        assert first["job"]["id"] == job_a.id  # A's snapshot of its running job
        job_b = submit_as(engine, cfg, UID_B)
        msg = wb.receive_json()  # B's first message is B's own job, never A's snapshot or events
        assert msg["job"]["id"] == job_b.id
        engine.queue.cancel(job_a.id)
        engine.wait_idle()
        assert wa.receive_json()["job"]["id"] in (job_a.id,)
        engine.queue.cancel(job_b.id)
        engine.wait_idle()
        while True:
            m = wb.receive_json()
            assert m["job"]["id"] == job_b.id
            if m["job"]["state"] == "cancelled":
                break
