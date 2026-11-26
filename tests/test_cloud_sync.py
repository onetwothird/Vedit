"""Cloud sync routes: the desktop app and the website see the same projects, with revisions and conflict copies."""
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="needs the studio extra")
jwt = pytest.importorskip("jwt", reason="needs the cloud extra")
from fastapi.testclient import TestClient  # noqa: E402

import cloud_cleanup  # noqa: E402
import cloud_storage as cs  # noqa: E402
import studio_server  # noqa: E402
import sync_bundle  # noqa: E402
from cloud_config import CloudConfig  # noqa: E402
from cloud_usage import MemoryUsage  # noqa: E402
from common import load_json, workdir_for  # noqa: E402
from studio_projects import load_project, save_project, use_home  # noqa: E402
from studio_server import create_app  # noqa: E402
from test_cloud_auth import SECRET, UID_A, UID_B, auth, make_token  # noqa: E402
from test_cloud_outputs import Runner, add_clip, wait_state  # noqa: E402
from test_cloud_projects import fake_probe, home_of, make_project, upload  # noqa: E402

A = auth(make_token(UID_A))
B = auth(make_token(UID_B))
NO_VIDEO = "Open in the desktop app to use the video"


class NoChargeUsage(MemoryUsage):
    """Fails the test if anything charges minutes."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.charges = []

    def charge(self, uid, job_id, seconds):
        self.charges.append((uid, job_id, seconds))
        return super().charge(uid, job_id, seconds)


@pytest.fixture(autouse=True)
def _fakes(monkeypatch):
    monkeypatch.setattr(studio_server, "cached_probe", fake_probe)


@pytest.fixture
def storage():
    return cs.MemoryStorage()


@pytest.fixture
def usage():
    return NoChargeUsage(default_limit=3600)


@pytest.fixture
def runner():
    return Runner()


@pytest.fixture
def cfg(tmp_path, storage, usage, runner):
    return CloudConfig(data_root=tmp_path / "data", origins=("https://web.example.test",),
                       supabase_url="https://x.supabase.test", jwt_secret=SECRET, storage=storage, usage=usage,
                       runner_factory=lambda: runner)


@pytest.fixture
def client(cfg, runner):
    with TestClient(create_app("desktop-token", cloud=cfg)) as c:
        yield c
        runner.release_all()


def bundle(title="Talk", note="v1"):
    """What the desktop app pushes: one clip, its edl, highlights; every path a placeholder."""
    return {"format": 1, "id": None, "rev": 0, "title": title,
            "project": {"type": "clip", "language": "en", "platform": "reels", "source": "source:",
                        "clips": [{"highlight": {"id": "h01", "start": 0, "end": 4, "title": "Hi"}, "kept": True,
                                   "edl_file": "clip-h01.edl.json"}],
                        "settings": {"title": title}},
            "files": {"clip-h01.edl.json": {"sources": {"main": "source:"}, "output": {"path": "", "aspect": "9:16"},
                                            "clips": [{"src": "main", "in": 0.0, "out": 4.0}], "note": note},
                      "highlights.json": {"source": "source:", "highlights": [], "reason": ""}},
            "brand": None, "has_video_in_cloud": False}


def push_new(client, headers=A, **kw):
    r = client.put("/sync/projects/new", headers=headers, json={"bundle": bundle(**kw)})
    assert r.status_code == 200, r.text
    return r.json()


def listed(client, pid, headers=A):
    return next(x for x in client.get("/sync/projects", headers=headers).json() if x["id"] == pid)


def attach(client, storage, pid, data=b"video:600", name="talk.mp4", headers=A):
    key = upload(client, storage, data, headers, name=name)
    return client.post(f"/sync/projects/{pid}/video", headers=headers, json={"upload_key": key})


# ---- list / get / put ------------------------------------------------------------------------------------------

def test_push_list_get_put_happy_path(client):
    made = push_new(client)
    pid = made["id"]
    assert made == {"id": pid, "rev": 1}
    row = listed(client, pid)
    assert row["title"] == "Talk" and row["rev"] == 1 and row["has_video_in_cloud"] is False
    assert row["expired"] is False and row["updated"]
    assert set(row) == {"id", "title", "rev", "updated", "has_video_in_cloud", "expired"}
    got = client.get(f"/sync/projects/{pid}", headers=A).json()
    assert got["id"] == pid and got["rev"] == 1 and got["has_video_in_cloud"] is False and "video_url" not in got
    assert got["files"]["clip-h01.edl.json"]["note"] == "v1"
    assert got["files"]["clip-h01.edl.json"]["sources"]["main"] == "source:"  # no Volume path leaks
    r = client.put(f"/sync/projects/{pid}", headers=A, json={"base_rev": 1, "bundle": bundle(note="v2")})
    assert r.status_code == 200, r.text
    assert r.json() == {"id": pid, "rev": 2}
    got = client.get(f"/sync/projects/{pid}", headers=A).json()
    assert got["rev"] == 2 and got["files"]["clip-h01.edl.json"]["note"] == "v2"
    assert [x["id"] for x in client.get("/sync/projects", headers=A).json()] == [pid]  # updated in place


def test_website_projects_are_listed_with_their_video(client, storage, cfg):
    pid = make_project(client, storage).json()["id"]
    row = listed(client, pid)
    assert row["has_video_in_cloud"] is True and row["rev"] == 0
    got = client.get(f"/sync/projects/{pid}", headers=A).json()
    assert got["id"] == pid and got["has_video_in_cloud"] is True
    url = got["video_url"]
    assert url.startswith("mem://get/") and "expires=86400" in url
    key = url[len("mem://get/"):].split("?")[0]
    assert storage.objects[key] == b"video:600"
    assert key.startswith(cs.zips_prefix(UID_A))  # the 24 h download area, counted toward storage
    # a website project pushed from the desktop (it pulled it, base_rev 0) is updated in place
    r = client.put(f"/sync/projects/{pid}", headers=A, json={"base_rev": 0, "bundle": bundle(note="desk")})
    assert r.json() == {"id": pid, "rev": 1}
    assert len(client.get("/sync/projects", headers=A).json()) == 1
    with use_home(home_of(cfg)):
        assert Path(load_project(pid).source).read_bytes() == b"video:600"  # its own video stays


def test_stale_base_rev_saves_a_copy_and_leaves_the_original(client):
    pid = push_new(client)["id"]
    assert client.put(f"/sync/projects/{pid}", headers=A,
                      json={"base_rev": 1, "bundle": bundle(note="first")}).json()["rev"] == 2
    # a second push that started from the old revision too
    r = client.put(f"/sync/projects/{pid}", headers=A, json={"base_rev": 1, "bundle": bundle(note="second")})
    assert r.status_code == 200, r.text
    body = r.json()
    new = body["id"]
    assert body == {"id": new, "rev": 1, "conflict": True, "original": pid} and new != pid
    orig = client.get(f"/sync/projects/{pid}", headers=A).json()
    assert orig["rev"] == 2 and orig["files"]["clip-h01.edl.json"]["note"] == "first"
    copy = client.get(f"/sync/projects/{new}", headers=A).json()
    assert copy["rev"] == 1 and copy["title"] == "Talk (copy)" and copy["files"]["clip-h01.edl.json"]["note"] == "second"
    assert listed(client, new)["title"] == "Talk (copy)"


def test_two_pushes_from_the_same_revision_cannot_both_win(client):
    pid = push_new(client)["id"]
    results = []

    def push(note):
        results.append(client.put(f"/sync/projects/{pid}", headers=A,
                                  json={"base_rev": 1, "bundle": bundle(note=note)}).json())

    threads = [threading.Thread(target=push, args=(n,)) for n in ("x", "y")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(bool(r.get("conflict")) for r in results) == [False, True]
    won = next(r for r in results if not r.get("conflict"))
    assert won == {"id": pid, "rev": 2}
    assert len(client.get("/sync/projects", headers=A).json()) == 2


def test_another_users_or_unknown_id_is_404(client, storage):
    pid = push_new(client)["id"]
    key_b = upload(client, storage, headers=B)
    calls = [lambda h, i: client.get(f"/sync/projects/{i}", headers=h),
             lambda h, i: client.put(f"/sync/projects/{i}", headers=h, json={"base_rev": 1, "bundle": bundle()}),
             lambda h, i: client.post(f"/sync/projects/{i}/video", headers=h, json={"upload_key": key_b}),
             lambda h, i: client.delete(f"/sync/projects/{i}/video", headers=h)]
    for call in calls:
        theirs, unknown = call(B, pid), call(B, "0123456789ab")
        assert theirs.status_code == unknown.status_code == 404
        assert theirs.json() == unknown.json()
    assert client.get("/sync/projects", headers=B).json() == []
    assert client.get(f"/sync/projects/{pid}", headers=A).json()["rev"] == 1  # untouched
    assert storage.size(key_b) is not None  # B's upload was never consumed


def test_bad_bundle_is_400_and_nothing_is_made(client):
    bad = bundle()
    bad["files"]["../evil.json"] = {}
    r = client.put("/sync/projects/new", headers=A, json={"bundle": bad})
    assert r.status_code == 400
    r = client.put("/sync/projects/new", headers=A, json={"bundle": "nope"})
    assert r.status_code == 400
    assert client.get("/sync/projects", headers=A).json() == []


def test_body_over_the_bundle_limit_is_refused_plainly(client, monkeypatch):
    monkeypatch.setattr(sync_bundle, "MAX_BUNDLE_BYTES", 2000)
    big = bundle(note="x" * 200_000)
    r = client.put("/sync/projects/new", headers=A, json={"bundle": big})
    assert r.status_code == 413
    assert r.json()["detail"] == "Project bundle is too large (limit 20 MB)"
    assert client.get("/sync/projects", headers=A).json() == []


def test_sync_routes_are_cloud_only(tmp_path, monkeypatch):
    monkeypatch.setenv("VEDIT_HOME", str(tmp_path / "home"))
    with TestClient(create_app("desk-token")) as c:
        h = {"Authorization": "Bearer desk-token"}
        assert c.get("/sync/projects", headers=h).status_code == 404
        assert c.put("/sync/projects/new", headers=h, json={"bundle": bundle()}).status_code in (404, 405)


# ---- the video ---------------------------------------------------------------------------------------------------

def test_attaching_a_video_charges_nothing_and_counts_toward_storage(client, storage, usage, cfg):
    pid = push_new(client)["id"]
    before = client.get("/usage", headers=A).json()["storage_bytes"]
    data = b"video:900:" + b"." * 5000
    key = upload(client, storage, data)
    r = client.post(f"/sync/projects/{pid}/video", headers=A, json={"upload_key": key})
    assert r.status_code == 200, r.text
    assert r.json()["has_video_in_cloud"] is True
    assert usage.charges == [] and client.get("/usage", headers=A).json()["seconds_used"] == 0
    assert client.get("/usage", headers=A).json()["storage_bytes"] >= before + len(data)
    assert storage.size(key) is None  # the upload is consumed
    assert not list((home_of(cfg) / "uploads").glob("*.json"))
    row = listed(client, pid)
    assert row["has_video_in_cloud"] is True and row["rev"] == 2  # web_video turned on: the bundle changed
    got = client.get(f"/sync/projects/{pid}", headers=A).json()
    assert got["project"]["settings"]["web_video"] is True and got["video_url"]
    with use_home(home_of(cfg)):
        p = load_project(pid)
        assert Path(p.source).read_bytes() == data
        edl = load_json(workdir_for(p.source) / "clip-h01.edl.json")
        assert edl["sources"]["main"] == p.source  # the edit plays the attached video
    # usable on the web now: analyse is guarded only by the usual checks (and charged there, not here)
    assert client.post(f"/projects/{pid}/analyze", headers=A, json={}).status_code == 200


def test_attach_refuses_other_kinds_and_another_users_upload(client, storage):
    pid = push_new(client)["id"]
    img = upload(client, storage, b"png", name="a.png", kind="image", ctype="image/png")
    assert client.post(f"/sync/projects/{pid}/video", headers=A, json={"upload_key": img}).status_code == 404
    theirs = upload(client, storage, headers=B)
    assert client.post(f"/sync/projects/{pid}/video", headers=A, json={"upload_key": theirs}).status_code == 404
    not_video = upload(client, storage, b"text")
    r = client.post(f"/sync/projects/{pid}/video", headers=A, json={"upload_key": not_video})
    assert r.status_code == 400 and r.json()["detail"] == "This file is not a video"
    assert listed(client, pid)["has_video_in_cloud"] is False


def test_attach_over_the_storage_limit_is_refused(client, storage, monkeypatch):
    import cloud_assets

    pid = push_new(client)["id"]
    key = upload(client, storage, b"video:60")
    monkeypatch.setattr(cloud_assets, "STORAGE_LIMIT", 1)
    r = client.post(f"/sync/projects/{pid}/video", headers=A, json={"upload_key": key})
    assert r.status_code == 400 and "10 GB" in r.json()["detail"]
    assert listed(client, pid)["has_video_in_cloud"] is False


def test_reattaching_replaces_and_deletes_the_previous_video(client, storage, cfg):
    pid = push_new(client)["id"]
    assert attach(client, storage, pid, b"video:100", "a.mp4").status_code == 200
    with use_home(home_of(cfg)):
        old = Path(load_project(pid).source)
    url = client.get(f"/sync/projects/{pid}", headers=A).json()["video_url"]
    published = url[len("mem://get/"):].split("?")[0]
    assert storage.size(published) is not None
    rev = listed(client, pid)["rev"]
    assert attach(client, storage, pid, b"video:200", "b.mov").status_code == 200
    with use_home(home_of(cfg)):
        p = load_project(pid)
    new = Path(p.source)
    assert new.suffix == ".mov" and new.read_bytes() == b"video:200"
    with use_home(home_of(cfg)):
        wd = workdir_for(p.source)
        assert load_json(wd / "clip-h01.edl.json")["sources"]["main"] == p.source  # the edits follow the new file
        assert load_json(wd / "highlights.json")["source"] == p.source
    assert not old.exists() and [f.name for f in new.parent.glob("source.*") if f.is_file()] == [new.name]
    assert storage.size(published) is None  # the old published copy is gone too
    assert listed(client, pid)["rev"] == rev  # web_video was already on: the bundle did not change
    url = client.get(f"/sync/projects/{pid}", headers=A).json()["video_url"]
    assert storage.objects[url[len("mem://get/"):].split("?")[0]] == b"video:200"


def test_deleting_the_video_keeps_the_edits(client, storage, cfg):
    pid = push_new(client, note="keep me")["id"]
    attach(client, storage, pid)
    with use_home(home_of(cfg)):
        src = Path(load_project(pid).source)
    rev = listed(client, pid)["rev"]
    r = client.delete(f"/sync/projects/{pid}/video", headers=A)
    assert r.status_code == 200, r.text
    assert r.json()["has_video_in_cloud"] is False
    assert not src.exists()
    row = listed(client, pid)
    assert row["has_video_in_cloud"] is False and row["rev"] == rev + 1  # web_video turned off
    got = client.get(f"/sync/projects/{pid}", headers=A).json()
    assert "video_url" not in got and got["files"]["clip-h01.edl.json"]["note"] == "keep me"
    assert got["project"]["clips"][0]["highlight"]["id"] == "h01"
    r = client.post(f"/projects/{pid}/analyze", headers=A, json={})
    assert r.status_code == 409 and r.json()["detail"] == NO_VIDEO


def test_routes_that_need_the_video_are_409(client):
    pid = push_new(client)["id"]
    calls = {"analyze": lambda: client.post(f"/projects/{pid}/analyze", headers=A, json={}),
             "export": lambda: client.post(f"/projects/{pid}/export", headers=A, json={"clips": ["h01"]}),
             "media": lambda: client.get(f"/projects/{pid}/media", headers=A),
             "filmstrip": lambda: client.get(f"/projects/{pid}/filmstrip", headers=A),
             "filmstrip.jpg": lambda: client.get(f"/projects/{pid}/filmstrip.jpg", headers=A),
             "clip": lambda: client.get(f"/projects/{pid}/clips/h01", headers=A),
             "patch": lambda: client.patch(f"/projects/{pid}/clips/h01", headers=A,
                                           json={"op": "patch", "section": "captions", "patch": {"style": "pop"}}),
             "broll": lambda: client.post(f"/projects/{pid}/clips/h01/broll", headers=A,
                                          json={"folder": "", "assets": ["asset:a0123456789ab"]})}
    for name, call in calls.items():
        r = call()
        assert r.status_code == 409, (name, r.text)
        assert r.json()["detail"] == NO_VIDEO, name
    # the project itself still opens and lists
    assert client.get(f"/projects/{pid}", headers=A).status_code == 200


# ---- revisions follow web edits ----------------------------------------------------------------------------------

def test_web_edits_bump_the_revision(client, storage, cfg, monkeypatch):
    monkeypatch.setattr(studio_server, "_clip_view", lambda wd, path: {"edl": load_json(path)})
    pid = make_project(client, storage).json()["id"]
    add_clip(cfg, pid, "h01", 10)
    assert listed(client, pid)["rev"] == 0
    r = client.patch(f"/projects/{pid}/clips/h01", headers=A,
                     json={"op": "patch", "section": "captions", "patch": {"style": "pop"}})
    assert r.status_code == 200, r.text
    assert listed(client, pid)["rev"] == 1
    assert client.put(f"/projects/{pid}/clips/h01/kept", headers=A, json={"kept": False}).status_code == 200
    assert listed(client, pid)["rev"] == 2
    # a stale desktop push now conflicts instead of overwriting the web edit
    r = client.put(f"/sync/projects/{pid}", headers=A, json={"base_rev": 0, "bundle": bundle()})
    assert r.json()["conflict"] is True


def test_web_analysis_bumps_the_revision(client, storage):
    pid = make_project(client, storage).json()["id"]
    job = client.post(f"/projects/{pid}/analyze", headers=A, json={}).json()
    assert wait_state(client, job["id"])["state"] == "done"
    assert listed(client, pid)["rev"] == 1


def test_expired_videos_show_in_the_list(client, storage, cfg):
    pid = make_project(client, storage).json()["id"]
    cloud_cleanup.sweep(cfg.data_root, storage, datetime.now(timezone.utc) + timedelta(days=8))
    row = listed(client, pid)
    assert row["expired"] is True and row["has_video_in_cloud"] is False
    assert "video_url" not in client.get(f"/sync/projects/{pid}", headers=A).json()


def test_attaching_after_expiry_makes_it_usable_again(client, storage, cfg):
    pid = push_new(client)["id"]
    with use_home(home_of(cfg)):
        p = load_project(pid)
        p.settings["expired"] = True
        save_project(p)
    assert attach(client, storage, pid).status_code == 200
    row = listed(client, pid)
    assert row["expired"] is False and row["has_video_in_cloud"] is True


def test_pushes_and_pulls_never_charge(client, storage, usage):
    pid = push_new(client)["id"]
    client.put(f"/sync/projects/{pid}", headers=A, json={"base_rev": 1, "bundle": bundle()})
    client.put(f"/sync/projects/{pid}", headers=A, json={"base_rev": 1, "bundle": bundle()})
    attach(client, storage, pid)
    client.get(f"/sync/projects/{pid}", headers=A)
    client.delete(f"/sync/projects/{pid}/video", headers=A)
    assert usage.charges == []
    assert client.get("/usage", headers=A).json()["seconds_used"] == 0


# ---- review fixes: length cap, busy pushes, published-copy accounting ---------------------------------------------

def test_attach_over_an_hour_is_refused_and_the_old_video_stays(client, storage, cfg):
    pid = push_new(client)["id"]
    assert attach(client, storage, pid, b"video:600", "a.mp4").status_code == 200
    with use_home(home_of(cfg)):
        src = Path(load_project(pid).source)
    rev = listed(client, pid)["rev"]
    key = upload(client, storage, b"video:10800", name="long.mp4")  # 3 h
    r = client.post(f"/sync/projects/{pid}/video", headers=A, json={"upload_key": key})
    assert r.status_code == 400 and r.json()["detail"] == "Videos can be up to 60 minutes"
    assert storage.size(key) is None  # the upload is deleted and its record gone, like the other refusals
    assert not list((home_of(cfg) / "uploads").glob("*.json"))
    assert not list((home_of(cfg) / "uploads").glob("*.claimed"))
    assert src.read_bytes() == b"video:600"
    assert not list(src.parent.glob("incoming-*"))
    row = listed(client, pid)
    assert row["has_video_in_cloud"] is True and row["rev"] == rev


def test_push_while_a_job_runs_is_409_and_changes_nothing(client, storage, cfg, runner):
    pid = make_project(client, storage).json()["id"]
    runner.block = True
    assert client.post(f"/projects/{pid}/analyze", headers=A, json={}).status_code == 200
    with use_home(home_of(cfg)):
        before = load_project(pid)
    r = client.put(f"/sync/projects/{pid}", headers=A, json={"base_rev": 0, "bundle": bundle(note="mid-job")})
    assert r.status_code == 409 and r.json()["detail"] == "This project is busy on the website. Try again in a moment."
    with use_home(home_of(cfg)):
        after = load_project(pid)
        assert not (workdir_for(after.source) / "clip-h01.edl.json").exists()
    assert after.clips == before.clips and after.settings == before.settings
    assert listed(client, pid)["rev"] == 0
    assert push_new(client)["rev"] == 1  # a new project is never busy
    runner.release_all()


def _expires(url):
    return int(url.split("expires=")[1].split("&")[0])


def _published(storage, url):
    return url[len("mem://get/"):].split("?")[0]


def test_published_copy_is_not_counted_and_is_reused_for_its_life(client, storage, monkeypatch):
    pid = make_project(client, storage).json()["id"]
    before = client.get("/usage", headers=A).json()["storage_bytes"]
    uploads = []
    real = storage.upload
    monkeypatch.setattr(storage, "upload", lambda src, key, ct: (uploads.append(key), real(src, key, ct)))
    first = client.get(f"/sync/projects/{pid}", headers=A).json()["video_url"]
    key = _published(storage, first)
    assert uploads == [key] and _expires(first) == 86400
    assert client.get("/usage", headers=A).json()["storage_bytes"] == before  # a copy of a file already counted
    # within its life: linked again, not uploaded again, and the link never outlives the object
    storage.modified[key] -= timedelta(hours=10)
    again = client.get(f"/sync/projects/{pid}", headers=A).json()["video_url"]
    assert uploads == [key]
    assert 14 * 3600 - 60 <= _expires(again) <= 14 * 3600
    # under 6 h left: published again, with a full day
    storage.modified[key] -= timedelta(hours=9)
    late = client.get(f"/sync/projects/{pid}", headers=A).json()["video_url"]
    assert uploads == [key, key] and _expires(late) == 86400
    assert client.get("/usage", headers=A).json()["storage_bytes"] == before
