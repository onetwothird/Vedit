"""The daily retention sweep (cloud mode): large media goes 7 days after the last activity, documents are kept.

Deleted: a project's source video, preview copy (proxy), local exports folder and its R2 exports (the project is
marked `settings.expired = true`; its transcript, edits and project.json stay), a story's rendered output (the story
document stays), "Download all" zips after 24 h, uploads never committed after 24 h, finished job files after 7 days.
Never touched: brand.json, stories/*/story.doc.json, assets/, transcripts, edls.

It runs beside the API, so it skips whatever has a job still queued or running, is safe to run twice, and an error
on one project or user is counted and does not stop the rest. Cloud only: the desktop app never calls it.
"""
from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cloud_assets
import cloud_storage as cs
from studio_jobs import TERMINAL
from studio_projects import PROJECT_FILE, _atomic_write_json, _read_project, _read_registry, project_doc, use_home

DAYS = 7
UPLOAD_TTL = timedelta(hours=24)  # a pending upload nobody committed
ZIP_TTL = timedelta(hours=24)
STALE_CLAIM = timedelta(hours=1)  # a commit takes seconds: an older claim marker is a crashed one
COUNTS = ("projects_expired", "story_outputs", "upload_objects", "zips", "job_files", "stale_claims", "errors")


def _warn(what: str, e: BaseException) -> None:
    print(f"vedit cleanup: {what}: {type(e).__name__}: {e}", file=sys.stderr)


def _parse(iso) -> datetime | None:
    try:
        d = datetime.fromisoformat(str(iso))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _mtime(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)


def _active_ids(home: Path) -> set[str]:
    """Project / story ids with a job that is still queued or running (read from the job files the API keeps)."""
    ids: set[str] = set()
    for f in (home / "jobs").glob("*.json"):
        try:
            job = json.loads(f.read_text(encoding="utf-8"))["job"]
            if job["state"] not in TERMINAL:
                ids.add(str(job["project_id"]))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return ids


def _activity(p, project_file: Path) -> datetime:
    """When the project was last used: its `last_activity`; a project that never had one counts from the later of
    its `created` time and its project file's modified time (never deleted blindly)."""
    stamp = _parse(p.last_activity) if p.last_activity else None
    if stamp is not None:
        return stamp
    return max(filter(None, [_parse(p.created), _mtime(project_file)]))


def _expire_projects(home: Path, uid: str, storage, now: datetime, days: int, counts: dict) -> None:
    cutoff = now - timedelta(days=days)
    active = _active_ids(home)
    work = (home / "work").resolve()
    groups: dict[Path, list] = {}  # folder -> [(project, project file path)]
    with use_home(home):
        for pid, entry in _read_registry().items():
            try:
                wd = Path(entry["workdir"])
                file = entry.get("file", PROJECT_FILE)
                p = _read_project(wd, file)
                groups.setdefault(Path(p.source).parent, []).append((p, wd / file, pid))
            except (OSError, ValueError, KeyError, TypeError):
                continue  # a project whose file is gone or unreadable has nothing for us to expire
    for folder, members in groups.items():
        try:
            if folder.resolve().parent != work:
                continue  # only cloud project folders (work/<token>/) are ever deleted
            todo = [m for m in members if not m[0].settings.get("expired")]
            if not todo:
                continue
            # one video can back several projects (clip, vlog, reel): it goes only when none of them is in use
            if any(pid in active or _activity(p, f) >= cutoff for p, f, pid in members):
                continue
            for p, _f, pid in todo:
                storage.delete_prefix(cs.exports_prefix(uid, pid))
            for f in folder.glob("source.*"):
                if f.is_file():
                    f.unlink(missing_ok=True)
            edit = folder / "source.edit"
            for name in ("proxy.mp4", "proxy.json"):
                (edit / name).unlink(missing_ok=True)
            shutil.rmtree(folder / "exports", ignore_errors=True)
            for p, f, _pid in todo:
                with use_home(home):
                    fresh = _read_project(f.parent, f.name)  # re-read: write only the flag onto what is there now
                fresh.settings["expired"] = True
                _atomic_write_json(f, project_doc(fresh))
                counts["projects_expired"] += 1
        except Exception as e:  # noqa: BLE001 - one project's trouble must not stop the sweep
            counts["errors"] += 1
            _warn(f"project folder {folder}", e)


