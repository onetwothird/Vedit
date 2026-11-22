"""Studio background job queue: runs engine steps as child processes with progress and cancellation."""
from __future__ import annotations

import dataclasses
import json
import os
import queue
import subprocess
import threading
import uuid
from collections import deque
from dataclasses import dataclass
from typing import Callable, Literal, Optional

PROGRESS_PREFIX = "@@progress "
TAIL_LINES = 20

Step = tuple[str, list[str], float]  # (stage name, argv, share of the whole job 0-1)


@dataclass
class Job:
    id: str
    project_id: str
    kind: Literal["analyze", "export", "model"]
    state: Literal["queued", "running", "done", "failed", "cancelled"] = "queued"
    stage: str = ""
    pct: float = 0.0
    message: str = ""
    error: Optional[str] = None
    result: Optional[dict] = None


@dataclass
class _Entry:
    """Private per-job bookkeeping; deliberately not on Job so env secrets never leave this module."""
    job: Job
    steps: list[Step]
    env: dict[str, str]
    cancelled: bool = False
    proc: Optional[subprocess.Popen] = None


def _kill_tree(proc: subprocess.Popen) -> None:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            import signal
            os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        pass
    try:
        proc.kill()  # fallback for the direct child
    except OSError:
        pass


class JobQueue:
    def __init__(self, on_event: Callable[[Job], None], max_parallel: int = 1):
        self._on_event = on_event
        self._lock = threading.Lock()
        self._entries: dict[str, _Entry] = {}
        self._queue: queue.Queue[Optional[str]] = queue.Queue()
        self._closed = False
        self._threads = [threading.Thread(target=self._worker, name=f"vedit-job-{i}", daemon=True)
                         for i in range(max(1, max_parallel))]
        for t in self._threads:
            t.start()

    # ---- public API -------------------------------------------------------------------------------------------
    def submit(self, project_id: str, kind: str, steps: list[Step], env: dict[str, str] | None = None) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], project_id=project_id, kind=kind)  # type: ignore[arg-type]
        with self._lock:
            if self._closed:
                raise RuntimeError("job queue is shut down")
            self._entries[job.id] = _Entry(job, list(steps), dict(env or {}))
            snap = dataclasses.replace(job)
        self._emit(snap)
        self._queue.put(job.id)
        return snap

    def get(self, job_id: str) -> Job:
        with self._lock:
            return dataclasses.replace(self._entries[job_id].job)

    def cancel(self, job_id: str) -> None:
        snap = None
        with self._lock:
            entry = self._entries[job_id]  # KeyError for unknown ids
            job = entry.job
            if job.state == "queued":
                entry.cancelled = True
                job.state = "cancelled"
                snap = dataclasses.replace(job)
            elif job.state == "running":
                entry.cancelled = True
                if entry.proc is not None:
                    _kill_tree(entry.proc)
        if snap:
            self._emit(snap)

    def shutdown(self) -> None:
        snaps = []
        with self._lock:
            already = self._closed
            self._closed = True
            for entry in self._entries.values():
                job = entry.job
                if job.state == "queued":
                    entry.cancelled = True
                    job.state = "cancelled"
                    snaps.append(dataclasses.replace(job))
                elif job.state == "running":
                    entry.cancelled = True
                    if entry.proc is not None:
                        _kill_tree(entry.proc)
        for s in snaps:
            self._emit(s)
        if not already:
            for _ in self._threads:
                self._queue.put(None)
        for t in self._threads:
            if t is not threading.current_thread():
                t.join(timeout=10)

    # ---- internals --------------------------------------------------------------------------------------------
    def _emit(self, snap: Job) -> None:
        try:
            self._on_event(snap)
        except Exception:
            pass  # a bad callback must never kill the worker

    def _update(self, entry: _Entry, **fields) -> None:
        """Apply field changes under the lock (pct stays monotonic) and emit a snapshot."""
        with self._lock:
            job = entry.job
            if job.state != "running":
                return
            if "pct" in fields:
                fields["pct"] = max(job.pct, min(100.0, float(fields["pct"])))
            for k, v in fields.items():
                setattr(job, k, v)
            snap = dataclasses.replace(job)
        self._emit(snap)

    def _worker(self) -> None:
        while True:
            job_id = self._queue.get()
            if job_id is None:
                return
            entry = self._entries[job_id]
            with self._lock:
                if entry.job.state != "queued" or entry.cancelled:
                    continue
                entry.job.state = "running"
                snap = dataclasses.replace(entry.job)
            self._emit(snap)
            try:
                self._run_job(entry)
            except Exception as exc:  # never let one job take the worker down
                self._finish(entry, "failed", error=f"internal error: {exc}")

    def _finish(self, entry: _Entry, state: str, error: str | None = None) -> None:
        with self._lock:
            job = entry.job
            if entry.cancelled and state != "done":
                state, error = "cancelled", None
            job.state = state
            job.error = error
            if state == "done":
                job.pct = 100.0
            snap = dataclasses.replace(job)
        self._emit(snap)

    def _run_job(self, entry: _Entry) -> None:
        done_share = 0.0
        for stage, argv, share in entry.steps:
            if entry.cancelled:
                return self._finish(entry, "cancelled")
            self._update(entry, stage=stage)
            code, tail = self._run_step(entry, stage, argv, done_share, share)
            if entry.cancelled:
                return self._finish(entry, "cancelled")
            if code != 0:
                err = "\n".join(tail) or f"step '{stage}' exited with code {code}"
                return self._finish(entry, "failed", error=err)
            done_share += share
            self._update(entry, pct=done_share * 100)
        self._finish(entry, "done")

    def _run_step(self, entry: _Entry, stage: str, argv: list[str], done_share: float, share: float):
        env = {**os.environ, **entry.env, "VEDIT_PROGRESS": "1", "PYTHONIOENCODING": "utf-8"}
        kwargs: dict = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        else:
            kwargs["start_new_session"] = True
        tail: deque[str] = deque(maxlen=TAIL_LINES)
        with self._lock:
            if entry.cancelled:
                return -1, []
            try:
                proc = subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                                        **kwargs)
            except OSError as exc:
                return -1, [f"could not start step '{stage}': {exc}"]
            entry.proc = proc
        drain = threading.Thread(target=lambda: [None for _ in proc.stdout], daemon=True)
        drain.start()
        try:
            for raw in proc.stderr:
                line = raw.rstrip("\r\n")
                row = self._parse_progress(line)
                if row is None:
                    if line:
                        tail.append(line)
                    continue
                try:
                    pct = float(row.get("pct", 0))
                except (TypeError, ValueError):
                    pct = 0.0
                pct = max(0.0, min(100.0, pct))
                self._update(entry, pct=(done_share + share * pct / 100.0) * 100.0,
                             message=str(row.get("message", "")))
            code = proc.wait()
        finally:
            with self._lock:
                entry.proc = None
            drain.join(timeout=2)
            for pipe in (proc.stdout, proc.stderr):
                try:
                    pipe.close()
                except OSError:
                    pass
        return code, list(tail)

    @staticmethod
    def _parse_progress(line: str) -> dict | None:
        if not line.startswith(PROGRESS_PREFIX):
            return None
        try:
            row = json.loads(line[len(PROGRESS_PREFIX):])
        except ValueError:
            return None
        return row if isinstance(row, dict) else None
