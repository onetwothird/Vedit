"""Studio job queue: progress aggregation, failure tails, cancellation, shutdown, ordering."""
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from studio_jobs import JobQueue

FAKE = str(Path(__file__).resolve().parent / "fixtures" / "fake_step.py")


def fake(stage, steps=2, sleep=0.05, code=0, msg="", *extra):
    return [sys.executable, FAKE, stage, str(steps), str(sleep), str(code), msg, *extra]


def wait_for(pred, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


def pid_alive(pid):
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True,
                             text=True).stdout
        return f'"{pid}"' in out
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


@pytest.fixture
def make_queue():
    made = []

    def _make(max_parallel=1):
        events = []
        lock = threading.Lock()

        def on_event(job):
            with lock:
                events.append(job)

        q = JobQueue(on_event, max_parallel=max_parallel)
        made.append(q)
        return q, events

    yield _make
    for q in made:
        q.shutdown()


def test_progress_aggregates_across_steps(make_queue):
    q, events = make_queue()
    job = q.submit("p1", "analyze", [("one", fake("one"), 0.25), ("two", fake("two", 4), 0.75)])
    assert wait_for(lambda: q.get(job.id).state == "done")
    pcts = [e.pct for e in events if e.id == job.id]
    assert pcts == sorted(pcts)
    assert pcts[-1] == 100.0 and q.get(job.id).pct == 100.0
    assert any(25 < p < 100 for p in pcts)
    assert {e.stage for e in events} >= {"one", "two"}
    assert any(e.message == "two 2/4" for e in events)


def test_failed_step_reports_stderr_tail(make_queue):
    q, _ = make_queue()
    job = q.submit("p1", "export", [("x", fake("x", 1, 0.01, 3, "boom"), 1.0), ("y", fake("y"), 0.0)])
    assert wait_for(lambda: q.get(job.id).state == "failed")
    j = q.get(job.id)
    assert "boom" in j.error and "@@progress" not in j.error
    assert j.stage == "x"


def test_cancel_kills_tree(make_queue, tmp_path):
    q, _ = make_queue()
    pidfile = tmp_path / "gc.pid"
    job = q.submit("p1", "export", [("x", fake("x", 1, 30, 0, "", "--grandchild", str(pidfile)), 1.0)])
    assert wait_for(lambda: pidfile.exists() and pidfile.read_text().strip() != "")
    gc = int(pidfile.read_text())
    assert pid_alive(gc)
    q.cancel(job.id)
    assert wait_for(lambda: not pid_alive(gc), 3.0)
    assert wait_for(lambda: q.get(job.id).state == "cancelled", 3.0)
    q.cancel(job.id)  # finished: no-op
    assert q.get(job.id).state == "cancelled"


def test_cancel_queued_and_unknown(make_queue):
    q, _ = make_queue()
    first = q.submit("p1", "export", [("a", fake("a", 1, 1.0), 1.0)])
    second = q.submit("p1", "export", [("b", fake("b"), 1.0)])
    q.cancel(second.id)
    assert q.get(second.id).state == "cancelled"
    q.cancel(first.id)
    with pytest.raises(KeyError):
        q.cancel("nope")
    with pytest.raises(KeyError):
        q.get("nope")
    time.sleep(0.2)
    assert q.get(second.id).state == "cancelled"


def test_shutdown_kills_running(make_queue, tmp_path):
    q, _ = make_queue()
    pidfile = tmp_path / "gc.pid"
    running = q.submit("p1", "export", [("x", fake("x", 1, 30, 0, "", "--grandchild", str(pidfile)), 1.0)])
    queued = q.submit("p1", "export", [("y", fake("y"), 1.0)])
    assert wait_for(lambda: pidfile.exists() and pidfile.read_text().strip() != "")
    gc = int(pidfile.read_text())
    q.shutdown()
    q.shutdown()  # safe twice
    assert wait_for(lambda: not pid_alive(gc), 3.0)
    assert q.get(running.id).state == "cancelled"
    assert q.get(queued.id).state == "cancelled"


