"""Cloud mode uploads and the per-user asset library.

A visitor PUTs a file straight to R2 with a presigned URL (POST /uploads), then commits it (POST /assets): the
object is copied onto the user's volume home and the R2 copy is deleted. Everything is scoped to `_home()`, which the
auth guard sets to data_root/users/<uid>, so another user's ids are simply not found.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path

import cloud_storage as cs
from fastapi import APIRouter
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException
from studio_projects import _atomic_write_json, _home

KIND_LIMITS = {"video": 3 * 1024 ** 3, "image": 20 * 1024 ** 2, "audio": 200 * 1024 ** 2,
               "font": 10 * 1024 ** 2, "lut": 10 * 1024 ** 2}
KIND_EXTS = {
    "video": (".mp4", ".mov", ".m4v", ".webm", ".mkv"),
    "image": (".png", ".jpg", ".jpeg", ".webp", ".gif"),
    "audio": (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"),
    "font": (".ttf", ".otf"),
    "lut": (".cube",),
}
STORAGE_LIMIT = 10 * 1024 ** 3
ASSET_ID = re.compile(r"^a[0-9a-f]{12}$")
UPLOAD_ID = re.compile(r"^[0-9a-f]{32}$")

_index_lock = threading.Lock()
_start_lock = threading.Lock()  # quota check + reservation must be one step


def _not_found() -> StarletteHTTPException:
    return StarletteHTTPException(404, "not found")


def _uid() -> str:
    return _home().name


def _assets_dir() -> Path:
    return _home() / "assets"


def _index_path() -> Path:
    return _assets_dir() / "index.json"


def _read_index() -> list[dict]:
    try:
        data = json.loads(_index_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _write_index(items: list[dict]) -> None:
    _atomic_write_json(_index_path(), items)


def _pending_path(upload_id: str) -> Path:
    return _home() / "uploads" / f"{upload_id}.json"


def _public(a: dict) -> dict:
    return {"asset_id": a["asset_id"], "kind": a["kind"], "name": a["name"], "size": a["size"]}


def resolve(ref: str) -> Path:
    """`asset:<id>` -> the file in this user's library. Plain paths and unknown ids are not references."""
    return lookup(ref)[0]


def lookup(ref: str) -> tuple[Path, dict]:
    """`asset:<id>` -> (the file, its public entry: kind and the name it was uploaded with). ValueError otherwise."""
    if not isinstance(ref, str) or not ref.startswith("asset:"):
        raise ValueError("expected an asset: reference")
    aid = ref[len("asset:"):]
    if not ASSET_ID.match(aid):
        raise ValueError("invalid asset id")
    with _index_lock:
        entry = next((a for a in _read_index() if a.get("asset_id") == aid), None)
    if entry is None:
        raise ValueError("unknown asset")
    path = _assets_dir() / f"{aid}{entry['ext']}"
    if not path.is_file():
        raise ValueError("unknown asset")
    return path, _public(entry)


def _disk_bytes(root: Path) -> int:
    total = 0
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, f))
            except OSError:
                pass
    return total


def _reserved_bytes(exclude: str | None = None) -> int:
    """Declared sizes of uploads started but not yet committed (their pending records)."""
    d = _home() / "uploads"
    if not d.is_dir():
        return 0
    total = 0
    for f in d.glob("*.json"):
        if exclude is not None and f.stem == exclude:
            continue
        try:
            total += int(json.loads(f.read_text(encoding="utf-8"))["size"])
        except (OSError, ValueError, KeyError, TypeError):
            pass
    return total


def storage_bytes(storage, uid: str, exclude_upload: str | None = None) -> int:
    """Bytes on this user's volume home, plus their exports and zips in R2, plus the declared size of every
    unfinished upload. Pending records are the reservation for uploads/, so the uploads/ prefix is not counted
    (it would double-count an object that is both pending and already in R2). A project video published for the
    desktop to download (cloud_sync) is a temporary copy of a file already counted on the volume: left out too."""
    zips = sum(size for key, size, _modified in storage.list(cs.zips_prefix(uid)) if not cs.is_sync_video_key(key))
    remote = storage.usage_bytes(cs.exports_prefix(uid)) + zips
    return _disk_bytes(_home()) + remote + _reserved_bytes(exclude_upload)


def _content_type_ok(kind: str, content_type: str) -> bool:
    ct = (content_type or "").lower().split(";")[0].strip()
    if kind in ("video", "image", "audio"):
        return ct.startswith(kind + "/")
    if kind == "font":
        return ct.startswith("font/") or ct == "application/octet-stream"
    return ct in ("application/octet-stream", "text/plain") or ct.startswith("text/")  # lut (.cube)


