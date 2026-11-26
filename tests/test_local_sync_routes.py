"""Desktop-only sync routes of the local engine: bundles, sync state, settings, import (with a video download) and
publishing the source to a presigned PUT URL. The video servers here are fakes on 127.0.0.1."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="needs the studio extra")
from fastapi.testclient import TestClient  # noqa: E402

import studio_server  # noqa: E402
import sync_bundle  # noqa: E402
from common import load_json, save_json, workdir_for  # noqa: E402
from studio_projects import ProjectClip, create_project, load_project, save_project  # noqa: E402
from studio_server import create_app  # noqa: E402

TOKEN = "t-" + "sync" + "-test"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
VIDEO = b"\x00\x00\x00\x18ftypmp42" + bytes(range(256)) * 400  # ~100 KB, not a real video: never probed


class Fake(BaseHTTPRequestHandler):
    """GET /video.mp4 -> VIDEO; GET /broken.mp4 -> a body cut short; PUT /put -> recorded."""
    puts: list = []

    def log_message(self, *a):  # quiet
        pass

    def do_GET(self):
        if self.path.startswith("/video.mp4"):
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(VIDEO)))
            self.end_headers()
            self.wfile.write(VIDEO)
        elif self.path.startswith("/broken.mp4"):
            self.send_response(200)
            self.send_header("Content-Length", str(len(VIDEO)))
            self.end_headers()
            self.wfile.write(VIDEO[:5000])
            self.wfile.flush()
            self.connection.close()
        elif self.path.startswith("/slow.mp4"):
            self.send_response(200)
            self.send_header("Content-Length", str(len(VIDEO)))
            self.end_headers()
            self.wfile.write(VIDEO[:5000])
            self.wfile.flush()
            time.sleep(8)  # long enough to be cancelled
        elif self.path.startswith("/jump"):
            self.send_response(302)
            self.send_header("Location", "file:///etc/passwd")
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def do_PUT(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n)
        Fake.puts.append({"path": self.path, "body": body, "type": self.headers.get("Content-Type"), "length": n})
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()


@pytest.fixture
def server():
    Fake.puts = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    monkeypatch.setenv("VEDIT_HOME", str(h))
    return h


@pytest.fixture
def videos(tmp_path, monkeypatch):
    d = tmp_path / "Videos" / "Vedit"
    monkeypatch.setattr(studio_server, "default_story_dir", lambda: d)
    return d


@pytest.fixture
def client(home, videos):
    app = create_app(TOKEN)  # the real job queue: downloads and uploads run as child processes
    with TestClient(app) as c:
        yield c


def wait_job(client, jid, timeout=30.0):
    end = time.time() + timeout
    while time.time() < end:
        j = client.get(f"/jobs/{jid}", headers=AUTH).json()
        if j["state"] in ("done", "failed", "cancelled"):
            return j
        time.sleep(0.05)
    raise AssertionError(f"job {jid} did not finish: {j}")


def edl(src, broll):
    return {"sources": {"main": str(src)}, "output": {"path": "", "aspect": "9:16"},
            "clips": [{"src": "main", "in": 0.0, "out": 4.0}],
            "overlays": [{"type": "broll", "path": broll, "mode": "full"}]}


@pytest.fixture
def desk(tmp_path, home):
    """A desktop project with one clip whose edl has a b-roll overlay from this computer."""
    src = tmp_path / "My Videos" / "talk.mp4"
    src.parent.mkdir()
    src.write_bytes(VIDEO)
    broll = tmp_path / "B Roll" / "cat.mp4"
    broll.parent.mkdir()
    broll.write_bytes(b"cat")
    p = create_project(src, "en", "reels")
    wd = workdir_for(src)
    save_json({"source": str(src), "highlights": [{"id": "h01", "start": 0, "end": 4}], "reason": ""},
              wd / "highlights.json")
    save_json(edl(src, str(broll)), wd / "clip-h01.edl.json")
    p.clips = [ProjectClip({"id": "h01", "start": 0, "end": 4, "title": "Hi"}, True, "clip-h01.edl.json")]
    save_project(p)
    return {"p": p, "src": src, "broll": broll, "wd": wd}


def state_of(client, pid):
    rows = client.get("/projects/sync-state", headers=AUTH).json()
    return next(r for r in rows if r["id"] == pid)


# ---- bundle, state, settings ----------------------------------------------------------------------------------

def test_bundle_route_exports_the_project(client, desk):
    r = client.get(f"/projects/{desk['p'].id}/bundle", headers=AUTH)
    assert r.status_code == 200
    b = r.json()
    assert b["format"] == 1 and b["project"]["source"] == "source:"
    assert b["files"]["clip-h01.edl.json"]["overlays"][0]["path"] == "local:cat.mp4"
    assert str(desk["src"].parent) not in json.dumps(b)
    assert client.get("/projects/nope/bundle", headers=AUTH).status_code == 404
    assert client.get(f"/projects/{desk['p'].id}/bundle").status_code == 401


def test_sync_state_lists_every_project_with_its_hash(client, desk, tmp_path):
    for i in range(12):  # more than /projects/recent answers: every local project syncs
        v = tmp_path / f"v{i}.mp4"
        v.write_bytes(b"v")
        create_project(v, "en", "reels")
    rows = client.get("/projects/sync-state", headers=AUTH).json()
    assert len(rows) == 13
    row = state_of(client, desk["p"].id)
    assert row["cloud_id"] is None and row["cloud_rev"] == 0 and row["cloud_hash"] is None
    assert isinstance(row["hash"], str) and len(row["hash"]) == 64
    assert row["title"] == "talk" and row["web_video"] is False and row["has_source"] is True
    assert row["source_name"] == "talk.mp4" and row["size"] == len(VIDEO) and row["content_type"] == "video/mp4"


def test_patch_settings_records_sync_fields_that_never_travel(client, desk):
    pid = desk["p"].id
    before = state_of(client, pid)["hash"]
    r = client.patch(f"/projects/{pid}/settings", headers=AUTH,
                     json={"cloud_id": "c123", "cloud_rev": 4, "cloud_hash": before})
    assert r.status_code == 200
    row = r.json()
    assert row["cloud_id"] == "c123" and row["cloud_rev"] == 4 and row["cloud_hash"] == before
    assert row["hash"] == before  # id, rev and the hash itself are not part of what is compared
    s = load_project(pid).settings
    assert s["cloud_id"] == "c123" and s["cloud_rev"] == 4 and s["cloud_hash"] == before
    b = client.get(f"/projects/{pid}/bundle", headers=AUTH).json()
    assert "cloud_hash" not in b["project"]["settings"] and "cloud_hash" not in json.dumps(b)
    # web_video is part of the bundle: the hash moves
    row = client.patch(f"/projects/{pid}/settings", headers=AUTH, json={"web_video": True}).json()
    assert row["web_video"] is True and row["hash"] != before and row["cloud_rev"] == 4
    # an edit moves it too
    e = load_json(desk["wd"] / "clip-h01.edl.json")
    e["clips"][0]["out"] = 3.0
    save_json(e, desk["wd"] / "clip-h01.edl.json")
    assert state_of(client, pid)["hash"] != row["hash"]


def test_patch_settings_refuses_bad_values(client, desk):
    pid = desk["p"].id
    for body in ({"cloud_rev": "x"}, {"cloud_rev": -1}, {"cloud_id": "../x"}, {"web_video": "yes"},
                 {"title": "renamed"}):
        assert client.patch(f"/projects/{pid}/settings", headers=AUTH, json=body).status_code in (400, 422), body
    assert "title" not in load_project(pid).settings


# ---- import ---------------------------------------------------------------------------------------------------

def cloud_copy(client, pid, cid, rev):
    """The project as the cloud would answer GET /sync/projects/{id}."""
    b = client.get(f"/projects/{pid}/bundle", headers=AUTH).json()
    b.update(id=cid, rev=rev)
    return b


def test_import_in_place_maps_local_refs_back_and_marks_it_synced(client, desk):
    pid = desk["p"].id
    client.patch(f"/projects/{pid}/settings", headers=AUTH, json={"cloud_id": "c1", "cloud_rev": 1})
    b = cloud_copy(client, pid, "c1", 2)
    e = b["files"]["clip-h01.edl.json"]
    e["clips"][0]["out"] = 2.5  # edited on the website
    e["overlays"].append({"type": "broll", "path": "local:dog.mp4", "mode": "full"})  # not on this computer
    r = client.post("/projects/import-bundle", headers=AUTH, json={"bundle": b})
    assert r.status_code == 200, r.text
    assert r.json()["project"]["id"] == pid  # updated in place, no new project
    saved = load_json(desk["wd"] / "clip-h01.edl.json")
    assert saved["clips"][0]["out"] == 2.5
    assert saved["sources"]["main"] == str(desk["src"])
    assert saved["overlays"][0]["path"] == str(desk["broll"])  # local:cat.mp4 -> the path it had before
    assert saved["overlays"][1]["path"] == "local:dog.mp4"  # unmatched: stays, shows missing
    row = state_of(client, pid)
    assert row["cloud_rev"] == 2 and row["cloud_hash"] == row["hash"]  # in step with the cloud


def test_import_without_video_makes_a_new_placeholder_project(client, desk):
    b = cloud_copy(client, desk["p"].id, "web1", 3)
    r = client.post("/projects/import-bundle", headers=AUTH, json={"bundle": b})
    assert r.status_code == 200
    q = r.json()["project"]
    assert q["id"] != desk["p"].id and q["settings"]["cloud_id"] == "web1" and q["settings"]["cloud_rev"] == 3
    row = state_of(client, q["id"])
    assert row["has_source"] is False and row["cloud_hash"] == row["hash"]


def test_import_with_video_downloads_once_into_the_cloud_folder(client, desk, server, videos):
    b = cloud_copy(client, desk["p"].id, "web2", 1)
    b["title"] = 'Trip: ../../"evil"/<x>'
    r = client.post("/projects/import-bundle", headers=AUTH,
                    json={"bundle": b, "video_url": f"{server}/video.mp4?X-Amz-Signature=abc"})
    assert r.status_code == 200, r.text
    job = r.json()["job"]
    assert job["kind"] == "sync"
    done = wait_job(client, job["id"])
    assert done["state"] == "done", done
    files = [f for f in (videos / "Cloud").iterdir() if f.is_file()]  # (the project's workdir sits beside it)
    assert len(files) == 1 and files[0].read_bytes() == VIDEO
    assert files[0].parent == videos / "Cloud" and files[0].suffix == ".mp4"
    assert not any(c in files[0].name for c in '/\\:"<>') and ".." not in files[0].name
    q = load_project(done["result"]["project"])
    assert Path(q.source) == files[0] and q.settings["cloud_id"] == "web2"
    row = state_of(client, q.id)
    assert row["cloud_hash"] == row["hash"]
    # opened again: the same project, no second download
    r = client.post("/projects/import-bundle", headers=AUTH,
                    json={"bundle": b, "video_url": f"{server}/video.mp4"})
    assert r.json()["project"]["id"] == q.id
    assert [f for f in (videos / "Cloud").iterdir() if f.is_file()] == files


def test_a_failed_download_leaves_no_partial_file(client, desk, server, videos):
    b = cloud_copy(client, desk["p"].id, "web3", 1)
    r = client.post("/projects/import-bundle", headers=AUTH, json={"bundle": b, "video_url": f"{server}/broken.mp4"})
    done = wait_job(client, r.json()["job"]["id"])
    assert done["state"] == "failed"
    assert list((videos / "Cloud").iterdir()) == []
    assert not any(q.settings.get("cloud_id") == "web3" for q in [load_project(r["id"]) for r in
                                                                 client.get("/projects/sync-state", headers=AUTH).json()])


def test_a_cancelled_download_leaves_no_partial_file(client, desk, server, videos):
    b = cloud_copy(client, desk["p"].id, "web6", 1)
    jid = client.post("/projects/import-bundle", headers=AUTH,
                      json={"bundle": b, "video_url": f"{server}/slow.mp4"}).json()["job"]["id"]
    end = time.time() + 15
    while client.get(f"/jobs/{jid}", headers=AUTH).json()["state"] != "running" and time.time() < end:
        time.sleep(0.05)
    assert list((videos / "Cloud").glob("*.part"))  # reserved and being written
    time.sleep(0.5)
    client.post(f"/jobs/{jid}/cancel", headers=AUTH)
    assert wait_job(client, jid)["state"] == "cancelled"
    client.app.state.engine.wait_idle()
    assert [f for f in (videos / "Cloud").iterdir() if f.is_file()] == []


def test_a_redirect_to_another_scheme_is_refused(client, desk, server, videos):
    b = cloud_copy(client, desk["p"].id, "web4", 1)
    r = client.post("/projects/import-bundle", headers=AUTH, json={"bundle": b, "video_url": f"{server}/jump"})
    assert wait_job(client, r.json()["job"]["id"])["state"] == "failed"
    assert list((videos / "Cloud").iterdir()) == []


BAD_URLS = ("file:///C:/Windows/win.ini", "http://example.com/v.mp4", "ftp://127.0.0.1/v.mp4", "C:\\v.mp4",
            "//127.0.0.1/v.mp4", "https://", "http://127.0.0.1.evil.com/v.mp4", "javascript:alert(1)")


def test_import_refuses_urls_that_are_not_https(client, desk, videos):
    b = cloud_copy(client, desk["p"].id, "web5", 1)
    for url in BAD_URLS:
        r = client.post("/projects/import-bundle", headers=AUTH, json={"bundle": b, "video_url": url})
        assert r.status_code == 400, url
    assert not (videos / "Cloud").exists() or list((videos / "Cloud").iterdir()) == []


def test_import_refuses_a_bad_bundle(client):
    r = client.post("/projects/import-bundle", headers=AUTH, json={"bundle": {"format": 9}})
    assert r.status_code == 400


# ---- publish --------------------------------------------------------------------------------------------------

def test_publish_streams_the_source_to_the_put_url(client, desk, server):
    pid = desk["p"].id
    r = client.post(f"/projects/{pid}/publish", headers=AUTH, json={"url": f"{server}/put?sig=1"})
    assert r.status_code == 200, r.text
    job = r.json()
    assert job["kind"] == "sync" and job["project_id"] == pid
    assert wait_job(client, job["id"])["state"] == "done"
    assert len(Fake.puts) == 1
    put = Fake.puts[0]
    assert put["body"] == VIDEO and put["type"] == "video/mp4" and put["length"] == len(VIDEO)
    assert desk["src"].read_bytes() == VIDEO  # the desktop copy is untouched
    assert load_project(pid).last_job is None  # a sync job is not the project's analysis or export


def test_publish_refuses_bad_urls(client, desk):
    for url in BAD_URLS:
        r = client.post(f"/projects/{desk['p'].id}/publish", headers=AUTH, json={"url": url})
        assert r.status_code == 400, url


def test_publish_needs_the_source(client, desk, server):
    desk["src"].unlink()
    r = client.post(f"/projects/{desk['p'].id}/publish", headers=AUTH, json={"url": f"{server}/put"})
    assert r.status_code == 400


# ---- the cloud engine has none of these ----------------------------------------------------------------------

def test_local_sync_routes_are_not_in_the_cloud_engine(tmp_path):
    pytest.importorskip("jwt", reason="needs the cloud extra")
    import cloud_storage as cs
    from cloud_config import CloudConfig
    from cloud_usage import MemoryUsage
    from test_cloud_auth import SECRET, UID_A, auth, make_token

    cfg = CloudConfig(data_root=tmp_path / "data", origins=("https://web.example.test",),
                      supabase_url="https://x.supabase.test", jwt_secret=SECRET, storage=cs.MemoryStorage(),
                      usage=MemoryUsage(default_limit=60), runner_factory=None)
    a = auth(make_token(UID_A))
    with TestClient(create_app("x", cloud=cfg)) as c:
        assert c.get("/projects/sync-state", headers=a).status_code == 404
        assert c.post("/projects/import-bundle", headers=a, json={"bundle": {}}).status_code in (404, 405)
        assert c.get("/projects/p1/bundle", headers=a).status_code == 404
        assert c.post("/projects/p1/publish", headers=a, json={"url": "https://x"}).status_code == 404
        assert c.patch("/projects/p1/settings", headers=a, json={}).status_code in (404, 405)


def test_bundle_hash_ignores_id_and_rev():
    b = {"format": 1, "id": "a", "rev": 1, "title": "T", "project": {"x": 1}, "files": {}}
    assert sync_bundle.bundle_hash(b) == sync_bundle.bundle_hash({**b, "id": "b", "rev": 7})
    assert sync_bundle.bundle_hash(b) != sync_bundle.bundle_hash({**b, "title": "U"})
