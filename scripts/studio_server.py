"""Studio local engine API: FastAPI on 127.0.0.1 that the desktop app drives (projects, jobs, clip edits, events).

Every request needs `Authorization: Bearer <token>` (the WebSocket takes `?token=`). A request that carries an
Origin header must come from the app's webview (or the Vite dev server); requests without one (native callers,
tests) are allowed.
"""
from __future__ import annotations

import asyncio
import dataclasses
import hmac
import json
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from fastapi import Body, FastAPI, Request, WebSocket  # noqa: E402
from fastapi.exceptions import RequestValidationError  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from pydantic import BaseModel  # noqa: E402

import vedit  # noqa: E402
from captions import caption_events  # noqa: E402
from common import ffmpeg_bin, ffprobe, load_json, py_cmd, save_json, workdir_for  # noqa: E402
from edl import Timeline  # noqa: E402
from edl_ops import clip_edl, cut_words, fix_word, kept_ranges, patch_section, restore_words, trim  # noqa: E402
from studio_jobs import Job, JobQueue  # noqa: E402
from studio_projects import (LANG_MODEL, Project, ProjectClip, create_project, list_recent,  # noqa: E402
                             load_project, output_dir, output_name, save_project)

ALLOWED_ORIGINS = ("tauri://localhost", "http://tauri.localhost", "http://localhost:5173")
FORMATS = ("9:16", "1:1", "4:5", "16:9")
HIGH_CRF = "18"
ANALYZE_STEPS = (("proxy", 0.1), ("analyze", 0.6), ("highlights", 0.15), ("framing", 0.15))


# ---- request bodies -------------------------------------------------------------------------------------------

class NewProject(BaseModel):
    source: str
    language: Literal["en", "tl", "taglish"]
    platform: Literal["reels", "shorts", "tiktok"]


class AIOptions(BaseModel):
    provider: Literal["anthropic", "openai"]
    api_key: str
    model: Optional[str] = None


class AnalyzeBody(BaseModel):
    ai: Optional[AIOptions] = None


class KeptBody(BaseModel):
    kept: bool


class ExportBody(BaseModel):
    clips: list[str]
    formats: list[str] = ["9:16"]
    quality: Literal["normal", "high"] = "normal"


# ---- engine state ---------------------------------------------------------------------------------------------

def _project_dict(p: Project) -> dict:
    return dataclasses.asdict(p)


def _job_dict(j: Job) -> dict:
    return dataclasses.asdict(j)


class NotFound(Exception):
    """An explicit lookup (project, clip, job, transcript) found nothing: the API answers 404."""


def _project(pid: str) -> Project:
    try:
        return load_project(pid)
    except KeyError:
        raise NotFound(f"project {pid}") from None


def _find_clip(p: Project, hid: str) -> ProjectClip:
    for c in p.clips:
        if c.highlight["id"] == hid:
            return c
    raise NotFound(f"clip {hid}")


_probe_cache: dict[tuple[str, int, int], dict] = {}
_probe_lock = threading.Lock()


def cached_probe(path: str | Path) -> dict:
    """common.ffprobe, memoised per (path, size, mtime) so clip edits never re-probe an unchanged source."""
    st = os.stat(path)
    key = (os.path.normcase(str(Path(path).resolve())), st.st_size, st.st_mtime_ns)
    with _probe_lock:
        hit = _probe_cache.get(key)
    if hit is None:
        hit = ffprobe(path)
        with _probe_lock:
            _probe_cache[key] = hit
    return json.loads(json.dumps(hit))  # callers may mutate their copy


def _timeline(edl_path: Path) -> Timeline:
    """edl.Timeline (cached probes), with its die() (SystemExit) turned into a ValueError the API reports as 400."""
    try:
        return Timeline(edl_path, probe=cached_probe)
    except SystemExit as e:
        raise ValueError(f"the edit could not be resolved ({e.code})") from None


