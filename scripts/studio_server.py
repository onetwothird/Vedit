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
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402
from starlette.exceptions import HTTPException as StarletteHTTPException  # noqa: E402

import accel  # noqa: E402
import cloud_projects  # noqa: E402
import broll  # noqa: E402
import models  # noqa: E402
import vedit  # noqa: E402
from captions import caption_events  # noqa: E402
from common import ffmpeg_bin, ffprobe, load_json, py_cmd, save_json, workdir_for  # noqa: E402
from edl import Timeline  # noqa: E402
from edl_ops import clip_edl, cut_words, fix_word, kept_ranges, patch_section, restore_words, trim  # noqa: E402
from highlights_llm import HID_RE  # noqa: E402
from studio_jobs import Job, JobQueue  # noqa: E402
import studio_stories as stories  # noqa: E402
from studio_projects import (LANG_MODEL, use_home, Project, ProjectClip, _atomic_write_json, _home, USER_HOME, project_doc,  # noqa: E402
                             create_project, list_recent, load_project, output_dir, output_name, save_project)

ALLOWED_ORIGINS = ("tauri://localhost", "http://tauri.localhost", "http://localhost:5173")
FORMATS = ("9:16", "1:1", "4:5", "16:9")
HIGH_CRF = "15"
ANALYZE_STEPS = (("proxy", 0.1), ("analyze", 0.6), ("highlights", 0.15), ("framing", 0.15))
# vlog / reel projects edit the whole video: no highlight search
WHOLE_STEPS = (("proxy", 0.15), ("analyze", 0.85))
WHOLE_ID = "h01"
PRESET_FOR = {"vlog": "youtube"}  # clip and reel projects use their platform's preset
TERMINAL = ("done", "failed", "cancelled")
WS_QUEUE_MAX = 256
CLOUD_PARALLEL_JOBS = 4  # cloud: jobs of different users run side by side (each user is capped at 2)
OVERLAP_DROP = 0.5  # a new highlight overlapping a kept clip by more than this share of the shorter one is dropped


# ---- request bodies -------------------------------------------------------------------------------------------

class NewProject(BaseModel):
    source: Optional[str] = None  # desktop: a path on this computer
    upload_key: Optional[str] = None  # cloud: a video the visitor uploaded to R2
    language: Literal["en", "tl", "taglish"]
    platform: Literal["reels", "shorts", "tiktok", "youtube"]
    type: Literal["clip", "vlog", "reel"] = "clip"


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


class BrollBody(BaseModel):
    folder: str  # absolute folder of photos and videos to suggest from
    ai: Optional[AIOptions] = None


class KeptBody(BaseModel):
    kept: bool


class ExportBody(BaseModel):
    clips: list[str]
    formats: list[str] = ["9:16"]
    quality: Literal["normal", "high"] = "normal"
    out_dir: Optional[str] = None  # absolute folder for the files; default: "<name> - Vedit" next to the video
    srt: bool = True  # also write the .srt subtitle file
    thumbnail: bool = False  # also save a .jpg frame beside each video
    first_number: int = Field(1, ge=1, le=999)  # the "NN" the first clip's file name starts at (a retry keeps its number)


class TTSKey(BaseModel):
    provider: Literal["openai", "elevenlabs"]
    api_key: str


class BuildStoryBody(BaseModel):
    out_dir: Optional[str] = None  # absolute folder for the video; default: the user's Videos/Vedit folder
    quality: Literal["normal", "high"] = "normal"
    tts: Optional[TTSKey] = None  # cloud: the visitor's premium-voice key, held only in the job's in-memory env


class BatchBody(BaseModel):
    sources: list[str] = Field(min_length=1, max_length=50)
    type: Literal["clip", "vlog", "reel"] = "clip"
    language: Literal["en", "tl", "taglish"]
    platform: Literal["reels", "shorts", "tiktok", "youtube"]
    ai: Optional[AIOptions] = None
    max_clips: Optional[int] = Field(default=None, ge=1, le=60)
    export: bool = True  # export every kept clip as soon as its video is analysed
    formats: list[str] = ["9:16"]
    quality: Literal["normal", "high"] = "normal"
    out_dir: Optional[str] = None
    srt: bool = True
    thumbnail: bool = False


class CloudBatchBody(BatchBody):
    """Cloud: the batch's videos are the visitor's uploads; `sources` and `out_dir` are refused (400), not ignored."""
    sources: Optional[list[str]] = None  # type: ignore[assignment]
    upload_keys: list[str] = Field(default_factory=list, max_length=50)


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


class CloudBrandBody(BaseModel):
    """The cloud brand kit (see cloud_outputs.put_brand): files are `asset:` references of the caller's library."""
    font: str = ""  # a caption font name (the bundled fonts), not a file
    caption_color: str = ""
    highlight: str = ""
    logo: str = ""
    intro: str = ""
    outro: str = ""
    music: str = ""
    lut: str = ""
    fonts: dict[str, str] = Field(default_factory=dict)  # card fonts by weight -> asset reference