@contextlib.contextmanager
def claim_pending(uid: str, key: str):
    """Claim this user's pending upload `key` exclusively and yield (upload_id, pending record, record path).

    Not this user's key, or no such pending upload -> 404 (the same answer, so nothing leaks); a concurrent claim
    -> 409. The claim is an exclusive-create marker next to the record, released on exit.
    """
    prefix = cs.uploads_prefix(uid)
    upload_id = key[len(prefix):] if isinstance(key, str) and key.startswith(prefix) else ""
    if not UPLOAD_ID.match(upload_id):
        raise _not_found()
    pending_file = _pending_path(upload_id)
    claimed = pending_file.with_suffix(".claimed")
    try:
        os.close(os.open(claimed, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except FileExistsError:
        raise StarletteHTTPException(409, "Upload is already being saved") from None
    except FileNotFoundError:  # the uploads/ directory does not exist: nothing was ever started
        raise _not_found() from None
    try:
        try:  # re-read under the claim: a commit that finished just before us removed the record
            pending = json.loads(pending_file.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            raise _not_found() from None
        yield upload_id, pending, pending_file
    finally:
        claimed.unlink(missing_ok=True)


class UploadRequest(BaseModel):
    kind: str
    name: str
    size: int
    content_type: str


class CommitRequest(BaseModel):
    upload_key: str


def build_router(cloud) -> APIRouter:
    router = APIRouter()
    storage = cloud.storage
    usage = cloud.usage

    @router.post("/uploads")
    def start_upload(req: UploadRequest):
        if req.kind not in KIND_LIMITS:
            raise ValueError(f"unknown kind {req.kind!r}")
        name = os.path.basename(req.name.replace("\\", "/")).strip()
        ext = os.path.splitext(name)[1].lower()
        if ext not in KIND_EXTS[req.kind]:
            raise ValueError(f"a {req.kind} file must end in {', '.join(KIND_EXTS[req.kind])}")
        if not _content_type_ok(req.kind, req.content_type):
            raise ValueError(f"content type {req.content_type!r} does not match a {req.kind} file")
        if req.size <= 0:
            raise ValueError("the file is empty")
        if req.size > KIND_LIMITS[req.kind]:
            raise ValueError(f"a {req.kind} file can be at most {KIND_LIMITS[req.kind] // 1024 ** 2} MB")
        uid = _uid()
        upload_id = secrets.token_hex(16)
        key = cs.upload_key(uid, upload_id)
        with _start_lock:
            if storage_bytes(storage, uid) + req.size > STORAGE_LIMIT:
                raise ValueError("Not enough storage: the 10 GB limit would be exceeded. Delete some files first.")
            _atomic_write_json(_pending_path(upload_id), {
                "kind": req.kind, "name": name, "size": req.size, "content_type": req.content_type,
                "created": time.time()})
        return {"upload_key": key, "url": storage.presign_put(key, req.content_type, req.size)}

    @router.post("/assets")
    def commit_asset(req: CommitRequest):
        uid = _uid()
        key = req.upload_key
        with claim_pending(uid, key) as (upload_id, pending, pending_file):
            remote = storage.size(key)
            if remote is None:
                raise ValueError("Upload not finished")  # record kept: the client can retry
            if remote != pending["size"] or storage_bytes(storage, uid, upload_id) + remote > STORAGE_LIMIT:
                storage.delete(key)
                pending_file.unlink(missing_ok=True)
                if remote != pending["size"]:
                    raise ValueError("Upload size does not match what was declared; upload it again")
                raise ValueError("Not enough storage: the 10 GB limit would be exceeded. Delete some files first.")
            ext = os.path.splitext(pending["name"])[1].lower()
            asset_id = "a" + secrets.token_hex(6)
            assets = _assets_dir()
            assets.mkdir(parents=True, exist_ok=True)
            part = assets / f"{asset_id}{ext}.part"
            try:
                storage.download(key, part)
                os.replace(part, assets / f"{asset_id}{ext}")
            finally:
                part.unlink(missing_ok=True)
            entry = {"asset_id": asset_id, "kind": pending["kind"], "name": pending["name"],
                     "size": pending["size"], "ext": ext, "created": time.time()}
            with _index_lock:
                _write_index(_read_index() + [entry])
            storage.delete(key)
            pending_file.unlink(missing_ok=True)  # before the claim is released
            return _public(entry)

    @router.get("/assets")
    def list_assets(kind: str | None = None):
        with _index_lock:
            items = _read_index()
        return [_public(a) for a in items if kind is None or a.get("kind") == kind]

    @router.delete("/assets/{asset_id}")
    def delete_asset(asset_id: str):
        with _index_lock:
            items = _read_index()
            entry = next((a for a in items if a.get("asset_id") == asset_id), None)
            if entry is None:
                raise _not_found()
            _write_index([a for a in items if a is not entry])
        (_assets_dir() / f"{entry['asset_id']}{entry['ext']}").unlink(missing_ok=True)
        return {"ok": True}

    @router.get("/usage")
    def get_usage():
        uid = _uid()
        status = usage.status(uid)
        return {"seconds_used": status["seconds_used"], "seconds_limit": status["seconds_limit"],
                "storage_bytes": storage_bytes(storage, uid), "storage_limit": STORAGE_LIMIT}

    return router
