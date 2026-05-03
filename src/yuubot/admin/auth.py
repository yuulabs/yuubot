"""Simple session-cookie auth for the admin panel."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

from fastapi import Cookie, HTTPException, Request, Response

_SESSION_COOKIE = "yuu_admin_session"
_SESSION_TTL = 7 * 24 * 3600  # 1 week


def _sign(secret: str, value: str) -> str:
    return hmac.new(secret.encode(), value.encode(), hashlib.sha256).hexdigest()


def make_session_token(secret: str) -> str:
    nonce = secrets.token_hex(16)
    exp = int(time.time()) + _SESSION_TTL
    payload = f"{nonce}:{exp}"
    sig = _sign(secret, payload)
    return f"{payload}:{sig}"


def verify_session_token(secret: str, token: str) -> bool:
    try:
        parts = token.split(":")
        if len(parts) != 3:
            return False
        nonce, exp_str, sig = parts
        exp = int(exp_str)
        if time.time() > exp:
            return False
        payload = f"{nonce}:{exp_str}"
        return hmac.compare_digest(sig, _sign(secret, payload))
    except Exception:
        return False


def require_auth(secret: str):
    """Return a FastAPI dependency that enforces cookie auth when secret is set."""

    async def _check(
        request: Request,
        yuu_admin_session: str | None = Cookie(default=None),
    ) -> None:
        if not secret:
            return
        token = yuu_admin_session or request.headers.get("X-Admin-Token", "")
        if not verify_session_token(secret, token):
            raise HTTPException(status_code=401, detail="unauthorized")

    return _check


def set_session_cookie(response: Response, secret: str) -> None:
    token = make_session_token(secret)
    response.set_cookie(
        _SESSION_COOKIE,
        token,
        max_age=_SESSION_TTL,
        httponly=True,
        samesite="strict",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(_SESSION_COOKIE)
