import os
import boto3
import pytest
from moto import mock_aws

from mysilo.config import Config

BUCKET = "test-mysilo-bucket"
REGION = "eu-west-1"


@pytest.fixture(autouse=True)
def aws_credentials():
    """Fake AWS credentials so boto3 never hits real AWS."""
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = REGION


@pytest.fixture
def mock_s3():
    """Start a moto S3 mock and create the test bucket. Yields the boto3 client."""
    with mock_aws():
        client = boto3.client("s3", region_name=REGION)
        client.create_bucket(
            Bucket=BUCKET,
            CreateBucketConfiguration={"LocationConstraint": REGION},
        )
        yield client


@pytest.fixture
def config(tmp_path, mock_s3):
    """A Config pointing at a temp directory with fast retry settings."""
    return Config(
        local_directory=str(tmp_path),
        bucket=BUCKET,
        region=REGION,
        max_retries=0,
        retry_backoff_seconds=0,
        upload_workers=2,
    )


@pytest.fixture
def syncer(config):
    from mysilo.syncer import Syncer
    return Syncer(config)
