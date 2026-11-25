# ruff: noqa: F811  (the cloud API fixtures are imported from test_cloud_projects)
"""The 7-day retention sweep: what it deletes, what it keeps, and the 400 an expired project gives."""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="needs the studio extra")
pytest.importorskip("jwt", reason="needs the cloud extra")

import cloud_cleanup  # noqa: E402
import cloud_storage as cs  # noqa: E402
from studio_projects import create_project, load_project, save_project, use_home  # noqa: E402
import studio_stories as stories  # noqa: E402

# fixtures and helpers of the cloud API tests (the 400 tests drive the real app)
from test_cloud_projects import (A, _probe, analyze, cfg, client, make_project,  # noqa: E402,F401
                                 runner, storage, usage)
from test_cloud_auth import UID_A, UID_B  # noqa: E402

NOW = datetime(2026, 12, 1, 12, 0, tzinfo=timezone.utc)
OLD = (NOW - timedelta(days=8)).isoformat()
FRESH = (NOW - timedelta(days=6)).isoformat()
EXPIRED = "Video expired — upload it again to keep editing"


def ts(delta: timedelta) -> float:
    return (NOW - delta).timestamp()


def old_file(path: Path, age: timedelta) -> None:
    os.utime(path, (ts(age), ts(age)))


@pytest.fixture
def root(tmp_path):
    return tmp_path / "data"


def user_home(root, uid=UID_A) -> Path:
    h = root / "users" / uid
    h.mkdir(parents=True, exist_ok=True)
    return h


def make(root, uid=UID_A, token="aaaa", activity=OLD, language="en"):
    """A cloud-shaped project: work/<token>/source.mp4 with proxy, local exports, transcript and edl; R2 not touched."""
    home = user_home(root, uid)
    folder = home / "work" / token
    folder.mkdir(parents=True)
    src = folder / "source.mp4"
    src.write_bytes(b"video:60")
    with use_home(home):
        p = create_project(src, language, "tiktok", "clip")
        p.last_activity = activity
        save_project(p)
    wd = folder / "source.edit"
    (wd / "proxy.mp4").write_bytes(b"proxy")
    (wd / "proxy.json").write_text("{}")
    (wd / "transcript.main.json").write_text('{"words": []}')
    (wd / "clip-h01.edl.json").write_text('{"cuts": []}')
    (folder / "exports").mkdir()
    (folder / "exports" / "out.mp4").write_bytes(b"render")
    return p, folder, wd


def put_exports(storage, uid, pid, names=("out.mp4",)):
    for n in names:
        storage.objects[cs.export_key(uid, pid, n)] = b"x"


def settings_of(root, pid, uid=UID_A) -> dict:
    with use_home(user_home(root, uid)):
        return load_project(pid).settings


def write_job(root, job_id, state, project_id, uid=UID_A, age=timedelta(days=0)):
    d = user_home(root, uid) / "jobs"
    d.mkdir(exist_ok=True)
    f = d / f"{job_id}.json"
    f.write_text(json.dumps({"job": {"id": job_id, "project_id": project_id, "kind": "analyze", "state": state,
                                     "stage": "", "pct": 0.0, "message": "", "error": None, "result": None},
                             "finalized": state in ("done", "failed", "cancelled")}))
    old_file(f, age)
    return f


# ---- projects ---------------------------------------------------------------------------------------------------

def test_old_project_loses_its_media_and_keeps_its_documents(root, storage):
    p, folder, wd = make(root)
    put_exports(storage, UID_A, p.id)
    other = cs.export_key(UID_A, "someoneelse", "x.mp4")
    storage.objects[other] = b"keep"
    home = user_home(root)
    (home / "brand.json").write_text('{"name": "b"}')
    doc = home / "stories" / "s0123456789ab" / "story.doc.json"
    doc.parent.mkdir(parents=True)
    doc.write_text('{"id": "s0123456789ab"}')
    (home / "assets").mkdir()
    (home / "assets" / "logo.png").write_bytes(b"png")
    counts = cloud_cleanup.sweep(root, storage, NOW)
    assert counts["projects_expired"] == 1 and counts["errors"] == 0
    assert not (folder / "source.mp4").exists() and not (wd / "proxy.mp4").exists()
    assert not (wd / "proxy.json").exists() and not (folder / "exports").exists()
    assert cs.export_key(UID_A, p.id, "out.mp4") not in storage.objects
    assert other in storage.objects
    assert settings_of(root, p.id)["expired"] is True
    assert (wd / "transcript.main.json").is_file() and (wd / "clip-h01.edl.json").is_file()
    assert (wd / "project.json").is_file()
    assert (home / "brand.json").is_file() and doc.is_file() and (home / "assets" / "logo.png").is_file()


