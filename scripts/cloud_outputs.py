"""Cloud mode outputs: exports, story builds, batches, "Download all" zips and the brand kit.

Renders run on the volume as before; the job's last step (a Python step, see studio_jobs.Step) uploads the files to
R2 and the job's result lists them as 24 h links. Everything runs inside the caller's `use_home`, so another user's
project, story or batch is simply not found. Every file a document names is an `asset:` reference of the caller's
library: a plain path or another user's id is refused (400) before anything is read from disk.

Lock order everywhere: the user's lock (cloud_projects.user_lock), then a project's or story's (engine.lock_for).
"""
from __future__ import annotations

import contextlib
import json
import math
import re
import secrets
import shutil
import sys
import threading
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import cloud_assets
import cloud_projects as cp
import cloud_storage as cs
import studio_stories as stories
from common import load_json, py_cmd
from studio_projects import PLATFORM_FOR, _atomic_write_json, _home, create_project, load_project, save_project

URL_SECONDS = 24 * 3600
CONTENT_TYPES = {".mp4": "video/mp4", ".srt": "application/x-subrip", ".jpg": "image/jpeg",
                 ".zip": "application/zip"}
TTS_ENV = {"openai": "OPENAI_API_KEY", "elevenlabs": "ELEVENLABS_API_KEY"}
STORY_TOO_LONG = "Stories can be up to 3 minutes"
OUTSIDE = "This edit uses a file that is not in your library"

_batch_file_lock = threading.Lock()


def attach(engine, cloud, **hooks) -> None:
    """Give the cloud code the server functions it needs (no import of studio_server, which may run as __main__)."""
    engine.cloud_hooks = SimpleNamespace(cloud=cloud, **hooks)


def _hooks(engine) -> SimpleNamespace:
    return engine.cloud_hooks


def _uid() -> str:
    return _home().name


def _detail(e: BaseException) -> str:
    return str(getattr(e, "detail", None) or e)


def safe_name(name: str) -> str:
    """An output file name usable as an R2 key part (no '..', which a title like "Wait... what" would carry)."""
    return re.sub(r"\.{2,}", ".", name)


def _link(storage, key: str, name: str) -> dict:
    return {"name": name, "url": storage.presign_get(key, name, URL_SECONDS)}


