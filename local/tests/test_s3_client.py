import hashlib
import json
import pytest
import boto3

from mysilo.s3_client import S3Client
from tests.conftest import BUCKET, REGION


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def client(mock_s3):
    return S3Client(bucket=BUCKET, region=REGION)


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

def test_upload_returns_true_on_success(client, tmp_path):
    f = tmp_path / "hello.txt"
    f.write_bytes(b"hello")
    assert client.upload(f, "hello.txt", _sha256(b"hello")) is True


def test_upload_stores_hash_in_metadata(client, tmp_path, mock_s3):
    content = b"test content"
    f = tmp_path / "file.txt"
    f.write_bytes(content)
    file_hash = _sha256(content)

    client.upload(f, "file.txt", file_hash)

    head = mock_s3.head_object(Bucket=BUCKET, Key="file.txt")
    assert head["Metadata"]["sha256"] == file_hash


def test_upload_stores_correct_content(client, tmp_path, mock_s3):
    content = b"the file content"
    f = tmp_path / "data.bin"
    f.write_bytes(content)

    client.upload(f, "data.bin", _sha256(content))

    obj = mock_s3.get_object(Bucket=BUCKET, Key="data.bin")
    assert obj["Body"].read() == content


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def test_download_returns_true_on_success(client, tmp_path, mock_s3):
    mock_s3.put_object(Bucket=BUCKET, Key="remote.txt", Body=b"data")
    dest = tmp_path / "remote.txt"
    assert client.download("remote.txt", dest) is True


def test_download_writes_correct_content(client, tmp_path, mock_s3):
    content = b"downloaded content"
    mock_s3.put_object(Bucket=BUCKET, Key="doc.txt", Body=content)
    dest = tmp_path / "sub" / "doc.txt"

    client.download("doc.txt", dest)

    assert dest.read_bytes() == content


def test_download_creates_parent_directories(client, tmp_path, mock_s3):
    mock_s3.put_object(Bucket=BUCKET, Key="a/b/c.txt", Body=b"x")
    dest = tmp_path / "a" / "b" / "c.txt"

    client.download("a/b/c.txt", dest)

    assert dest.exists()


def test_download_returns_false_for_missing_key(client, tmp_path):
    assert client.download("nonexistent.txt", tmp_path / "out.txt") is False


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

def test_delete_returns_true_on_success(client, mock_s3):
    mock_s3.put_object(Bucket=BUCKET, Key="todelete.txt", Body=b"x")
    assert client.delete("todelete.txt") is True


def test_delete_removes_object(client, mock_s3):
    mock_s3.put_object(Bucket=BUCKET, Key="gone.txt", Body=b"x")
    client.delete("gone.txt")

    keys = {o["Key"] for page in mock_s3.get_paginator("list_objects_v2")
            .paginate(Bucket=BUCKET) for o in page.get("Contents", [])}
    assert "gone.txt" not in keys


# ---------------------------------------------------------------------------
# List keys
# ---------------------------------------------------------------------------

def test_list_keys_empty_bucket(client):
    assert client.list_keys() == set()


def test_list_keys_returns_all_objects(client, mock_s3):
    for key in ["a.txt", "b/c.txt", "d.png"]:
        mock_s3.put_object(Bucket=BUCKET, Key=key, Body=b"x")

    assert client.list_keys() == {"a.txt", "b/c.txt", "d.png"}


# ---------------------------------------------------------------------------
# get_hash
# ---------------------------------------------------------------------------

def test_get_hash_returns_metadata_hash(client, mock_s3):
    file_hash = _sha256(b"content")
    mock_s3.put_object(
        Bucket=BUCKET, Key="hashed.txt", Body=b"content",
        Metadata={"sha256": file_hash},
    )
    assert client.get_hash("hashed.txt") == file_hash


def test_get_hash_returns_none_for_missing_key(client):
    assert client.get_hash("ghost.txt") is None


def test_get_hash_returns_none_when_no_metadata(client, mock_s3):
    mock_s3.put_object(Bucket=BUCKET, Key="no-meta.txt", Body=b"x")
    assert client.get_hash("no-meta.txt") is None


# ---------------------------------------------------------------------------
# Remote state
# ---------------------------------------------------------------------------

def test_get_remote_state_returns_none_when_missing(client):
    assert client.get_remote_state(".mysilo_remote_state.json") is None


def test_get_remote_state_returns_parsed_json(client, mock_s3):
    payload = {"version": 1, "files": {"a.txt": {"hash": "abc", "size": 10}}}
    mock_s3.put_object(
        Bucket=BUCKET,
        Key=".mysilo_remote_state.json",
        Body=json.dumps(payload).encode(),
    )
    result = client.get_remote_state(".mysilo_remote_state.json")
    assert result == payload


def test_get_remote_state_etag_returns_none_when_missing(client):
    assert client.get_remote_state_etag(".mysilo_remote_state.json") is None


def test_get_remote_state_etag_returns_string_when_exists(client, mock_s3):
    mock_s3.put_object(Bucket=BUCKET, Key=".mysilo_remote_state.json", Body=b"{}")
    etag = client.get_remote_state_etag(".mysilo_remote_state.json")
    assert etag is not None
    assert isinstance(etag, str)