def test_project_within_seven_days_is_kept(root, storage):
    p, folder, _wd = make(root, activity=FRESH)
    put_exports(storage, UID_A, p.id)
    counts = cloud_cleanup.sweep(root, storage, NOW)
    assert counts["projects_expired"] == 0
    assert (folder / "source.mp4").is_file() and cs.export_key(UID_A, p.id, "out.mp4") in storage.objects
    assert "expired" not in settings_of(root, p.id)


def test_exactly_seven_days_is_kept_and_a_second_later_is_expired(root, storage):
    edge, *_ = make(root, token="edge", activity=(NOW - timedelta(days=7)).isoformat())
    past, *_ = make(root, token="past", activity=(NOW - timedelta(days=7, seconds=1)).isoformat())
    cloud_cleanup.sweep(root, storage, NOW)
    assert "expired" not in settings_of(root, edge.id) and settings_of(root, past.id)["expired"] is True


def test_project_without_last_activity_uses_its_file_time(root, storage):
    p, folder, wd = make(root, activity=None)
    pf = wd / "project.json"
    created = json.loads(pf.read_text())
    assert "last_activity" not in created
    created["created"] = (NOW - timedelta(days=30)).isoformat()
    pf.write_text(json.dumps(created))
    old_file(pf, timedelta(days=2))  # modified 2 days ago: that is its activity, so it stays
    assert cloud_cleanup.sweep(root, storage, NOW)["projects_expired"] == 0
    assert (folder / "source.mp4").is_file()
    old_file(pf, timedelta(days=9))
    assert cloud_cleanup.sweep(root, storage, NOW)["projects_expired"] == 1
    assert not (folder / "source.mp4").exists()


def test_sweep_is_idempotent(root, storage):
    p, *_ = make(root)
    put_exports(storage, UID_A, p.id)
    first = cloud_cleanup.sweep(root, storage, NOW)
    again = cloud_cleanup.sweep(root, storage, NOW)
    assert first["projects_expired"] == 1 and again["projects_expired"] == 0 and again["errors"] == 0
    assert settings_of(root, p.id)["expired"] is True


@pytest.mark.parametrize("state", ["queued", "running"])
def test_project_with_an_active_job_is_skipped_until_it_ends(root, storage, state):
    p, folder, _wd = make(root)
    put_exports(storage, UID_A, p.id)
    write_job(root, "j1", state, p.id)
    assert cloud_cleanup.sweep(root, storage, NOW)["projects_expired"] == 0
    assert (folder / "source.mp4").is_file() and cs.export_key(UID_A, p.id, "out.mp4") in storage.objects
    write_job(root, "j1", "done", p.id)
    assert cloud_cleanup.sweep(root, storage, NOW)["projects_expired"] == 1


def test_a_job_of_another_project_does_not_block(root, storage):
    make(root)
    write_job(root, "j1", "running", "someotherproject")
    assert cloud_cleanup.sweep(root, storage, NOW)["projects_expired"] == 1


def test_projects_sharing_a_source_expire_together_or_not_at_all(root, storage):
    make(root)
    folder = user_home(root) / "work" / "aaaa"
    home = user_home(root)
    with use_home(home):
        reel = create_project(folder / "source.mp4", "en", "tiktok", "reel")
        reel.last_activity = FRESH  # the same video, still in use
        save_project(reel)
    assert cloud_cleanup.sweep(root, storage, NOW)["projects_expired"] == 0
    assert (folder / "source.mp4").is_file()
    with use_home(home):
        reel.last_activity = OLD
        save_project(reel)
    assert cloud_cleanup.sweep(root, storage, NOW)["projects_expired"] == 2
    assert not (folder / "source.mp4").exists()


