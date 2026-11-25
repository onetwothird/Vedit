"""Studio background job queue: runs engine steps as child processes with progress and cancellation."""
from __future__ import annotations

import dataclasses
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, Optional, Protocol, Union

from studio_projects import USER_HOME, _atomic_write_json, use_home

PROGRESS_PREFIX = "@@progress "
TAIL_LINES = 20
TERMINAL = ("done", "failed", "cancelled")
INTERRUPTED = "Interrupted — start it again"
_JOB_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")  # job ids name files: nothing that could leave the jobs folder

# (stage name, argv, share of the whole job 0-1[, env merged into this step only]). Instead of an argv, a step can be
# a zero-argument Python callable: it runs on the queue's worker thread (under the job's home), never on the runner,
# and fails the job by raising (cloud: publishing the rendered files to R2 is such a step)
Step = Union[tuple[str, Union[list[str], Callable[[], None]], float],
             tuple[str, Union[list[str], Callable[[], None]], float, dict[str, str]]]


@dataclass
class Job:
    id: str
    project_id: str
    kind: Literal["analyze", "export", "model", "story", "zip"]
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
    home: Optional[Path] = None  # the submitter's USER_HOME (cloud); worker threads don't inherit the context
    # cloud (persist=True) only: what <home>/jobs/<id>.json holds so a restarted API can answer for / finish the job
    meta: dict = dataclasses.field(default_factory=dict)  # e.g. charged_seconds (refund decisions survive restarts)
    plan: list = dataclasses.field(default_factory=list)  # [{stage, share, argv | None}]: None = cannot be resumed
    step_index: Optional[int] = None
    done_share: float = 0.0
    call_id: Optional[str] = None  # the running step's remote call (Modal FunctionCall object id)
    resume: Optional[dict] = None  # set on a recovered job whose running call is followed again
    view: Optional[dict] = None  # the server's final view of the job (result, post-processing outcome)
    finalized: bool = False
    detached: bool = False  # the queue shut down leaving this job to the next API container: never finished here
    seq: int = 0
    written: int = 0
    wlock: threading.Lock = dataclasses.field(default_factory=threading.Lock)


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


class StepRunner(Protocol):
    """Runs one step of a job somewhere (a local subprocess, a remote container). Returns (exit code, last stderr lines)."""

    def run(self, job_id: str, stage: str, argv: list[str], env: dict[str, str], on_line: Callable[[str], None],
            cancelled: Callable[[], bool]) -> tuple[int, list[str]]: ...

    def cancel(self, job_id: str) -> None: ...


def _parse_progress(line: str) -> dict | None:
    if not line.startswith(PROGRESS_PREFIX):
        return None
    try:
        row = json.loads(line[len(PROGRESS_PREFIX):])
    except ValueError:
        return None
    return row if isinstance(row, dict) else None


