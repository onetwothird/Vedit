"""Cloud uploads and assets: presigned uploads, commit into the user's library, limits, isolation."""
import threading

import pytest

pytest.importorskip("fastapi", reason="needs the studio extra")
jwt = pytest.importorskip("jwt", reason="needs the cloud extra")
from fastapi.testclient import TestClient  # noqa: E402

import cloud_assets  # noqa: E402
from cloud_config import CloudConfig  # noqa: E402
from scripts import cloud_storage as cs  # noqa: E402
from scripts.cloud_usage import MemoryUsage  # noqa: E402
from studio_projects import use_home  # noqa: E402
from studio_server import create_app  # noqa: E402
from test_cloud_auth import SECRET, UID_A, UID_B, auth, make_token  # noqa: E402

GB = 1024 ** 3


@pytest.fixture
def storage():
    return cs.MemoryStorage()


@pytest.fixture
def usage():
    return MemoryUsage(default_limit=3600)


@pytest.fixture
def cfg(tmp_path, storage, usage):
    return CloudConfig(data_root=tmp_path / "data", origins=("https://web.example.test",),
                       supabase_url="https://x.supabase.test", jwt_secret=SECRET, storage=storage, usage=usage)


@pytest.fixture
def client(cfg):
    with TestClient(create_app("desktop-token", cloud=cfg)) as c:
        yield c


A = auth(make_token(UID_A))
B = auth(make_token(UID_B))


def start(client, headers=A, kind="video", name="Clip.MP4", size=5, content_type="video/mp4"):
    return client.post("/uploads", headers=headers,
                       json={"kind": kind, "name": name, "size": size, "content_type": content_type})


def finish(client, storage, headers=A, data=b"hello", **kw):
    r = start(client, headers, size=len(data), **kw)
    assert r.status_code == 200, r.text
    key = r.json()["upload_key"]
    storage.objects[key] = data
    return key, client.post("/assets", headers=headers, json={"upload_key": key})


def test_upload_returns_key_and_presigned_url(client, storage):
    r = start(client)
    assert r.status_code == 200
    body = r.json()
    assert body["upload_key"].startswith(cs.uploads_prefix(UID_A))
    assert body["url"] == storage.presign_put(body["upload_key"], "video/mp4", 5)


def test_requires_auth(client):
    assert client.post("/uploads", json={"kind": "video", "name": "a.mp4", "size": 1,
                                         "content_type": "video/mp4"}).status_code == 401


@pytest.mark.parametrize("kind,name,size,ctype", [
    ("video", "a.mp4", 3 * GB + 1, "video/mp4"),
    ("image", "a.png", 20 * 1024 ** 2 + 1, "image/png"),
    ("audio", "a.mp3", 200 * 1024 ** 2 + 1, "audio/mpeg"),
    ("font", "a.ttf", 10 * 1024 ** 2 + 1, "font/ttf"),
    ("lut", "a.cube", 10 * 1024 ** 2 + 1, "application/octet-stream"),
    ("video", "a.mp4", 0, "video/mp4"),
    ("video", "a.mp4", -1, "video/mp4"),
])
def test_size_over_kind_limit_is_400(client, kind, name, size, ctype):
    assert start(client, kind=kind, name=name, size=size, content_type=ctype).status_code == 400


@pytest.mark.parametrize("kind,name,ctype", [
    ("video", "a.mp4", "image/png"),
    ("image", "a.png", "video/mp4"),
    ("audio", "a.mp3", "text/html"),
    ("font", "a.ttf", "image/png"),
    ("video", "a.exe", "video/mp4"),
    ("video", "noext", "video/mp4"),
    ("image", "a.mp4", "image/png"),
    ("lut", "a.txt", "application/octet-stream"),
    ("bogus", "a.mp4", "video/mp4"),
])
def test_wrong_type_or_name_is_400(client, kind, name, ctype):
    assert start(client, kind=kind, name=name, content_type=ctype).status_code == 400


def test_font_and_lut_content_types_ok(client):
    assert start(client, kind="font", name="f.otf", content_type="application/octet-stream").status_code == 200
    assert start(client, kind="font", name="f.ttf", content_type="font/ttf").status_code == 200
    assert start(client, kind="lut", name="l.cube", content_type="application/octet-stream").status_code == 200


def test_storage_over_10gb_is_400(client, storage):
    storage.usage_bytes = lambda prefix: 10 * GB - 2 if prefix == cs.exports_prefix(UID_A) else 0
    r = start(client, size=5)
    assert r.status_code == 400 and "storage" in r.json()["detail"].lower()
    assert start(client, size=2).status_code == 200


def test_commit_moves_object_to_volume(client, cfg, storage):
    key, r = finish(client, storage, data=b"hello")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"asset_id", "kind", "name", "size"}
    assert body["kind"] == "video" and body["size"] == 5 and body["name"] == "Clip.MP4"
    path = cfg.data_root / "users" / UID_A / "assets" / f"{body['asset_id']}.mp4"
    assert path.read_bytes() == b"hello"
    assert key not in storage.objects
    assert not list((cfg.data_root / "users" / UID_A / "uploads").glob("*.json"))
    # committing twice: the pending record is gone
    assert client.post("/assets", headers=A, json={"upload_key": key}).status_code == 404