def brand_file() -> Path:
    """Where the app's brand kit lives: %LOCALAPPDATA%\vedit\brand.json, else ~/.vedit/brand.json (VEDIT_HOME wins)."""
    if USER_HOME.get() is not None:
        return _home() / "brand.json"
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
    return project_doc(p)


DOCTOR_UID = "00000000-0000-0000-0000-000000000000"  # nobody: /doctor reads its minutes to reach the usage table


def _check(fn) -> bool:
    try:
        return bool(fn())
    except Exception:  # noqa: BLE001
        return False


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


# Files the preview loads with a plain <video> or <img>, which cannot send headers: these GET routes take the token as
# ?token= instead (like /events). Each serves only what a project already uses, never an arbitrary path.
MEDIA_ROUTE = re.compile(r"^/(projects/[^/]+/(media|filmstrip\.jpg)|file)$")
MEDIA_EXT = {".mp4": "video/mp4", ".mov": "video/quicktime", ".m4v": "video/mp4", ".webm": "video/webm",
             ".mkv": "video/x-matroska", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
             ".webp": "image/webp"}


def _media_response(path: Path) -> FileResponse:
    """The file with its media type, answering Range requests so the player can seek."""
    if not path.is_file():
        raise NotFound(path.name)
    return FileResponse(path, media_type=MEDIA_EXT.get(path.suffix.lower(), "application/octet-stream"),
                        headers={"Cache-Control": "no-store"})


def _same_file(a: Path, b: Path) -> bool:
    return os.path.normcase(str(a.resolve())) == os.path.normcase(str(b.resolve()))


