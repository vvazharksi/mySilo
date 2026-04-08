import os
import yaml
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

# Precedence: env var > config file > default
_ENV_OVERRIDES = {
    "local_directory": "MYSILO_LOCAL_DIR",
    "bucket":          "MYSILO_REMOTE_BUCKET",
    "region":          "MYSILO_REGION",
    "kms_key_id":      "MYSILO_KMS_KEY_ID",
}


@dataclass
class Config:
    local_directory: str
    bucket: str
    region: str = "eu-west-1"
    sync_interval_minutes: int = 60
    upload_workers: int = 5
    # Retry settings for failed uploads/downloads
    max_retries: int = 3
    retry_backoff_seconds: float = 2.0
    # Remote state file — written by Lambda, read by local clients.
    remote_state_key: str = ".mysilo_remote_state.json"
    # How often to poll the remote state file ETag for changes (seconds).
    # Only kicks in when the Lambda is deployed; ignored otherwise.
    remote_poll_seconds: int = 30
    kms_key_id: Optional[str] = None
    ignore_patterns: List[str] = field(default_factory=lambda: [
        ".DS_Store", "__pycache__", "*.pyc", ".git", "Thumbs.db",
        ".Spotlight-V100", ".Trashes", ".fseventsd", "*.tmp", "~$*",
        ".mysilo_state.json", ".mysilo_state.json.tmp",
        ".mysilo_remote_state.json",
        ".mysilo",
    ])

    @classmethod
    def load(cls, path: str = "config.yaml") -> "Config":
        with open(path) as f:
            data = yaml.safe_load(f)

        for field_name, env_var in _ENV_OVERRIDES.items():
            value = os.environ.get(env_var)
            if value:
                data[field_name] = value

        return cls(**data)

    @property
    def root(self) -> Path:
        return Path(self.local_directory)

    @property
    def state_file_path(self) -> Path:
        return self.root / ".mysilo_state.json"

    @property
    def log_file(self) -> Path:
        return self.root / ".mysilo" / "mysilo.log"
