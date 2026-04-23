"""
WebDAV subapp, served at /fs over the same port + auth as the dashboard.

Why wsgidav + a2wsgi rather than a native ASGI lib: ASGIWebDAV v2 has a bug in
its `__init__.py` that rebinds `__name__`, breaking submodule imports. wsgidav
is the battle-tested alternative (years in OwnCloud-adjacent tooling), and
a2wsgi is a small shim to run it under Starlette's ASGI mount.

Auth: we rely on the outer BasicAuthMiddleware for credential checking — the
wsgidav app is configured with `allow_anonymous` so it never challenges on its
own. macOS Finder / Windows Explorer see the outer 401 + WWW-Authenticate and
prompt the user once; the creds then flow through on every PROPFIND/GET/PUT.
"""

from __future__ import annotations

import logging
from pathlib import Path

from a2wsgi import WSGIMiddleware
from fastapi import FastAPI
from wsgidav.fs_dav_provider import FilesystemProvider
from wsgidav.wsgidav_app import WsgiDAVApp

from claude_p.config import Config

log = logging.getLogger(__name__)


def _build_wsgidav_app(fs_root: Path) -> WsgiDAVApp:
    fs_root.mkdir(parents=True, exist_ok=True)
    config = {
        "provider_mapping": {"/": FilesystemProvider(str(fs_root))},
        "http_authenticator": {
            "accept_basic": True,
            "accept_digest": False,
            "default_to_digest": False,
            "domain_controller": "wsgidav.dc.simple_dc.SimpleDomainController",
        },
        "simple_dc": {"user_mapping": {"*": True}},  # anonymous; outer middleware enforces auth
        "verbose": 1,
        "logging": {"enable_loggers": []},
        "property_manager": True,
        "lock_storage": True,
        "dir_browser": {
            "enable": True,
            "response_trailer": "claude-p",
            "show_user": False,
            "davmount": False,
            "ms_sharepoint_support": True,
            "libre_office_support": True,
        },
    }
    return WsgiDAVApp(config)


def mount_webdav(app: FastAPI, cfg: Config) -> None:
    wsgi_app = _build_wsgidav_app(cfg.fs_root)
    # a2wsgi's Scope/Receive/Send protocols don't match Starlette's exactly,
    # but the runtime behavior is correct — a2wsgi is designed for this mount.
    app.mount("/fs", WSGIMiddleware(wsgi_app))  # type: ignore[arg-type]
    log.info("mounted WebDAV at /fs (root=%s)", cfg.fs_root)