def _used_broll(path: Path, projects: list[Project]) -> Path | None:
    """`path` when a clip of one of these projects shows it as b-roll (relative paths are the workdir's)."""
    if path.suffix.lower() not in MEDIA_EXT:
        return None
    for p in projects:
        wd = workdir_for(p.source)
        for c in p.clips:
            try:
                edl = load_json(wd / c.edl_file)
            except (OSError, ValueError):
                continue
            for o in edl.get("overlays") or []:
                if isinstance(o, dict) and o.get("type") in ("image", "video") and isinstance(o.get("path"), str):
                    used = Path(o["path"]) if Path(o["path"]).is_absolute() else wd / o["path"]
                    if _same_file(used, path if path.is_absolute() else wd / path):
                        return used
    return None


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

    def __init__(self, token: str, job_queue=None, scoped: bool = False, cloud=None):
        self.token = token
        self.cloud = cloud  # cloud only: failed jobs give their minutes back through cloud.usage
        self.scoped = scoped  # cloud: jobs and their events belong to the user (home) that submitted them
        self._events: queue.Queue[Optional[Job]] = queue.Queue()
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._meta_lock = threading.Lock()  # guards the overlay, export plans and latest snapshots
        self._exports: dict[str, list[dict]] = {}
        self._overlay: dict[str, dict] = {}
        self._finalized: set[str] = set()
        self._jobs: dict[str, Job] = {}
        self._clients: dict[tuple[asyncio.AbstractEventLoop, asyncio.Queue], Path | None] = {}
        self._clients_lock = threading.Lock()
        self._closed = False
        # cloud only (see cloud_projects / cloud_outputs): job ids this process charged (only those are refunded),
        # the files a job published to R2, the batches running now, the server functions the cloud code calls
        self.charged: set[str] = set()
        self.charged_seconds: dict[str, float] = {}  # charged, not yet submitted: then it goes in the job's file
        self.published: dict[str, list[dict]] = {}
        self.batch_runs: dict[str, dict] = {}
        self.cloud_hooks = None
        self._recover_interrupted()
        if job_queue is not None:
            self.queue = job_queue
        elif cloud is not None and cloud.runner_factory is not None:  # cloud: jobs are also written to their files
            self.queue = JobQueue(self.on_event, max_parallel=CLOUD_PARALLEL_JOBS, runner=cloud.runner_factory(),
                                  persist=True)
        elif cloud is not None:
            self.queue = JobQueue(self.on_event, persist=True)
        else:
            self.queue = JobQueue(self.on_event)
        self._dispatcher = threading.Thread(target=self._dispatch, name="vedit-dispatch", daemon=True)
        self._dispatcher.start()
        if cloud is not None:
            self._recover_cloud()

    def _recover_cloud(self) -> None:
        """After an API restart: batches that were running end, and the jobs the last container left unfinished
        are followed again or failed (refunded when they were charged; their charge is in their file)."""
        import cloud_outputs

        users = self.cloud.data_root / "users"
        try:
            cloud_outputs.recover_batches(users)
        except Exception as e:  # noqa: BLE001 - a bad batch file must never stop the engine
            print(f"vedit: could not recover batches: {e}", file=sys.stderr)
        recover = getattr(self.queue, "recover", None)
        if not callable(recover):
            return
        for row in recover(users):
            seconds = row["meta"].get("charged_seconds")
            if isinstance(seconds, (int, float)):
                with self._meta_lock:
                    self.charged.add(row["id"])
        self.queue.start_recovered()

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
        for doc in stories.list_stories(limit=sys.maxsize):
            job = doc.get("last_job")
            if isinstance(job, dict) and job.get("state") in ("running", "queued"):
                doc["last_job"] = {**job, "state": "failed", "error": "interrupted"}
                doc.pop("building", None)
                try:
                    stories.save_story_doc(doc)
                except OSError as e:
                    print(f"vedit: could not mark story {doc['id']} interrupted: {e}", file=sys.stderr)

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

    def submit(self, project_id: str, kind: str, steps: list, renders: list[dict] | None = None,
               job_id: str | None = None) -> Job:
        with self._meta_lock:  # held so no event of this job is processed before its plan is registered
            extra: dict = {'job_id': job_id} if job_id else {}
            if job_id in self.charged_seconds:  # cloud: written in the job's file with it
                extra["meta"] = {"charged_seconds": self.charged_seconds.pop(job_id)}
            job = self.queue.submit(project_id, kind, steps, **extra)
            # visible to active_jobs() at once: the dispatcher only learns of the job later, and the cloud per-user
            # checks must not miss a job submitted a moment ago (the dispatcher's later snapshots replace this one)
            self._jobs[job.id] = dataclasses.replace(job)
            if renders is not None:
                self._exports[job.id] = renders
                self._overlay[job.id] = {"result": {"renders": _public_renders(renders), "files": []}}
            return self._overlaid(job)

    def _owner(self, job_id: str) -> Path | None:
        return self.queue.owner(job_id)

    def job_snapshot(self, job_id: str) -> Job:
        try:
            job = self.queue.get(job_id)
            if self.scoped and self._owner(job_id) != USER_HOME.get():
                raise KeyError(job_id)
        except KeyError:
            raise NotFound(f"job {job_id}") from None
        with self._meta_lock:
            return self._overlaid(job)

    def active_jobs(self) -> list[Job]:
        mine = USER_HOME.get()
        with self._meta_lock:
            return [dataclasses.replace(j) for j in self._jobs.values()
                    if j.state not in TERMINAL and (not self.scoped or self._owner(j.id) == mine)]

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
                            owner = self._owner(j.id) if self.scoped else None
                            if owner is not None:  # the dispatcher thread works as the job's owner
                                with use_home(owner):
                                    self._handle(j)
                            else:
                                self._handle(j)
                        else:  # superseded within the burst: still sent, never written to disk
                            with self._meta_lock:
                                j = self._overlaid(j)
                            self._broadcast(_event(j), j.id)
                    except Exception as e:  # noqa: BLE001 - the dispatcher must survive anything
                        print(f"vedit: event handling failed for job {j.id}: {e}", file=sys.stderr)
            finally:
                for _ in batch:
                    self._events.task_done()
            if any(j is None for j in batch):
                return

    def _take_published(self, job_id: str) -> list[dict]:
        with self._meta_lock:
            return self.published.pop(job_id, [])

    def _handle(self, raw: Job) -> None:
        finished_now = False
        with self.lock_for(raw.project_id):
            try:
                p = load_project(raw.project_id)
            except (KeyError, ValueError, OSError):
                p = None
            if raw.state in TERMINAL and raw.id not in self._finalized:
                self._finalized.add(raw.id)
                finished_now = True
                try:
                    if raw.kind == "analyze" and raw.state == "done" and p is not None:
                        self._set_overlay(raw.id, result=self._finish_analyze(p))
                    elif raw.kind == "export":
                        result = self._finish_export(raw)
                        if self.cloud is not None:  # the downloads are the files the job published to R2
                            result["files"] = self._take_published(raw.id)
                        self._set_overlay(raw.id, result=result)
                    elif raw.kind == "story":
                        self._set_overlay(raw.id, result={"files": self._take_published(raw.id)}
                                          if self.cloud is not None else self._finish_story(raw))
                    elif raw.kind == "zip":
                        self._set_overlay(raw.id, result={"files": self._take_published(raw.id)})
                except Exception as e:  # noqa: BLE001 - post-processing failure fails the job, not the server
                    self._set_overlay(raw.id, state="failed", error=f"post-processing failed: {e}")
                if self.cloud is not None:  # a failed job (even one that failed after its steps) gives its minutes back
                    with self._meta_lock:
                        final = self._overlaid(raw).state
                    if final == "failed":
                        cloud_projects.refund_charged(self, self.cloud, raw.id)
            with self._meta_lock:
                job = self._overlaid(raw)
                self._jobs[job.id] = job
            if p is not None:
                p.last_job = _job_dict(job)
                auto = p.settings.get("auto_export")
                if raw.kind == "analyze" and job.state == "done" and isinstance(auto, dict) and not auto.get("job") \
                        and self.cloud is None:  # cloud: the batch runner exports (cloud_outputs.after_job)
                    self._auto_export(p, auto)
                save_project(p)
            elif raw.kind == "story":
                self._record_story_job(job)
            if finished_now and self.cloud is not None:  # last: its file answers with the result after a restart,
                finalize = getattr(self.queue, "finalize", None)  # and a restart before this replays the end
                if callable(finalize):
                    finalize(job.id, _job_dict(job))
        self._broadcast(_event(job), job.id)
        if finished_now and self.cloud is not None:  # outside the project lock: it may submit a batch's next job
            import cloud_outputs

            cloud_outputs.after_job(self, job)

    def _auto_export(self, p: Project, auto: dict) -> None:
        """A batch video: export its kept clips right after analysis. A failure is kept on the project."""
        kept = [c.highlight["id"] for c in p.clips if c.kept]
        if not kept:
            auto["error"] = "no clips were found in this video"
            return
        body = ExportBody(clips=kept, formats=auto.get("formats") or ["9:16"], quality=auto.get("quality", "normal"),
                          out_dir=auto.get("out_dir"), srt=auto.get("srt", True), thumbnail=auto.get("thumbnail", False))
        try:
            job = submit_export(self, p, body)
        except (ValueError, NotFound, OSError) as e:
            auto["error"] = str(e)
            return
        auto["job"] = job.id

    def _finish_story(self, job: Job) -> dict:
        try:
            doc = stories.load_story_doc(job.project_id)
        except KeyError:
            return {"files": []}
        out = doc.get("building") or doc.get("output")
        files = [out] if job.state == "done" and out and Path(out).is_file() else []
        return {"files": files}

    def _record_story_job(self, job: Job) -> None:
        try:
            doc = stories.load_story_doc(job.project_id)
        except KeyError:
            return
        doc["last_job"] = _job_dict(job)
        if job.state == "done" and doc.get("building"):
            doc["output"] = doc.pop("building")
        elif job.state in TERMINAL:
            doc.pop("building", None)
        stories.save_story_doc(doc)

    def _finish_analyze(self, p: Project) -> dict:
        """Add clips for new highlights. Existing clips whose edit file exists are never touched."""
        if p.type != "clip":
            return self._finish_whole(p)
        wd = workdir_for(p.source)
        data = load_json(wd / "highlights.json")
        language = LANG_MODEL[p.language][0]
        preserved = [c for c in p.clips if _valid_hid(c.highlight.get("id")) and (wd / c.edl_file).is_file()]
        used = {c.highlight["id"] for c in preserved}
        added = []
        brand = self._brand_file()
        for h in data.get("highlights") or []:
            hid = h.get("id")
            if not _valid_hid(hid):
                print(f"vedit: skipping highlight with an unsafe id: {hid!r}", file=sys.stderr)
                continue
            if any(_overlap_share(h, c.highlight) > OVERLAP_DROP for c in preserved):
                continue
            try:
                edl = clip_edl(wd, h, p.platform, language, brand)
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
        self._set_media(p, wd)
        return {"clips": len(p.clips), "new": len(added), "reason": data.get("reason")}

    def _brand_file(self) -> Path | None:
        """The brand kit edits are built with. Cloud: its asset references resolved to this user's files now."""
        if self.cloud is not None:
            import cloud_outputs

            return cloud_outputs.resolved_brand_file()
        return usable_brand_file()

    @staticmethod
    def _set_media(p: Project, wd: Path) -> None:
        proxy = wd / "proxy.mp4"
        try:
            has_proxy = proxy.is_file() and load_json(wd / "proxy.json").get("src") == str(Path(p.source).resolve())
        except (OSError, ValueError):
            has_proxy = False
        p.settings.update(workdir=str(wd), proxy=str(proxy if has_proxy else Path(p.source)),
                          filmstrip=str(wd / "filmstrip.json"))

    def _finish_whole(self, p: Project) -> dict:
        """A vlog or reel project: one edit of the whole video, with the preset's cuts. An edit that already
        exists is kept, so analysing again never throws away someone's work."""
        wd = workdir_for(p.source)
        name = f"{p.type}-{WHOLE_ID}.edl.json"
        existing = [c for c in p.clips if c.edl_file == name and (wd / name).is_file()]
        if existing:
            p.clips = existing
            self._set_media(p, wd)
            return {"clips": 1, "new": 0, "reason": None}
        h = whole_highlight(wd, p.source)
        language = LANG_MODEL[p.language][0]
        try:
            edl = clip_edl(wd, h, PRESET_FOR.get(p.type, p.platform), language, self._brand_file())
        except ValueError:
            raise ValueError("no speech was found in this video, so there is nothing to edit") from None
        if not edl["output"].get("aspect"):
            # a vlog keeps the video's own shape; saying it outright lets the Edit screen show the same shape
            edl["output"]["aspect"] = source_aspect(p.source)
        _atomic_write_json(wd / name, edl)
        p.clips = [ProjectClip(highlight=h, kept=True, edl_file=name)]
        self._set_media(p, wd)
        return {"clips": 1, "new": 1, "reason": None}

    def _finish_export(self, job: Job) -> dict:
        """Finished files (+ .srt) and, on failure, which render failed. Always removes the temp edits."""
        with self._meta_lock:
            plan = self._exports.pop(job.id, [])
        by_stage = {}
        for i, e in enumerate(plan):
            by_stage[e["stage"]] = i
            by_stage[e["stage"].replace("render ", "thumb ")] = i
        if job.state == "done" or job.stage == "publish":  # cloud: every render finished before the upload failed
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
            self._clients[client] = USER_HOME.get()

    def remove_client(self, client) -> None:
        with self._clients_lock:
            self._clients.pop(client, None)

    def _broadcast(self, msg: dict, job_id: str | None = None) -> None:
        """Send to every client; when scoped, a job's events go only to the clients of its owner."""
        owner = self._owner(job_id) if self.scoped and job_id is not None else None
        with self._clients_lock:
            clients = [c for c, home in self._clients.items() if not self.scoped or job_id is None or home == owner]
        for loop, q in clients:
            try:
                loop.call_soon_threadsafe(_offer, q, msg)
            except RuntimeError:  # loop already closed
                self.remove_client((loop, q))


