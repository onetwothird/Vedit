"""Cloud mode projects: created from an upload, analysed against the monthly limit, refunded when a job fails.

Everything here runs inside the request's `use_home(<data_root>/users/<uid>)`, so `_home()` is the caller's home and
another user's ids are simply not found.
"""
from __future__ import annotations

import math
import secrets
import shutil
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import cloud_assets
from cloud_usage import Busy, LimitReached
from starlette.exceptions import HTTPException as StarletteHTTPException
from studio_projects import (PLATFORM_FOR, TYPES, Project, _home, create_project, load_project, save_project,
                            LANG_MODEL)

MAX_VIDEO_SECONDS = 3600.0
MAX_ACTIVE_JOBS = 2
BUSY_COPY = "Vedit is busy, try again later"
NOT_A_VIDEO = "This file is not a video"

_user_locks: dict[str, threading.RLock] = {}
_user_locks_guard = threading.Lock()
_runs_lock = threading.Lock()  # guards engine.batch_runs (see cloud_outputs)


def _lock_for(uid: str) -> threading.RLock:
    with _user_locks_guard:
        return _user_locks.setdefault(uid, threading.RLock())  # re-entrant: a batch submits its first item under it


def uid() -> str:
    return _home().name


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class _Rejected(Exception):
    """The upload is unusable: its object, record and folder are all deleted and the visitor is told why."""


def video_seconds(probe: Callable[[Path], dict], path: Path) -> float:
    """Duration of a video file; ValueError("This file is not a video") for anything else."""
    try:
        info = probe(path)
        if info.get("has_video") is False or ("streams" in info and not any(
                s.get("codec_type") == "video" for s in info["streams"])):
            raise ValueError(NOT_A_VIDEO)
        seconds = float(info.get("duration") or (info.get("format") or {}).get("duration") or 0)
    except (Exception, SystemExit):  # noqa: BLE001 - ffprobe fails in many ways (it may even exit)
        raise ValueError(NOT_A_VIDEO) from None
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError(NOT_A_VIDEO)
    return seconds


def check_duration(seconds: float, remaining: float) -> None:
    if seconds > MAX_VIDEO_SECONDS:
        raise ValueError("Videos can be up to 60 minutes")
    if seconds > remaining:
        raise ValueError(f"This video is {math.ceil(seconds / 60)} min; you have {math.floor(remaining / 60)} min "
                         "left this month")


def touch(pid: str) -> None:
    """Stamp the project's last_activity (retention counts from it). The caller holds the project's lock."""
    p = load_project(pid)
    p.last_activity = now_iso()
    save_project(p)


def create_from_upload(cloud, upload_key: str, language: str, platform: str, kind: str,
                       probe: Callable[[Path], dict]) -> Project:
    """A project from a video the visitor already uploaded to R2. Nothing is charged here."""
    if language not in LANG_MODEL or kind not in TYPES or platform not in PLATFORM_FOR[kind]:
        raise ValueError(f"unsupported language, type or platform: {language!r}, {kind!r}, {platform!r}")
    storage = cloud.storage
    with cloud_assets.claim_pending(uid(), upload_key) as (_upload_id, pending, pending_file):
        if pending.get("kind") != "video":
            raise cloud_assets._not_found()
        folder = _home() / "work" / secrets.token_hex(8)

        def reject_all() -> None:
            storage.delete(upload_key)
            pending_file.unlink(missing_ok=True)

        try:
            remote = storage.size(upload_key)
            if remote is None:
                raise ValueError("Upload not finished")  # record kept: the client can retry
            if remote != pending["size"]:
                reject_all()
                raise ValueError("Upload size does not match what was declared; upload it again")
            ext = Path(str(pending["name"])).suffix.lower()
            source = folder / f"source{ext}"
            folder.mkdir(parents=True)
            part = folder / f"source{ext}.part"
            storage.download(upload_key, part)
            part.replace(source)
            try:
                seconds = video_seconds(probe, source)
                check_duration(seconds, cloud.usage.remaining(uid()))
            except ValueError:
                reject_all()
                raise
            project = create_project(source, language, platform, kind)
            project.last_activity = now_iso()
            save_project(project)
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)  # a half-made project folder is never kept
            raise
        storage.delete(upload_key)
        pending_file.unlink(missing_ok=True)  # before the claim is released
        return project