def test_commit_before_upload_finished_is_400_and_retryable(client, storage):
    key = start(client, size=5).json()["upload_key"]
    r = client.post("/assets", headers=A, json={"upload_key": key})
    assert r.status_code == 400 and "Upload not finished" in r.json()["detail"]
    storage.objects[key] = b"12345"
    assert client.post("/assets", headers=A, json={"upload_key": key}).status_code == 200


def test_commit_size_mismatch_deletes_object(client, storage):
    key = start(client, size=5).json()["upload_key"]
    storage.objects[key] = b"123456789"
    assert client.post("/assets", headers=A, json={"upload_key": key}).status_code == 400
    assert key not in storage.objects
    assert client.get("/assets", headers=A).json() == []


def test_other_users_upload_key_is_404(client, storage):
    key = start(client, A, size=5).json()["upload_key"]
    storage.objects[key] = b"12345"
    r = client.post("/assets", headers=B, json={"upload_key": key})
    assert r.status_code == 404 and r.json()["code"] == "not_found"
    assert key in storage.objects  # untouched
    assert client.get("/assets", headers=B).json() == []


@pytest.mark.parametrize("key", [
    "uploads/" + UID_A + "/" + "0" * 32,  # no pending record
    "uploads/" + UID_A + "/../" + UID_B + "/" + "0" * 32,
    "exports/" + UID_A + "/p/out.mp4",
    "garbage", "", "uploads/" + UID_A + "/",
])
def test_unknown_or_malformed_keys_are_404(client, key):
    r = client.post("/assets", headers=A, json={"upload_key": key})
    assert r.status_code == 404 and r.json()["code"] == "not_found"


def test_list_filter_and_delete(client, cfg, storage):
    _, v = finish(client, storage, data=b"vid")
    _, i = finish(client, storage, data=b"img", kind="image", name="p.PNG", content_type="image/png")
    listed = client.get("/assets", headers=A).json()
    assert {a["asset_id"] for a in listed} == {v.json()["asset_id"], i.json()["asset_id"]}
    only = client.get("/assets?kind=image", headers=A).json()
    assert [a["asset_id"] for a in only] == [i.json()["asset_id"]]
    assert only[0]["name"] == "p.PNG"
    aid = v.json()["asset_id"]
    assert client.delete(f"/assets/{aid}", headers=A).status_code == 200
    assert not (cfg.data_root / "users" / UID_A / "assets" / f"{aid}.mp4").exists()
    assert [a["asset_id"] for a in client.get("/assets", headers=A).json()] == [i.json()["asset_id"]]


def test_other_users_asset_is_404_on_delete_and_absent_from_list(client, cfg, storage):
    _, v = finish(client, storage)
    aid = v.json()["asset_id"]
    r = client.delete(f"/assets/{aid}", headers=B)
    assert r.status_code == 404 and r.json()["code"] == "not_found"
    assert client.get("/assets", headers=B).json() == []
    assert (cfg.data_root / "users" / UID_A / "assets" / f"{aid}.mp4").is_file()
    assert client.delete("/assets/anotherunknown", headers=A).status_code == 404


def test_usage_endpoint(client, usage, storage):
    usage.charge(UID_A, "j1", 120)
    finish(client, storage, data=b"12345")
    storage.objects[cs.export_key(UID_A, "p", "o.mp4")] = b"x" * 100
    r = client.get("/usage", headers=A).json()
    assert r["seconds_used"] == 120 and r["seconds_limit"] == 3600
    assert r["storage_limit"] == 10 * GB
    assert r["storage_bytes"] >= 105
    assert client.get("/usage", headers=B).json()["seconds_used"] == 0


def test_desktop_app_has_no_cloud_routes():
    c = TestClient(create_app("tok"))
    h = {"Authorization": "Bearer tok"}
    assert c.post("/uploads", headers=h, json={}).status_code == 404
    assert c.get("/assets", headers=h).status_code == 404
    assert c.get("/usage", headers=h).status_code == 404


def test_concurrent_commits_keep_every_index_entry(client, storage):
    keys = []
    for n in range(6):
        d = b"d%d" % n
        k = start(client, size=len(d)).json()["upload_key"]
        storage.objects[k] = d
        keys.append(k)
    codes = []
    threads = [threading.Thread(target=lambda k=k: codes.append(
        client.post("/assets", headers=A, json={"upload_key": k}).status_code)) for k in keys]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert codes == [200] * 6
    assert len(client.get("/assets", headers=A).json()) == 6


def test_resolve_rejects_paths_and_unknown_ids(cfg):
    with use_home(cfg.data_root / "users" / UID_A):
        for ref in ("C:/x.mp4", "/etc/passwd", "asset:a00000000000", "asset:a" + "0" * 12, "asset:../../x", ""):
            with pytest.raises(ValueError):
                cloud_assets.resolve(ref)


def test_resolve_known_asset_and_isolation(client, cfg, storage):
    _, r = finish(client, storage, data=b"abc")
    aid = r.json()["asset_id"]
    with use_home(cfg.data_root / "users" / UID_A):
        assert cloud_assets.resolve(f"asset:{aid}").read_bytes() == b"abc"
    with use_home(cfg.data_root / "users" / UID_B):
        with pytest.raises(ValueError):
            cloud_assets.resolve(f"asset:{aid}")
