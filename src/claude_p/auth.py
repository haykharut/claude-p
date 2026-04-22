from __future__ import annotations

import base64
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Request
from fastapi.responses import Response
from starlette.middleware.base import BaseHTTPMiddleware

from claude_p.db import connect, get_setting, set_setting

PH = PasswordHasher()
_SETTINGS_KEY = "dashboard_password_hash"
_REALM = "claude-p"


def hash_password(password: str) -> str:
    return PH.hash(password)


def set_dashboard_password(db_path: Path, password: str) -> None:
    h = hash_password(password)
    with connect(db_path) as conn:
        set_setting(conn, _SETTINGS_KEY, h)


def get_dashboard_password_hash(db_path: Path) -> str | None:
    with connect(db_path) as conn:
        return get_setting(conn, _SETTINGS_KEY)


def verify(db_path: Path, password: str) -> bool:
    h = get_dashboard_password_hash(db_path)
    if not h:
        return False
    try:
        PH.verify(h, password)
        return True
    except VerifyMismatchError:
        return False


def _unauthorized() -> Response:
    return Response(
        status_code=401,
        headers={"WWW-Authenticate": f'Basic realm="{_REALM}"'},
        content="Authentication required",
    )


class BasicAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, db_path: Path, skip_prefixes: tuple[str, ...] = ("/static",)):
        super().__init__(app)
        self.db_path = db_path
        self.skip_prefixes = skip_prefixes

    async def dispatch(self, request: Request, call_next):
        if any(request.url.path.startswith(p) for p in self.skip_prefixes):
            return await call_next(request)

        pw_hash = get_dashboard_password_hash(self.db_path)
        if not pw_hash:
            return Response(
                status_code=503,
                content="Dashboard password not set. Run `claude-p set-password` to configure.",
            )

        header = request.headers.get("authorization", "")
        if not header.lower().startswith("basic "):
            return _unauthorized()
        try:
            raw = base64.b64decode(header.split(None, 1)[1]).decode()
            _, password = raw.split(":", 1)
        except Exception:
            return _unauthorized()

        if not verify(self.db_path, password):
            return _unauthorized()
        return await call_next(request)