def test_one_users_error_does_not_stop_the_others(root, storage):
    pa, *_ = make(root, UID_A)
    pb, folder_b, _ = make(root, UID_B, token="bbbb")

    class Flaky(cs.MemoryStorage):
        def delete_prefix(self, prefix):
            if UID_A in prefix:
                raise RuntimeError("r2 down")
            return super().delete_prefix(prefix)

    counts = cloud_cleanup.sweep(root, Flaky(), NOW)
    assert counts["errors"] >= 1 and counts["projects_expired"] == 1
    assert not (folder_b / "source.mp4").exists()
    assert "expired" not in settings_of(root, pa.id)  # not marked: it is retried tomorrow
    assert cloud_cleanup.sweep(root, storage, NOW)["projects_expired"] == 1


def test_a_users_folder_without_a_registry_is_fine(root, storage):
    user_home(root)
    (root / "users" / "stray.txt").write_text("x")
    assert cloud_cleanup.sweep(root, storage, NOW)["errors"] == 0
    assert cloud_cleanup.sweep(root / "nowhere", storage, NOW)["errors"] == 0


# ---- stories ----------------------------------------------------------------------------------------------------

def make_story(root, sid, updated):
    home = user_home(root)
    d = home / "stories" / sid
    (d / "output").mkdir(parents=True)
    (d / "output" / "story.mp4").write_bytes(b"render")
    doc = {**stories.default_doc(sid, "t"), "updated": updated}
    (d / "story.doc.json").write_text(json.dumps(doc))
    return d


def test_story_outputs_follow_the_story_docs_updated_time(root, storage):
    old = make_story(root, "s0000000000a", OLD)
    new = make_story(root, "s0000000000b", FRESH)
    storage.objects[cs.export_key(UID_A, "s0000000000a", "story.mp4")] = b"x"
    storage.objects[cs.export_key(UID_A, "s0000000000b", "story.mp4")] = b"x"
    counts = cloud_cleanup.sweep(root, storage, NOW)
    assert counts["story_outputs"] == 1
    assert not (old / "output").exists() and (old / "story.doc.json").is_file()
    assert (new / "output" / "story.mp4").is_file()
    assert list(storage.objects) == [cs.export_key(UID_A, "s0000000000b", "story.mp4")]
    assert cloud_cleanup.sweep(root, storage, NOW)["story_outputs"] == 0


def test_a_story_with_an_active_job_keeps_its_output(root, storage):
    old = make_story(root, "s0000000000a", OLD)
    write_job(root, "j9", "running", "s0000000000a")
    assert cloud_cleanup.sweep(root, storage, NOW)["story_outputs"] == 0
    assert (old / "output" / "story.mp4").is_file()


# ---- uploads, zips, job files -----------------------------------------------------------------------------------

def pending(root, upload_id, age, uid=UID_A, claimed_age=None):
    d = user_home(root, uid) / "uploads"
    d.mkdir(exist_ok=True)
    rec = d / f"{upload_id}.json"
    rec.write_text(json.dumps({"kind": "video", "name": "a.mp4", "size": 1, "content_type": "video/mp4",
                               "created": ts(age)}))
    if claimed_age is not None:
        marker = d / f"{upload_id}.claimed"
        marker.write_bytes(b"")
        old_file(marker, claimed_age)
    return rec


def test_uploads_never_committed_after_a_day_are_deleted_with_their_object(root, storage):
    old_id, new_id = "a" * 32, "b" * 32
    old_rec, new_rec = pending(root, old_id, timedelta(hours=25)), pending(root, new_id, timedelta(hours=23))
    storage.objects[cs.upload_key(UID_A, old_id)] = b"x"
    storage.objects[cs.upload_key(UID_A, new_id)] = b"x"
    counts = cloud_cleanup.sweep(root, storage, NOW)
    assert counts["upload_objects"] == 1
    assert not old_rec.exists() and new_rec.exists()
    assert list(storage.objects) == [cs.upload_key(UID_A, new_id)]


