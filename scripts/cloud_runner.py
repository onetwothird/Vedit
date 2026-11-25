"""Cloud steps on Modal: the API container spawns each step as a Modal function call and follows it.

Both sides live here so they can be tested without Modal: `ModalRunner` (the API side, a studio_jobs.StepRunner) and
`execute_step` (what step_gpu / step_cpu run in their container). `modal` itself is never imported here; the Modal
objects (functions, the line queue, the secrets dict, the volume's commit/reload) are passed in by cloud/modal_app.py.

The visitor's AI / TTS keys never become call arguments (Modal keeps call inputs): they go into `secret_dict[job_id]`,
the spawned env carries only `VEDIT_SECRET_REF=<job_id>`, the step pops the entry when it starts, and the runner
deletes it again when the step returns and when the job ends.
"""
from __future__ import annotations

import queue as _queue
import sys
import threading
import time
from collections import deque
from typing import Any, Callable, Optional

from studio_jobs import INTERRUPTED, TAIL_LINES, LocalRunner
from studio_projects import USER_HOME

SECRET_ENV = ("VEDIT_AI_KEY", "OPENAI_API_KEY", "ELEVENLABS_API_KEY")
SECRET_SUFFIXES = ("_API_KEY", "_KEY", "_TOKEN", "_SECRET")
SECRET_REF = "VEDIT_SECRET_REF"
GPU_STAGES = frozenset({"analyze"})  # transcription; everything else (proxy, highlights, framing, renders) is CPU
FLUSH_SECONDS = 0.25

__all__ = ["INTERRUPTED", "ModalRunner", "ModalLines", "execute_step", "split_secrets"]


def is_secret(name: str) -> bool:
    return name in SECRET_ENV or name.upper().endswith(SECRET_SUFFIXES)