def _write_json_atomic(data: Any, path: Path) -> None:
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class Engine:
    """Projects + job queue + event fan-out. on_event only enqueues; one dispatcher thread does the rest."""

    def __init__(self, token: str, job_queue=None):
        self.token = token
        self._events: queue.Queue[Optional[Job]] = queue.Queue()
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._meta_lock = threading.Lock()  # held while a submit registers its post-processing metadata
        self._exports: dict[str, list[Path]] = {}
        self._results: dict[str, dict] = {}
        self._clients: set[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = set()
        self._clients_lock = threading.Lock()
        self._closed = False
        self._recover_interrupted()
        self.queue = job_queue if job_queue is not None else JobQueue(self.on_event)
        self._dispatcher = threading.Thread(target=self._dispatch, name="vedit-dispatch", daemon=True)
        self._dispatcher.start()

    # -- lifecycle --
    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.queue.shutdown()
        finally:
            self._events.put(None)
            self._dispatcher.join(timeout=10)

    def wait_idle(self) -> None:
        """Block until every event queued so far has been processed (tests)."""
        self._events.join()

    @staticmethod
    def _recover_interrupted() -> None:
        for p in list_recent(limit=sys.maxsize):
            job = p.last_job
            if isinstance(job, dict) and job.get("state") in ("running", "queued"):
                p.last_job = {**job, "state": "failed", "error": "interrupted"}
                try:
                    save_project(p)
                except OSError as e:
                    print(f"vedit: could not mark job interrupted in {p.source}: {e}", file=sys.stderr)

    def lock_for(self, pid: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(pid, threading.Lock())

    # -- jobs --
    def on_event(self, job: Job) -> None:
        """JobQueue callback: never blocks, never raises."""
        self._events.put(dataclasses.replace(job))

    def submit(self, project_id: str, kind: str, steps: list, env: dict | None = None,
               exports: list[Path] | None = None) -> Job:
        with self._meta_lock:
            job = self.queue.submit(project_id, kind, steps, env=env)
            if exports is not None:
                self._exports[job.id] = exports
        return job

    def job_snapshot(self, job_id: str) -> Job:
        job = self.queue.get(job_id)
        if job_id in self._results:
            job.result = self._results[job_id]
        return job

    def _dispatch(self) -> None:
        while True:
            first = self._events.get()
            batch = [first]
            while True:  # coalesce a burst: only the newest snapshot of each job matters
                try:
                    batch.append(self._events.get_nowait())
                except queue.Empty:
                    break
            try:
                last = {j.id: k for k, j in enumerate(batch) if j is not None}
                for k, j in enumerate(batch):
                    if j is None:
                        continue
                    try:
                        if last[j.id] == k:  # newest snapshot of this job: persist + post-process, then send
                            self._handle(j)
                        else:  # superseded within the burst: still sent, never written to disk
                            self._broadcast({"type": "job", "job": _job_dict(j)})
                    except Exception as e:  # noqa: BLE001 - the dispatcher must survive anything
                        print(f"vedit: event handling failed for job {j.id}: {e}", file=sys.stderr)
            finally:
                for _ in batch:
                    self._events.task_done()
            if any(j is None for j in batch):
                return

    def _handle(self, job: Job) -> None:
        with self.lock_for(job.project_id):
            try:
                p = load_project(job.project_id)
            except (KeyError, ValueError, OSError):
                p = None
            if job.state == "done" and job.id not in self._results:
                try:
                    if job.kind == "analyze" and p is not None:
                        self._results[job.id] = self._finish_analyze(p)
                    elif job.kind == "export":
                        self._results[job.id] = self._finish_export(job.id)
                except Exception as e:  # noqa: BLE001 - post-processing failure fails the job, not the server
                    job.state, job.error = "failed", f"post-processing failed: {e}"
            if job.id in self._results:
                job.result = self._results[job.id]
            if p is not None:
                p.last_job = _job_dict(job)
                save_project(p)
        self._broadcast({"type": "job", "job": _job_dict(job)})

    def _finish_analyze(self, p: Project) -> dict:
        wd = workdir_for(p.source)
        data = load_json(wd / "highlights.json")
        language = LANG_MODEL[p.language][0]
        kept = {c.highlight["id"]: c.kept for c in p.clips}
        clips = []
        for h in data.get("highlights") or []:
            try:
                edl = clip_edl(wd, h, p.platform, language, None)
            except ValueError as e:
                print(f"vedit: skipping highlight {h.get('id')}: {e}", file=sys.stderr)
                continue
            except SystemExit:
                print(f"vedit: skipping highlight {h.get('id')}: edit could not be built", file=sys.stderr)
                continue
            name = f"clip-{h['id']}.edl.json"
            save_json(edl, wd / name)
            clips.append(ProjectClip(highlight=h, kept=kept.get(h["id"], True), edl_file=name))
        p.clips = clips
        return {"clips": len(clips), "reason": data.get("reason")}

    def _finish_export(self, job_id: str) -> dict:
        with self._meta_lock:
            outs = self._exports.pop(job_id, [])
        files = []
        for out in outs:
            files.append(str(out))
            if out.with_suffix(".srt").is_file():
                files.append(str(out.with_suffix(".srt")))
        return {"files": files}

    # -- websocket fan-out --
    def add_client(self, client: tuple[asyncio.AbstractEventLoop, asyncio.Queue]) -> None:
        with self._clients_lock:
            self._clients.add(client)

    def remove_client(self, client) -> None:
        with self._clients_lock:
            self._clients.discard(client)

    def _broadcast(self, msg: dict) -> None:
        with self._clients_lock:
            clients = list(self._clients)
        for loop, q in clients:
            try:
                loop.call_soon_threadsafe(q.put_nowait, msg)
            except RuntimeError:  # loop already closed
                self.remove_client((loop, q))


# ---- route helpers --------------------------------------------------------------------------------------------

def _clip_view(wd: Path, edl_path: Path) -> dict:
    edl = load_json(edl_path)
    tp = wd / "transcript.main.json"
    tr = load_json(tp) if tp.is_file() else {}
    return {"edl": edl, "kept_ranges": [list(r) for r in kept_ranges(edl, tr)],
            "captions": caption_events(_timeline(edl_path))}


def _int_arg(body: dict, key: str) -> int:
    if key not in body:
        raise ValueError(f"missing '{key}'")
    v = body[key]
    if isinstance(v, bool) or not isinstance(v, int):
        raise ValueError(f"'{key}' must be an integer")
    return v


def _str_arg(body: dict, key: str) -> str:
    v = body.get(key)
    if not isinstance(v, str):
        raise ValueError(f"'{key}' must be a string")
    return v


def _apply_op(edl: dict, body: dict) -> dict:
    op = body.get("op")
    if op == "cut":
        return cut_words(edl, _int_arg(body, "w0"), _int_arg(body, "w1"))
    if op == "restore":
        return restore_words(edl, _int_arg(body, "w0"), _int_arg(body, "w1"))
    if op == "trim":
        return trim(edl, _int_arg(body, "w0"), _int_arg(body, "w1"))
    if op == "fix_word":
        return fix_word(edl, _str_arg(body, "wrong"), _str_arg(body, "right"))
    if op == "patch":
        patch = body.get("patch")
        if not isinstance(patch, dict):
            raise ValueError("'patch' must be an object")
        return patch_section(edl, _str_arg(body, "section"), patch)  # type: ignore[arg-type]
    raise ValueError(f"unknown op {op!r}")


def _ffmpeg_status() -> tuple[bool, bool]:
    exe = ffmpeg_bin("ffmpeg")
    try:
        r = subprocess.run([exe, "-hide_banner", "-filters"], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=15,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        return False, False
    if r.returncode != 0:
        return False, False
    return True, re.search(r"^\s*\S+\s+(ass|subtitles)\s", r.stdout, re.M) is not None


# ---- app ------------------------------------------------------------------------------------------------------

def create_app(token: str, queue: JobQueue | None = None) -> FastAPI:
    engine = Engine(token, queue)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        await asyncio.to_thread(engine.close)

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.engine = engine

    def origin_ok(origin: str | None) -> bool:
        return origin is None or origin in ALLOWED_ORIGINS

    def token_ok(given: str | None) -> bool:
        return given is not None and hmac.compare_digest(given.encode("utf-8"), token.encode("utf-8"))

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if not origin_ok(request.headers.get("origin")):
            return JSONResponse({"detail": "origin not allowed"}, status_code=403)
        auth = request.headers.get("authorization") or ""
        if not (auth.startswith("Bearer ") and token_ok(auth[len("Bearer "):])):
            return JSONResponse({"detail": "missing or wrong token"}, status_code=401)
        return await call_next(request)

    # added after guard, so it wraps it: preflights are answered here and errors still carry CORS headers
    app.add_middleware(CORSMiddleware, allow_origins=list(ALLOWED_ORIGINS), allow_methods=["*"],
                       allow_headers=["*"])

    @app.exception_handler(NotFound)
    async def not_found(_request: Request, exc: NotFound):
        return JSONResponse({"detail": f"not found: {exc}"}, status_code=404)

    @app.exception_handler(ValueError)
    async def bad_request(_request: Request, exc: ValueError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(RequestValidationError)
    async def invalid_body(_request: Request, exc: RequestValidationError):
        # never echo the submitted values back: the body can carry an API key
        errors = [{"loc": list(e.get("loc", ())), "msg": e.get("msg", "")} for e in exc.errors()]
        return JSONResponse({"detail": errors}, status_code=422)

    @app.get("/health")
    def health():
        return {"ok": True, "version": vedit.__version__}

    @app.get("/doctor")
    def doctor():
        ffmpeg, libass = _ffmpeg_status()
        return {"ffmpeg": ffmpeg, "libass": libass, "models": {}}

    @app.get("/projects/recent")
    def recent():
        return [_project_dict(p) for p in list_recent()]

    @app.post("/projects")
    def new_project(body: NewProject):
        src = Path(body.source)
        if not src.is_file():
            raise ValueError(f"source video not found: {body.source}")
        return _project_dict(create_project(src, body.language, body.platform))

    @app.get("/projects/{pid}")
    def get_project(pid: str):
        return _project_dict(_project(pid))

    @app.post("/projects/{pid}/analyze")
    def analyze(pid: str, body: Optional[AnalyzeBody] = None):
        p = _project(pid)
        lang, model = LANG_MODEL[p.language]
        src = str(Path(p.source))
        hl_args = []
        env = None
        if body is not None and body.ai is not None:
            hl_args = ["--ai", body.ai.provider] + (["--ai-model", body.ai.model] if body.ai.model else [])
            env = {"VEDIT_AI_KEY": body.ai.api_key}
        argv = {"proxy": py_cmd("proxy.py", src),
                "analyze": py_cmd("analyze.py", src, "--model", model, "--language", lang),
                "highlights": py_cmd("highlights.py", src, *hl_args),
                "framing": py_cmd("highlights.py", src, "--framing")}
        steps = [(stage, argv[stage], share) for stage, share in ANALYZE_STEPS]
        return _job_dict(engine.submit(pid, "analyze", steps, env=env))

    @app.get("/projects/{pid}/transcript")
    def transcript(pid: str):
        p = _project(pid)
        tp = workdir_for(p.source) / "transcript.main.json"
        if not tp.is_file():
            raise NotFound("transcript")
        return load_json(tp)

    @app.get("/projects/{pid}/clips/{hid}")
    def get_clip(pid: str, hid: str):
        p = _project(pid)
        wd = workdir_for(p.source)
        return _clip_view(wd, wd / _find_clip(p, hid).edl_file)

    @app.patch("/projects/{pid}/clips/{hid}")
    def patch_clip(pid: str, hid: str, body: dict = Body(...)):
        with engine.lock_for(pid):
            p = _project(pid)
            wd = workdir_for(p.source)
            path = wd / _find_clip(p, hid).edl_file
            new = _apply_op(load_json(path), body)
            fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=wd)
            os.close(fd)
            tmp_path = Path(tmp)
            try:
                save_json(new, tmp_path)
                view = _clip_view(wd, tmp_path)  # validate before replacing the saved edit
                os.replace(tmp_path, path)
            finally:
                tmp_path.unlink(missing_ok=True)
            return view

    @app.put("/projects/{pid}/clips/{hid}/kept")
    def set_kept(pid: str, hid: str, body: KeptBody):
        with engine.lock_for(pid):
            p = _project(pid)
            _find_clip(p, hid).kept = body.kept
            save_project(p)
            return _project_dict(p)

    @app.post("/projects/{pid}/export")
    def export(pid: str, body: ExportBody):
        if not body.clips or not body.formats:
            raise ValueError("export needs at least one clip and one format")
        bad = [f for f in body.formats if f not in FORMATS]
        if bad:
            raise ValueError(f"unsupported formats: {bad}")
        with engine.lock_for(pid):
            p = _project(pid)
            wd = workdir_for(p.source)
            out_dir = output_dir(Path(p.source))
            plan = []
            for i, hid in enumerate(body.clips, 1):
                clip = _find_clip(p, hid)
                edl = load_json(wd / clip.edl_file)
                for aspect in body.formats:
                    e = json.loads(json.dumps(edl))
                    out = {k: v for k, v in (e.get("output") or {}).items() if k not in ("width", "height")}
                    path = out_dir / output_name(i, clip.highlight.get("title", ""), aspect)
                    out.update(aspect=aspect, path=str(path))
                    e["output"] = out
                    plan.append((hid, aspect, e, path))
        share = 1.0 / len(plan)
        crf = ["--crf", HIGH_CRF] if body.quality == "high" else []
        steps, outs = [], []
        tag = uuid.uuid4().hex[:8]  # queued exports never share (and overwrite) each other's edit files
        for hid, aspect, e, path in plan:
            tmp = wd / f"export-{tag}-{hid}-{aspect.replace(':', 'x')}.edl.json"
            _write_json_atomic(e, tmp)
            steps.append(("render", py_cmd("render.py", str(tmp), *crf), share))
            outs.append(path)
        return _job_dict(engine.submit(pid, "export", steps, exports=outs))

    @app.post("/jobs/{job_id}/cancel")
    def cancel(job_id: str):
        try:
            engine.queue.cancel(job_id)
        except KeyError:
            raise NotFound(f"job {job_id}") from None
        return _job_dict(engine.job_snapshot(job_id))

    @app.websocket("/events")
    async def events(ws: WebSocket):
        if not origin_ok(ws.headers.get("origin")) or not token_ok(ws.query_params.get("token")):
            await ws.close(code=1008)
            return
        await ws.accept()
        q: asyncio.Queue = asyncio.Queue()
        client = (asyncio.get_running_loop(), q)
        engine.add_client(client)

        async def pump():
            while True:
                await ws.send_json(await q.get())

        async def drain():
            while (await ws.receive())["type"] != "websocket.disconnect":
                pass

        tasks = [asyncio.ensure_future(pump()), asyncio.ensure_future(drain())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            engine.remove_client(client)
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    return app


def serve(port: int, token: str) -> None:
    """Serve on 127.0.0.1 only. Prints one stdout line `@@ready {"port": N}` once listening (port 0 = any free)."""
    import socket

    import uvicorn

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", port))
    real_port = sock.getsockname()[1]
    app = create_app(token)
    config = uvicorn.Config(app, log_config=None, access_log=False, log_level="warning", lifespan="on")
    server = uvicorn.Server(config)

    async def announce():
        while not server.started:
            if server.should_exit:
                return
            await asyncio.sleep(0.02)
        sys.stdout.write("@@ready " + json.dumps({"port": real_port}) + "\n")
        sys.stdout.flush()

    async def main():
        watcher = asyncio.ensure_future(announce())
        try:
            await server.serve(sockets=[sock])
        finally:
            watcher.cancel()

    try:
        asyncio.run(main())
    finally:
        app.state.engine.close()
        sock.close()
