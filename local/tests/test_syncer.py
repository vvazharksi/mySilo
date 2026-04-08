import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import boto3
import pytest

from mysilo.state import FileRecord, FileStatus
from tests.conftest import BUCKET, REGION


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _put_s3(mock_s3, key: str, content: bytes):
    """Helper — put a file in the mock bucket with correct hash metadata."""
    mock_s3.put_object(
        Bucket=BUCKET,
        Key=key,
        Body=content,
        Metadata={"sha256": _sha256(content)},
    )


def _put_remote_state(mock_s3, files: dict):
    """Helper — write a remote state file so reconcile uses it instead of list_objects."""
    payload = {"version": 1, "files": files}
    mock_s3.put_object(
        Bucket=BUCKET,
        Key=".mysilo_remote_state.json",
        Body=json.dumps(payload).encode(),
    )


def _object_exists(mock_s3, key: str) -> bool:
    try:
        mock_s3.head_object(Bucket=BUCKET, Key=key)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Upload — new local file
# ---------------------------------------------------------------------------

def test_reconcile_uploads_new_local_file(syncer, tmp_path, mock_s3):
    (tmp_path / "hello.txt").write_bytes(b"hello")
    syncer.reconcile()
    assert _object_exists(mock_s3, "hello.txt")


def test_reconcile_records_synced_status_after_upload(syncer, tmp_path):
    (tmp_path / "hello.txt").write_bytes(b"hello")
    syncer.reconcile()
    record = syncer._state.get("hello.txt")
    assert record is not None
    assert record.status == FileStatus.SYNCED
    assert record.hash == _sha256(b"hello")


def test_reconcile_uploads_file_in_subdirectory(syncer, tmp_path, mock_s3):
    sub = tmp_path / "docs"
    sub.mkdir()
    (sub / "notes.txt").write_bytes(b"notes")
    syncer.reconcile()
    assert _object_exists(mock_s3, "docs/notes.txt")


def test_reconcile_skips_unchanged_file(syncer, tmp_path, mock_s3):
    (tmp_path / "stable.txt").write_bytes(b"content")
    syncer.reconcile()  # first run — uploads

    # Patch upload to detect any second upload attempt
    with patch.object(syncer._s3, "upload", wraps=syncer._s3.upload) as mock_upload:
        syncer.reconcile()  # second run — should skip
        mock_upload.assert_not_called()


def test_reconcile_reuploads_modified_file(syncer, tmp_path, mock_s3):
    f = tmp_path / "changing.txt"
    f.write_bytes(b"version 1")
    syncer.reconcile()

    f.write_bytes(b"version 2")
    syncer.reconcile()

    obj = mock_s3.get_object(Bucket=BUCKET, Key="changing.txt")
    assert obj["Body"].read() == b"version 2"
    assert syncer._state.get("changing.txt").hash == _sha256(b"version 2")


# ---------------------------------------------------------------------------
# Download — remote-only file
# ---------------------------------------------------------------------------

def test_reconcile_downloads_remote_only_file(syncer, tmp_path, mock_s3):
    content = b"from the cloud"
    _put_s3(mock_s3, "remote.txt", content)
    syncer.reconcile()
    assert (tmp_path / "remote.txt").read_bytes() == content


def test_reconcile_records_synced_status_after_download(syncer, tmp_path, mock_s3):
    _put_s3(mock_s3, "remote.txt", b"data")
    syncer.reconcile()
    record = syncer._state.get("remote.txt")
    assert record is not None
    assert record.status == FileStatus.SYNCED


def test_reconcile_downloads_into_subdirectory(syncer, tmp_path, mock_s3):
    _put_s3(mock_s3, "sub/dir/file.txt", b"nested")
    syncer.reconcile()
    assert (tmp_path / "sub" / "dir" / "file.txt").exists()


# ---------------------------------------------------------------------------
# Delete — local deletion propagated to S3
# ---------------------------------------------------------------------------

def test_reconcile_deletes_from_s3_when_local_deleted(syncer, tmp_path, mock_s3):
    f = tmp_path / "gone.txt"
    f.write_bytes(b"temporary")
    syncer.reconcile()
    assert _object_exists(mock_s3, "gone.txt")

    f.unlink()
    syncer.reconcile()
    assert not _object_exists(mock_s3, "gone.txt")


def test_reconcile_removes_state_entry_after_delete(syncer, tmp_path):
    f = tmp_path / "gone.txt"
    f.write_bytes(b"bye")
    syncer.reconcile()

    f.unlink()
    syncer.reconcile()
    assert syncer._state.get("gone.txt") is None


# ---------------------------------------------------------------------------
# Conflict — both local and remote changed
# ---------------------------------------------------------------------------

def test_reconcile_detects_conflict(syncer, tmp_path, mock_s3):
    content = b"original"
    f = tmp_path / "shared.txt"
    f.write_bytes(content)
    syncer.reconcile()  # establishes synced baseline

    # Simulate remote change by updating S3 directly with different content
    _put_s3(mock_s3, "shared.txt", b"remote version")

    # Also change locally
    f.write_bytes(b"local version")

    syncer.reconcile()

    record = syncer._state.get("shared.txt")
    assert record.status == FileStatus.CONFLICT
    assert record.conflict_copy is not None
    assert Path(record.conflict_copy).exists()


