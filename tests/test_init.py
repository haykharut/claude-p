import json
from pathlib import Path

from claude_p import _maybe_write_ledger
from claude_p.models import BackendResult


class _TrackedFile:
    def __init__(self, file_obj):
        self._file_obj = file_obj

    def write(self, data: str) -> int:
        return self._file_obj.write(data)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._file_obj.close()

    @property
    def closed(self) -> bool:
        return self._file_obj.closed

    def __getattr__(self, name):
        return getattr(self._file_obj, name)


def test_maybe_write_ledger_closes_calls_file(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_P_RUN_ID", "run-123")
    monkeypatch.setenv("CLAUDE_P_JOB_DIR", str(tmp_path))

    tracked_path = tmp_path / "runs" / "run-123" / "claude_calls.jsonl"
    original_open = Path.open
    tracked = {}

    def tracked_open(self, *args, **kwargs):
        file_obj = original_open(self, *args, **kwargs)
        if self == tracked_path:
            wrapped = _TrackedFile(file_obj)
            tracked["file"] = wrapped
            return wrapped
        return file_obj

    monkeypatch.setattr(Path, "open", tracked_open)

    _maybe_write_ledger(
        BackendResult(
            cost_usd=1.25,
            input_tokens=10,
            output_tokens=20,
            cache_read_tokens=30,
            cache_creation_tokens=40,
            num_turns=2,
            session_id="sess-1",
            is_error=False,
            model_usage={"claude-sonnet": {"costUSD": 1.25}},
        )
    )

    assert tracked["file"].closed is True
    ledger_entry = json.loads(tracked_path.read_text().strip())
    assert ledger_entry["cost_usd"] == 1.25
    assert ledger_entry["session_id"] == "sess-1"