def source_aspect(source: str) -> str:
    """16:9 for a landscape (or square-ish wide) video, 9:16 for a portrait one."""
    try:
        v = next(s for s in cached_probe(source).get("streams", []) if s.get("codec_type") == "video")
        w, h = int(v["width"]), int(v["height"])
        if abs(int(v.get("tags", {}).get("rotate", 0) or 0)) in (90, 270):
            w, h = h, w
    except (StopIteration, KeyError, ValueError, TypeError, OSError):
        return "16:9"
    return "9:16" if h > w else "16:9"


def whole_highlight(wd: Path, source: str) -> dict:
    """The whole video as one "highlight": every word, titled after the file."""
    tp = Path(wd) / "transcript.main.json"
    words = load_json(tp).get("words", []) if tp.is_file() else []
    if words:
        start, end = float(words[0]["start"]), float(words[-1]["end"])
    else:
        start, end = 0.0, float(cached_probe(source).get("format", {}).get("duration") or 0)
    return {"id": WHOLE_ID, "start": round(start, 3), "end": round(end, 3), "w0": 0, "w1": max(len(words) - 1, 0),
            "score": 100, "title": Path(source).stem[:60] or "Video", "features": {}}


def export_seconds(edl_path: Path) -> float:
    """Output length of one planned render (cloud charges the sum over an export)."""
    return float(_timeline(edl_path).duration)


