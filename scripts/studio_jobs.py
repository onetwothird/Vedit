"""Studio background job queue: runs engine steps as child processes with progress and cancellation."""
from __future__ import annotations

import dataclasses
import json
import os
import queue
import subprocess
import sys
import threading
import time
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
    tree: Optional[_Tree] = None


def _close_quietly(pipe) -> None:
    try:
        pipe.close()
    except (OSError, ValueError):
        pass


def _taskkill_exe() -> str:
    root = os.environ.get("SystemRoot") or r"C:\Windows"
    path = os.path.join(root, "System32", "taskkill.exe")
    return path if os.path.isfile(path) else r"C:\Windows\System32\taskkill.exe"


class _WinJob:
    """Windows Job Object that kills every process in it (grandchildren included) when terminated or closed."""

    def __init__(self, pid: int):
        import ctypes
        from ctypes import wintypes
        self._k32 = k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]

        class BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class IOC(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOps", "WriteOps", "OtherOps", "ReadBytes",
                                                       "WriteBytes", "OtherBytes")]

        class EXT(ctypes.Structure):
            _fields_ = [("Basic", BASIC), ("Io", IOC), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        self._handle = k32.CreateJobObjectW(None, None)
        if not self._handle:
            raise OSError("CreateJobObjectW failed")
        try:
            info = EXT()
            info.Basic.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not k32.SetInformationJobObject(self._handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
                raise OSError("SetInformationJobObject failed")
            proc = k32.OpenProcess(0x0001 | 0x0100, False, pid)  # PROCESS_TERMINATE | PROCESS_SET_QUOTA
            if not proc:
                raise OSError("OpenProcess failed")
            try:
                if not k32.AssignProcessToJobObject(self._handle, proc):
                    raise OSError("AssignProcessToJobObject failed")
            finally:
                k32.CloseHandle(proc)
        except OSError:
            self.close()
            raise

    def terminate(self) -> None:
        if self._handle:
            self._k32.TerminateJobObject(self._handle, 1)

    def close(self) -> None:
        if self._handle:
            self._k32.CloseHandle(self._handle)
            self._handle = None


class _Tree:
    """Everything a step spawned: kill it all, now or after the direct child has exited."""

    def __init__(self, proc: subprocess.Popen):
        self.proc = proc
        self.job: Optional[_WinJob] = None
        if os.name == "nt":
            try:
                self.job = _WinJob(proc.pid)
            except Exception:
                self.job = None  # any Job Object failure: fall back to taskkill

    def kill(self) -> None:
        try:
            if os.name == "nt":
                if self.job is not None:
                    self.job.terminate()
                else:
                    subprocess.run([_taskkill_exe(), "/T", "/F", "/PID", str(self.proc.pid)], capture_output=True,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                import signal
                os.killpg(self.proc.pid, signal.SIGKILL)
        except OSError:
            pass
        try:
            self.proc.kill()
        except OSError:
            pass

    def close(self) -> None:
        if self.job is not None:
            self.job.close()
            self.job = None


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
                if entry.tree is not None:
                    entry.tree.kill()
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
                    if entry.tree is not None:
                        entry.tree.kill()
        for s in snaps:
            self._emit(s)
        if not already:
            for _ in self._threads:
                self._queue.put(None)
        for t in self._threads:
            if t is not threading.current_thread():
                t.join(timeout=10)
                if t.is_alive():
                    print(f"studio_jobs: worker {t.name} did not stop within 10 s of shutdown", file=sys.stderr)

    # ---- internals --------------------------------------------------------------------------------------------
    def _emit(self, snap: Job) -> None:
        """Call on_event, which may therefore be invoked from any thread (submit/cancel callers, workers, readers)."""
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
            tree = entry.tree = _Tree(proc)

        def drain_stdout():
            try:
                for _ in proc.stdout:
                    pass
            finally:
                _close_quietly(proc.stdout)

        def read_stderr():
            try:
                read_stderr_lines()
            finally:
                _close_quietly(proc.stderr)

        def read_stderr_lines():
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

        readers = [threading.Thread(target=drain_stdout, daemon=True),
                   threading.Thread(target=read_stderr, daemon=True)]
        for t in readers:
            t.start()
        try:
            code = proc.wait()
            tree.kill()  # reap leftover grandchildren (they hold the pipes open otherwise)
            deadline = time.monotonic() + 1.5
            for t in readers:
                t.join(timeout=max(0.0, deadline - time.monotonic()))
                if t.is_alive():
                    # Closing the pipe here would block on the reader's buffer lock; the daemon reader closes
                    # its own pipe when it reaches EOF.
                    print(f"studio_jobs: step '{stage}' pipe still held after exit; abandoned", file=sys.stderr)
        finally:
            if proc.poll() is None:
                tree.kill()
            with self._lock:
                entry.tree = None
                tree.close()
        for _ in range(3):
            try:
                return code, list(tail)
            except RuntimeError:  # deque mutated by an abandoned reader
                pass
        return code, []

    @staticmethod
    def _parse_progress(line: str) -> dict | None:
        if not line.startswith(PROGRESS_PREFIX):
            return None
        try:
            row = json.loads(line[len(PROGRESS_PREFIX):])
        except ValueError:
            return None
        return row if isinstance(row, dict) else None
