"""Cloud mode: Supabase JWT sign-in, per-user data root, origins, websocket."""
import time

import pytest

pytest.importorskip("fastapi", reason="needs the studio extra")
jwt = pytest.importorskip("jwt", reason="needs the cloud extra")
from fastapi.testclient import TestClient  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402

from cloud_config import CloudConfig  # noqa: E402
from studio_server import create_app  # noqa: E402

SECRET = "test-" + "jwt-" + "secret-" + "x" * 32
ORIGIN = "https://web.example.test"
UID_A = "11111111-1111-4111-8111-111111111111"
UID_B = "22222222-2222-4222-8222-222222222222"


def make_token(uid=UID_A, aud="authenticated", exp=3600, secret=SECRET):
    return jwt.encode({"sub": uid, "aud": aud, "exp": int(time.time()) + exp}, secret, algorithm="HS256")


@pytest.fixture
def cfg(tmp_path):
    return CloudConfig(data_root=tmp_path / "data", origins=(ORIGIN,), supabase_url="https://x.supabase.test",
                       jwt_secret=SECRET)


@pytest.fixture
def client(cfg):
    app = create_app("desktop-token", cloud=cfg)
    with TestClient(app) as c:
        yield c


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_valid_token_lists_projects(client):
    r = client.get("/projects/recent", headers=auth(make_token()))
    assert r.status_code == 200 and r.json() == []


def test_health_is_public(client):
    assert client.get("/health").status_code == 200


@pytest.mark.parametrize("token", [
    None, "", "garbage", make_token(exp=-10), make_token(aud="anon"), make_token(secret="wrong" * 8),
    make_token(uid="not-a-uuid"), "desktop-token"])
def test_bad_token_is_401(client, token):
    h = auth(token) if token is not None else {}
    assert client.get("/projects/recent", headers=h).status_code == 401


def test_project_lands_under_user_root_and_is_private(client, cfg, tmp_path):
    src = tmp_path / "v.mp4"
    src.write_bytes(b"x")
    r = client.post("/projects", headers=auth(make_token(UID_A)),
                    json={"source": str(src), "language": "en", "platform": "tiktok", "type": "clip"})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    assert (cfg.data_root / "users" / UID_A / "projects.json").is_file()
    assert not (cfg.data_root / "users" / UID_B).exists()
    assert client.get(f"/projects/{pid}", headers=auth(make_token(UID_A))).status_code == 200
    rb = client.get(f"/projects/{pid}", headers=auth(make_token(UID_B)))
    assert rb.status_code == 404 and rb.json()["code"] == "not_found"


def test_disallowed_origin_is_403(client):
    r = client.get("/projects/recent", headers={**auth(make_token()), "Origin": "https://evil.test"})
    assert r.status_code == 403


def test_allowed_origin_ok_and_desktop_origin_not(client):
    assert client.get("/projects/recent", headers={**auth(make_token()), "Origin": ORIGIN}).status_code == 200
    r = client.get("/projects/recent", headers={**auth(make_token()), "Origin": "tauri://localhost"})
    assert r.status_code == 403


def test_media_route_takes_query_token(client):
    r = client.get("/projects/nope/media?token=" + make_token())
    assert r.status_code != 401
    assert client.get("/projects/nope/media?token=bad").status_code == 401


def test_websocket_bad_token_closes_1008(client):
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect("/events?token=bad"):
            pass
    assert e.value.code == 1008


def test_websocket_good_token_connects(client):
    with client.websocket_connect("/events?token=" + make_token()):
        pass


def test_desktop_token_rejected_on_websocket_in_cloud(client):
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect("/events?token=desktop-token"):
            pass
    assert e.value.code == 1008


def test_desktop_mode_unchanged_without_cloud(tmp_path):
    app = create_app("t")
    with TestClient(app) as c:
        assert c.get("/projects/recent", headers=auth(make_token())).status_code == 401
