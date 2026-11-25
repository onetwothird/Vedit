import json
import threading
from datetime import datetime, timezone

import pytest

from scripts.cloud_usage import Busy, LimitReached, MemoryUsage, SupabaseUsage

UID = "00000000-0000-0000-0000-000000000001"
UID2 = "00000000-0000-0000-0000-000000000002"


class Clock:
    def __init__(self, dt):
        self.dt = dt

    def __call__(self):
        return self.dt


def clock(y=2026, m=11, d=25):
    return Clock(datetime(y, m, d, 12, 0, tzinfo=timezone.utc))


def test_charge_within_limit():
    u = MemoryUsage(default_limit=3600, now=clock())
    assert u.remaining(UID) == 3600
    u.charge(UID, "j1", 600)
    assert u.remaining(UID) == 3000


def test_charge_over_limit_raises_and_charges_nothing():
    u = MemoryUsage(default_limit=3600, now=clock())
    u.charge(UID, "j1", 3000)
    with pytest.raises(LimitReached) as e:
        u.charge(UID, "j2", 700)
    assert e.value.remaining == 600
    assert u.remaining(UID) == 600


def test_charge_exactly_to_limit_ok():
    u = MemoryUsage(default_limit=100, now=clock())
    u.charge(UID, "j1", 100)
    assert u.remaining(UID) == 0


def test_charge_is_idempotent_per_job():
    u = MemoryUsage(default_limit=3600, now=clock())
    u.charge(UID, "j1", 600)
    u.charge(UID, "j1", 600)
    assert u.remaining(UID) == 3000


def test_refund_twice_adds_back_once_and_unknown_is_noop():
    u = MemoryUsage(default_limit=3600, now=clock())
    u.charge(UID, "j1", 600)
    u.refund("j1")
    u.refund("j1")
    assert u.remaining(UID) == 3600
    u.refund("nope")
    assert u.remaining(UID) == 3600


def test_month_rolls_over_utc_and_refund_goes_to_charge_month():
    c = clock(2026, 11, 30)
    u = MemoryUsage(default_limit=3600, now=c)
    u.charge(UID, "j1", 3600)
    assert u.remaining(UID) == 0
    c.dt = datetime(2026, 12, 1, 0, 0, tzinfo=timezone.utc)
    assert u.remaining(UID) == 3600
    u.charge(UID, "j2", 100)
    u.refund("j1")  # goes back to November, not December
    assert u.remaining(UID) == 3500
    c.dt = datetime(2026, 11, 30, 23, 0, tzinfo=timezone.utc)
    assert u.remaining(UID) == 3600


def test_per_user_limit_override():
    u = MemoryUsage(default_limit=3600, now=clock(), limits={UID: 100})
    assert u.remaining(UID) == 100
    assert u.remaining(UID2) == 3600
    with pytest.raises(LimitReached):
        u.charge(UID, "j1", 101)


def test_global_budget_busy():
    u = MemoryUsage(default_limit=3600, global_limit=1000, now=clock())
    u.charge(UID, "j1", 600)
    with pytest.raises(Busy):
        u.charge(UID2, "j2", 500)
    u.charge(UID2, "j3", 400)
    u.refund("j1")
    u.charge(UID2, "j4", 500)


def test_no_global_cap_when_none_or_zero():
    for g in (None, 0):
        u = MemoryUsage(default_limit=10**9, global_limit=g, now=clock())
        u.charge(UID, "j", 10**8)


def test_concurrent_charges_never_exceed_limit():
    u = MemoryUsage(default_limit=1000, now=clock())
    ok = []

    def go(i):
        try:
            u.charge(UID, f"j{i}", 100)
            ok.append(i)
        except LimitReached:
            pass

    ts = [threading.Thread(target=go, args=(i,)) for i in range(30)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(ok) == 10
    assert u.remaining(UID) == 0


# ---- SupabaseUsage over httpx.MockTransport ----

KEY = "fake-secret-key-for-tests"


def _make(handler, **kw):
    httpx = pytest.importorskip("httpx")
    return SupabaseUsage(
        "https://example.supabase.test/",
        KEY,
        transport=httpx.MockTransport(handler),
        now=clock(),
        **kw,
    )


def test_supabase_charge_ok_sends_expected_request():
    httpx = pytest.importorskip("httpx")
    seen = {}

    def handler(req):
        seen["url"] = str(req.url)
        seen["headers"] = req.headers
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"status": "ok", "remaining": 3000})

    u = _make(handler, global_limit=5000)
    u.charge(UID, "j1", 600)
    assert seen["url"] == "https://example.supabase.test/rest/v1/rpc/vedit_charge"
    assert seen["headers"]["apikey"] == KEY
    assert seen["headers"]["authorization"] == f"Bearer {KEY}"
    assert seen["body"] == {
        "p_uid": UID,
        "p_job_id": "j1",
        "p_seconds": 600,
        "p_month": "2026-11",
        "p_default_limit": 3600,
        "p_global_limit": 5000,
    }


def test_supabase_charge_limit_and_busy():
    httpx = pytest.importorskip("httpx")
    u = _make(lambda r: httpx.Response(200, json={"status": "limit", "remaining": 12.5}))
    with pytest.raises(LimitReached) as e:
        u.charge(UID, "j1", 600)
    assert e.value.remaining == 12.5
    u = _make(lambda r: httpx.Response(200, json={"status": "busy", "remaining": 3600}))
    with pytest.raises(Busy):
        u.charge(UID, "j1", 600)


def test_supabase_remaining_and_refund_bodies():
    httpx = pytest.importorskip("httpx")
    calls = []

    def handler(req):
        calls.append((req.url.path, json.loads(req.content)))
        if req.url.path.endswith("vedit_remaining"):
            return httpx.Response(200, json=1234.5)
        return httpx.Response(204)

    u = _make(handler)
    assert u.remaining(UID) == 1234.5
    u.refund("j1")
    assert calls[0] == (
        "/rest/v1/rpc/vedit_remaining",
        {"p_uid": UID, "p_month": "2026-11", "p_default_limit": 3600},
    )
    assert calls[1] == ("/rest/v1/rpc/vedit_refund", {"p_job_id": "j1"})


def test_supabase_http_error_is_raised_not_ok():
    httpx = pytest.importorskip("httpx")
    from scripts.cloud_usage import UsageError

    u = _make(lambda r: httpx.Response(500, text="boom"))
    with pytest.raises(UsageError):
        u.charge(UID, "j1", 1)
    with pytest.raises(UsageError):
        u.remaining(UID)
    with pytest.raises(UsageError):
        u.refund("j1")


def test_supabase_unknown_status_is_error():
    httpx = pytest.importorskip("httpx")
    from scripts.cloud_usage import UsageError

    u = _make(lambda r: httpx.Response(200, json={"status": "wat"}))
    with pytest.raises(UsageError):
        u.charge(UID, "j1", 1)
