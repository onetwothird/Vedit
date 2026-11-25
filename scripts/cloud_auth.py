"""Verify a Supabase access token and return the user id (the `sub` claim)."""
from __future__ import annotations

import re
import threading
import time

_UID = re.compile(r"^[0-9a-f-]{36}$")
_jwks_clients: dict[str, object] = {}
_last_refresh: dict[str, float] = {}
_lock = threading.Lock()
JWKS_TIMEOUT = 5  # seconds for one JWKS fetch
JWKS_COOLDOWN = 60  # an unknown kid forces at most one refetch per window


def _new_jwks_client(url: str):
    import jwt

    return jwt.PyJWKClient(url, cache_keys=True, timeout=JWKS_TIMEOUT)


def _signing_key(url: str, token: str):
    """The key for the token's kid. Unknown kids refetch the key set at most once per cooldown, then are rejected."""
    import jwt

    kid = jwt.get_unverified_header(token).get("kid")
    with _lock:
        client = _jwks_clients.get(url)
        if client is None:
            client = _jwks_clients[url] = _new_jwks_client(url)
            _last_refresh[url] = time.monotonic()
        for refresh in (False, True):
            for k in client.get_jwk_set(refresh=refresh).keys:
                if k.key_id == kid:
                    return k.key
            if time.monotonic() - _last_refresh[url] < JWKS_COOLDOWN:
                break
            _last_refresh[url] = time.monotonic()
    raise PermissionError("unknown signing key")


def user_id(token: str, cfg) -> str:
    """Raises PermissionError for a missing, bad, expired or wrong-audience token."""
    if not token:
        raise PermissionError("missing token")
    import jwt  # lazy: desktop installs without the cloud extra never import this

    try:
        if cfg.jwt_secret:
            claims = jwt.decode(token, cfg.jwt_secret, algorithms=["HS256"], audience="authenticated")
        else:
            url = f"{cfg.supabase_url}/auth/v1/.well-known/jwks.json"
            key = _signing_key(url, token)
            claims = jwt.decode(token, key, algorithms=["ES256", "RS256"], audience="authenticated")
    except jwt.PyJWTError as e:
        raise PermissionError("invalid token") from e
    uid = claims.get("sub")
    if not isinstance(uid, str) or not _UID.fullmatch(uid):
        raise PermissionError("invalid user id")
    return uid
