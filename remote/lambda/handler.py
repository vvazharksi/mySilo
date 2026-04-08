"""
mySilo — S3 state updater Lambda.

Triggered by S3 ObjectCreated and ObjectRemoved events.
Maintains a remote state file in the bucket so local clients can detect changes with a single HEAD call instead of
listing the entire bucket.
"""

import json
import logging
import os
from datetime import datetime, timezone
from urllib.parse import unquote_plus

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

BUCKET = os.environ["BUCKET"]
REMOTE_STATE_KEY = os.environ.get("REMOTE_STATE_KEY", ".mysilo_remote_state.json")

# Keys that should never appear in the remote state file.
_IGNORED_PREFIXES = (".mysilo",)
_IGNORED_KEYS = {REMOTE_STATE_KEY}

s3 = boto3.client("s3")

# Remote state helpers
def _load_state() -> dict:
    try:
        response = s3.get_object(Bucket=BUCKET, Key=REMOTE_STATE_KEY)
        return json.loads(response["Body"].read())
    except ClientError as exc:
        if exc.response["Error"]["Code"] in ("NoSuchKey", "404"):
            return {"version": 1, "files": {}}
        raise


def _save_state(state: dict) -> None:
    state["last_updated"] = datetime.now(timezone.utc).isoformat()
    s3.put_object(
        Bucket=BUCKET,
        Key=REMOTE_STATE_KEY,
        Body=json.dumps(state, indent=2).encode(),
        ContentType="application/json",
    )


def _get_file_hash(key: str) -> str | None:
    """Return the SHA-256 hash stored in S3 object metadata."""
    try:
        head = s3.head_object(Bucket=BUCKET, Key=key)
        return head.get("Metadata", {}).get("sha256")
    except ClientError:
        return None


def _is_ignored(key: str) -> bool:
    if key in _IGNORED_KEYS:
        return True
    return any(key.startswith(p) for p in _IGNORED_PREFIXES)


def handler(event, context):
    state = _load_state()
    changed = False

    for record in event.get("Records", []):
        event_name = record["eventName"]
        # S3 encodes spaces as '+' in event key names
        key = unquote_plus(record["s3"]["object"]["key"])

        if _is_ignored(key):
            logger.info("Ignoring key: %s", key)
            continue

        if event_name.startswith("ObjectRemoved"):
            if key in state["files"]:
                del state["files"][key]
                logger.info("Removed from remote state: %s", key)
                changed = True

        elif event_name.startswith("ObjectCreated"):
            obj = record["s3"]["object"]
            file_hash = _get_file_hash(key)
            state["files"][key] = {
                "hash": file_hash,
                "size": obj.get("size", 0),
            }
            logger.info("Updated remote state: %s (hash=%s)", key, file_hash)
            changed = True

    if changed:
        _save_state(state)
        logger.info("Remote state file saved. Total files: %d", len(state["files"]))

    return {"statusCode": 200}