def test_stale_claim_is_removed_and_a_fresh_one_is_left(root, storage):
    stale_id, fresh_id = "c" * 32, "d" * 32
    pending(root, stale_id, timedelta(hours=3), claimed_age=timedelta(hours=2))
    pending(root, fresh_id, timedelta(hours=3), claimed_age=timedelta(minutes=10))
    counts = cloud_cleanup.sweep(root, storage, NOW)
    up = user_home(root) / "uploads"
    assert counts["stale_claims"] == 1
    assert not (up / f"{stale_id}.claimed").exists() and (up / f"{stale_id}.json").exists()  # retry or expire
    assert (up / f"{fresh_id}.claimed").exists()


def test_a_claimed_upload_older_than_a_day_with_a_stale_claim_expires(root, storage):
    upload_id = "e" * 32
    pending(root, upload_id, timedelta(hours=30), claimed_age=timedelta(hours=5))
    storage.objects[cs.upload_key(UID_A, upload_id)] = b"x"
    counts = cloud_cleanup.sweep(root, storage, NOW)
    up = user_home(root) / "uploads"
    assert counts["stale_claims"] == 1 and counts["upload_objects"] == 1
    assert list(up.glob("*")) == [] and storage.objects == {}


def test_a_claimed_upload_under_a_fresh_claim_is_never_expired(root, storage):
    upload_id = "f" * 32
    pending(root, upload_id, timedelta(hours=30), claimed_age=timedelta(minutes=5))  # a commit is running right now
    storage.objects[cs.upload_key(UID_A, upload_id)] = b"x"
    assert cloud_cleanup.sweep(root, storage, NOW)["upload_objects"] == 0
    assert cs.upload_key(UID_A, upload_id) in storage.objects


def test_old_upload_objects_without_a_record_are_deleted_but_recorded_ones_kept(root, storage):
    user_home(root)
    orphan, started = cs.upload_key(UID_A, "1" * 32), cs.upload_key(UID_A, "2" * 32)
    pending(root, "2" * 32, timedelta(hours=2))
    storage.objects[orphan] = b"x"
    storage.objects[started] = b"x"
    storage.modified[orphan] = NOW - timedelta(hours=30)
    storage.modified[started] = NOW - timedelta(hours=30)
    counts = cloud_cleanup.sweep(root, storage, NOW)
    assert counts["upload_objects"] == 1 and list(storage.objects) == [started]


def test_zips_older_than_a_day_are_deleted(root, storage):
    user_home(root)
    old, new = cs.zip_key(UID_A, "b1"), cs.zip_key(UID_A, "b2")
    storage.objects[old] = storage.objects[new] = b"z"
    storage.modified[old] = NOW - timedelta(hours=25)
    storage.modified[new] = NOW - timedelta(hours=23)
    counts = cloud_cleanup.sweep(root, storage, NOW)
    assert counts["zips"] == 1 and list(storage.objects) == [new]


def test_only_terminal_job_files_older_than_seven_days_are_deleted(root, storage):
    user_home(root)
    gone = [write_job(root, f"d{i}", s, "p", age=timedelta(days=8)) for i, s in
            enumerate(("done", "failed", "cancelled"))]
    kept = [write_job(root, "recent", "done", "p", age=timedelta(days=6)),
            write_job(root, "running", "running", "p", age=timedelta(days=30)),
            write_job(root, "queued", "queued", "p", age=timedelta(days=30))]
    bad = user_home(root) / "jobs" / "junk.json"
    bad.write_text("{not json")
    old_file(bad, timedelta(days=30))
    counts = cloud_cleanup.sweep(root, storage, NOW)
    assert counts["job_files"] == 3 and counts["errors"] == 0
    assert not any(f.exists() for f in gone) and all(f.is_file() for f in kept) and bad.is_file()


# ---- the engine -------------------------------------------------------------------------------------------------

def test_expired_project_dict_and_the_400_copy(client, storage, cfg, runner):
    r = make_project(client, storage)
    pid = r.json()["id"]
    assert "expired" not in r.json()
    counts = cloud_cleanup.sweep(cfg.data_root, storage, datetime.now(timezone.utc) + timedelta(days=8))
    assert counts["projects_expired"] == 1
    got = client.get(f"/projects/{pid}", headers=A).json()
    assert got["expired"] is True
    for call in (lambda: analyze(client, pid),
                 lambda: client.post(f"/projects/{pid}/export", headers=A,
                                     json={"clips": ["h01"], "formats": ["9:16"], "quality": "normal"})):
        res = call()
        assert res.status_code == 400 and res.json()["detail"] == EXPIRED
    assert runner.calls == []