def _check_export_body(body: ExportBody) -> None:
    if not body.clips or not body.formats:
        raise ValueError("export needs at least one clip and one format")
    bad = [f for f in body.formats if f not in FORMATS]
    if bad:
        raise ValueError(f"unsupported formats: {bad}")
    if len(set(body.clips)) != len(body.clips) or len(set(body.formats)) != len(body.formats):
        raise ValueError("duplicate clips or formats in export")


def submit_export(engine: "Engine", p: Project, body: ExportBody) -> Job:
    """Plan and queue the renders of an export. The caller holds the project's lock."""
    _check_export_body(body)  # before out_dir is created
    out_dir = output_dir(Path(p.source))
    if body.out_dir:
        out_dir = _safe_out_dir(body.out_dir)
    steps, renders = plan_export(p, body, out_dir)
    return engine.submit(p.id, "export", steps, renders=renders)


def plan_export(p: Project, body: ExportBody, out_dir: Path, name=None, check=None) -> tuple[list, list[dict]]:
    """The render steps of an export and their plan; writes each render's edit file. `name` maps the output file
    name and `check(edl, workdir)` vets each planned edit before anything is written (cloud). The caller holds the
    project's lock."""
    _check_export_body(body)
    wd = workdir_for(p.source)
    plan = []
    for i, hid in enumerate(body.clips, body.first_number):
        clip = _find_clip(p, hid)
        if not _valid_hid(hid):
            raise ValueError(f"unsafe clip id {hid!r}")
        edl = load_json(_edl_path(wd, clip))
        for aspect in body.formats:
            e = json.loads(json.dumps(edl))
            out = {k: v for k, v in (e.get("output") or {}).items() if k not in ("width", "height")}
            file = output_name(i, clip.highlight.get("title", ""), aspect)
            path = out_dir / (name(file) if name is not None else file)
            out.update(aspect=aspect, path=str(path))
            e["output"] = out
            if not body.srt:
                e["captions"] = {**(e.get("captions") or {}), "srt": False}
            if check is not None:
                check(e, wd)
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
    return steps, renders


def analyze_steps(p: Project, body: Optional[AnalyzeBody]) -> list:
    """The steps of analysing a project (the desktop route adds the model download step when it is needed)."""
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
    stages = ANALYZE_STEPS if p.type == "clip" else WHOLE_STEPS
    return [(stage, argv[stage], share) + ((step_env[stage],) if stage in step_env else ())
            for stage, share in stages]


def _load_story(sid: str) -> dict:
    try:
        return stories.load_story_doc(sid)
    except KeyError:
        raise NotFound(f"story {sid}") from None


def default_story_dir() -> Path:
    """Videos/Vedit in the user's home (Windows keeps Videos there too)."""
    return Path.home() / "Videos" / "Vedit"