def _expire_story_outputs(home: Path, uid: str, storage, now: datetime, days: int, counts: dict) -> None:
    cutoff = now - timedelta(days=days)
    active = _active_ids(home)
    for doc_path in sorted((home / "stories").glob("*/story.doc.json")):
        sid = doc_path.parent.name
        try:
            doc = json.loads(doc_path.read_text(encoding="utf-8"))
            # a story's activity is its document's `updated` time (saved on every edit and every build)
            stamp = _parse(doc.get("updated")) or _parse(doc.get("created")) or _mtime(doc_path)
            if stamp >= cutoff or sid in active:
                continue
            removed = storage.delete_prefix(cs.exports_prefix(uid, sid))
            out = doc_path.parent / "output"
            if out.exists():
                shutil.rmtree(out)
                removed += 1
            if removed:
                counts["story_outputs"] += 1
        except Exception as e:  # noqa: BLE001
            counts["errors"] += 1
            _warn(f"story {sid}", e)


def _expire_uploads(home: Path, uid: str, storage, now: datetime, counts: dict) -> None:
    folder = home / "uploads"
    records = {f.stem: f for f in folder.glob("*.json")}
    for upload_id, rec in sorted(records.items()):
        try:
            if not cloud_assets.UPLOAD_ID.match(upload_id):
                continue
            marker = rec.with_suffix(".claimed")
            if marker.exists():
                if now - _mtime(marker) <= STALE_CLAIM:
                    continue  # a commit is running right now
                marker.unlink(missing_ok=True)  # crashed commit: the upload can be retried or expire
                counts["stale_claims"] += 1
            created = datetime.fromtimestamp(float(json.loads(rec.read_text(encoding="utf-8"))["created"]),
                                             timezone.utc)
            if now - created > UPLOAD_TTL:
                storage.delete(cs.upload_key(uid, upload_id))
                rec.unlink(missing_ok=True)
                records.pop(upload_id)
                counts["upload_objects"] += 1
        except Exception as e:  # noqa: BLE001
            counts["errors"] += 1
            _warn(f"upload {upload_id}", e)
    # objects whose record is gone (a delete that failed half way) and that are a day old
    prefix = cs.uploads_prefix(uid)
    for key, _size, modified in storage.list(prefix):
        if key[len(prefix):] not in records and now - modified > UPLOAD_TTL:
            storage.delete(key)
            counts["upload_objects"] += 1


def _expire_zips(uid: str, storage, now: datetime, counts: dict) -> None:
    for key, _size, modified in storage.list(cs.zips_prefix(uid)):
        if now - modified > ZIP_TTL:
            storage.delete(key)
            counts["zips"] += 1


def _expire_job_files(home: Path, now: datetime, days: int, counts: dict) -> None:
    cutoff = now - timedelta(days=days)
    for f in sorted((home / "jobs").glob("*.json")):
        try:
            if _mtime(f) >= cutoff:
                continue
            if json.loads(f.read_text(encoding="utf-8"))["job"]["state"] in TERMINAL:
                f.unlink(missing_ok=True)
                counts["job_files"] += 1
        except (OSError, ValueError, KeyError, TypeError):
            continue  # unreadable: left alone
        except Exception as e:  # noqa: BLE001
            counts["errors"] += 1
            _warn(f"job file {f}", e)


def sweep(data_root: Path, storage, now: datetime, days: int = DAYS) -> dict:
    """Run the retention rules over every user under <data_root>/users; returns what was removed."""
    counts = dict.fromkeys(COUNTS, 0)
    users = Path(data_root) / "users"
    if not users.is_dir():
        return counts
    for home in sorted(p for p in users.iterdir() if p.is_dir()):
        uid = home.name
        steps = (lambda: _expire_projects(home, uid, storage, now, days, counts),
                 lambda: _expire_story_outputs(home, uid, storage, now, days, counts),
                 lambda: _expire_uploads(home, uid, storage, now, counts),
                 lambda: _expire_zips(uid, storage, now, counts),
                 lambda: _expire_job_files(home, now, days, counts))
        for step in steps:
            try:
                step()
            except Exception as e:  # noqa: BLE001 - a bad user folder or a storage outage: count it, go on
                counts["errors"] += 1
                _warn(f"user {uid}", e)
    return counts


if __name__ == "__main__":  # pragma: no cover - the Modal cron is the normal way in
    raise SystemExit("run through cloud/modal_app.py")
