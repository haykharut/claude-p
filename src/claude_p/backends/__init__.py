"""
Backend registry.

To add a new backend:

  1. Create `claude_p/backends/<name>.py` with a `Backend` subclass.
  2. Import and register it in `_BACKENDS` below.
  3. Set `CLAUDE_P_BACKEND=<name>`.

The config default is `claude_cli`. The rest of the daemon goes through
`get_backend(cfg)` — no module else imports concrete backend classes.
"""

from __future__ import annotations

from claude_p.config import Config

from .base import Backend, fold_event
from .claude_cli import ClaudeCLIBackend

_BACKENDS: dict[str, type[Backend]] = {
    ClaudeCLIBackend.name: ClaudeCLIBackend,
}


def get_backend(cfg: Config) -> Backend:
    try:
        cls = _BACKENDS[cfg.backend]
    except KeyError:
        known = ", ".join(sorted(_BACKENDS)) or "<none>"
        raise ValueError(
            f"unknown CLAUDE_P_BACKEND={cfg.backend!r}; known backends: {known}"
        ) from None
    return cls(cfg)


__all__ = ["Backend", "ClaudeCLIBackend", "fold_event", "get_backend"]
