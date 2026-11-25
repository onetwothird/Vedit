"""Cloud mode uploads and the per-user asset library.

A visitor PUTs a file straight to R2 with a presigned URL (POST /uploads), then commits it (POST /assets): the
object is copied onto the user's volume home and the R2 copy is deleted. Everything is scoped to `_home()`, which the
auth guard sets to data_root/users/<uid>, so another user's ids are simply not found.
"""
from __future__ import annotations

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
    return path


def _disk_bytes(root: Path) -> int:
    total = 0
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, f))
            except OSError:
                pass
    return total


def storage_bytes(storage, uid: str) -> int:
    """Bytes on this user's volume home plus their objects still in R2 (uploads, exports, zips)."""
    remote = sum(storage.usage_bytes(p) for p in (cs.uploads_prefix(uid), cs.exports_prefix(uid), cs.zips_prefix(uid)))
    return _disk_bytes(_home()) + remote


def _content_type_ok(kind: str, content_type: str) -> bool:
    ct = (content_type or "").lower().split(";")[0].strip()
    if kind in ("video", "image", "audio"):
        return ct.startswith(kind + "/")
    if kind == "font":
        return ct.startswith("font/") or ct == "application/octet-stream"
    return ct in ("application/octet-stream", "text/plain") or ct.startswith("text/")  # lut (.cube)


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
        if storage_bytes(storage, uid) + req.size > STORAGE_LIMIT:
            raise ValueError("Not enough storage: the 10 GB limit would be exceeded. Delete some files first.")
        upload_id = secrets.token_hex(16)
        key = cs.upload_key(uid, upload_id)
        _atomic_write_json(_pending_path(upload_id), {
            "kind": req.kind, "name": name, "size": req.size, "content_type": req.content_type,
            "created": time.time()})
        return {"upload_key": key, "url": storage.presign_put(key, req.content_type, req.size)}

    @router.post("/assets")
    def commit_asset(req: CommitRequest):
        uid = _uid()
        prefix = cs.uploads_prefix(uid)
        key = req.upload_key
        upload_id = key[len(prefix):] if key.startswith(prefix) else ""
        if not UPLOAD_ID.match(upload_id):
            raise _not_found()
        try:
            pending = json.loads(_pending_path(upload_id).read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            raise _not_found() from None
        remote = storage.size(key)
        if remote is None:
            raise ValueError("Upload not finished")
        if remote != pending["size"]:
            storage.delete(key)
            _pending_path(upload_id).unlink(missing_ok=True)
            raise ValueError("Upload size does not match what was declared; upload it again")
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
        entry = {"asset_id": asset_id, "kind": pending["kind"], "name": pending["name"], "size": pending["size"],
                 "ext": ext, "created": time.time()}
        with _index_lock:
            _write_index(_read_index() + [entry])
        storage.delete(key)
        _pending_path(upload_id).unlink(missing_ok=True)
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
