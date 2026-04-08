import os
import pytest
from pathlib import Path

from mysilo.config import Config

MINIMAL_YAML = """\
local_directory: /tmp/mysilo_test
bucket: my-bucket
region: eu-west-1
"""


def test_defaults():
    config = Config(local_directory="/tmp/x", bucket="b")
    assert config.region == "eu-west-1"
    assert config.sync_interval_minutes == 60
    assert config.upload_workers == 5
    assert config.max_retries == 3
    assert config.retry_backoff_seconds == 2.0
    assert config.remote_poll_seconds == 30
    assert config.kms_key_id is None


def test_load_from_yaml(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(MINIMAL_YAML)
    config = Config.load(str(cfg_file))
    assert config.local_directory == "/tmp/mysilo_test"
    assert config.bucket == "my-bucket"
    assert config.region == "eu-west-1"


def test_env_var_overrides_local_directory(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(MINIMAL_YAML)
    monkeypatch.setenv("MYSILO_LOCAL_DIR", "/overridden/path")
    config = Config.load(str(cfg_file))
    assert config.local_directory == "/overridden/path"


def test_env_var_overrides_bucket(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(MINIMAL_YAML)
    monkeypatch.setenv("MYSILO_REMOTE_BUCKET", "override-bucket")
    config = Config.load(str(cfg_file))
    assert config.bucket == "override-bucket"


def test_env_var_overrides_region(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(MINIMAL_YAML)
    monkeypatch.setenv("MYSILO_REGION", "us-east-1")
    config = Config.load(str(cfg_file))
    assert config.region == "us-east-1"


def test_env_var_overrides_kms_key(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(MINIMAL_YAML)
    monkeypatch.setenv("MYSILO_KMS_KEY_ID", "arn:aws:kms:eu-west-1:123:key/abc")
    config = Config.load(str(cfg_file))
    assert config.kms_key_id == "arn:aws:kms:eu-west-1:123:key/abc"


def test_root_property():
    config = Config(local_directory="/tmp/mysilo", bucket="b")
    assert config.root == Path("/tmp/mysilo")


def test_state_file_path_property():
    config = Config(local_directory="/tmp/mysilo", bucket="b")
    assert config.state_file_path == Path("/tmp/mysilo/.mysilo_state.json")


def test_log_file_property():
    config = Config(local_directory="/tmp/mysilo", bucket="b")
    assert config.log_file == Path("/tmp/mysilo/.mysilo/mysilo.log")


def test_ignore_patterns_include_mysilo_files():
    config = Config(local_directory="/tmp/x", bucket="b")
    assert ".mysilo_state.json" in config.ignore_patterns
    assert ".mysilo_remote_state.json" in config.ignore_patterns
    assert ".mysilo" in config.ignore_patterns
    assert ".DS_Store" in config.ignore_patterns
