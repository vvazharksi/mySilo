import json
import logging
import os
from pathlib import Path
from typing import Dict, Optional, Set

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

_HASH_META_KEY = "sha256"


class S3Client:
    def __init__(self, bucket: str, region: str, kms_key_id: Optional[str] = None) -> None:
        self._bucket = bucket
        self._client = boto3.client("s3", region_name=region)
        self._kms_key_id = kms_key_id

    def _upload_extra_args(self, file_hash: str) -> dict:
        """Build the ExtraArgs dict for upload_file, including SSE-KMS headers."""
        args: dict = {"Metadata": {_HASH_META_KEY: file_hash}}
        args["ServerSideEncryption"] = "aws:kms"
        if self._kms_key_id:
            args["SSEKMSKeyId"] = self._kms_key_id
        return args


    def upload(self, local_path: Path, s3_key: str, file_hash: str) -> bool:
        try:
            self._client.upload_file(
                str(local_path),
                self._bucket,
                s3_key,
                ExtraArgs=self._upload_extra_args(file_hash),
            )
            logger.info("Uploaded: %s", s3_key)
            return True
        except ClientError as exc:
            logger.error("Upload failed [%s]: %s", s3_key, exc)
            return False

    def delete(self, s3_key: str) -> bool:
        try:
            self._client.delete_object(Bucket=self._bucket, Key=s3_key)
            logger.info("Deleted from S3: %s", s3_key)
            return True
        except ClientError as exc:
            logger.error("Delete failed [%s]: %s", s3_key, exc)
            return False


    def download(self, s3_key: str, local_path: Path) -> bool:
        try:
            local_path.parent.mkdir(parents=True, exist_ok=True)
            self._client.download_file(self._bucket, s3_key, str(local_path))
            logger.info("Downloaded: %s", s3_key)
            return True
        except ClientError as exc:
            logger.error("Download failed [%s]: %s", s3_key, exc)
            return False

    def get_hash(self, s3_key: str) -> Optional[str]:
        """Return the SHA-256 hash stored in S3 object metadata, or None."""
        try:
            response = self._client.head_object(Bucket=self._bucket, Key=s3_key)
            return response.get("Metadata", {}).get(_HASH_META_KEY)
        except ClientError:
            return None

    def list_keys(self) -> Set[str]:
        """Return the set of all object keys in the bucket."""
        keys: Set[str] = set()
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket):
            for obj in page.get("Contents", []):
                keys.add(obj["Key"])
        return keys

 
    def get_remote_state_etag(self, key: str) -> Optional[str]:
        """Return the ETag of the remote state file, or None if it doesn't exist.

        Used by the poller to detect changes with a single cheap HEAD call.
        """
        try:
            response = self._client.head_object(Bucket=self._bucket, Key=key)
            return response.get("ETag")
        except ClientError:
            return None

    def get_remote_state(self, key: str) -> Optional[Dict]:
        """Download and parse the remote state JSON, or None if not found."""
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
            return json.loads(response["Body"].read())
        except ClientError:
            return None