def test_one_job_at_a_time(make_queue):
    q, _ = make_queue()
    first = q.submit("p1", "export", [("a", fake("a", 2, 0.3), 1.0)])
    second = q.submit("p1", "export", [("b", fake("b"), 1.0)])
    assert wait_for(lambda: q.get(first.id).state == "running")
    assert q.get(second.id).state == "queued"
    assert wait_for(lambda: q.get(first.id).state == "done")
    assert wait_for(lambda: q.get(second.id).state == "done")


def test_env_reaches_child_but_not_job_or_events(make_queue):
    q, events = make_queue()
    secret = "s3" + "cret-" + "value"
    job = q.submit("p1", "analyze", [("x", fake("x", 1, 0.01, 7, "", "--env", "VEDIT_TEST_ENV"), 1.0)],
                   env={"VEDIT_TEST_ENV": secret})
    assert wait_for(lambda: q.get(job.id).state == "failed")
    assert secret not in repr(q.get(job.id)) and all(secret not in repr(e) for e in events)


def test_bad_callback_does_not_kill_worker():
    def boom(_job):
        raise RuntimeError("bad callback")

    q = JobQueue(boom)
    try:
        job = q.submit("p1", "analyze", [("a", fake("a", 1, 0.01), 1.0)])
        assert wait_for(lambda: q.get(job.id).state == "done")
    finally:
        q.shutdown()


def test_missing_executable_fails_cleanly(make_queue):
    q, _ = make_queue()
    job = q.submit("p1", "model", [("a", ["definitely-not-a-real-binary-xyz"], 1.0)])
    assert wait_for(lambda: q.get(job.id).state == "failed")
    assert "could not start" in q.get(job.id).error


@pytest.mark.parametrize("code,state", [(0, "done"), (3, "failed")])
def test_step_exit_reaps_orphaned_grandchild(make_queue, tmp_path, code, state):
    q, _ = make_queue()
    pidfile = tmp_path / "gc.pid"
    job = q.submit("p1", "analyze", [("x", fake("x", 1, 0, code, "", "--grandchild", str(pidfile)), 1.0)])
    assert wait_for(lambda: q.get(job.id).state == state, 3.0)
    gc = int(pidfile.read_text())
    assert wait_for(lambda: not pid_alive(gc), 3.0)
    follow = q.submit("p1", "analyze", [("y", fake("y", 1, 0), 1.0)])
    assert wait_for(lambda: q.get(follow.id).state == "done", 3.0)  # queue not blocked


def test_held_pipe_does_not_hang_job(make_queue, tmp_path, monkeypatch):
    import studio_jobs
    monkeypatch.setattr(studio_jobs._Tree, "kill", lambda self: None)  # grandchild survives and holds the pipes
    q, _ = make_queue()
    pidfile = tmp_path / "gc.pid"
    job = q.submit("p1", "analyze", [("x", fake("x", 1, 0, 0, "", "--grandchild", str(pidfile)), 1.0)])
    try:
        assert wait_for(lambda: q.get(job.id).state == "done", 3.5)
    finally:
        monkeypatch.undo()
        if pidfile.exists():
            gc = int(pidfile.read_text())
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/PID", str(gc)], capture_output=True)
            else:
                os.kill(gc, 9)


def test_job_object_failure_falls_back_to_taskkill(make_queue, tmp_path, monkeypatch):
    import studio_jobs

    def broken(pid):
        raise RuntimeError("ctypes exploded")

    monkeypatch.setattr(studio_jobs, "_WinJob", broken)
    q, _ = make_queue()
    pidfile = tmp_path / "gc.pid"
    job = q.submit("p1", "export", [("x", fake("x", 1, 30, 0, "", "--grandchild", str(pidfile)), 1.0)])
    assert wait_for(lambda: pidfile.exists() and pidfile.read_text().strip() != "")
    gc = int(pidfile.read_text())
    q.cancel(job.id)
    assert wait_for(lambda: not pid_alive(gc), 3.0)
    assert wait_for(lambda: q.get(job.id).state == "cancelled", 3.0)