def test_desktop_project_is_never_expired(tmp_path):
    src = tmp_path / "v.mp4"
    src.write_bytes(b"x")
    with use_home(tmp_path / "desk"):
        p = create_project(src, "en", "tiktok", "clip")
    import studio_server

    assert "expired" not in studio_server._project_dict(p)
    assert cloud_cleanup.sweep(tmp_path / "nodata", cs.MemoryStorage(), NOW)["projects_expired"] == 0


# ---- running safely beside the API ------------------------------------------------------------------------------

def test_a_project_used_after_the_sweep_looked_keeps_its_source(root, storage):
    """An analyse that starts between the sweep's first look and its delete (it stamps last_activity and queues
    a job under the user's lock) must not lose the video."""
    import contextlib

    p, folder, _wd = make(root)
    put_exports(storage, UID_A, p.id)

    @contextlib.contextmanager
    def lock(uid):
        with use_home(user_home(root)):
            fresh = load_project(p.id)
            fresh.last_activity = NOW.isoformat()
            save_project(fresh)
        write_job(root, "late", "queued", p.id)
        yield

    counts = cloud_cleanup.sweep(root, storage, NOW, lock=lock)
    assert counts["projects_expired"] == 0
    assert (folder / "source.mp4").is_file() and cs.export_key(UID_A, p.id, "out.mp4") in storage.objects
    assert "expired" not in settings_of(root, p.id)


def test_a_job_started_after_the_sweep_looked_also_keeps_the_source(root, storage):
    import contextlib

    p, folder, _wd = make(root)

    @contextlib.contextmanager
    def lock(uid):
        write_job(root, "late", "running", p.id)  # only the job file appears; last_activity is unchanged
        yield

    assert cloud_cleanup.sweep(root, storage, NOW, lock=lock)["projects_expired"] == 0
    assert (folder / "source.mp4").is_file()


def test_sweep_takes_the_users_lock_around_the_delete(root, storage):
    import contextlib

    make(root)
    seen = []

    @contextlib.contextmanager
    def lock(uid):
        seen.append(("in", uid))
        yield
        seen.append(("out", uid))

    assert cloud_cleanup.sweep(root, storage, NOW, lock=lock)["projects_expired"] == 1
    assert seen == [("in", UID_A), ("out", UID_A)]


def test_analyse_stamps_activity(client, storage, cfg, runner):
    r = make_project(client, storage)
    pid = r.json()["id"]
    before = r.json()["last_activity"]
    assert analyze(client, pid).status_code == 200
    assert client.get(f"/projects/{pid}", headers=A).json()["last_activity"] > before


def test_sweep_route_rejects_without_the_token_and_runs_with_it(client, cfg, storage, monkeypatch):

    calls = []
    committed = []

    def fake(data_root, store, now, **kw):
        calls.append((data_root, store, kw.get("lock")))
        return {"projects_expired": 0}

    monkeypatch.setattr(cloud_cleanup, "sweep", fake)
    cfg.commit = lambda: committed.append(1)
    # no token configured: the route does not exist
    assert client.post("/internal/sweep", headers={"Authorization": "Bearer anything"}).status_code == 404
    cfg.sweep_token = "s3cret-sweep"
    assert client.post("/internal/sweep").status_code == 401
    assert client.post("/internal/sweep", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.post("/internal/sweep", headers=A).status_code == 401  # a user's JWT is not the sweep token
    assert calls == []
    r = client.post("/internal/sweep", headers={"Authorization": "Bearer s3cret-sweep"})
    assert r.status_code == 202 and r.json() == {"started": True}
    client.app.state.sweep_thread.join(5)
    assert len(calls) == 1 and calls[0][0] == cfg.data_root and calls[0][1] is storage and calls[0][2] is not None
    assert committed == [1] and client.app.state.last_sweep == {"projects_expired": 0}
    assert client.app.state.sweeping is False