def test_conflict_copy_contains_remote_content(syncer, tmp_path, mock_s3):
    (tmp_path / "doc.txt").write_bytes(b"original")
    syncer.reconcile()

    _put_s3(mock_s3, "doc.txt", b"remote edit")
    (tmp_path / "doc.txt").write_bytes(b"local edit")
    syncer.reconcile()

    record = syncer._state.get("doc.txt")
    assert Path(record.conflict_copy).read_bytes() == b"remote edit"


def test_local_file_untouched_during_conflict(syncer, tmp_path, mock_s3):
    (tmp_path / "doc.txt").write_bytes(b"original")
    syncer.reconcile()

    _put_s3(mock_s3, "doc.txt", b"remote edit")
    (tmp_path / "doc.txt").write_bytes(b"local edit")
    syncer.reconcile()

    assert (tmp_path / "doc.txt").read_bytes() == b"local edit"


# ---------------------------------------------------------------------------
# Retry and FAILED status
# ---------------------------------------------------------------------------

def test_upload_failure_marks_file_as_failed(syncer, tmp_path):
    (tmp_path / "bad.txt").write_bytes(b"content")

    with patch.object(syncer._s3, "upload", return_value=False):
        syncer.reconcile()

    record = syncer._state.get("bad.txt")
    assert record.status == FileStatus.FAILED
    assert record.retry_count == 1


def test_failed_file_retried_on_next_reconcile(syncer, tmp_path, mock_s3):
    f = tmp_path / "retry.txt"
    f.write_bytes(b"content")

    # First reconcile — upload fails
    with patch.object(syncer._s3, "upload", return_value=False):
        syncer.reconcile()
    assert syncer._state.get("retry.txt").status == FileStatus.FAILED

    # Second reconcile — upload succeeds
    syncer.reconcile()
    assert syncer._state.get("retry.txt").status == FileStatus.SYNCED


def test_retry_count_increments_on_repeated_failure(syncer, tmp_path):
    (tmp_path / "fail.txt").write_bytes(b"x")

    with patch.object(syncer._s3, "upload", return_value=False):
        syncer.reconcile()
        syncer.reconcile()

    assert syncer._state.get("fail.txt").retry_count == 2


# ---------------------------------------------------------------------------
# Ignored files
# ---------------------------------------------------------------------------

def test_ignored_files_not_uploaded(syncer, tmp_path, mock_s3):
    (tmp_path / ".DS_Store").write_bytes(b"junk")
    (tmp_path / "keep.txt").write_bytes(b"keep")
    syncer.reconcile()

    assert not _object_exists(mock_s3, ".DS_Store")
    assert _object_exists(mock_s3, "keep.txt")


def test_mysilo_state_file_not_uploaded(syncer, tmp_path, mock_s3):
    (tmp_path / "real.txt").write_bytes(b"real")
    syncer.reconcile()
    assert not _object_exists(mock_s3, ".mysilo_state.json")


def test_mysilo_dir_not_uploaded(syncer, tmp_path, mock_s3):
    hidden = tmp_path / ".mysilo"
    hidden.mkdir()
    (hidden / "mysilo.log").write_bytes(b"log data")
    (tmp_path / "real.txt").write_bytes(b"real")
    syncer.reconcile()
    assert not _object_exists(mock_s3, ".mysilo/mysilo.log")


# ---------------------------------------------------------------------------
# Watcher event handler
# ---------------------------------------------------------------------------

def test_handle_event_created_uploads_file(syncer, tmp_path, mock_s3):
    f = tmp_path / "new.txt"
    f.write_bytes(b"new content")
    syncer.handle_event(str(f), "created")
    assert _object_exists(mock_s3, "new.txt")


def test_handle_event_modified_reuploads_file(syncer, tmp_path, mock_s3):
    f = tmp_path / "mod.txt"
    f.write_bytes(b"v1")
    syncer.handle_event(str(f), "created")

    f.write_bytes(b"v2")
    syncer.handle_event(str(f), "modified")

    obj = mock_s3.get_object(Bucket=BUCKET, Key="mod.txt")
    assert obj["Body"].read() == b"v2"


def test_handle_event_deleted_removes_from_s3(syncer, tmp_path, mock_s3):
    f = tmp_path / "del.txt"
    f.write_bytes(b"bye")
    syncer.handle_event(str(f), "created")
    assert _object_exists(mock_s3, "del.txt")

    f.unlink()
    syncer.handle_event(str(f), "deleted")
    assert not _object_exists(mock_s3, "del.txt")


def test_handle_event_ignored_for_directory(syncer, tmp_path, mock_s3):
    d = tmp_path / "subdir"
    d.mkdir()
    # Should not raise and should not upload anything
    syncer.handle_event(str(d), "created")
    assert syncer._state.all() == {}


# ---------------------------------------------------------------------------
# handle_move
# ---------------------------------------------------------------------------

def test_handle_move_uploads_dest_and_deletes_src(syncer, tmp_path, mock_s3):
    src = tmp_path / "src.txt"
    src.write_bytes(b"moving")
    syncer.handle_event(str(src), "created")
    assert _object_exists(mock_s3, "src.txt")

    dest = tmp_path / "dest.txt"
    src.rename(dest)
    syncer.handle_move(str(src), str(dest))

    assert not _object_exists(mock_s3, "src.txt")
    assert _object_exists(mock_s3, "dest.txt")
