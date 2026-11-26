"""Cloud mode sync: the desktop app and the website see the same projects (CapCut-style).

One project store: a bundle the desktop pushes becomes a normal cloud project in the caller's home (sync_bundle
import_bundle), and every cloud project, website-made ones too, is listed. The sync id is the cloud project's id and
its revision is `settings.cloud_rev`. A push names the revision it started from: when the cloud has moved on, the push
is saved as "<title> (copy)" and the original is left alone. The rev check and the write happen under the caller's
lock (cloud_projects.user_lock), so two pushes from the same revision cannot both win.

The video is in the cloud only when it was attached (an upload, like any other: kind video, its size limit, counted
toward the 10 GB, no minutes charged) or the project was made on the website. Without it the project's source is a
placeholder and the routes that need the video answer 409 (cloud_projects.check_has_video). Nothing here charges.
"""
from __future__ import annotations

import asyncio
import copy
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cloud_assets
import cloud_cleanup
import cloud_projects as cp
import cloud_storage as cs
import sync_bundle
from fastapi import APIRouter, Request
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException
from studio_projects import Project, _home, list_recent, load_project, save_project

# a published video lives as long as a zip (the retention sweep deletes it ZIP_TTL after its upload): it is linked
# again for the rest of that life, and published again only when less than this would be left
MIN_LINK_LIFE = timedelta(hours=6)
ENVELOPE_BYTES = 64 * 1024  # base_rev and the JSON around the bundle
TOO_LARGE = "Project bundle is too large (limit 20 MB)"
BUSY_PROJECT = "This project has a job running; wait for it to finish"
BUSY_PUSH = "This project is busy on the website. Try again in a moment."
_UNSAFE_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')


class VideoBody(BaseModel):
    upload_key: str


def _load(pid: str) -> Project:
    """The caller's project; an unknown id and another user's id get the same 404."""
    try:
        return load_project(pid)
    except (KeyError, ValueError):
        raise cloud_assets._not_found() from None


def _title(p: Project) -> str:
    return str(p.settings.get("title") or Path(p.source).stem)


def summary(p: Project) -> dict:
    return {"id": p.id, "title": _title(p), "rev": sync_bundle.rev_of(p), "updated": p.last_activity or p.created,
            "has_video_in_cloud": cp.has_video(p), "expired": bool(p.settings.get("expired"))}


def _check_idle(engine, pid: str, copy_: str = BUSY_PROJECT) -> None:
    if any(j.project_id == pid for j in engine.active_jobs()):
        raise StarletteHTTPException(409, copy_)


async def _read_body(request: Request) -> dict:
    """The JSON body, refused (413) once it is larger than a bundle may be, before it is all read."""
    limit = sync_bundle.MAX_BUNDLE_BYTES + ENVELOPE_BYTES
    try:
        declared = int(request.headers.get("content-length") or 0)
    except ValueError:
        declared = 0
    if declared > limit:
        raise StarletteHTTPException(413, TOO_LARGE)
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise StarletteHTTPException(413, TOO_LARGE)
        chunks.append(chunk)
    try:
        body = json.loads(b"".join(chunks) or b"null")
    except ValueError:
        raise ValueError("The request body is not JSON") from None
    if not isinstance(body, dict) or not isinstance(body.get("bundle"), dict):
        raise ValueError("Send a project bundle as `bundle`")
    return body


def _create(bundle: dict, title: str | None = None) -> Project:
    """A new cloud project from a bundle, at revision 1, without a video. `title` names a conflict copy."""
    incoming = copy.deepcopy(bundle)
    incoming["id"], incoming["rev"] = None, 1  # never an update of whatever project the bundle names
    doc = incoming.get("project")
    if title is not None and isinstance(doc, dict) and isinstance(doc.get("settings", {}), dict):
        doc["settings"] = {**doc.get("settings", {}), "title": title}
    p = sync_bundle.import_bundle(incoming, None)
    p.settings.update(cloud_id=p.id, cloud_rev=1)
    p.last_activity = cp.now_iso()
    save_project(p)
    return p


def _drop_video(storage, p: Project) -> None:
    """Delete the project's video from the cloud: the source on the Volume, its preview copy and the published
    download. Edits, transcript and highlights stay. The caller holds the user's and the project's locks."""
    src = Path(p.source)
    folder = src.parent
    if folder.resolve().parent != (_home() / "work").resolve():
        raise ValueError("This project's video is not in the cloud")  # never deletes outside a cloud project folder
    for f in folder.glob("source.*"):
        if f.is_file():
            f.unlink(missing_ok=True)
    wd = folder / (src.stem + ".edit")
    for name in ("proxy.mp4", "proxy.json"):
        (wd / name).unlink(missing_ok=True)
    p.settings.pop("proxy", None)  # the player falls back to the source
    storage.delete(cs.sync_video_key(_home().name, p.id))


def _set_web_video(p: Project, on: bool) -> None:
    """web_video is part of the bundle: the revision moves only when it changes."""
    if bool(p.settings.get("web_video", False)) != on:
        p.settings["web_video"] = on
        sync_bundle.bump_rev(p)


