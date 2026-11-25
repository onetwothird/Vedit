"""Verify a Supabase access token and return the user id (the `sub` claim)."""
from __future__ import annotations

import re

_UID = re.compile(r"^[0-9a-f-]{36}$")
_jwks_clients: dict[str, object] = {}


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
            client = _jwks_clients.get(url)
            if client is None:
                client = _jwks_clients[url] = jwt.PyJWKClient(url, cache_keys=True)
            key = client.get_signing_key_from_jwt(token).key
            claims = jwt.decode(token, key, algorithms=["ES256", "RS256"], audience="authenticated")
    except jwt.PyJWTError as e:
        raise PermissionError("invalid token") from e
    uid = claims.get("sub")
    if not isinstance(uid, str) or not _UID.fullmatch(uid):
        raise PermissionError("invalid user id")
    return uid
