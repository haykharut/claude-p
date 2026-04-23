"""
Backend registry.

To add a new backend:

  1. Create `claude_p/backends/<name>.py` with a `Backend` subclass that
     declares a `name: ClassVar[str]` and an `Options` nested Pydantic
     model (extra="forbid") for its `llm.options` schema.
  2. Import and register it in `_BACKENDS` below.
  3. Set `CLAUDE_P_BACKEND=<name>` (or pin per-job in `job.yaml`).

The config default is `claude_cli`. The rest of the daemon goes through
`get_backend(cfg)` and `resolve_backend_class(name)` — no module else
imports concrete backend classes.
"""

from __future__ import annotations

from claude_p.config import Config
from claude_p.manifest import Manifest, ManifestError

from .base import Backend, fold_event
from .claude_cli import ClaudeCLIBackend

_BACKENDS: dict[str, type[Backend]] = {
    ClaudeCLIBackend.name: ClaudeCLIBackend,
}


def resolve_backend_class(name: str) -> type[Backend]:
    """Look up a registered backend class by name. Raises `ValueError`
    (not `KeyError`) with a message listing the known backends so the
    mistake is legible in the dashboard / logs."""
    try:
        return _BACKENDS[name]
    except KeyError:
        known = ", ".join(sorted(_BACKENDS)) or "<none>"
        raise ValueError(f"unknown backend {name!r}; known backends: {known}") from None


def get_backend(cfg: Config) -> Backend:
    """Instantiate the daemon-default backend. Per-job overrides happen
    later (via `llm.backend` in the manifest)."""
    return resolve_backend_class(cfg.backend)(cfg)


def effective_backend_name(manifest: Manifest | None, cfg: Config) -> str:
    """Resolve which backend a given job should use.

    Per-job (`manifest.llm.backend`) wins if set; otherwise the
    daemon-wide `CLAUDE_P_BACKEND`. Used by both the registry
    validator and the executor's config-injection path.
    """
    if manifest is not None and manifest.llm is not None and manifest.llm.backend:
        return manifest.llm.backend
    return cfg.backend


def validate_llm_options(manifest: Manifest, cfg: Config) -> None:
    """Validate `manifest.llm.options` against the selected backend's
    `Options` schema. Raises `ManifestError` on failure — the registry
    catches this and marks the job broken.

    Called lazily (from `registry._load`) rather than inside
    `Manifest.model_validate` so `manifest.py` doesn't have to import
    the backends module (circular-import trap: models.py → manifest.py,
    and backends/ imports models.py).
    """
    if manifest.llm is None:
        return
    backend_name = effective_backend_name(manifest, cfg)
    try:
        cls = resolve_backend_class(backend_name)
    except ValueError as e:
        raise ManifestError(f"llm.backend: {e}") from None
    try:
        cls.Options.model_validate(manifest.llm.options)
    except Exception as e:
        raise ManifestError(f"llm.options (for backend {backend_name!r}): {e}") from e


__all__ = [
    "Backend",
    "ClaudeCLIBackend",
    "effective_backend_name",
    "fold_event",
    "get_backend",
    "resolve_backend_class",
    "validate_llm_options",
]
