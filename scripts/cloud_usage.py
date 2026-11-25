"""Usage metering for cloud mode: monthly processed-seconds limits (UTC), Supabase-backed or in-memory."""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

DEFAULT_LIMIT_SECONDS = 3600.0
RPC_TIMEOUT_SECONDS = 10.0


class LimitReached(Exception):
    def __init__(self, remaining: float):
        super().__init__(f"monthly limit reached ({remaining:.0f}s remaining)")
        self.remaining = remaining


class Busy(Exception):
    """The global monthly budget is spent."""


class UsageError(RuntimeError):
    """The usage backend failed or answered something unexpected."""


class Usage(Protocol):
    def remaining(self, uid: str) -> float: ...
    def status(self, uid: str) -> dict[str, float]: ...
    def charge(self, uid: str, job_id: str, seconds: float) -> None: ...
    def refund(self, job_id: str) -> None: ...


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _month(now: Callable[[], datetime]) -> str:
    return now().astimezone(timezone.utc).strftime("%Y-%m")


class MemoryUsage:
    """Thread-safe stand-in mirroring the SQL functions' semantics."""

    def __init__(
        self,
        default_limit: float = DEFAULT_LIMIT_SECONDS,
        global_limit: float | None = None,
        now: Callable[[], datetime] = _utc_now,
        limits: dict[str, float] | None = None,
    ):
        self.default_limit = default_limit
        self.global_limit = global_limit
        self._now = now
        self._limits = dict(limits or {})
        self._usage: dict[tuple[str, str], float] = {}
        self._charges: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def _remaining(self, uid: str, month: str) -> float:
        limit = self._limits.get(uid, self.default_limit)
        return max(0.0, limit - self._usage.get((uid, month), 0.0))

    def remaining(self, uid: str) -> float:
        with self._lock:
            return self._remaining(uid, _month(self._now))

    def status(self, uid: str) -> dict[str, float]:
        with self._lock:
            month = _month(self._now)
            return {"seconds_used": float(self._usage.get((uid, month), 0.0)),
                    "seconds_limit": float(self._limits.get(uid, self.default_limit))}

    def charge(self, uid: str, job_id: str, seconds: float) -> None:
        with self._lock:
            if job_id in self._charges:
                return
            month = _month(self._now)
            left = self._remaining(uid, month)
            if seconds > left:
                raise LimitReached(left)
            if self.global_limit and self.global_limit > 0:
                total = sum(v for (_, m), v in self._usage.items() if m == month)
                if total + seconds > self.global_limit:
                    raise Busy()
            self._usage[(uid, month)] = self._usage.get((uid, month), 0.0) + seconds
            self._charges[job_id] = {"uid": uid, "month": month, "seconds": seconds, "refunded": False}

    def refund(self, job_id: str) -> None:
        with self._lock:
            c = self._charges.get(job_id)
            if c is None or c["refunded"]:
                return
            key = (c["uid"], c["month"])
            self._usage[key] = max(0.0, self._usage.get(key, 0.0) - c["seconds"])
            c["refunded"] = True


class SupabaseUsage:
    """Calls the vedit_* Postgres functions (cloud/supabase/001_usage.sql) through PostgREST with the secret key."""

    def __init__(
        self,
        url: str,
        secret_key: str,
        default_limit: float = DEFAULT_LIMIT_SECONDS,
        global_limit: float | None = None,
        *,
        transport: Any = None,
        now: Callable[[], datetime] = _utc_now,
    ):
        import httpx  # lazy: desktop installs do not need the cloud extra

        self._base = url.rstrip("/") + "/rest/v1/rpc/"
        self.default_limit = default_limit
        self.global_limit = global_limit
        self._now = now
        self._client = httpx.Client(
            transport=transport,
            timeout=RPC_TIMEOUT_SECONDS,
            headers={"apikey": secret_key, "Authorization": f"Bearer {secret_key}"},
        )

    def _rpc(self, fn: str, body: dict[str, Any]) -> Any:
        try:
            r = self._client.post(self._base + fn, json=body)
        except Exception as e:  # network / timeout
            raise UsageError(f"{fn} failed: {type(e).__name__}") from e
        if not 200 <= r.status_code < 300:
            raise UsageError(f"{fn} failed: HTTP {r.status_code}")
        if r.status_code == 204 or not r.content:
            return None
        try:
            return r.json()
        except ValueError as e:
            raise UsageError(f"{fn} returned invalid JSON") from e

    def remaining(self, uid: str) -> float:
        out = self._rpc(
            "vedit_remaining",
            {"p_uid": uid, "p_month": _month(self._now), "p_default_limit": self.default_limit},
        )
        if isinstance(out, bool) or not isinstance(out, (int, float)):
            raise UsageError("vedit_remaining returned an unexpected value")
        return float(out)

    def status(self, uid: str) -> dict[str, float]:
        out = self._rpc(
            "vedit_status",
            {"p_uid": uid, "p_month": _month(self._now), "p_default_limit": self.default_limit},
        )
        try:
            used, limit = out["seconds_used"], out["seconds_limit"]
        except (TypeError, KeyError):
            raise UsageError("vedit_status returned an unexpected value") from None
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in (used, limit)):
            raise UsageError("vedit_status returned an unexpected value")
        return {"seconds_used": float(used), "seconds_limit": float(limit)}

    def charge(self, uid: str, job_id: str, seconds: float) -> None:
        out = self._rpc(
            "vedit_charge",
            {
                "p_uid": uid,
                "p_job_id": job_id,
                "p_seconds": seconds,
                "p_month": _month(self._now),
                "p_default_limit": self.default_limit,
                "p_global_limit": self.global_limit,
            },
        )
        status = out.get("status") if isinstance(out, dict) else None
        if status == "ok":
            return
        if status == "limit":
            raise LimitReached(float(out.get("remaining") or 0))
        if status == "busy":
            raise Busy()
        raise UsageError(f"vedit_charge returned unexpected status: {status!r}")

    def refund(self, job_id: str) -> None:
        self._rpc("vedit_refund", {"p_job_id": job_id})
