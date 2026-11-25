"""Modal step runner (faked spawn / queue / dict / volume), job files that survive an API restart, recovery."""
import itertools
import json
import queue
import sys
import threading
import time
from pathlib import Path

import pytest

import cloud_runner
from cloud_runner import INTERRUPTED, ModalRunner, execute_step
from studio_jobs import JobQueue
from studio_projects import use_home

AI_KEY = "sk-" + "fake" + "-" + "ai42" * 4  # never a real key; built from pieces
TTS_KEY = "el-" + "fake" + "-" + "tts9" * 4
OPENAI_KEY = "sk-" + "fake" + "-" + "oai3" * 4
SECRETS = {"VEDIT_AI_KEY": AI_KEY, "OPENAI_API_KEY": OPENAI_KEY, "ELEVENLABS_API_KEY": TTS_KEY}


def wait_for(pred, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


def read_job(path):
    """The job file, or {} while it is missing or being replaced (Windows refuses a read during the replace)."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def progress(pct, msg=""):
    return '@@progress {"pct": %s, "message": "%s"}' % (pct, msg)


# ---- fakes for modal.Function / FunctionCall / Queue / Dict -----------------------------------------------------

class Remote:
    """The state of one spawned call on 'Modal': runs execute_step on a thread, like a step container would."""

    def __init__(self, cloud, fn_name, kwargs):
        self.cloud, self.fn_name, self.kwargs = cloud, fn_name, kwargs
        self.done = threading.Event()
        self.cancelled = False
        self.result = None
        self.error = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        try:
            self.result = execute_step(**self.kwargs, lines=self.cloud.lines_for(self.kwargs["job_id"]),
                                       secrets=self.cloud.secrets, commit=self.cloud.step_commit,
                                       reload=self.cloud.step_reload, run=self.cloud.step_run)
        except BaseException as e:  # noqa: BLE001
            self.error = e
        finally:
            self.done.set()


class Handle:
    """A FunctionCall handle; a `dead` handle never answers again (its API container is gone)."""

    def __init__(self, remote, object_id):
        self.remote, self.object_id = remote, object_id
        self.dead = False

    def get(self, timeout=None):
        if self.dead:
            threading.Event().wait()  # blocks forever, like a process that has stopped
        if self.remote.cancelled:
            raise RuntimeError("input cancelled")
        if not self.remote.done.wait(timeout or 0):
            raise TimeoutError("not yet")
        if self.remote.error is not None:
            raise self.remote.error
        return self.remote.result

    def cancel(self):
        self.remote.cancelled = True
        self.remote.cloud.step_gates.setdefault(self.remote.kwargs["job_id"], threading.Event()).set()


class Function:
    def __init__(self, cloud, name):
        self.cloud, self.name = cloud, name
        self.fail = False

    def spawn(self, **kwargs):
        self.cloud.spawns.append((self.name, kwargs))
        if self.fail:
            raise ConnectionError("modal down")
        remote = Remote(self.cloud, self.name, json.loads(json.dumps(kwargs)))  # inputs are serialised
        object_id = f"fc-{next(self.cloud.ids)}"
        self.cloud.remotes[object_id] = remote
        remote.thread.start()
        handle = Handle(remote, object_id)
        self.cloud.handles.append(handle)
        return handle


class Lines:
    """One partition of the line queue (modal.Queue): what the step puts, the runner gets."""

    def __init__(self):
        self.q = queue.Queue()

    def put_many(self, values):
        for v in values:
            self.q.put(v)

    def get_many(self, n, timeout=None):
        out = []
        try:
            out.append(self.q.get(timeout=timeout))
            while len(out) < n:
                out.append(self.q.get_nowait())
        except queue.Empty:
            pass
        return out

    def clear(self):
        while True:
            try:
                self.q.get_nowait()
            except queue.Empty:
                return


class FakeModal:
    def __init__(self):
        self.ids = itertools.count(1)
        self.spawns, self.remotes, self.handles = [], {}, []
        self.partitions: dict[str, Lines] = {}
        self.secrets: dict = {}
        self.events: list[str] = []  # order of commit / spawn / reload / step-side commit
        self.step_gates: dict[str, threading.Event] = {}
        self.block_stage = None
        self.exit_code = 0
        self.seen_env: list[dict] = []
        self.gpu, self.cpu = Function(self, "gpu"), Function(self, "cpu")

    def lines_for(self, job_id):
        return self.partitions.setdefault(job_id, Lines())

    def step_commit(self):
        self.events.append("step-commit")

    def step_reload(self):
        self.events.append("step-reload")

    def step_run(self, job_id, stage, argv, env, on_line, cancelled):
        """Stands in for the child process: progress lines, a tail, a blocking stage."""
        self.seen_env.append(dict(env))
        on_line(progress(50, f"{stage} half"))
        if stage == self.block_stage:
            self.step_gates.setdefault(job_id, threading.Event()).wait(10)
        if stage == "highlights" and len(argv) > 2:
            src = Path(argv[2])
            (src.parent / (src.stem + ".edit") / "highlights.json").write_text(
                json.dumps({"highlights": [], "reason": "none"}), encoding="utf-8")
        if self.exit_code:
            on_line("Traceback: boom")
            return self.exit_code, ["Traceback: boom"]
        return 0, []

    def from_id(self, object_id):
        return Handle(self.remotes[object_id], object_id)

    def runner(self, **kw):
        return ModalRunner(self.gpu, self.cpu, self.lines_for, self.secrets, reload=lambda: self.events.append("reload"),
                           commit=lambda: self.events.append("commit"), from_id=self.from_id, poll=0.02, **kw)


@pytest.fixture
def modal():
    m = FakeModal()
    yield m
    for ev in m.step_gates.values():
        ev.set()


def run_job(q, steps, env=None, home=None, timeout=5.0):
    if home is not None:
        with use_home(home):
            job = q.submit("p1", "analyze", steps, env=env)
    else:
        job = q.submit("p1", "analyze", steps, env=env)
    assert wait_for(lambda: q.get(job.id).state in ("done", "failed", "cancelled"), timeout)
    return q.get(job.id)


# ---- the runner -------------------------------------------------------------------------------------------------

def test_secrets_never_reach_spawn_arguments_and_the_step_still_gets_them(modal, tmp_path):
    q = JobQueue(lambda j: None, runner=modal.runner())
    try:
        job = run_job(q, [("highlights", ["x.py"], 1.0, dict(SECRETS))], env={"VEDIT_LANG": "en"},
                      home=tmp_path / "u1")
        assert job.state == "done", job.error
        ((name, kwargs),) = modal.spawns
        assert name == "cpu"
        assert set(kwargs) == {"job_id", "stage", "argv", "env", "home"}
        dumped = repr(kwargs)
        for key in SECRETS.values():
            assert key not in dumped
        assert kwargs["env"] == {"VEDIT_LANG": "en", "VEDIT_SECRET_REF": job.id}
        assert kwargs["home"] == str(tmp_path / "u1")
        seen = modal.seen_env[0]
        assert {k: seen[k] for k in SECRETS} == SECRETS  # the step read them from the dict
        assert "VEDIT_SECRET_REF" not in seen and seen["VEDIT_HOME"] == str(tmp_path / "u1")
        assert modal.secrets == {}
    finally:
        q.shutdown()


def test_any_key_like_env_name_is_moved_too():
    clean, secret = cloud_runner.split_secrets({"ANTHROPIC_API_KEY": "a", "VEDIT_LANG": "en", "SOME_TOKEN": "t",
                                                "VEDIT_AI_KEY": "k"})
    assert clean == {"VEDIT_LANG": "en"}
    assert secret == {"ANTHROPIC_API_KEY": "a", "SOME_TOKEN": "t", "VEDIT_AI_KEY": "k"}


def test_analyze_runs_on_the_gpu_and_everything_else_on_the_cpu(modal):
    q = JobQueue(lambda j: None, runner=modal.runner())
    try:
        job = run_job(q, [("proxy", ["p"], 0.2), ("analyze", ["a"], 0.6), ("framing", ["f"], 0.2)])
        assert job.state == "done"
        assert [(n, k["stage"]) for n, k in modal.spawns] == [("cpu", "proxy"), ("gpu", "analyze"), ("cpu", "framing")]
    finally:
        q.shutdown()


def test_progress_lines_reach_the_queue_and_a_failure_returns_the_tail(modal):
    events = []
    q = JobQueue(events.append, runner=modal.runner())
    try:
        job = run_job(q, [("analyze", ["a"], 1.0)])
        assert job.state == "done"
        assert any(e.message == "analyze half" and e.pct == 50.0 for e in events)
        modal.exit_code = 3
        modal.secrets.clear()
        bad = run_job(q, [("render 1/1", ["r"], 1.0, {"OPENAI_API_KEY": OPENAI_KEY})])
        assert bad.state == "failed" and bad.error == "Traceback: boom"
        assert modal.secrets == {}  # deleted when the job failed
    finally:
        q.shutdown()


def test_volume_commit_before_spawn_and_reload_after_every_step(modal):
    events = []
    q = JobQueue(lambda j: events.append(("job", j.state)), runner=modal.runner())
    modal.events = events
    try:
        job = run_job(q, [("proxy", ["p"], 0.5), ("analyze", ["a"], 0.5)])
        assert job.state == "done"
        names = [e for e in events if isinstance(e, str)]
        assert names == ["commit", "step-reload", "step-commit", "reload"] * 2
        assert events.index("reload") < events.index(("job", "done"))  # outputs read after the reload
    finally:
        q.shutdown()


def test_step_commits_and_returns_the_tail_when_the_child_fails(modal):
    lines = Lines()
    out = execute_step("j1", "render", ["r"], {}, None, lines=lines, secrets={}, commit=modal.step_commit,
                       run=lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    assert out["code"] != 0 and "disk full" in out["tail"][-1]
    assert modal.events == ["step-commit"]


def test_step_pops_its_secret_entry_at_start(modal):
    secrets = {"j1": {"VEDIT_AI_KEY": AI_KEY}}
    seen = {}

    def run(job_id, stage, argv, env, on_line, cancelled):
        seen["dict_empty"] = "j1" not in secrets
        seen["key"] = env.get("VEDIT_AI_KEY") == AI_KEY
        return 0, []

    execute_step("j1", "highlights", ["h"], {"VEDIT_SECRET_REF": "j1"}, None, lines=Lines(), secrets=secrets,
                 commit=None, run=run)
    assert seen == {"dict_empty": True, "key": True}


def test_real_child_process_lines_reach_the_queue():
    lines = Lines()
    code = "import sys; sys.stderr.write('@@progress {\"pct\": 40}\\n'); sys.stderr.write('oops\\n'); sys.exit(2)"
    out = execute_step("j1", "proxy", [sys.executable, "-c", code], {}, None, lines=lines, secrets={}, commit=None)
    assert out == {"code": 2, "tail": ["oops"]}
    got = lines.get_many(10, timeout=0.1)
    assert '@@progress {"pct": 40}' in got and "oops" in got


def test_secret_entry_deleted_on_cancel_and_on_spawn_failure(modal):
    modal.block_stage = "highlights"
    q = JobQueue(lambda j: None, runner=modal.runner())
    try:
        job = q.submit("p1", "analyze", [("highlights", ["h"], 1.0, {"VEDIT_AI_KEY": AI_KEY})])
        assert wait_for(lambda: modal.spawns and modal.step_gates.get(job.id))
        q.cancel(job.id)
        assert wait_for(lambda: q.get(job.id).state == "cancelled")
        assert modal.secrets == {}

        modal.cpu.fail = True
        failed = run_job(q, [("highlights", ["h"], 1.0, {"VEDIT_AI_KEY": AI_KEY})])
        assert failed.state == "failed" and "modal down" in failed.error
        assert modal.secrets == {}
    finally:
        q.shutdown()


def test_cancel_before_spawn_never_spawns(modal):
    runner = modal.runner()
    code, _tail = runner.run("j9", "proxy", ["p"], {"VEDIT_AI_KEY": AI_KEY}, lambda line: None, lambda: True)
    assert code != 0 and modal.spawns == [] and modal.secrets == {}


def test_modal_is_never_imported():
    assert "modal" not in sys.modules


# ---- job files and restarts -------------------------------------------------------------------------------------

def test_job_file_lets_a_fresh_queue_answer_only_its_owner(modal, tmp_path):
    home_a, home_b = tmp_path / "users" / "a", tmp_path / "users" / "b"
    q = JobQueue(lambda j: None, runner=modal.runner(), persist=True)
    try:
        job = run_job(q, [("highlights", ["h"], 1.0, {"VEDIT_AI_KEY": AI_KEY})], home=home_a)
    finally:
        q.shutdown()
    assert job.state == "done"
    path = home_a / "jobs" / f"{job.id}.json"
    assert json.loads(path.read_text(encoding="utf-8"))["job"]["state"] == "done"
    other = JobQueue(lambda j: None, runner=modal.runner(), persist=True)
    try:
        with use_home(home_b), pytest.raises(KeyError):  # only the caller's own jobs folder is read
            other.get(job.id)
        with pytest.raises(KeyError):  # no home: nothing on disk is read
            other.get(job.id)
        with use_home(home_a), pytest.raises(KeyError):
            other.get("../b/jobs/x")
    finally:
        other.shutdown()
    fresh = JobQueue(lambda j: None, runner=modal.runner(), persist=True)
    try:
        with use_home(home_a):
            assert fresh.get(job.id).state == "done"
            assert fresh.owner(job.id) == home_a  # what the server compares with the caller's home
    finally:
        fresh.shutdown()
    for f in tmp_path.rglob("*"):
        if f.is_file():
            assert AI_KEY not in f.read_text(encoding="utf-8", errors="replace")


def test_desktop_queue_writes_no_job_files(tmp_path):
    from test_step_runner import FakeRunner

    q = JobQueue(lambda j: None, runner=FakeRunner())
    try:
        run_job(q, [("a", ["x"], 1.0)], home=tmp_path / "u")
    finally:
        q.shutdown()
    assert not (tmp_path / "u" / "jobs").exists()


def test_restart_reattaches_the_running_call_and_finishes_the_remaining_steps(modal, tmp_path):
    home = tmp_path / "users" / "a"
    modal.block_stage = "analyze"
    q1 = JobQueue(lambda j: None, runner=modal.runner(), persist=True)
    with use_home(home):
        job = q1.submit("p1", "analyze", [("proxy", ["p"], 0.2), ("analyze", ["a"], 0.6), ("framing", ["f"], 0.2)],
                        job_id="j0000001")
    path = home / "jobs" / "j0000001.json"
    assert wait_for(lambda: read_job(path).get("call_id") and read_job(path)["job"]["stage"] == "analyze")
    for h in modal.handles:
        h.dead = True  # the API container is gone; its worker never hears back
    events = []
    q2 = JobQueue(events.append, runner=modal.runner(), persist=True)
    try:
        rows = q2.recover(tmp_path / "users")
        assert [r["id"] for r in rows] == [job.id]
        q2.start_recovered()
        assert wait_for(lambda: any(e.state == "running" for e in events))
        modal.step_gates[job.id].set()
        assert wait_for(lambda: events and events[-1].state == "done")
        assert [k["stage"] for _n, k in modal.spawns] == ["proxy", "analyze", "framing"]  # analyze not spawned again
        assert wait_for(lambda: read_job(path).get("job", {}).get("state") == "done")
        with use_home(home):
            assert q2.owner(job.id) == home
    finally:
        q2.shutdown()


def test_restart_fails_a_job_it_cannot_reattach(tmp_path):
    from test_step_runner import FakeRunner

    home = tmp_path / "users" / "a"
    (home / "jobs").mkdir(parents=True)
    rows = {"j1": {"state": "running", "call_id": None}, "j2": {"state": "queued", "call_id": None},
            "j3": {"state": "running", "call_id": "fc-1"}}
    for jid, r in rows.items():
        (home / "jobs" / f"{jid}.json").write_text(json.dumps({
            "job": {"id": jid, "project_id": "p", "kind": "analyze", "state": r["state"], "stage": "a", "pct": 10.0,
                    "message": "", "error": None, "result": None},
            "home": str(home), "meta": {"charged_seconds": 60.0}, "call_id": r["call_id"], "step": 0,
            "done_share": 0.0, "steps": [{"stage": "a", "share": 1.0, "argv": ["x"]}], "view": None,
            "finalized": False}), encoding="utf-8")
    events = []
    q = JobQueue(events.append, runner=FakeRunner(), persist=True)  # a runner that cannot reattach
    try:
        rows_back = q.recover(tmp_path / "users")
        assert sorted(r["id"] for r in rows_back) == ["j1", "j2", "j3"]
        assert all(r["meta"] == {"charged_seconds": 60.0} for r in rows_back)
        q.start_recovered()
        assert wait_for(lambda: len([e for e in events if e.state == "failed"]) == 3)
        assert {e.error for e in events if e.state == "failed"} == {INTERRUPTED}
        assert json.loads((home / "jobs" / "j1.json").read_text())["job"]["state"] == "failed"
    finally:
        q.shutdown()


def test_shutdown_with_a_modal_runner_leaves_running_calls_for_the_next_container(modal, tmp_path):
    home = tmp_path / "users" / "a"
    modal.block_stage = "analyze"
    events = []
    q = JobQueue(events.append, runner=modal.runner(), persist=True)
    with use_home(home):
        job = q.submit("p1", "analyze", [("analyze", ["a"], 1.0, {"VEDIT_AI_KEY": AI_KEY})])
    assert wait_for(lambda: job.id in modal.step_gates)
    q.shutdown()
    assert not any(r.cancelled for r in modal.remotes.values())
    assert json.loads((home / "jobs" / f"{job.id}.json").read_text())["job"]["state"] == "running"
    assert not any(e.state == "cancelled" for e in events)


# ---- the server: restart mid-job, refunds, batches, /doctor -----------------------------------------------------

fastapi = pytest.importorskip("fastapi", reason="needs the studio extra")
pytest.importorskip("jwt", reason="needs the cloud extra")
from fastapi.testclient import TestClient  # noqa: E402

import cloud_storage as cs  # noqa: E402
import studio_server  # noqa: E402
from cloud_config import CloudConfig  # noqa: E402
from cloud_usage import MemoryUsage  # noqa: E402
from studio_server import create_app  # noqa: E402
from test_cloud_auth import SECRET, UID_A, UID_B, auth, make_token  # noqa: E402
from test_cloud_projects import fake_probe, make_project, upload, used, wait_state  # noqa: E402

A = auth(make_token(UID_A))
B = auth(make_token(UID_B))


@pytest.fixture
def _probe(monkeypatch):
    monkeypatch.setattr(studio_server, "cached_probe", fake_probe)


def cloud_cfg(tmp_path, storage, usage, runner_factory):
    return CloudConfig(data_root=tmp_path / "data", origins=("https://web.example.test",),
                       supabase_url="https://x.supabase.test", jwt_secret=SECRET, storage=storage, usage=usage,
                       runner_factory=runner_factory)


def test_api_restart_mid_job_still_gives_the_outcome(modal, tmp_path, _probe):
    storage, usage = cs.MemoryStorage(), MemoryUsage(default_limit=3600)
    modal.block_stage = "analyze"
    cfg = cloud_cfg(tmp_path, storage, usage, modal.runner)
    first = TestClient(create_app(None, cloud=cfg))  # no `with`: this container is never shut down cleanly
    pid = make_project(first, storage, b"video:600").json()["id"]
    r = first.post(f"/projects/{pid}/analyze", headers=A, json={})
    assert r.status_code == 200, r.text
    job_id = r.json()["id"]
    path = cfg.data_root / "users" / UID_A / "jobs" / f"{job_id}.json"
    assert wait_for(lambda: read_job(path).get("call_id") and read_job(path)["job"]["stage"] == "analyze")
    for h in modal.handles:
        h.dead = True
    time.sleep(0.1)  # its last poll drains; then the dead container's dispatcher has written all it ever will
    first.app.state.engine.wait_idle()
    with TestClient(create_app(None, cloud=cfg)) as second:
        assert second.get(f"/jobs/{job_id}", headers=A).json()["state"] == "running"
        assert second.get(f"/jobs/{job_id}", headers=B).status_code == 404
        modal.step_gates[job_id].set()
        assert wait_state(second, job_id)["state"] == "done"
        second.app.state.engine.wait_idle()
        assert second.get(f"/projects/{pid}", headers=A).json()["last_job"]["state"] == "done"
        assert used(usage) == 600
    with TestClient(create_app(None, cloud=cfg)) as third:  # and after yet another restart, from the job file
        j = third.get(f"/jobs/{job_id}", headers=A).json()
        assert j["state"] == "done" and j["result"] == {"clips": 0, "new": 0, "reason": "none"}
        assert third.get(f"/jobs/{job_id}", headers=B).status_code == 404


class StuckRunner:
    """A local runner whose API container died mid-step: the step never returns and nothing can reattach it."""

    def __init__(self):
        self.calls = []

    def run(self, job_id, stage, argv, env, on_line, cancelled):
        self.calls.append((job_id, stage))
        threading.Event().wait()

    def cancel(self, job_id):
        pass


def test_restart_that_cannot_reattach_fails_and_refunds_the_charge(tmp_path, _probe):
    storage, usage = cs.MemoryStorage(), MemoryUsage(default_limit=3600)
    runner = StuckRunner()
    cfg = cloud_cfg(tmp_path, storage, usage, lambda: runner)
    first = TestClient(create_app(None, cloud=cfg))
    pid = make_project(first, storage, b"video:600").json()["id"]
    job_id = first.post(f"/projects/{pid}/analyze", headers=A, json={}).json()["id"]
    assert wait_for(lambda: runner.calls)
    first.app.state.engine.wait_idle()
    assert used(usage) == 600
    with TestClient(create_app(None, cloud=cfg)) as second:
        j = wait_state(second, job_id)
        assert j["state"] == "failed" and j["error"] == INTERRUPTED
        assert used(usage) == 0  # refunded: the charge was kept in the job file
        second.app.state.engine.wait_idle()
        assert second.get(f"/projects/{pid}", headers=A).json()["last_job"]["state"] == "failed"


def test_a_running_batch_is_ended_on_restart_and_no_file_holds_the_ai_key(tmp_path, _probe):
    storage, usage = cs.MemoryStorage(), MemoryUsage(default_limit=3600)
    runner = StuckRunner()
    cfg = cloud_cfg(tmp_path, storage, usage, lambda: runner)
    first = TestClient(create_app(None, cloud=cfg))
    keys = [upload(first, storage, b"video:300") for _ in range(3)]
    r = first.post("/batches", headers=A, json={"upload_keys": keys, "language": "en", "platform": "tiktok",
                                                "export": False, "ai": {"provider": "openai", "api_key": AI_KEY}})
    assert r.status_code == 200, r.text
    bid = r.json()["id"]
    assert wait_for(lambda: runner.calls)
    first.app.state.engine.wait_idle()
    assert used(usage) == 300  # only the first item started
    with TestClient(create_app(None, cloud=cfg)) as second:
        second.app.state.engine.wait_idle()
        view = second.get(f"/batches/{bid}", headers=A).json()
        assert view["running"] is False
        assert [it["error"] for it in view["items"][1:]] == ["Interrupted — start them again"] * 2
        assert used(usage) == 0  # the started item failed (refunded); the others were never charged
        assert second.get("/jobs", headers=A).json() == []
    for f in tmp_path.rglob("*"):
        if f.is_file():
            assert AI_KEY not in f.read_text(encoding="utf-8", errors="replace"), f


def test_doctor_reports_cloud_readiness(modal, tmp_path):
    storage, usage = cs.MemoryStorage(), MemoryUsage(default_limit=3600)
    cfg = cloud_cfg(tmp_path, storage, usage, modal.runner)
    with TestClient(create_app(None, cloud=cfg)) as c:
        assert c.get("/doctor", headers=A).json() == {"cloud": True, "gpu_function": True, "storage": True,
                                                      "usage": True}

        def boom(*a, **k):
            raise ConnectionError("down")

        storage.size = boom
        usage.remaining = boom
        modal.gpu.get_current_stats = boom
        assert c.get("/doctor", headers=A).json() == {"cloud": True, "gpu_function": False, "storage": False,
                                                      "usage": False}
        assert c.get("/doctor").status_code == 401
