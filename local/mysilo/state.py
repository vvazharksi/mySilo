import json
import threading
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional


class FileStatus:
    SYNCED = "synced"
    CONFLICT = "conflict"
    FAILED = "failed"


@dataclass
class FileRecord:
    hash: str
    size: int
    mtime: float
    s3_key: str
    status: str
    last_synced: Optional[str] = None
    conflict_copy: Optional[str] = None
    retry_count: int = 0
    last_error: Optional[str] = None


class State:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._files: Dict[str, FileRecord] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        with open(self._path) as f:
            raw = json.load(f)
        self._files = {
            k: FileRecord(**v) for k, v in raw.get("files", {}).items()
        }

    def save(self) -> None:
        with self._lock:
            payload = {
                "version": 1,
                "last_sync": datetime.now(timezone.utc).isoformat(),
                "files": {k: asdict(v) for k, v in self._files.items()},
            }
            # never leaves a corrupt state file.
            tmp = self._path.with_suffix(".json.tmp")
            with open(tmp, "w") as f:
                json.dump(payload, f, indent=2)
            tmp.replace(self._path)

    def get(self, rel_path: str) -> Optional[FileRecord]:
        return self._files.get(rel_path)

    def set(self, rel_path: str, record: FileRecord) -> None:
        with self._lock:
            self._files[rel_path] = record

    def remove(self, rel_path: str) -> None:
        with self._lock:
            self._files.pop(rel_path, None)

    def all(self) -> Dict[str, FileRecord]:
        return dict(self._files)

    def conflicts(self) -> Dict[str, FileRecord]:
        return {
            k: v for k, v in self._files.items()
            if v.status == FileStatus.CONFLICT
        }

    def failures(self) -> Dict[str, FileRecord]:
        return {
            k: v for k, v in self._files.items()
            if v.status == FileStatus.FAILED
        }