def _batch_path(bid: str) -> Path:
    if not re.fullmatch(r"b[0-9a-f]{12}", bid or ""):
        raise NotFound(f"batch {bid}")
    return _home() / "batches" / f"{bid}.json"


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
    tl = _timeline(edl_path)
    clips = [c for c in tl.clips if not c.get("_bumper")]
    # the zoom each clip is drawn at (automatic punch-ins capped where the picture would soften) and how far a zoom
    # can go on this video before it does: the preview frames clips exactly like the export
    return {"edl": edl, "kept_ranges": [list(r) for r in kept_ranges(edl, tr)],
            "captions": caption_events(tl),
            "zooms": [c["zoom"] if isinstance(c.get("zoom"), (int, float)) else 1 for c in clips],
            "sharp_zoom": round(tl.sharp_zoom(tl.ref), 3)}


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

def create_app(token: str | None, queue: JobQueue | None = None, cloud=None) -> FastAPI:
    """Desktop: one bearer token. Cloud (a cloud_config.CloudConfig): per-user Supabase JWTs, the token is ignored."""
    if cloud is None and (not isinstance(token, str) or not token.strip()):
        raise ValueError("the engine needs a non-empty bearer token")
    token = token if isinstance(token, str) else ""
    engine = Engine(token, queue, scoped=cloud is not None, cloud=cloud)
    if cloud is not None:
        import cloud_assets  # lazy: only the cloud needs these
        import cloud_outputs

        cloud_outputs.attach(engine, cloud, project=_project, story=_load_story, batch_path=_batch_path,
                             plan_export=plan_export, export_seconds=lambda path: export_seconds(path),
                             probe=lambda f: cached_probe(f), analyze_steps=analyze_steps, ExportBody=ExportBody,
                             AnalyzeBody=AnalyzeBody, not_found=NotFound, job_dict=_job_dict)
    allowed_origins = tuple(cloud.origins) if cloud is not None else ALLOWED_ORIGINS

    def user_home(jwt_token: str | None) -> Path:
        import cloud_auth  # lazy: needs the cloud extra

        return cloud.data_root / "users" / cloud_auth.user_id(jwt_token or "", cloud)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        await asyncio.to_thread(engine.close)

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.engine = engine

    def origin_ok(origin: str | None) -> bool:
        return origin is None or origin in allowed_origins

    def token_ok(given: str | None) -> bool:
        return given is not None and hmac.compare_digest(given.encode("utf-8"), token.encode("utf-8"))

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if not origin_ok(request.headers.get("origin")):
            return _error(403, "forbidden", "origin not allowed")
        auth = request.headers.get("authorization") or ""
        if request.method in ("GET", "HEAD") and MEDIA_ROUTE.match(request.url.path) and not auth:
            auth = "Bearer " + (request.query_params.get("token") or "")
        given = auth[len("Bearer "):] if auth.startswith("Bearer ") else None
        if cloud is not None:
            if request.url.path == "/health":
                return await call_next(request)
            try:
                home = await asyncio.to_thread(user_home, given)
            except PermissionError:
                return _error(401, "unauthorized", "missing or wrong token")
            with use_home(home):  # call_next's endpoint task copies this context
                return await call_next(request)
        if not token_ok(given):
            return _error(401, "unauthorized", "missing or wrong token")
        return await call_next(request)

    # added after guard, so it wraps it: preflights are answered here and errors still carry CORS headers
    app.add_middleware(CORSMiddleware, allow_origins=list(allowed_origins), allow_methods=["*"],
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
        if cloud is not None:  # each a cheap reachability check; any error is False
            return {"cloud": True, "gpu_function": _check(lambda: getattr(engine.queue._runner, "ping")()),
                    "storage": _check(lambda: [cloud.storage.size("health/doctor")]),  # None (absent) is fine
                    "usage": _check(lambda: cloud.usage.remaining(DOCTOR_UID) is not None)}
        ffmpeg, libass = _ffmpeg_status()
        return {"ffmpeg": ffmpeg, "libass": libass, "models": models.status(), "gpu": accel.gpu_ready()}

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

    if cloud is not None:  # registered first, so they answer /brand in cloud mode
        @app.get("/brand")
        def get_cloud_brand():
            return cloud_outputs.brand_view(read_brand())

        @app.put("/brand")
        def put_cloud_brand(body: CloudBrandBody):
            """Every file of the kit is an asset reference of the caller's library, checked before anything is saved."""
            return cloud_outputs.put_brand(body, read_brand(), brand_file())

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
        if cloud is not None:  # the visitor's uploaded video; a path on the server is never read
            if body.source is not None or not body.upload_key:
                raise ValueError("Upload a video to start a project")
            return _project_dict(cloud_projects.create_from_upload(
                cloud, body.upload_key, body.language, body.platform, body.type, lambda f: cached_probe(f)))
        if not body.source:
            raise ValueError("source is required")
        src = Path(body.source)
        if not src.is_file():
            raise ValueError(f"source video not found: {body.source}")
        return _project_dict(create_project(src, body.language, body.platform, body.type))

    @app.get("/projects/{pid}")
    def get_project(pid: str):
        return _project_dict(_project(pid))

    @app.post("/projects/{pid}/analyze")
    def analyze(pid: str, body: Optional[AnalyzeBody] = None):
        p = _project(pid)
        model = LANG_MODEL[p.language][1]
        steps = analyze_steps(p, body)
        if cloud is not None:  # models live on the Volume and the step runs on a GPU: no model step, no CPU stand-in
            return _job_dict(cloud_projects.start_analyze(engine, cloud, p, steps, lambda f: cached_probe(f)))
        # the model this computer will run: without a GPU, the faster stand-in (see accel.speech_plan)
        need = model if accel.gpu_ready() else accel.CPU_SWAP.get(model, model)
        if not models.status()[need]["installed"]:  # fetch the speech model first, as 30% of the job
            steps = [("model", py_cmd("models.py", "download", need), 0.3)] +                     [(st, argv_, share * 0.7, *rest) for st, argv_, share, *rest in steps]
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

    @app.get("/projects/{pid}/media")
    def media(pid: str):
        """What the player plays: the proxy analysis made, else the source video."""
        p = _project(pid)
        return _media_response(Path(p.settings.get("proxy") or p.source))

    @app.get("/projects/{pid}/filmstrip.jpg")
    def filmstrip_image(pid: str):
        return _media_response(workdir_for(_project(pid).source) / "filmstrip.jpg")

    @app.get("/file")
    def broll_file(path: str):
        """A b-roll photo or video, only when a recent project's clip already shows it."""
        used = _used_broll(Path(path), list_recent(50))
        if used is None:
            raise NotFound("file")
        return _media_response(used)

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
            if cloud is not None:
                cloud_projects.touch(pid)
            return view

    @app.post("/projects/{pid}/clips/{hid}/broll")
    def suggest_broll(pid: str, hid: str, body: BrollBody):
        """B-roll for the moments the clip names a thing or place, from the photos and videos in `folder`.
        Suggests only: the app places the ones the user accepts through the overlays patch."""
        folder = Path(body.folder.strip())
        if not folder.is_absolute() or not folder.is_dir():
            raise ValueError("folder must be an existing absolute folder")
        p = _project(pid)
        wd = workdir_for(p.source)
        edl = load_json(_edl_path(wd, _find_clip(p, hid)))
        tp = wd / "transcript.main.json"
        if not tp.is_file():
            raise NotFound("transcript")
        tr = load_json(tp)
        ai = {"provider": body.ai.provider, "api_key": body.ai.api_key.strip(), "model": body.ai.model} if body.ai else None
        return broll.suggest(tr, kept_ranges(edl, tr), folder, overlays=edl.get("overlays") or [], ai=ai)

    @app.put("/projects/{pid}/clips/{hid}/kept")
    def set_kept(pid: str, hid: str, body: KeptBody):
        with engine.lock_for(pid):
            p = _project(pid)
            _find_clip(p, hid).kept = body.kept
            if cloud is not None:
                p.last_activity = cloud_projects.now_iso()
            save_project(p)
            return _project_dict(p)

    @app.post("/projects/{pid}/export")
    def export(pid: str, body: ExportBody):
        if cloud is not None:  # rendered on the volume, published to R2, charged its output length
            return _job_dict(cloud_outputs.start_export(engine, cloud, pid, body))
        with engine.lock_for(pid):
            p = _project(pid)
            return _job_dict(submit_export(engine, p, body))

    if cloud is not None:
        @app.get("/projects/{pid}/exports")
        def list_exports(pid: str):
            """This project's exported files with fresh 24 h download links."""
            return cloud_outputs.list_exports(cloud, pid)

    # ---- stories: photos or clips + a script -> a narrated video
    _story = _load_story

    def _story_dict(doc: dict) -> dict:
        return {k: v for k, v in doc.items() if k != "building"}

    @app.get("/stories")
    def list_stories():
        return [_story_dict(d) for d in stories.list_stories()]

    @app.get("/stories/voices")
    def story_voices():
        return [{"id": k, "name": v} for k, v in stories.VOICES.items()]

    @app.post("/stories")
    def new_story(body: dict = Body(default={})):
        title = body.get("title", "") if isinstance(body, dict) else ""
        if not isinstance(title, str) or len(title) > stories.MAX_TITLE:
            raise ValueError(f"the title can be at most {stories.MAX_TITLE} characters")
        return _story_dict(stories.create_story(title))

    @app.get("/stories/{sid}")
    def get_story(sid: str):
        return _story_dict(_story(sid))

    @app.put("/stories/{sid}")
    def put_story(sid: str, body: dict = Body(...)):
        with engine.lock_for(sid):
            old = _story(sid)
            doc = stories.clean_doc(sid, body, old, cloud_assets.resolve if cloud is not None else None)
            stories.save_story_doc(doc)
            return _story_dict(doc)

    @app.get("/stories/{sid}/file")
    def story_file(sid: str, path: str):
        """A photo, clip or music file, only when this story uses it."""
        doc = _story(sid)
        if path not in stories.story_media(doc):
            raise NotFound("file")
        if cloud is not None:  # an asset reference of this user; a plain path is never read
            return _media_response(cloud_assets.resolve(path))
        return _media_response(Path(path))

    if cloud is not None:
        @app.get("/stories/{sid}/exports")
        def list_story_exports(sid: str):
            """This story's built videos with fresh 24 h download links."""
            return cloud_outputs.list_story_exports(cloud, sid)

    @app.post("/stories/{sid}/build")
    def build_story(sid: str, body: Optional[BuildStoryBody] = None):
        body = body or BuildStoryBody()
        if cloud is not None:
            return _job_dict(cloud_outputs.start_story(engine, cloud, sid, body))
        with engine.lock_for(sid):
            doc = _story(sid)
            stories.check_buildable(doc)
            out_dir = _safe_out_dir(body.out_dir) if body.out_dir else default_story_dir()
            out_dir.mkdir(parents=True, exist_ok=True)
            out = stories.output_file(doc, out_dir)
            path = stories.write_story_json(doc, out)
            # voices first (needs the internet once; cached after), then the render (its own progress)
            steps = [("voices", py_cmd("story.py", "voices", str(path)), 0.25),
                     ("render", py_cmd("story.py", "build", str(path)), 0.75)]
            job = engine.submit(sid, "story", steps)
            doc["building"] = str(out)
            doc["last_job"] = _job_dict(job)
            stories.save_story_doc(doc)
            return _job_dict(job)

    # ---- batch: many videos, analysed (and exported) one after another
    def _batch_view(bid: str) -> dict:
        path = _batch_path(bid)
        if not path.is_file():
            raise NotFound(f"batch {bid}")
        data = load_json(path)
        items = []
        for it in data.get("items", []):
            try:
                p = load_project(it["project"])
            except (KeyError, ValueError, OSError):
                items.append({**it, "project": None, "export": None, "error": "the project is gone"})
                continue
            auto = p.settings.get("auto_export") if isinstance(p.settings.get("auto_export"), dict) else {}
            items.append({**it, "project": _project_dict(p), "export": auto.get("job"),
                          "error": auto.get("error") or it.get("error")})
        return {**data, "items": items}

    if cloud is not None:  # registered first, so they answer these paths in cloud mode
        @app.post("/batches")
        def new_cloud_batch(body: CloudBatchBody):
            return _batch_view(cloud_outputs.start_batch(engine, cloud, body))

        @app.post("/batches/{bid}/zip")
        def zip_batch(bid: str):
            """Every exported file of the batch in one zip, built in a job; its result is a 24 h link."""
            return _job_dict(cloud_outputs.start_zip(engine, cloud, bid))

    @app.post("/batches")
    def new_batch(body: BatchBody):
        if body.platform not in {"clip": ("reels", "shorts", "tiktok"), "reel": ("reels", "shorts", "tiktok"),
                                 "vlog": ("youtube",)}[body.type]:
            raise ValueError(f"unsupported platform for a {body.type} project: {body.platform}")
        if len(set(body.sources)) != len(body.sources):
            raise ValueError("the same video is in the batch twice")
        bad = [f for f in body.formats if f not in FORMATS]
        if body.export and (bad or not body.formats):
            raise ValueError(f"unsupported formats: {bad}" if bad else "pick at least one format")
        missing = [s for s in body.sources if not Path(s).is_file()]
        if missing:
            raise ValueError(f"video not found: {Path(missing[0]).name}")
        if body.out_dir:
            _safe_out_dir(body.out_dir)
        analyze_body = AnalyzeBody(ai=body.ai, max_clips=body.max_clips)
        items = []
        for src in body.sources:
            p = create_project(Path(src), body.language, body.platform, body.type)
            with engine.lock_for(p.id):
                p = _project(p.id)
                if body.export:
                    p.settings["auto_export"] = {"formats": body.formats, "quality": body.quality,
                                                 "out_dir": body.out_dir, "srt": body.srt,
                                                 "thumbnail": body.thumbnail}
                else:
                    p.settings.pop("auto_export", None)
                save_project(p)
            job = analyze(p.id, analyze_body)
            items.append({"project": p.id, "source": p.source, "analyze": job["id"]})
        bid = "b" + uuid.uuid4().hex[:12]
        _atomic_write_json(_batch_path(bid), {"id": bid, "created": stories._now(), "type": body.type,
                                              "export": body.export, "items": items})
        return _batch_view(bid)

    @app.get("/batches/{bid}")
    def get_batch(bid: str):
        return _batch_view(bid)

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
            engine.job_snapshot(job_id)  # another user's job is not found either
            engine.queue.cancel(job_id)
        except (KeyError, NotFound):
            raise NotFound(f"job {job_id}") from None
        return _job_dict(engine.job_snapshot(job_id))

    @app.websocket("/events")
    async def events(ws: WebSocket):
        if not origin_ok(ws.headers.get("origin")):
            await ws.close(code=1008)
            return
        if cloud is not None:
            try:
                home = await asyncio.to_thread(user_home, ws.query_params.get("token"))
            except PermissionError:
                await ws.close(code=1008)
                return
            with use_home(home):
                await _serve_events(ws)
            return
        if not token_ok(ws.query_params.get("token")):
            await ws.close(code=1008)
            return
        await _serve_events(ws)

    async def _serve_events(ws: WebSocket):
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

    if cloud is not None:
        app.include_router(cloud_assets.build_router(cloud))

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