def publish_step(engine, cloud, job_id: str, files: Callable[[], list[tuple[Path, str]]]) -> Callable[[], None]:
    """The job's last step: upload each (local file, R2 key), drop the volume copy, and record the 24 h links the
    job's result will carry. It runs on the queue's worker, before the job is marked done; raising fails the job
    (which refunds it)."""
    def run() -> None:
        done: list[dict] = []
        for path, key in files():
            cloud.storage.upload(path, key, CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream"))
            done.append(_link(cloud.storage, key, key.rsplit("/", 1)[1]))
            with engine._meta_lock:
                engine.published[job_id] = list(done)
            path.unlink(missing_ok=True)  # R2 is the download now; the volume copy would count twice
    return run


# ---- exports ---------------------------------------------------------------------------------------------------

def check_inside_home(edl: dict, wd: Path) -> None:
    """Every file an edit names (sources, overlays, intro/outro, music, LUT) must be inside the caller's home."""
    home = _home().resolve()

    def walk(x) -> None:
        if isinstance(x, dict):
            for k, v in x.items():
                if k in ("path", "lut") and isinstance(v, str):
                    p = Path(v) if Path(v).is_absolute() else wd / v
                    if not p.resolve().is_relative_to(home):
                        raise ValueError(OUTSIDE)
                else:
                    walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(edl)


def start_export(engine, cloud, pid: str, body, *, in_batch: bool = False, job_id: str | None = None):
    """Plan the renders into the project's own exports/ folder (out_dir is ignored), charge their total output
    length, and queue them with a publish step."""
    h = _hooks(engine)
    user = _uid()
    with cp.user_lock():
        if not in_batch:
            cp.check_cap(engine)
        cp.check_paused(cloud)
        with engine.lock_for(pid):
            p = h.project(pid)
            p.last_activity = cp.now_iso()  # retention counts from here
            save_project(p)
            out_dir = Path(p.source).parent / "exports"
            steps, renders = h.plan_export(p, body, out_dir, safe_name, check_inside_home)
            try:
                seconds = sum(h.export_seconds(Path(r["tmp"])) for r in renders)
                job_id = job_id or cp.new_job_id()

                def files() -> list[tuple[Path, str]]:
                    out = []
                    for r in renders:
                        video = Path(r["path"])
                        for f in (video, video.with_suffix(".srt"), video.with_suffix(".jpg")):
                            if f.is_file():
                                out.append((f, cs.export_key(user, pid, f.name)))
                    return out

                steps = steps + [("publish", publish_step(engine, cloud, job_id, files), 0.0)]
                return cp.submit_charged(engine, cloud, job_id, seconds,
                                         lambda: engine.submit(pid, "export", steps, renders=renders, job_id=job_id))
            except BaseException:
                for r in renders:
                    Path(r["tmp"]).unlink(missing_ok=True)
                raise


def list_exports(cloud, pid: str) -> list[dict]:
    """This project's exported files, each with a fresh 24 h link. Another user's project is not found."""
    try:
        load_project(pid)
    except (KeyError, ValueError):
        raise cloud_assets._not_found() from None
    return _fresh_links(cloud, pid)


def list_story_exports(cloud, sid: str) -> list[dict]:
    """A story's published videos (exports/<uid>/<sid>/), each with a fresh 24 h link. Another user's story: 404."""
    try:
        stories.load_story_doc(sid)
    except KeyError:
        raise cloud_assets._not_found() from None
    return _fresh_links(cloud, sid)


def _fresh_links(cloud, owner_id: str) -> list[dict]:
    prefix = cs.exports_prefix(_uid(), owner_id)
    return [_link(cloud.storage, k, k[len(prefix):]) for k in cloud.storage.list_keys(prefix)]


# ---- stories ---------------------------------------------------------------------------------------------------

def _story_seconds(doc: dict, probe) -> float:
    """Checks every reference (400 for a plain path or a foreign id, nothing read) and the 3-minute cap."""
    stories.check_buildable(doc, cloud_assets.resolve)
    try:
        seconds = stories.estimate_seconds(doc, cloud_assets.resolve, probe)
    except ValueError:
        raise
    except (Exception, SystemExit):  # noqa: BLE001 - ffprobe fails in many ways
        raise ValueError("A clip in this story could not be read") from None
    if not math.isfinite(seconds) or seconds > stories.MAX_STORY_SECONDS:
        raise ValueError(STORY_TOO_LONG)
    return seconds


def start_story(engine, cloud, sid: str, body):
    """Validate (refs, 3 minutes), guard, charge the estimated output length and queue voices + render + publish.
    The visitor's TTS key lives only in the steps' in-memory env: never in story.json, a job file or an argv."""
    h = _hooks(engine)
    _story_seconds(h.story(sid), h.probe)  # validated before anything else (and before any guard)
    user = _uid()
    with cp.user_lock():
        cp.check_cap(engine)
        cp.check_paused(cloud)
        with engine.lock_for(sid):
            doc = h.story(sid)
            seconds = _story_seconds(doc, h.probe)
            out_dir = stories.story_dir(sid) / "output"
            out = out_dir / safe_name(stories.output_file(doc, out_dir).name)
            path = stories.write_story_json(doc, out, cloud_assets.resolve)
            env = {TTS_ENV[body.tts.provider]: body.tts.api_key.strip()} if body.tts is not None else {}
            extra = (env,) if env else ()
            job_id = cp.new_job_id()
            steps = [("voices", py_cmd("story.py", "voices", str(path)), 0.25, *extra),
                     ("render", py_cmd("story.py", "build", str(path)), 0.75, *extra),
                     ("publish", publish_step(engine, cloud, job_id,
                                              lambda: [(out, cs.export_key(user, sid, out.name))]), 0.0)]
            job = cp.submit_charged(engine, cloud, job_id, seconds,
                                    lambda: engine.submit(sid, "story", steps, job_id=job_id))
            doc["building"] = out.name  # the file's name, never a volume path
            doc["last_job"] = h.job_dict(job)
            stories.save_story_doc(doc)
            return job


# ---- brand kit -------------------------------------------------------------------------------------------------

BRAND_FILE_KEYS = ("logo", "intro", "outro", "music", "lut")
BRAND_TEXT_KEYS = ("font", "caption_color", "highlight")


def brand_view(kit: dict) -> dict:
    view = {k: kit[k] if isinstance(kit.get(k), str) else "" for k in (*BRAND_TEXT_KEYS, *BRAND_FILE_KEYS)}
    fonts = kit.get("fonts")
    view["fonts"] = {k: v for k, v in fonts.items() if isinstance(v, str)} if isinstance(fonts, dict) else {}
    return view


def _ref_ok(key: str, ref: str) -> None:
    try:
        cloud_assets.resolve(ref)
    except ValueError:
        raise ValueError(f"{key}: choose a file from your library") from None


def put_brand(body, kit: dict, brand_path: Path) -> dict:
    """Check every value first; nothing is saved when one is a plain path or another user's asset."""
    values = body.model_dump()
    for k in BRAND_FILE_KEYS:
        if values[k].strip():
            _ref_ok(k, values[k].strip())
    for weight, ref in values["fonts"].items():
        _ref_ok(f"fonts.{weight}", ref.strip())
    font = values["font"].strip()
    if "/" in font or "\\" in font or font.lower().startswith("asset:") or Path(font).suffix.lower() in (".ttf", ".otf"):
        raise ValueError("font must be a font name; add font files under fonts")
    kit = dict(kit)
    for k in (*BRAND_TEXT_KEYS, *BRAND_FILE_KEYS):
        v = values[k].strip()
        if v:
            kit[k] = v
        else:
            kit.pop(k, None)
    if values["fonts"]:
        kit["fonts"] = {w: r.strip() for w, r in values["fonts"].items()}
    else:
        kit.pop("fonts", None)
    _atomic_write_json(brand_path, kit)
    return brand_view(kit)


def resolved_brand_file() -> Path | None:
    """The brand kit with its references resolved to this user's files, written beside it for the step that builds
    edits. A reference that no longer resolves (the asset was deleted) is left out; a non-reference is never used."""
    try:
        kit = json.loads((_home() / "brand.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(kit, dict) or not kit:
        return None
    out = {k: v for k, v in kit.items() if k not in (*BRAND_FILE_KEYS, "fonts")}
    for k in BRAND_FILE_KEYS:
        with contextlib.suppress(ValueError):
            if isinstance(kit.get(k), str) and kit[k]:
                out[k] = str(cloud_assets.resolve(kit[k]))
    fonts = {}
    for w, ref in (kit.get("fonts") or {}).items() if isinstance(kit.get("fonts"), dict) else ():
        with contextlib.suppress(ValueError):
            fonts[w] = str(cloud_assets.resolve(ref))
    if fonts:
        out["fonts"] = fonts
    path = _home() / "brand.resolved.json"
    _atomic_write_json(path, out)
    return path


# ---- batches ---------------------------------------------------------------------------------------------------

def _write_batch(path: Path, update: Callable[[dict], None]) -> None:
    with _batch_file_lock:
        data = load_json(path)
        update(data)
        _atomic_write_json(path, data)


def start_batch(engine, cloud, body) -> str:
    """Claim every upload (each must be the caller's pending video, else 404 and nothing is consumed), download and
    probe them all, and refuse the whole batch (400, nothing charged) when it is over the minutes left. Then each video
    becomes a project and the batch runs them one at a time, each analysis charged at its own submit."""
    h = _hooks(engine)
    if body.sources is not None or body.out_dir is not None:
        raise ValueError("Upload the videos for a batch; paths and folders are not used on the web")
    keys = body.upload_keys
    if not keys:
        raise ValueError("Upload at least one video")
    if body.platform not in PLATFORM_FOR[body.type]:
        raise ValueError(f"unsupported platform for a {body.type} project: {body.platform}")
    if len(set(keys)) != len(keys):
        raise ValueError("the same video is in the batch twice")
    bad = [f for f in body.formats if f not in ("9:16", "1:1", "4:5", "16:9")]
    if body.export and (bad or not body.formats):
        raise ValueError(f"unsupported formats: {bad}" if bad else "pick at least one format")
    user = _uid()
    storage = cloud.storage
    with cp.user_lock():  # refuse early, before any upload is touched
        cp.check_cap(engine)
        cp.check_paused(cloud)
    folders: list[Path] = []
    with contextlib.ExitStack() as stack:
        claims = [stack.enter_context(cloud_assets.claim_pending(user, k)) for k in keys]  # 404 / 409, nothing used
        if any(pending.get("kind") != "video" for _id, pending, _f in claims):
            raise cloud_assets._not_found()

        def reject_all() -> None:
            for k, (_id, _pending, pending_file) in zip(keys, claims):
                storage.delete(k)
                pending_file.unlink(missing_ok=True)

        try:
            sources: list[Path] = []
            for k, (_id, pending, _f) in zip(keys, claims):
                remote = storage.size(k)
                if remote is None:
                    raise ValueError("Upload not finished")  # records kept: the client can retry
                if remote != pending["size"]:
                    reject_all()
                    raise ValueError(f"{pending['name']}: upload size does not match what was declared")
            for k, (_id, pending, _f) in zip(keys, claims):
                folder = _home() / "work" / secrets.token_hex(8)
                folder.mkdir(parents=True)
                folders.append(folder)
                source = folder / f"source{Path(str(pending['name'])).suffix.lower()}"
                part = source.with_name(source.name + ".part")
                storage.download(k, part)
                part.replace(source)
                sources.append(source)
            total = 0.0
            for (_id, pending, _f), source in zip(claims, sources):
                try:
                    seconds = cp.video_seconds(h.probe, source)
                    if seconds > cp.MAX_VIDEO_SECONDS:
                        raise ValueError("Videos can be up to 60 minutes")
                except ValueError as e:
                    reject_all()
                    raise ValueError(f"{pending['name']}: {e}") from None
                total += seconds
            remaining = cloud.usage.remaining(user)
            if total > remaining:
                reject_all()
                raise ValueError(f"These videos are {math.ceil(total / 60)} min; you have "
                                 f"{math.floor(remaining / 60)} min left this month")
            with cp.user_lock():
                cp.check_cap(engine)  # the uploads are kept: the visitor can try again without uploading
                cp.check_paused(cloud)
                bid = "b" + secrets.token_hex(6)
                items, pids = [], []
                for (_id, pending, _f), source in zip(claims, sources):
                    p = create_project(source, body.language, body.platform, body.type)
                    p.last_activity = cp.now_iso()
                    if body.export:
                        p.settings["auto_export"] = {"formats": body.formats, "quality": body.quality,
                                                     "out_dir": None, "srt": body.srt, "thumbnail": body.thumbnail}
                    save_project(p)
                    pids.append(p.id)
                    items.append({"project": p.id, "source": pending["name"], "analyze": None})
                _atomic_write_json(h.batch_path(bid), {"id": bid, "created": stories._now(), "type": body.type,
                                                       "export": body.export, "items": items})
                run = {"home": _home(), "bid": bid, "items": pids, "next": 0, "current": None,
                       "analyze": h.AnalyzeBody(ai=body.ai, max_clips=body.max_clips), "export": body.export}
                with cp._runs_lock:
                    engine.batch_runs[bid] = run
                folders = []  # the projects own them now
        except BaseException:
            for f in folders:
                shutil.rmtree(f, ignore_errors=True)
            raise
        # consumed: before the claims are released. Best-effort: the batch is registered and holds a slot, so a
        # failed delete must never stop it from starting (a leftover object is only storage, cleaned by retention)
        for k, (_id, _pending, pending_file) in zip(keys, claims):
            try:
                storage.delete(k)
                pending_file.unlink(missing_ok=True)
            except Exception as e:  # noqa: BLE001
                print(f"vedit: batch {bid}: could not remove the used upload {k}: {e}", file=sys.stderr)
    try:
        _advance(engine, cloud, run)
    except BaseException:  # never leave a registered batch holding the user's slot
        with cp._runs_lock:
            engine.batch_runs.pop(bid, None)
        raise
    return bid


def _record_item(run: dict, pid: str, **fields) -> None:
    def update(data: dict) -> None:
        for it in data.get("items", []):
            if it.get("project") == pid:
                it.update(fields)
    try:
        _write_batch(_batch_file(run), update)
    except (OSError, ValueError) as e:
        print(f"vedit: could not update batch {run['bid']}: {e}", file=sys.stderr)


def _batch_file(run: dict) -> Path:
    return _home() / "batches" / f"{run['bid']}.json"


def _advance(engine, cloud, run: dict) -> None:
    """Submit the batch's next item (its analysis, charged at its submit); an item that cannot start is marked
    with why and the next one is tried. The last item done ends the batch's slot."""
    h = _hooks(engine)
    while run["next"] < len(run["items"]):
        pid = run["items"][run["next"]]
        run["next"] += 1
        job_id = cp.new_job_id()
        with cp._runs_lock:
            run["current"] = job_id  # before the submit, so the job is never counted twice toward the cap
        try:
            p = load_project(pid)
            cp.start_analyze(engine, cloud, p, h.analyze_steps(p, run["analyze"]), h.probe, in_batch=True,
                             job_id=job_id)
        except Exception as e:  # noqa: BLE001 - one item that cannot start never stops the batch
            _record_item(run, pid, error=_detail(e))
            continue
        _record_item(run, pid, analyze=job_id)
        return
    with cp._runs_lock:
        engine.batch_runs.pop(run["bid"], None)


def _start_item_export(engine, cloud, run: dict, pid: str) -> bool:
    """Export a batch item's kept clips right after its analysis; False when there is nothing to export."""
    h = _hooks(engine)

    def note(**fields) -> None:
        with engine.lock_for(pid):
            p = load_project(pid)
            auto = p.settings.get("auto_export")
            if isinstance(auto, dict):
                auto.update(fields)
                save_project(p)

    p = load_project(pid)
    auto = p.settings.get("auto_export")
    if not isinstance(auto, dict) or auto.get("job"):
        return False
    kept = [c.highlight["id"] for c in p.clips if c.kept]
    if not kept:
        note(error="no clips were found in this video")
        return False
    body = h.ExportBody(clips=kept, formats=auto.get("formats") or ["9:16"], quality=auto.get("quality", "normal"),
                        srt=auto.get("srt", True), thumbnail=auto.get("thumbnail", False))
    job_id = cp.new_job_id()
    with cp._runs_lock:
        run["current"] = job_id
    try:
        start_export(engine, cloud, pid, body, in_batch=True, job_id=job_id)
    except Exception as e:  # noqa: BLE001
        note(error=_detail(e))
        return False
    note(job=job_id)
    return True


def after_job(engine, job) -> None:
    """Called once per finished job (dispatcher thread, as the job's owner, no project lock held): moves a batch on."""
    cloud = _hooks(engine).cloud
    with cp._runs_lock:
        run = next((r for r in engine.batch_runs.values() if r.get("current") == job.id), None)
    if run is None:
        return
    try:
        if job.kind == "analyze" and job.state == "done" and run["export"] \
                and _start_item_export(engine, cloud, run, job.project_id):
            return
        _advance(engine, cloud, run)
    except Exception as e:  # noqa: BLE001 - never leave a batch holding its slot
        print(f"vedit: batch {run['bid']} stopped: {e}", file=sys.stderr)
        with cp._runs_lock:
            engine.batch_runs.pop(run["bid"], None)


# ---- "Download all" zips ---------------------------------------------------------------------------------------

def start_zip(engine, cloud, bid: str):
    """Zip every exported file of the batch's items (a job step), upload it to zips/<uid>/<bid>.zip; not charged."""
    h = _hooks(engine)
    path = h.batch_path(bid)
    if not path.is_file():
        raise h.not_found(f"batch {bid}")
    items = load_json(path).get("items", [])
    user = _uid()
    storage = cloud.storage
    with cp.user_lock():
        cp.check_cap(engine)
        cp.check_paused(cloud)
        job_id = cp.new_job_id()
        tmp = _home() / "tmp" / job_id

        def build() -> None:
            entries = []
            for n, it in enumerate(items, 1):
                if not isinstance(it.get("project"), str):
                    continue
                prefix = cs.exports_prefix(user, it["project"])
                folder = f"{n:02d} {Path(str(it.get('source') or '')).stem}".strip()
                entries += [(f"{folder}/{k[len(prefix):]}", k) for k in storage.list_keys(prefix)]
            if not entries:
                raise ValueError("nothing in this batch has been exported yet")
            tmp.mkdir(parents=True, exist_ok=True)
            try:
                out = tmp / f"{bid}.zip"
                with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as z:  # videos do not compress
                    for i, (arc, key) in enumerate(entries):
                        local = tmp / f"{i}.part"
                        storage.download(key, local)
                        z.write(local, arc)
                        local.unlink()
                publish_step(engine, cloud, job_id, lambda: [(out, cs.zip_key(user, bid))])()
                with engine._meta_lock:  # a friendlier download name than the key's
                    engine.published[job_id] = [_link(storage, cs.zip_key(user, bid), f"Vedit batch {bid}.zip")]
            finally:
                shutil.rmtree(tmp, ignore_errors=True)

        return engine.submit(bid, "zip", [("zip", build, 1.0)], job_id=job_id)