class LocalRunner:
    """Steps as child processes of this machine (the desktop app); kills the whole process tree on cancel."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._trees: dict[str, _Tree] = {}

    def cancel(self, job_id: str) -> None:
        with self._lock:
            tree = self._trees.get(job_id)
            if tree is not None:
                tree.kill()

    def run(self, job_id: str, stage: str, argv: list[str], env: dict[str, str], on_line: Callable[[str], None],
            cancelled: Callable[[], bool]) -> tuple[int, list[str]]:
        env = {**os.environ, **env, "VEDIT_PROGRESS": "1", "PYTHONIOENCODING": "utf-8"}
        kwargs: dict = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        else:
            kwargs["start_new_session"] = True
        tail: deque[str] = deque(maxlen=TAIL_LINES)
        with self._lock:
            if cancelled():
                return -1, []
            try:
                proc = subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                                        **kwargs)
            except OSError as exc:
                return -1, [f"could not start step '{stage}': {exc}"]
            tree = self._trees[job_id] = _Tree(proc)

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
                if _parse_progress(line) is None and line:
                    tail.append(line)
                on_line(line)

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
                self._trees.pop(job_id, None)
                tree.close()
        for _ in range(3):
            try:
                return code, list(tail)
            except RuntimeError:  # deque mutated by an abandoned reader
                pass
        return code, []


def _job_from(d: dict) -> Job:
    names = {f.name for f in dataclasses.fields(Job)}
    job = Job(**{k: v for k, v in d.items() if k in names})
    if not isinstance(job.id, str) or job.state not in ("queued", "running") + TERMINAL:
        raise ValueError("not a job")
    return job


class JobQueue:
    """`persist` (cloud): every job submitted under a home is also written to <home>/jobs/<id>.json on each change,
    so a fresh queue answers get() for it (only to its owner) and `recover` can finish or fail what was running."""

    def __init__(self, on_event: Callable[[Job], None], max_parallel: int = 1, runner: StepRunner | None = None,
                 persist: bool = False):
        self._on_event = on_event
        self._runner: StepRunner = runner if runner is not None else LocalRunner()
        self._persist = persist
        self._lock = threading.Lock()
        self._entries: dict[str, _Entry] = {}
        self._recovered: list[tuple[str, _Entry]] = []
        bind = getattr(self._runner, "bind", None)
        if callable(bind):
            bind(self._record_call)
        self._queue: queue.Queue[Optional[str]] = queue.Queue()
        self._closed = False
        self._threads = [threading.Thread(target=self._worker, name=f"vedit-job-{i}", daemon=True)
                         for i in range(max(1, max_parallel))]
        for t in self._threads:
            t.start()

    # ---- public API -------------------------------------------------------------------------------------------
    def submit(self, project_id: str, kind: str, steps: list[Step], env: dict[str, str] | None = None,
               job_id: str | None = None, meta: dict | None = None) -> Job:
        """`job_id` lets a caller that must charge for the job first know its id; it must never be reused.
        `meta` (JSON) is kept in the job's file, e.g. what was charged for it."""
        job = Job(id=job_id or uuid.uuid4().hex[:12], project_id=project_id, kind=kind)  # type: ignore[arg-type]
        steps = [tuple(st[:3]) + ((dict(st[3]),) if len(st) > 3 else ()) for st in steps]
        # a step a restarted API could run again: an argv with no env of its own (a step env carries keys, which
        # are never written down) and no in-process callable
        plan = [{"stage": st[0], "share": float(st[2]),
                 "argv": list(st[1]) if not callable(st[1]) and not env and not (len(st) > 3 and st[3]) else None}
                for st in steps]
        with self._lock:
            if self._closed:
                raise RuntimeError("job queue is shut down")
            if self._persist and job_id is not None and not _JOB_ID.match(job_id):
                raise ValueError(f"invalid job id: {job_id!r}")
            entry = self._entries[job.id] = _Entry(job, steps, dict(env or {}), home=USER_HOME.get(),
                                                   meta=dict(meta or {}), plan=plan)
            snap = dataclasses.replace(job)
            payload = self._payload(entry)
        self._write(entry, payload)
        self._emit(snap)
        self._queue.put(job.id)
        return snap

    def get(self, job_id: str) -> Job:
        with self._lock:
            entry = self._entries.get(job_id)
            if entry is not None:
                return dataclasses.replace(entry.job)
        entry = self._from_disk(job_id)
        with self._lock:
            return dataclasses.replace(entry.job)

    def owner(self, job_id: str) -> Optional[Path]:
        """The USER_HOME the job was submitted under (None on the desktop). KeyError for unknown ids."""
        with self._lock:
            entry = self._entries.get(job_id)
            if entry is not None:
                return entry.home
        return self._from_disk(job_id).home

    def finalize(self, job_id: str, view: dict) -> None:
        """The server has finished with this job (result set, refund done): kept in its file as what get() says."""
        with self._lock:
            entry = self._entries.get(job_id)
            if entry is None:
                return
            entry.view = view
            entry.finalized = True
            payload = self._payload(entry)
        self._write(entry, payload)

    def recover(self, users_root: Path) -> list[dict]:
        """After a restart: load every job file under <users_root>/<uid>/jobs that the server never finished.

        A running job whose remote call can be followed again (the runner can `reattach`) and whose later steps can
        all run again is resumed; any other unfinished job fails with INTERRUPTED. A job that ended but was not
        finalised is announced again. Returns [{id, meta}] so the caller can restore what it charged; nothing runs
        until start_recovered()."""
        if not self._persist:
            return []
        rows = []
        can_reattach = callable(getattr(self._runner, "reattach", None))
        for path in sorted(Path(users_root).glob("*/jobs/*.json")):
            job_id = path.stem
            if not _JOB_ID.match(job_id):
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                job = _job_from(data["job"])
                plan = [s for s in data.get("steps") or [] if isinstance(s, dict)]
                idx = data.get("step")
                meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
                done_share = float(data.get("done_share") or 0.0)
            except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
                print(f"studio_jobs: unreadable job file {path}: {e}", file=sys.stderr)
                continue
            if job.id != job_id or (job.state in TERMINAL and data.get("finalized")):
                continue
            entry = _Entry(job, [], {}, home=path.parent.parent, meta=meta, plan=plan,
                           step_index=idx if isinstance(idx, int) else None, done_share=done_share,
                           call_id=data.get("call_id") if isinstance(data.get("call_id"), str) else None)
            if job.state in TERMINAL:
                kind = "replay"
            else:
                rest = plan[idx + 1:] if isinstance(idx, int) and 0 <= idx < len(plan) else None
                if (job.state == "running" and can_reattach and entry.call_id and rest is not None
                        and all(isinstance(s.get("argv"), list) for s in rest)):
                    cur = plan[idx]
                    entry.resume = {"call_id": entry.call_id, "step": idx, "stage": str(cur.get("stage", "")),
                                    "share": float(cur.get("share") or 0.0), "done_share": done_share}
                    entry.steps = [(str(s.get("stage", "")), [str(a) for a in s["argv"]], float(s.get("share") or 0.0))
                                   for s in rest]
                    kind = "resume"
                else:
                    kind = "fail"
                job.state = "running"
            with self._lock:
                if job_id in self._entries:
                    continue
                self._entries[job_id] = entry
                self._recovered.append((kind, entry))
            rows.append({"id": job_id, "meta": dict(meta)})
        return rows

    def start_recovered(self) -> None:
        """Run what recover() loaded: follow the resumable jobs, fail the others, announce the ended ones."""
        with self._lock:
            recovered, self._recovered = self._recovered, []
        for kind, entry in recovered:
            if kind == "fail":
                self._finish(entry, "failed", error=INTERRUPTED)
                continue
            with self._lock:
                snap = dataclasses.replace(entry.job)
                payload = self._payload(entry) if kind == "resume" else None
            self._write(entry, payload)
            self._emit(snap)
            if kind == "resume":
                self._queue.put(entry.job.id)

    def cancel(self, job_id: str) -> None:
        snap = None
        kill = False
        with self._lock:
            entry = self._entries[job_id]  # KeyError for unknown ids
            job = entry.job
            if job.state == "queued":
                entry.cancelled = True
                job.state = "cancelled"
                snap = dataclasses.replace(job)
                payload = self._payload(entry)
            elif job.state == "running":
                entry.cancelled = True
                kill = True
        if kill:
            self._runner.cancel(job_id)
        if snap:
            self._write(entry, payload)
            self._release(job_id)
            self._emit(snap)

    def shutdown(self) -> None:
        """Cancel what is queued or running. A persisted queue whose runner can `detach` (Modal) instead leaves its
        jobs as they are on disk, their remote calls running, for the next API container to recover."""
        snaps = []
        running = []
        detach = self._persist and callable(getattr(self._runner, "detach", None))
        with self._lock:
            already = self._closed
            self._closed = True
            for entry in self._entries.values():
                job = entry.job
                if job.state not in ("queued", "running"):
                    continue
                if detach:
                    entry.detached = True
                elif job.state == "queued":
                    entry.cancelled = True
                    job.state = "cancelled"
                    snaps.append((entry, dataclasses.replace(job), self._payload(entry)))
                else:
                    entry.cancelled = True
                    running.append(job.id)
        if detach:
            self._runner.detach()  # type: ignore[attr-defined]
        for job_id in running:
            self._runner.cancel(job_id)
        for entry, s, payload in snaps:
            self._write(entry, payload)
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

    def _payload(self, entry: _Entry) -> Optional[tuple[int, dict]]:
        """What the job's file holds now (caller holds _lock); None when this job is not persisted."""
        if not self._persist or entry.home is None:
            return None
        entry.seq += 1
        return entry.seq, json.loads(json.dumps({
            "job": dataclasses.asdict(entry.job), "meta": entry.meta, "steps": entry.plan, "step": entry.step_index,
            "done_share": entry.done_share, "call_id": entry.call_id, "view": entry.view,
            "finalized": entry.finalized}, default=str))

    def _write(self, entry: _Entry, payload: Optional[tuple[int, dict]]) -> None:
        """Atomically write a payload unless a newer one is already on disk (writers run on several threads)."""
        if payload is None:
            return
        seq, data = payload
        with entry.wlock:
            if seq <= entry.written:
                return
            for attempt in range(5):
                try:
                    _atomic_write_json(Path(entry.home) / "jobs" / f"{entry.job.id}.json", data)
                    entry.written = seq
                    return
                except PermissionError as e:  # Windows: the file is open in a reader right now
                    if attempt == 4:
                        print(f"studio_jobs: could not write job {entry.job.id}: {e}", file=sys.stderr)
                    time.sleep(0.02)
                except (OSError, TypeError, ValueError) as e:
                    print(f"studio_jobs: could not write job {entry.job.id}: {e}", file=sys.stderr)
                    return

    def _from_disk(self, job_id: str) -> _Entry:
        """A job of the caller's home this process never ran (an earlier API container did). KeyError otherwise."""
        home = USER_HOME.get()
        if not self._persist or home is None or not isinstance(job_id, str) or not _JOB_ID.match(job_id):
            raise KeyError(job_id)
        try:
            data = json.loads((Path(home) / "jobs" / f"{job_id}.json").read_text(encoding="utf-8"))
            job = _job_from(data.get("view") or data["job"])
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            raise KeyError(job_id) from None
        if job.id != job_id:
            raise KeyError(job_id)
        entry = _Entry(job, [], {}, home=Path(home), view=data.get("view"), finalized=bool(data.get("finalized")))
        with self._lock:
            return self._entries.setdefault(job_id, entry)

    def _record_call(self, job_id: str, call_id: str) -> None:
        """The runner started the remote call of the job's current step: written down for a restarted API."""
        with self._lock:
            entry = self._entries.get(job_id)
            if entry is None:
                return
            entry.call_id = call_id
            payload = self._payload(entry)
        self._write(entry, payload)

    def _at_step(self, entry: _Entry, index: int, done_share: float, keep_call: bool = False) -> None:
        with self._lock:
            entry.step_index = index
            entry.done_share = done_share
            if not keep_call:
                entry.call_id = None
            payload = self._payload(entry)
        self._write(entry, payload)

    def _release(self, job_id: str) -> None:
        """The job ended: the runner drops anything it still holds for it (Modal: the job's secrets entry)."""
        release = getattr(self._runner, "release", None)
        if callable(release):
            try:
                release(job_id)
            except Exception as e:  # noqa: BLE001
                print(f"studio_jobs: runner release failed for job {job_id}: {e}", file=sys.stderr)

    def _update(self, entry: _Entry, **fields) -> None:
        """Apply field changes under the lock (pct stays monotonic) and emit a snapshot."""
        with self._lock:
            job = entry.job
            if job.state != "running" or entry.detached:
                return
            if "pct" in fields:
                fields["pct"] = max(job.pct, min(100.0, float(fields["pct"])))
            for k, v in fields.items():
                setattr(job, k, v)
            snap = dataclasses.replace(job)
            payload = self._payload(entry)
        self._write(entry, payload)
        self._emit(snap)

    def _worker(self) -> None:
        while True:
            job_id = self._queue.get()
            if job_id is None:
                return
            entry = self._entries[job_id]
            with self._lock:
                if entry.detached:
                    continue
                if entry.resume is None:
                    if entry.job.state != "queued" or entry.cancelled:
                        continue
                    entry.job.state = "running"
                elif entry.job.state != "running":
                    continue
                snap = dataclasses.replace(entry.job)
                payload = self._payload(entry)
            self._write(entry, payload)
            self._emit(snap)
            try:
                if entry.home is not None:
                    with use_home(entry.home):
                        self._run_job(entry)
                else:
                    self._run_job(entry)
            except Exception as exc:  # never let one job take the worker down
                self._finish(entry, "failed", error=f"internal error: {exc}")

    def _finish(self, entry: _Entry, state: str, error: str | None = None) -> None:
        with self._lock:
            job = entry.job
            if entry.detached:
                return
            if entry.cancelled and state != "done":
                state, error = "cancelled", None
            job.state = state
            job.error = error
            if state == "done":
                job.pct = 100.0
            snap = dataclasses.replace(job)
            payload = self._payload(entry)
        self._write(entry, payload)
        self._release(job.id)
        self._emit(snap)

    def _ended(self, entry: _Entry, stage: str, code: int, tail: list[str]) -> bool:
        """After a step: True when the job is over (detached, cancelled or failed; finished accordingly)."""
        if entry.detached:
            return True
        if entry.cancelled:
            self._finish(entry, "cancelled")
            return True
        if code != 0:
            self._finish(entry, "failed", error="\n".join(tail) or f"step '{stage}' exited with code {code}")
            return True
        return False

    def _run_job(self, entry: _Entry) -> None:
        done_share = 0.0
        first = 0
        resume = entry.resume
        if resume is not None:  # a recovered job: follow its running call again, then the steps after it
            done_share, share, stage = float(resume["done_share"]), float(resume["share"]), resume["stage"]
            self._at_step(entry, resume["step"], done_share, keep_call=True)
            self._update(entry, stage=stage)
            code, tail = self._run_step(entry, stage, [], done_share, share, reattach=resume["call_id"])
            if self._ended(entry, stage, code, tail):
                return
            done_share += share
            self._update(entry, pct=done_share * 100)
            first = resume["step"] + 1
        for i, step in enumerate(entry.steps):
            stage, argv, share = step[0], step[1], step[2]
            step_env = step[3] if len(step) > 3 else {}
            if entry.detached:
                return
            if entry.cancelled:
                return self._finish(entry, "cancelled")
            self._at_step(entry, first + i, done_share)
            self._update(entry, stage=stage)
            if callable(argv):
                try:
                    argv()
                except Exception as exc:  # noqa: BLE001 - any failure of an in-process step fails the job
                    if entry.cancelled:
                        return self._finish(entry, "cancelled")
                    return self._finish(entry, "failed", error=f"step '{stage}' failed: {exc}")
                code, tail = 0, []
            else:
                code, tail = self._run_step(entry, stage, argv, done_share, share, step_env)
            if self._ended(entry, stage, code, tail):
                return
            done_share += share
            self._update(entry, pct=done_share * 100)
        self._finish(entry, "done")

    @staticmethod
    def _parse_progress(line: str) -> dict | None:
        return _parse_progress(line)

    def _run_step(self, entry: _Entry, stage: str, argv: list[str], done_share: float, share: float,
                  step_env: dict[str, str] | None = None, reattach: str | None = None):
        def on_line(line: str) -> None:
            row = _parse_progress(line)
            if row is None:
                return
            try:
                pct = float(row.get("pct", 0))
            except (TypeError, ValueError):
                pct = 0.0
            pct = max(0.0, min(100.0, pct))
            self._update(entry, pct=(done_share + share * pct / 100.0) * 100.0,
                         message=str(row.get("message", "")))

        if reattach is not None:  # a call an earlier API container spawned (recover)
            return self._runner.reattach(entry.job.id, reattach, on_line,  # type: ignore[attr-defined]
                                         lambda: entry.cancelled)
        return self._runner.run(entry.job.id, stage, argv, {**entry.env, **(step_env or {})}, on_line,
                                lambda: entry.cancelled)
