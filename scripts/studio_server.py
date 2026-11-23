"""Studio local engine API: FastAPI on 127.0.0.1 that the desktop app drives (projects, jobs, clip edits, events).

Every request needs `Authorization: Bearer <token>` (the WebSocket takes `?token=`). A request that carries an
Origin header must come from the app's webview (or the Vite dev server); requests without one (native callers,
tests) are allowed. Error bodies are {"detail": str, "code": str}.
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
from typing import Literal, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from fastapi import Body, FastAPI, Request, WebSocket  # noqa: E402
from fastapi.exceptions import RequestValidationError  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402
from starlette.exceptions import HTTPException as StarletteHTTPException  # noqa: E402

import models  # noqa: E402
import vedit  # noqa: E402
from captions import caption_events  # noqa: E402
from common import ffmpeg_bin, ffprobe, load_json, py_cmd, save_json, workdir_for  # noqa: E402
from edl import Timeline  # noqa: E402
from edl_ops import clip_edl, cut_words, fix_word, kept_ranges, patch_section, restore_words, trim  # noqa: E402
from highlights_llm import HID_RE  # noqa: E402
from studio_jobs import Job, JobQueue  # noqa: E402
from studio_projects import (LANG_MODEL, Project, ProjectClip, _atomic_write_json,  # noqa: E402
                             create_project, list_recent, load_project, output_dir, output_name, save_project)

ALLOWED_ORIGINS = ("tauri://localhost", "http://tauri.localhost", "http://localhost:5173")
FORMATS = ("9:16", "1:1", "4:5", "16:9")
HIGH_CRF = "18"
ANALYZE_STEPS = (("proxy", 0.1), ("analyze", 0.6), ("highlights", 0.15), ("framing", 0.15))
TERMINAL = ("done", "failed", "cancelled")
WS_QUEUE_MAX = 256
OVERLAP_DROP = 0.5  # a new highlight overlapping a kept clip by more than this share of the shorter one is dropped


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
    max_clips: Optional[int] = Field(default=None, ge=1, le=60)  # most highlights to keep; None = the engine default


class AITestBody(BaseModel):
    provider: Literal["anthropic", "openai"]
    api_key: str


class KeptBody(BaseModel):
    kept: bool


class ExportBody(BaseModel):
    clips: list[str]
    formats: list[str] = ["9:16"]
    quality: Literal["normal", "high"] = "normal"
    out_dir: Optional[str] = None  # absolute folder for the files; default: "<name> - vedit" next to the video
    srt: bool = True  # also write the .srt subtitle file
    thumbnail: bool = False  # also save a .jpg frame beside each video
    first_number: int = Field(1, ge=1, le=999)  # the "NN" the first clip's file name starts at (a retry keeps its number)


BRAND_KEYS = ("font", "caption_color", "highlight", "logo", "intro", "outro", "music")
AI_TEST_URLS = {"anthropic": "https://api.anthropic.com/v1/models", "openai": "https://api.openai.com/v1/models"}


class BrandBody(BaseModel):
    """The brand kit keys the Settings screen edits (see presets.apply_brand); an empty string means unset."""
    font: str = ""
    caption_color: str = ""
    highlight: str = ""
    logo: str = ""
    intro: str = ""
    outro: str = ""
    music: str = ""


def brand_file() -> Path:
    """Where the app's brand kit lives: %LOCALAPPDATA%\vedit\brand.json, else ~/.vedit/brand.json (VEDIT_HOME wins)."""
    home = os.environ.get("VEDIT_HOME")
    if home:
        return Path(home) / "brand.json"
    local = os.environ.get("LOCALAPPDATA")
    return (Path(local) / "vedit" if local else Path.home() / ".vedit") / "brand.json"


def read_brand() -> dict:
    """The saved brand kit, or {} when there is none or the file is unreadable or not a JSON object."""
    try:
        data = load_json(brand_file())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def usable_brand_file() -> Path | None:
    f = brand_file()
    return f if read_brand() and f.exists() else None


def _http_get(url: str, headers: dict) -> int:
    import httpx  # lazy, like highlights_llm
    return httpx.get(url, headers=headers, timeout=15).status_code