def user_lock() -> threading.RLock:
    """The caller's lock: guard checks, the charge and the submit of one user's jobs happen one at a time.
    Lock order: this lock first, then a project's (engine.lock_for); never the other way round."""
    return _lock_for(uid())


def active_count(engine) -> int:
    """The caller's jobs toward the 2-job cap. A running batch is one job however many items it has: its items run
    one after another and the job it is running right now is not counted again on its own."""
    home = _home()
    with _runs_lock:
        runs = [r for r in engine.batch_runs.values() if r["home"] == home]
        in_batch = {r["current"] for r in runs if r.get("current")}
    return sum(1 for j in engine.active_jobs() if j.id not in in_batch) + len(runs)


def check_cap(engine) -> None:
    if active_count(engine) >= MAX_ACTIVE_JOBS:
        raise StarletteHTTPException(409, "You already have 2 jobs running; wait for one to finish")


def check_paused(cloud) -> None:
    if cloud.paused:
        raise StarletteHTTPException(503, BUSY_COPY)


def new_job_id() -> str:
    return uuid.uuid4().hex[:12]  # unique per run: a refunded id charged again would be a silent no-op


def charge(engine, cloud, job_id: str, seconds: float) -> None:
    """Charge the caller for a job about to be submitted; remembered so only charged jobs are ever refunded."""
    try:
        cloud.usage.charge(uid(), job_id, seconds)
    except LimitReached as e:
        raise ValueError(f"You have {math.floor(e.remaining / 60)} min left this month") from None
    except Busy:
        raise StarletteHTTPException(503, BUSY_COPY) from None
    with engine._meta_lock:
        engine.charged.add(job_id)
        engine.charged_seconds[job_id] = float(seconds)  # written in the job's file at submit (restart refunds)


def refund_charged(engine, cloud, job_id: str) -> None:
    """Refund a job this process charged (once); a job never charged here makes no Usage call."""
    with engine._meta_lock:
        if job_id not in engine.charged:
            return
        engine.charged.discard(job_id)
        engine.charged_seconds.pop(job_id, None)
    refund(cloud, job_id)


def submit_charged(engine, cloud, job_id: str, seconds: float, submit: Callable[[], object]):
    """Charge, then submit; a submit that fails gives the minutes back."""
    charge(engine, cloud, job_id, seconds)
    try:
        return submit()
    except BaseException:
        refund_charged(engine, cloud, job_id)
        raise


def start_analyze(engine, cloud, p: Project, steps: list, probe: Callable[[Path], dict], *, in_batch: bool = False,
                  job_id: str | None = None):
    """Guard, charge, then queue an analyse job; returns the queued Job.

    One lock per user makes the checks, the charge and the submit a single step, so two requests can never both
    slip under the 2-job limit. The job id is made here so the charge can name it before the job exists. A batch
    item (`in_batch`) is already inside its batch's slot, so only the pause applies.
    """
    with user_lock():
        if not in_batch:
            check_cap(engine)
        if any(j.project_id == p.id and j.kind == "analyze" for j in engine.active_jobs()):
            raise StarletteHTTPException(409, "This video is already being analysed")
        check_paused(cloud)
        seconds = video_seconds(probe, Path(p.source))
        job_id = job_id or new_job_id()

        def submit():
            with engine.lock_for(p.id):
                touch(p.id)
            return engine.submit(p.id, "analyze", steps, job_id=job_id)

        return submit_charged(engine, cloud, job_id, seconds, submit)


def refund(cloud, job_id: str) -> None:
    """Give back a job's minutes. Logged, never raised: this runs on the event dispatcher thread."""
    try:
        cloud.usage.refund(job_id)
    except Exception as e:  # noqa: BLE001
        print(f"vedit: could not refund job {job_id}: {e}", file=sys.stderr)
