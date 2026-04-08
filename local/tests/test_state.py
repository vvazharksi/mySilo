import json
import pytest
from pathlib import Path

from mysilo.state import FileRecord, FileStatus, State


def _record(status=FileStatus.SYNCED, **kwargs) -> FileRecord:
    defaults = dict(
        hash="abc123", size=100, mtime=1000.0,
        s3_key="file.txt", status=status,
    )
    defaults.update(kwargs)
    return FileRecord(**defaults)


# ---------------------------------------------------------------------------
# Load / save
# ---------------------------------------------------------------------------

def test_empty_state_when_file_missing(tmp_path):
    state = State(tmp_path / ".mysilo_state.json")
    assert state.all() == {}


def test_save_and_reload(tmp_path):
    path = tmp_path / ".mysilo_state.json"
    state = State(path)
    state.set("docs/notes.txt", _record(hash="deadbeef"))
    state.save()

    reloaded = State(path)
    record = reloaded.get("docs/notes.txt")
    assert record is not None
    assert record.hash == "deadbeef"
    assert record.status == FileStatus.SYNCED


def test_atomic_write_uses_tmp_then_replaces(tmp_path):
    """The tmp file must not persist after save()."""
    path = tmp_path / ".mysilo_state.json"
    state = State(path)
    state.set("a.txt", _record())
    state.save()

    tmp = path.with_suffix(".json.tmp")
    assert not tmp.exists()
    assert path.exists()


def test_saved_file_is_valid_json(tmp_path):
    path = tmp_path / ".mysilo_state.json"
    state = State(path)
    state.set("x.txt", _record())
    state.save()

    with open(path) as f:
        data = json.load(f)
    assert data["version"] == 1
    assert "last_sync" in data
    assert "x.txt" in data["files"]


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def test_get_returns_none_for_missing_key(tmp_path):
    state = State(tmp_path / "s.json")
    assert state.get("nonexistent.txt") is None


def test_set_and_get(tmp_path):
    state = State(tmp_path / "s.json")
    state.set("img.png", _record(hash="ff00"))
    assert state.get("img.png").hash == "ff00"


def test_remove(tmp_path):
    state = State(tmp_path / "s.json")
    state.set("del.txt", _record())
    state.remove("del.txt")
    assert state.get("del.txt") is None


def test_remove_nonexistent_does_not_raise(tmp_path):
    state = State(tmp_path / "s.json")
    state.remove("ghost.txt")  # should not raise


def test_all_returns_copy(tmp_path):
    state = State(tmp_path / "s.json")
    state.set("a.txt", _record())
    snapshot = state.all()
    state.set("b.txt", _record())
    # Snapshot should not reflect the new addition
    assert "b.txt" not in snapshot


# ---------------------------------------------------------------------------
# Status queries
# ---------------------------------------------------------------------------

def test_conflicts_returns_only_conflict_records(tmp_path):
    state = State(tmp_path / "s.json")
    state.set("ok.txt", _record(status=FileStatus.SYNCED))
    state.set("bad.txt", _record(status=FileStatus.CONFLICT))
    state.set("broken.txt", _record(status=FileStatus.FAILED))

    conflicts = state.conflicts()
    assert list(conflicts.keys()) == ["bad.txt"]


def test_failures_returns_only_failed_records(tmp_path):
    state = State(tmp_path / "s.json")
    state.set("ok.txt", _record(status=FileStatus.SYNCED))
    state.set("bad.txt", _record(status=FileStatus.CONFLICT))
    state.set("broken.txt", _record(status=FileStatus.FAILED))

    failures = state.failures()
    assert list(failures.keys()) == ["broken.txt"]


def test_retry_count_and_last_error_persisted(tmp_path):
    path = tmp_path / "s.json"
    state = State(path)
    state.set("fail.txt", _record(
        status=FileStatus.FAILED,
        retry_count=3,
        last_error="attempt 4 of 4 failed",
    ))
    state.save()

    reloaded = State(path)
    rec = reloaded.get("fail.txt")
    assert rec.retry_count == 3
    assert rec.last_error == "attempt 4 of 4 failed"


def test_conflict_copy_persisted(tmp_path):
    path = tmp_path / "s.json"
    state = State(path)
    state.set("photo.jpg", _record(
        status=FileStatus.CONFLICT,
        conflict_copy="/tmp/photo.conflict_20260408_120000.jpg",
    ))
    state.save()

    reloaded = State(path)
    rec = reloaded.get("photo.jpg")
    assert rec.conflict_copy == "/tmp/photo.conflict_20260408_120000.jpg"