def build_router(engine, cloud, probe) -> APIRouter:
    router = APIRouter()
    storage = cloud.storage

    def video_url(p: Project) -> str:
        """A link to the project's video, published to R2 (the Volume copy stays the project's source). The copy
        is reused while it has at least MIN_LINK_LIFE left, and the link never outlives it."""
        user = _home().name
        key = cs.sync_video_key(user, p.id)
        src = Path(p.source)
        now = datetime.now(timezone.utc)
        left = next((cloud_cleanup.ZIP_TTL - (now - modified) for k, size, modified in storage.list(cs.zips_prefix(user))
                     if k == key and size == src.stat().st_size), None)
        if left is None or left < MIN_LINK_LIFE:
            storage.upload(src, key, "video/mp4" if src.suffix.lower() in (".mp4", ".m4v") else "application/octet-stream")
            left = cloud_cleanup.ZIP_TTL
        name = " ".join(_UNSAFE_NAME.sub("", _title(p)).split()).strip(" .") or "video"
        return storage.presign_get(key, name + src.suffix.lower(), int(left.total_seconds()))

    @router.get("/sync/projects")
    def list_projects():
        return [summary(p) for p in list_recent(limit=sys.maxsize)]

    @router.get("/sync/projects/{pid}")
    def get_project(pid: str):
        _load(pid)
        with engine.lock_for(pid):
            p = _load(pid)
            bundle = sync_bundle.export_bundle(p)
        bundle.update(id=p.id, rev=sync_bundle.rev_of(p), has_video_in_cloud=cp.has_video(p))
        if bundle["has_video_in_cloud"]:
            bundle["video_url"] = video_url(p)
        return bundle

    def put(pid: str, body: dict) -> dict:
        bundle = body["bundle"]
        with cp.user_lock():
            if pid == "new":
                return {"id": _create(bundle).id, "rev": 1}
            base = body.get("base_rev")
            if isinstance(base, bool) or not isinstance(base, int):
                raise ValueError("base_rev must be an integer")
            _load(pid)
            with engine.lock_for(pid):
                p = _load(pid)
                _check_idle(engine, pid, BUSY_PUSH)  # a running analysis would overwrite what is pushed
                cur = sync_bundle.rev_of(p)
                if base != cur:  # the cloud moved on: the push is kept as its own project
                    t = bundle.get("title")
                    made = _create(bundle, f"{t if isinstance(t, str) and t else 'Project'} (copy)")
                    return {"id": made.id, "rev": 1, "conflict": True, "original": pid}
                if p.settings.get("cloud_id") != pid:  # a website project's first push: import finds it by this
                    p.settings["cloud_id"] = pid
                    save_project(p)
                q = sync_bundle.import_bundle({**bundle, "id": pid, "rev": cur + 1}, None)
                if q.id != pid:
                    raise RuntimeError(f"sync import wrote {q.id}, not {pid}")
                q.last_activity = cp.now_iso()
                save_project(q)
                return {"id": pid, "rev": cur + 1}

    @router.put("/sync/projects/{pid}")
    async def put_project(pid: str, request: Request):
        """{base_rev, bundle} -> {id, rev}; a stale base_rev -> {id: <copy>, rev: 1, conflict: true, original}.
        `new` creates. The body is read with a size limit, then the work runs off the event loop as the caller."""
        body = await _read_body(request)
        return await asyncio.to_thread(put, pid, body)

    @router.post("/sync/projects/{pid}/video")
    def attach_video(pid: str, req: VideoBody):
        """The caller's uploaded video becomes the project's source (replacing any earlier one). Not charged."""
        user = _home().name
        key = req.upload_key
        _load(pid)  # 404 before the upload is touched
        with cloud_assets.claim_pending(user, key) as (upload_id, pending, pending_file):
            if pending.get("kind") != "video":
                raise cloud_assets._not_found()

            def discard_upload() -> None:
                storage.delete(key)
                pending_file.unlink(missing_ok=True)

            remote = storage.size(key)
            if remote is None:
                raise ValueError("Upload not finished")  # record kept: the client can retry
            if remote != pending["size"]:
                discard_upload()
                raise ValueError("Upload size does not match what was declared; upload it again")
            old = Path(_load(pid).source)
            replaced = old.stat().st_size if old.is_file() else 0
            if cloud_assets.storage_bytes(storage, user, upload_id) - replaced + remote > cloud_assets.STORAGE_LIMIT:
                discard_upload()
                raise ValueError("Not enough storage: the 10 GB limit would be exceeded. Delete some files first.")
            ext = Path(str(pending["name"])).suffix.lower()
            part = old.parent / f"incoming-{upload_id}{ext}.part"
            try:
                storage.download(key, part)
                try:
                    if cp.video_seconds(probe, part) > cp.MAX_VIDEO_SECONDS:  # the cap every web video has
                        raise ValueError("Videos can be up to 60 minutes")
                except ValueError:
                    discard_upload()  # the project's own video is not touched: the swap comes later
                    raise
                with cp.user_lock(), engine.lock_for(pid):
                    p = _load(pid)
                    _check_idle(engine, pid)
                    _drop_video(storage, p)
                    new = Path(p.source).with_name(f"source{ext}")  # same folder and workdir: the edits stay
                    part.replace(new)
                    was, p.source = p.source, str(new)
                    if was != p.source:
                        sync_bundle.repoint_source(p, was)
                    p.settings.pop("expired", None)
                    _set_web_video(p, True)
                    p.last_activity = cp.now_iso()
                    save_project(p)
            finally:
                part.unlink(missing_ok=True)
            discard_upload()  # consumed: the object and its record go before the claim is released
        return summary(p)

    @router.delete("/sync/projects/{pid}/video")
    def delete_video(pid: str):
        """Remove the project's video from the cloud; its edits stay and the desktop copy is untouched."""
        _load(pid)
        with cp.user_lock(), engine.lock_for(pid):
            p = _load(pid)
            _check_idle(engine, pid)
            _drop_video(storage, p)
            _set_web_video(p, False)
            p.last_activity = cp.now_iso()
            save_project(p)
        return summary(p)

    return router