def split_secrets(env: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    """(env without anything key-like, the key-like entries)."""
    clean = {k: v for k, v in env.items() if not is_secret(k)}
    return clean, {k: v for k, v in env.items() if is_secret(k)}


def _pop(mapping, key):
    """dict.pop(key, None) for a modal.Dict too (whose pop raises KeyError for a missing key)."""
    try:
        return mapping.pop(key)
    except KeyError:
        return None


def _quietly(fn: Optional[Callable[[], Any]], what: str) -> None:
    if fn is None:
        return
    try:
        fn()
    except Exception as e:  # noqa: BLE001 - a volume hiccup must never fail or hang a job by itself
        print(f"vedit: volume {what} failed: {type(e).__name__}: {e}", file=sys.stderr)


def _is_poll_timeout(exc: BaseException) -> bool:
    """FunctionCall.get(timeout=0) on a call still running (builtin or modal.exception.TimeoutError)."""
    return isinstance(exc, TimeoutError) or type(exc).__name__ == "TimeoutError"


class ModalLines:
    """One job's partition of the shared `modal.Queue` of step output lines."""

    def __init__(self, q, job_id: str):
        self._q, self._key = q, job_id

    def put_many(self, values: list[str]) -> None:
        self._q.put_many(values, partition=self._key)

    def get_many(self, n: int, timeout: float | None = None) -> list[str]:
        try:
            got = self._q.get_many(n, block=True, timeout=timeout, partition=self._key)
        except _queue.Empty:
            return []
        return list(got or [])

    def clear(self) -> None:
        self._q.clear(partition=self._key)


class ModalRunner:
    """studio_jobs.StepRunner on Modal: `analyze` on `step_gpu`, every other stage on `step_cpu`.

    `queue_factory(job_id)` gives the job's line queue (get_many / clear), `secret_dict` is a modal.Dict, `commit`
    / `reload` are the API container's volume calls (commit before a spawn so the step sees what the API wrote,
    reload after it returns so the API sees what the step wrote), `from_id` is modal.FunctionCall.from_id.
    """

    def __init__(self, step_gpu, step_cpu, queue_factory: Callable[[str], Any], secret_dict, *,
                 reload: Optional[Callable[[], Any]] = None, commit: Optional[Callable[[], Any]] = None,
                 from_id: Optional[Callable[[str], Any]] = None, poll: float = 0.5):
        self._gpu, self._cpu = step_gpu, step_cpu
        self._queue_factory = queue_factory
        self._secrets = secret_dict
        self._reload, self._commit, self._from_id = reload, commit, from_id
        self._poll = poll
        self._lock = threading.Lock()
        self._calls: dict[str, Any] = {}
        self._on_spawn: Callable[[str, str], None] = lambda job_id, call_id: None
        self._detached = threading.Event()

    # ---- hooks the job queue uses -----------------------------------------------------------------------------
    def bind(self, on_spawn: Callable[[str, str], None]) -> None:
        """The queue records each call id in the job's file, so a restarted API can follow the call again."""
        self._on_spawn = on_spawn

    def release(self, job_id: str) -> None:
        """The job ended (any state): its secrets entry must be gone."""
        self._forget(job_id)

    def detach(self) -> None:
        """The API container is stopping: stop following every call but leave them running for the next one."""
        self._detached.set()

    def ping(self) -> bool:
        """Cheap: can the GPU function be reached? (/doctor)"""
        try:
            stats = getattr(self._gpu, "get_current_stats", None)
            if stats is None:
                return callable(getattr(self._gpu, "spawn", None))
            stats()
            return True
        except Exception:  # noqa: BLE001
            return False

    # ---- StepRunner ----------------------------------------------------------------------------------------------
    def cancel(self, job_id: str) -> None:
        with self._lock:
            call = self._calls.get(job_id)
        if call is not None:
            try:
                call.cancel()
            except Exception as e:  # noqa: BLE001
                print(f"vedit: could not cancel the step of job {job_id}: {e}", file=sys.stderr)

    def run(self, job_id: str, stage: str, argv: list[str], env: dict[str, str], on_line: Callable[[str], None],
            cancelled: Callable[[], bool]) -> tuple[int, list[str]]:
        clean, secrets = split_secrets(env)
        if cancelled() or self._detached.is_set():
            return -1, []
        home = USER_HOME.get()
        fn = self._gpu if stage in GPU_STAGES else self._cpu
        try:
            lines = self._queue_factory(job_id)
            clear = getattr(lines, "clear", None)
            if clear is not None:
                clear()  # nothing from an earlier step of this job is read as this one's
            if secrets:
                self._secrets[job_id] = secrets
                clean[SECRET_REF] = job_id
            _quietly(self._commit, "commit")
            with self._lock:
                if cancelled():
                    return -1, []
                call = fn.spawn(job_id=job_id, stage=stage, argv=list(argv), env=clean,
                                home=str(home) if home is not None else None)
                self._calls[job_id] = call
        except Exception as e:  # noqa: BLE001
            self._forget(job_id)
            return -1, [f"could not start step '{stage}': {type(e).__name__}: {e}"]
        try:
            self._on_spawn(job_id, call.object_id)
        except Exception as e:  # noqa: BLE001
            print(f"vedit: could not record the call of job {job_id}: {e}", file=sys.stderr)
        return self._finish(job_id, stage, call, lines, on_line, cancelled)

    def reattach(self, job_id: str, call_id: str, on_line: Callable[[str], None],
                 cancelled: Callable[[], bool]) -> tuple[int, list[str]]:
        """Follow a call an earlier API container spawned (restart). A call that is gone fails the step."""
        try:
            if self._from_id is None:
                raise RuntimeError("no FunctionCall.from_id")
            call = self._from_id(call_id)
            lines = self._queue_factory(job_id)
        except Exception as e:  # noqa: BLE001
            print(f"vedit: could not reattach job {job_id}: {e}", file=sys.stderr)
            self._forget(job_id)
            return -1, [INTERRUPTED]
        with self._lock:
            self._calls[job_id] = call
        return self._finish(job_id, "", call, lines, on_line, cancelled)

    # ---- internals ---------------------------------------------------------------------------------------------
    def _forget(self, job_id: str) -> None:
        try:
            _pop(self._secrets, job_id)
        except Exception as e:  # noqa: BLE001
            print(f"vedit: could not delete the secrets of job {job_id}: {e}", file=sys.stderr)

    def _finish(self, job_id, stage, call, lines, on_line, cancelled) -> tuple[int, list[str]]:
        detached = False
        try:
            code, tail, detached = self._follow(stage, call, lines, on_line, cancelled)
            return code, tail
        finally:
            with self._lock:
                self._calls.pop(job_id, None)
            if not detached:  # a detached step may not have read its secrets yet: the next container deletes them
                self._forget(job_id)
                _quietly(self._reload, "reload")  # the step's files, before the queue reads any output

    def _follow(self, stage, call, lines, on_line, cancelled) -> tuple[int, list[str], bool]:
        cancel_sent = False

        def drain(block: float | None) -> None:
            try:
                got = lines.get_many(100, timeout=block)
            except Exception as e:  # noqa: BLE001 - progress is best-effort; the result still arrives
                print(f"vedit: step lines unavailable: {e}", file=sys.stderr)
                time.sleep(block or 0)
                return
            for line in got:
                on_line(str(line))

        while True:
            if self._detached.is_set():
                return -1, [], True
            drain(self._poll)
            if cancelled() and not cancel_sent:
                cancel_sent = True
                try:
                    call.cancel()
                except Exception as e:  # noqa: BLE001
                    print(f"vedit: could not cancel step '{stage}': {e}", file=sys.stderr)
            try:
                result = call.get(timeout=0)
            except Exception as e:  # noqa: BLE001
                if _is_poll_timeout(e):
                    continue
                drain(0)
                what = f"step '{stage}'" if stage else "step"
                return -1, [f"{what} failed: {type(e).__name__}: {e}"], False
            drain(0)
            code, tail = _result(result)
            return code, tail, False


def _result(result) -> tuple[int, list[str]]:
    if isinstance(result, dict):
        return int(result.get("code", -1)), [str(x) for x in result.get("tail") or []]
    code, tail = result
    return int(code), [str(x) for x in tail]


def execute_step(job_id: str, stage: str, argv: list[str], env: dict[str, str], home: Optional[str], *, lines,
                 secrets, commit: Optional[Callable[[], Any]] = None, reload: Optional[Callable[[], Any]] = None,
                 run: Optional[Callable[..., tuple[int, list[str]]]] = None) -> dict:
    """Run one step in a Modal container: secrets from the dict (popped at once), lines to the job's queue, and
    the volume committed at the end, failure included, so its files and logs reach the API container."""
    env = dict(env)
    ref = env.pop(SECRET_REF, None)
    found = _pop(secrets, ref) if ref else None
    if isinstance(found, dict):
        env.update(found)
    if home:
        env["VEDIT_HOME"] = home
    buf: list[str] = []
    last = [time.monotonic()]
    buf_lock = threading.Lock()

    def flush() -> None:
        with buf_lock:
            out, buf[:] = list(buf), []
            last[0] = time.monotonic()
        if out:
            try:
                lines.put_many(out)
            except Exception as e:  # noqa: BLE001
                print(f"vedit: could not forward step lines: {e}", file=sys.stderr)

    def on_line(line: str) -> None:
        if not line:
            return
        with buf_lock:
            buf.append(line)
            due = len(buf) >= 50 or time.monotonic() - last[0] >= FLUSH_SECONDS
        if due:
            flush()

    tail: deque[str] = deque(maxlen=TAIL_LINES)
    try:
        _quietly(reload, "reload")  # what the API container committed before spawning this step
        try:
            code, out_tail = (run or LocalRunner().run)(job_id, stage, list(argv), env, on_line, lambda: False)
            tail.extend(out_tail)
        except Exception as e:  # noqa: BLE001
            code = -1
            tail.append(f"step '{stage}' could not run: {type(e).__name__}: {e}")
        return {"code": int(code), "tail": list(tail)}
    finally:
        flush()
        _quietly(commit, "commit")