def check_ai_key(provider: str, api_key: str) -> dict:
    """Ask the provider to list its models: the cheapest authenticated call, no tokens spent. Never logs the key."""
    headers = ({"x-api-key": api_key, "anthropic-version": "2023-06-01"} if provider == "anthropic"
               else {"Authorization": f"Bearer {api_key}"})
    try:
        status = _http_get(AI_TEST_URLS[provider], headers)
    except Exception:  # noqa: BLE001 - any network failure is one plain answer; the exception text could echo the key
        return {"ok": False, "error": "network"}
    if 200 <= status < 300:
        return {"ok": True}
    return {"ok": False, "error": "rejected" if status in (401, 403) else f"status_{status}"}


# ---- errors and small helpers ---------------------------------------------------------------------------------

class NotFound(Exception):
    """An explicit lookup (project, clip, job, transcript) found nothing: the API answers 404."""
    code = "not_found"


class NoFilmstrip(NotFound):
    """The project has no thumbnail sprite (yet)."""
    code = "no_filmstrip"


class ClipMissing(NotFound):
    """The project lists the clip but its edit file is gone from the workdir."""
    code = "clip_missing"


def _error(status: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse({"detail": detail, "code": code}, status_code=status)


def _project_dict(p: Project) -> dict:
    return dataclasses.asdict(p)


def _job_dict(j: Job) -> dict:
    return dataclasses.asdict(j)


def _event(job: Job) -> dict:
    return {"type": "job", "job": _job_dict(job)}


def _valid_hid(hid) -> bool:
    return isinstance(hid, str) and HID_RE.fullmatch(hid) is not None


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


def _edl_path(wd: Path, clip: ProjectClip) -> Path:
    path = wd / clip.edl_file
    if not path.is_file():
        raise ClipMissing(f"edit file for clip {clip.highlight.get('id')} is missing: {clip.edl_file}")
    return path


def _overlap_share(a: dict, b: dict) -> float:
    """Overlap of two highlights as a share of the shorter one."""
    inter = min(float(a["end"]), float(b["end"])) - max(float(a["start"]), float(b["start"]))
    shorter = min(float(a["end"]) - float(a["start"]), float(b["end"]) - float(b["start"]))
    return max(0.0, inter) / shorter if shorter > 0 else 0.0


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


def _offer(q: asyncio.Queue, msg: dict) -> None:
    """Put on a bounded client queue, dropping the oldest event when a slow client lets it fill."""
    if q.full():
        try:
            q.get_nowait()
        except asyncio.QueueEmpty:
            pass
    q.put_nowait(msg)


# ---- engine state ---------------------------------------------------------------------------------------------

class Engine:
    """Projects + job queue + event fan-out. on_event only enqueues; one dispatcher thread does the rest.

    Fields the queue doesn't know (a post-processing failure, the result) live in a server-side overlay applied to
    every snapshot, so GET /jobs, cancel, last_job and the WebSocket always agree.
    """

    def __init__(self, token: str, job_queue=None):
        self.token = token
        self._events: queue.Queue[Optional[Job]] = queue.Queue()
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._meta_lock = threading.Lock()  # guards the overlay, export plans and latest snapshots
        self._exports: dict[str, list[dict]] = {}
        self._overlay: dict[str, dict] = {}
        self._finalized: set[str] = set()
        self._jobs: dict[str, Job] = {}
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

    def _overlaid(self, job: Job) -> Job:
        """Copy of `job` with the server-side overlay applied. Caller holds _meta_lock."""
        job = dataclasses.replace(job)
        for k, v in self._overlay.get(job.id, {}).items():
            setattr(job, k, json.loads(json.dumps(v)) if isinstance(v, (dict, list)) else v)
        return job

    def _set_overlay(self, job_id: str, **fields) -> None:
        with self._meta_lock:
            self._overlay.setdefault(job_id, {}).update(fields)

    def submit(self, project_id: str, kind: str, steps: list, renders: list[dict] | None = None) -> Job:
        with self._meta_lock:  # held so no event of this job is processed before its plan is registered
            job = self.queue.submit(project_id, kind, steps)
            if renders is not None:
                self._exports[job.id] = renders
                self._overlay[job.id] = {"result": {"renders": _public_renders(renders), "files": []}}
            return self._overlaid(job)

    def job_snapshot(self, job_id: str) -> Job:
        try:
            job = self.queue.get(job_id)
        except KeyError:
            raise NotFound(f"job {job_id}") from None
        with self._meta_lock:
            return self._overlaid(job)

    def active_jobs(self) -> list[Job]:
        with self._meta_lock:
            return [dataclasses.replace(j) for j in self._jobs.values() if j.state not in TERMINAL]

    def _dispatch(self) -> None:
        while True:
            first = self._events.get()
            batch = [first]
            while True:  # coalesce a burst for persistence: only the newest snapshot of each job is written
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
                        if last[j.id] == k:  # newest snapshot of this job: post-process + persist, then send
                            self._handle(j)
                        else:  # superseded within the burst: still sent, never written to disk
                            with self._meta_lock:
                                j = self._overlaid(j)
                            self._broadcast(_event(j))
                    except Exception as e:  # noqa: BLE001 - the dispatcher must survive anything
                        print(f"vedit: event handling failed for job {j.id}: {e}", file=sys.stderr)
            finally:
                for _ in batch:
                    self._events.task_done()
            if any(j is None for j in batch):
                return

    def _handle(self, raw: Job) -> None:
        with self.lock_for(raw.project_id):
            try:
                p = load_project(raw.project_id)
            except (KeyError, ValueError, OSError):
                p = None
            if raw.state in TERMINAL and raw.id not in self._finalized:
                self._finalized.add(raw.id)
                try:
                    if raw.kind == "analyze" and raw.state == "done" and p is not None:
                        self._set_overlay(raw.id, result=self._finish_analyze(p))
                    elif raw.kind == "export":
                        self._set_overlay(raw.id, result=self._finish_export(raw))
                except Exception as e:  # noqa: BLE001 - post-processing failure fails the job, not the server
                    self._set_overlay(raw.id, state="failed", error=f"post-processing failed: {e}")
            with self._meta_lock:
                job = self._overlaid(raw)
                self._jobs[job.id] = job
            if p is not None:
                p.last_job = _job_dict(job)
                save_project(p)
        self._broadcast(_event(job))

    def _finish_analyze(self, p: Project) -> dict:
        """Add clips for new highlights. Existing clips whose edit file exists are never touched."""
        wd = workdir_for(p.source)
        data = load_json(wd / "highlights.json")
        language = LANG_MODEL[p.language][0]
        preserved = [c for c in p.clips if _valid_hid(c.highlight.get("id")) and (wd / c.edl_file).is_file()]
        used = {c.highlight["id"] for c in preserved}
        added = []
        for h in data.get("highlights") or []:
            hid = h.get("id")
            if not _valid_hid(hid):
                print(f"vedit: skipping highlight with an unsafe id: {hid!r}", file=sys.stderr)
                continue
            if any(_overlap_share(h, c.highlight) > OVERLAP_DROP for c in preserved):
                continue
            try:
                edl = clip_edl(wd, h, p.platform, language, usable_brand_file())
            except ValueError as e:
                print(f"vedit: skipping highlight {hid}: {e}", file=sys.stderr)
                continue
            except SystemExit:
                print(f"vedit: skipping highlight {hid}: edit could not be built", file=sys.stderr)
                continue
            if hid in used:
                n = 1
                while f"h{n:02d}" in used:
                    n += 1
                hid = f"h{n:02d}"
            name = f"clip-{hid}.edl.json"
            _atomic_write_json(wd / name, edl)
            used.add(hid)
            added.append(ProjectClip(highlight={**h, "id": hid}, kept=True, edl_file=name))
        p.clips = preserved + added
        proxy = wd / "proxy.mp4"
        try:
            has_proxy = proxy.is_file() and load_json(wd / "proxy.json").get("src") == str(Path(p.source).resolve())
        except (OSError, ValueError):
            has_proxy = False
        p.settings.update(workdir=str(wd), proxy=str(proxy if has_proxy else Path(p.source)),
                          filmstrip=str(wd / "filmstrip.json"))
        return {"clips": len(p.clips), "new": len(added), "reason": data.get("reason")}

    def _finish_export(self, job: Job) -> dict:
        """Finished files (+ .srt) and, on failure, which render failed. Always removes the temp edits."""
        with self._meta_lock:
            plan = self._exports.pop(job.id, [])
        by_stage = {}
        for i, e in enumerate(plan):
            by_stage[e["stage"]] = i
            by_stage[e["stage"].replace("render ", "thumb ")] = i
        if job.state == "done":
            finished, failed = plan, None
        else:
            at = by_stage.get(job.stage, 0)
            in_thumb = job.stage.startswith("thumb ")  # its video was already rendered
            finished = plan[:at + 1] if in_thumb else plan[:at]
            failed = plan[at] if job.state == "failed" and plan and not in_thumb else None
        files, warnings = [], []
        for e in finished:
            out = Path(e["path"])
            files.append(str(out))
            for extra in (".srt", ".jpg"):
                if out.with_suffix(extra).is_file():
                    files.append(str(out.with_suffix(extra)))
            thumb_ran = job.state == "done" or (job.state == "failed" and e is finished[-1] and job.stage.startswith("thumb "))
            if e.get("thumb") and thumb_ran and not out.with_suffix(".jpg").is_file():
                warnings.append({"hid": e["hid"], "aspect": e["aspect"], "kind": "thumbnail"})
        for e in plan:
            Path(e["tmp"]).unlink(missing_ok=True)
        result = {"renders": _public_renders(plan), "files": files}
        if warnings:
            result["warnings"] = warnings
        if failed is not None:
            result["failed"] = {"hid": failed["hid"], "aspect": failed["aspect"]}
        return result

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
                loop.call_soon_threadsafe(_offer, q, msg)
            except RuntimeError:  # loop already closed
                self.remove_client((loop, q))


def _safe_out_dir(raw: str) -> Path:
    """A local absolute folder with no parent steps, no UNC and no device path; created when missing."""
    flat = raw.replace("\\", "/")
    if flat.startswith("//"):
        raise ValueError("out_dir must be a local folder, not a network or device path")
    path = Path(raw)
    if not path.is_absolute() or ".." in flat.split("/"):
        raise ValueError("out_dir must be an absolute folder without '..'")
    path = path.resolve()
    if path.exists() and not path.is_dir():
        raise ValueError("out_dir is a file, not a folder")
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise ValueError("out_dir could not be created") from None
    return path


def _public_renders(plan: list[dict]) -> list[dict]:
    """What the UI may read about each render step: its stage name, clip and format, output path."""
    return [{k: e[k] for k in ("stage", "hid", "aspect", "path")} for e in plan]


# ---- route helpers --------------------------------------------------------------------------------------------

def _clip_view(wd: Path, edl_path: Path) -> dict:
    """GET/PATCH response: the edit, its source ranges and the live caption plan."""
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
        return trim(edl, _int_arg(body, "w_first"), _int_arg(body, "w_last"))
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
    if not isinstance(token, str) or not token.strip():
        raise ValueError("the engine needs a non-empty bearer token")
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
            return _error(403, "forbidden", "origin not allowed")
        auth = request.headers.get("authorization") or ""
        if not (auth.startswith("Bearer ") and token_ok(auth[len("Bearer "):])):
            return _error(401, "unauthorized", "missing or wrong token")
        return await call_next(request)

    # added after guard, so it wraps it: preflights are answered here and errors still carry CORS headers
    app.add_middleware(CORSMiddleware, allow_origins=list(ALLOWED_ORIGINS), allow_methods=["*"],
                       allow_headers=["*"])

    @app.exception_handler(NotFound)
    async def not_found(_request: Request, exc: NotFound):
        return _error(404, exc.code, f"not found: {exc}")

    @app.exception_handler(ValueError)
    async def bad_request(_request: Request, exc: ValueError):
        return _error(400, "bad_request", str(exc))

    @app.exception_handler(RequestValidationError)
    async def invalid_body(_request: Request, exc: RequestValidationError):
        # never echo the submitted values back: the body can carry an API key
        msgs = [".".join(str(x) for x in e.get("loc", ())) + ": " + str(e.get("msg", "")) for e in exc.errors()]
        return _error(422, "bad_request", "; ".join(msgs) or "invalid request")

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_request: Request, exc: StarletteHTTPException):
        return _error(exc.status_code, "not_found" if exc.status_code == 404 else "bad_request", str(exc.detail))

    @app.exception_handler(Exception)
    async def internal(_request: Request, exc: Exception):
        print(f"vedit: internal error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return _error(500, "internal", "internal error")

    @app.get("/health")
    def health():
        return {"ok": True, "version": vedit.__version__}

    @app.get("/doctor")
    def doctor():
        ffmpeg, libass = _ffmpeg_status()
        return {"ffmpeg": ffmpeg, "libass": libass, "models": models.status()}

    @app.get("/models")
    def model_status():
        return models.status()

    @app.post("/models/{name}/download")
    def model_download(name: str):
        if name not in models.MODELS:
            raise NotFound(f"model {name}")
        return _job_dict(engine.submit("", "model", [("model", py_cmd("models.py", "download", name), 1.0)]))

    @app.delete("/models/{name}")
    def model_delete(name: str):
        if name not in models.MODELS:
            raise NotFound(f"model {name}")
        if engine.active_jobs():
            return _error(409, "busy", "a job is running; try again when it finishes")
        models.delete(name)
        return models.status()

    @app.post("/ai/test")
    def ai_test(body: AITestBody):
        if not body.api_key.strip():
            return {"ok": False, "error": "empty"}
        return check_ai_key(body.provider, body.api_key.strip())

    @app.get("/brand")
    def get_brand():
        saved = read_brand()
        return {k: saved.get(k) if isinstance(saved.get(k), str) else "" for k in BRAND_KEYS}

    @app.put("/brand")
    def put_brand(body: BrandBody):
        kit = read_brand()  # keys Settings does not edit (caption_style, replace, ...) survive
        for k, v in body.model_dump().items():
            if v.strip():
                kit[k] = v
            else:
                kit.pop(k, None)
        _atomic_write_json(brand_file(), kit)
        return {k: kit[k] if isinstance(kit.get(k), str) else "" for k in BRAND_KEYS}

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
        hl_args: list[str] = []
        if body is not None and body.max_clips is not None:
            hl_args += ["--max", str(body.max_clips)]
        step_env: dict[str, dict] = {}
        if body is not None and body.ai is not None:
            hl_args += ["--ai", body.ai.provider] + (["--ai-model", body.ai.model] if body.ai.model else [])
            step_env["highlights"] = {"VEDIT_AI_KEY": body.ai.api_key}  # only the step that calls the AI sees it
        argv = {"proxy": py_cmd("proxy.py", src),
                "analyze": py_cmd("analyze.py", src, "--model", model, "--language", lang),
                "highlights": py_cmd("highlights.py", src, *hl_args),
                "framing": py_cmd("highlights.py", src, "--framing")}
        steps = [(stage, argv[stage], share) + ((step_env[stage],) if stage in step_env else ())
                 for stage, share in ANALYZE_STEPS]
        if not models.status()[model]["installed"]:  # fetch the speech model first, as 30% of the job
            steps = [("model", py_cmd("models.py", "download", model), 0.3)] +                     [(st, argv_, share * 0.7, *rest) for st, argv_, share, *rest in steps]
        return _job_dict(engine.submit(pid, "analyze", steps))

    @app.get("/projects/{pid}/transcript")
    def transcript(pid: str):
        p = _project(pid)
        tp = workdir_for(p.source) / "transcript.main.json"
        if not tp.is_file():
            raise NotFound("transcript")
        return load_json(tp)

    @app.get("/projects/{pid}/filmstrip")
    def filmstrip(pid: str):
        wd = workdir_for(_project(pid).source)
        meta_path, image = wd / "filmstrip.json", wd / "filmstrip.jpg"
        if not meta_path.is_file() or not image.is_file():
            raise NoFilmstrip("this project has no thumbnails yet")
        return {**load_json(meta_path), "image": str(image)}

    @app.get("/projects/{pid}/clips/{hid}")
    def get_clip(pid: str, hid: str):
        p = _project(pid)
        wd = workdir_for(p.source)
        return _clip_view(wd, _edl_path(wd, _find_clip(p, hid)))

    @app.patch("/projects/{pid}/clips/{hid}")
    def patch_clip(pid: str, hid: str, body: dict = Body(...)):
        with engine.lock_for(pid):
            p = _project(pid)
            wd = workdir_for(p.source)
            path = _edl_path(wd, _find_clip(p, hid))
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
        if len(set(body.clips)) != len(body.clips) or len(set(body.formats)) != len(body.formats):
            raise ValueError("duplicate clips or formats in export")
        with engine.lock_for(pid):
            p = _project(pid)
            wd = workdir_for(p.source)
            out_dir = output_dir(Path(p.source))
            if body.out_dir:
                out_dir = _safe_out_dir(body.out_dir)
            plan = []
            for i, hid in enumerate(body.clips, body.first_number):
                clip = _find_clip(p, hid)
                if not _valid_hid(hid):
                    raise ValueError(f"unsafe clip id {hid!r}")
                edl = load_json(_edl_path(wd, clip))
                for aspect in body.formats:
                    e = json.loads(json.dumps(edl))
                    out = {k: v for k, v in (e.get("output") or {}).items() if k not in ("width", "height")}
                    path = out_dir / output_name(i, clip.highlight.get("title", ""), aspect)
                    out.update(aspect=aspect, path=str(path))
                    e["output"] = out
                    if not body.srt:
                        e["captions"] = {**(e.get("captions") or {}), "srt": False}
                    plan.append((hid, aspect, e, path))
        n = len(plan)
        crf = ["--crf", HIGH_CRF] if body.quality == "high" else []
        steps, renders = [], []
        tag = uuid.uuid4().hex[:8]  # queued exports never share (and overwrite) each other's edit files
        for k, (hid, aspect, e, path) in enumerate(plan, 1):
            tmp = wd / f"export-{tag}-{hid}-{aspect.replace(':', 'x')}.edl.json"
            _atomic_write_json(tmp, e)
            stage = f"render {k}/{n}"
            share = 1.0 / n
            steps.append((stage, py_cmd("render.py", str(tmp), *crf), share * (0.9 if body.thumbnail else 1.0)))
            if body.thumbnail:
                path.with_suffix(".jpg").unlink(missing_ok=True)  # an older thumbnail must not hide a new failure
                # its own stage, and the script never fails the job
                steps.append((f"thumb {k}/{n}", py_cmd("thumb_frame.py", str(path), str(path.with_suffix(".jpg"))), share * 0.1))
            renders.append({"stage": stage, "hid": hid, "aspect": aspect, "path": str(path), "tmp": str(tmp),
                            "thumb": body.thumbnail})
        return _job_dict(engine.submit(pid, "export", steps, renders=renders))

    @app.get("/jobs")
    def list_jobs(project: Optional[str] = None, kind: Optional[str] = None):
        """Jobs still queued or running, optionally of one project and kind (a reloaded app asks for these)."""
        return [_job_dict(engine.job_snapshot(j.id)) for j in engine.active_jobs()
                if (project is None or j.project_id == project) and (kind is None or j.kind == kind)]

    @app.get("/jobs/{job_id}")
    def get_job(job_id: str):
        return _job_dict(engine.job_snapshot(job_id))

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
        q: asyncio.Queue = asyncio.Queue(maxsize=WS_QUEUE_MAX)
        client = (asyncio.get_running_loop(), q)
        engine.add_client(client)
        for job in engine.active_jobs():  # a (re)connecting client catches up on what is in flight
            _offer(q, _event(job))

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

    app = create_app(token)  # validates the token before anything binds
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", port))
    real_port = sock.getsockname()[1]
    config = uvicorn.Config(app, log_config=None, access_log=False, log_level="warning", lifespan="on",
                            timeout_graceful_shutdown=3)
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
