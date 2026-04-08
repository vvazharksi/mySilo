import fnmatch
import hashlib
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Set

from .config import Config
from .s3_client import S3Client
from .state import FileRecord, FileStatus, State

logger = logging.getLogger(__name__)


def _compute_hash(path: Path) -> str:
    sha256 = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65_536), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def _conflict_path(path: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return path.parent / f"{path.stem}.conflict_{ts}{path.suffix}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Syncer:
    def __init__(self, config: Config) -> None:
        self._config = config
        self._root = config.root
        self._state = State(config.state_file_path)
        self._s3 = S3Client(config.bucket, config.region, config.kms_key_id)
        # Serialise reconcile() and handle_event() so they never overlap.
        self._lock = threading.Lock()


    def handle_event(self, abs_path: str, event_type: str) -> None:
        path = Path(abs_path)
        rel = self._rel(path)
        if self._ignored(rel):
            return

        with self._lock:
            if event_type in ("created", "modified"):
                if not path.is_file():
                    return
                self._push(rel, path)
            elif event_type == "deleted":
                self._delete(rel)
            self._state.save()

    def handle_move(self, src: str, dest: str) -> None:
        src_path, dest_path = Path(src), Path(dest)
        src_rel = self._rel(src_path)
        dest_rel = self._rel(dest_path)

        with self._lock:
            if not self._ignored(src_rel):
                self._delete(src_rel)
            if dest_path.is_file() and not self._ignored(dest_rel):
                self._push(dest_rel, dest_path)
            self._state.save()

    def reconcile(self) -> None:
        logger.info("Reconciliation started.")

        with self._lock:
            tasks = self._plan()

        if tasks:
            logger.info(
                "Executing %d task(s) with %d worker(s).",
                len(tasks), self._config.upload_workers,
            )
            with ThreadPoolExecutor(max_workers=self._config.upload_workers) as pool:
                futures = {pool.submit(t): t for t in tasks}
                for future in as_completed(futures):
                    exc = future.exception()
                    if exc:
                        logger.error("Task failed: %s", exc)

        with self._lock:
            self._state.save()

        logger.info("Reconciliation complete.")
        self._report_conflicts()
        self._report_failed()


    def _plan(self) -> List[Callable]:
        local_files: Set[str] = self._scan_local()
        remote_files = self._load_remote_files()
        tasks: List[Callable] = []

        # Files that exist locally 
        for rel in local_files:
            local_path = self._root / rel
            local_hash = _compute_hash(local_path)
            record = self._state.get(rel)
            remote_info = remote_files.get(rel)
            in_remote = remote_info is not None
            remote_hash = remote_info.get("hash") if remote_info else None

            if record is None or record.status == FileStatus.FAILED:
                if not in_remote:
                    tasks.append(lambda r=rel, lp=local_path, lh=local_hash:
                                 self._upload(r, lp, lh))
                else:
                    if remote_hash == local_hash:
                        self._record_synced(rel, local_path, local_hash)
                    else:
                        tasks.append(lambda r=rel, lp=local_path, lh=local_hash:
                                     self._upload(r, lp, lh))
            else:
                synced_hash = record.hash
                local_changed = local_hash != synced_hash
                remote_changed = remote_hash != synced_hash  # covers None

                if not local_changed and not remote_changed:
                    pass  # In sync.
                elif local_changed and not remote_changed:
                    tasks.append(lambda r=rel, lp=local_path, lh=local_hash:
                                 self._upload(r, lp, lh))
                elif not local_changed and remote_changed:
                    if remote_hash is None:
                        logger.info("Remote deleted externally, re-uploading: %s", rel)
                        tasks.append(lambda r=rel, lp=local_path, lh=local_hash:
                                     self._upload(r, lp, lh))
                    else:
                        tasks.append(lambda r=rel, lp=local_path, rh=remote_hash:
                                     self._download(r, lp, rh))
                else:
                    tasks.append(lambda r=rel, lp=local_path, lh=local_hash:
                                 self._handle_conflict(r, lp, lh))

        # Files only in remote 
        for key, info in remote_files.items():
            if self._ignored(key) or key in local_files:
                continue
            local_path = self._root / key
            record = self._state.get(key)
            remote_hash = info.get("hash")

            if record is not None:
                tasks.append(lambda k=key: self._delete(k))
            else:
                tasks.append(lambda k=key, lp=local_path, rh=remote_hash:
                             self._download(k, lp, rh))

        return tasks

    def _load_remote_files(self) -> Dict[str, dict]:
        remote_state = self._s3.get_remote_state(self._config.remote_state_key)
        if remote_state is not None:
            return remote_state.get("files", {})

        logger.warning(
            "Remote state file not found (%s). "
            "Lambda may not be deployed — falling back to list_objects.",
            self._config.remote_state_key,
        )
        keys = self._s3.list_keys()
        return {k: {"hash": self._s3.get_hash(k)} for k in keys}


    def _with_retry(self, label: str, operation, *args) -> tuple[bool, str]:
        delay = self._config.retry_backoff_seconds
        last_error = ""
        for attempt in range(self._config.max_retries + 1):
            if operation(*args):
                return True, ""
            last_error = f"attempt {attempt + 1} of {self._config.max_retries + 1} failed"
            if attempt < self._config.max_retries:
                logger.warning("[%s] %s — retrying in %.0fs…", label, last_error, delay)
                time.sleep(delay)
                delay *= 2
        return False, last_error

    # Atomic operations
    def _push(self, rel: str, local_path: Path) -> None:
        """Upload if the file has changed since last sync."""
        local_hash = _compute_hash(local_path)
        record = self._state.get(rel)
        if record and record.hash == local_hash and record.status == FileStatus.SYNCED:
            return
        self._upload(rel, local_path, local_hash)

    def _upload(self, rel: str, local_path: Path, local_hash: str) -> None:
        success, error = self._with_retry(rel, self._s3.upload, local_path, rel, local_hash)
        stat = local_path.stat()
        if success:
            self._state.set(rel, FileRecord(
                hash=local_hash,
                size=stat.st_size,
                mtime=stat.st_mtime,
                s3_key=rel,
                status=FileStatus.SYNCED,
                last_synced=_now_iso(),
            ))
        else:
            existing = self._state.get(rel)
            retry_count = (existing.retry_count if existing else 0) + 1
            logger.error("Upload permanently failed after %d attempt(s): %s",
                         self._config.max_retries + 1, rel)
            self._state.set(rel, FileRecord(
                hash=local_hash,
                size=stat.st_size,
                mtime=stat.st_mtime,
                s3_key=rel,
                status=FileStatus.FAILED,
                last_synced=_now_iso(),
                retry_count=retry_count,
                last_error=error,
            ))

    def _download(self, rel: str, local_path: Path, remote_hash: str) -> None:
        success, error = self._with_retry(rel, self._s3.download, rel, local_path)
        if success:
            stat = local_path.stat()
            self._state.set(rel, FileRecord(
                hash=remote_hash,
                size=stat.st_size,
                mtime=stat.st_mtime,
                s3_key=rel,
                status=FileStatus.SYNCED,
                last_synced=_now_iso(),
            ))
        else:
            existing = self._state.get(rel)
            retry_count = (existing.retry_count if existing else 0) + 1
            logger.error("Download permanently failed after %d attempt(s): %s",
                         self._config.max_retries + 1, rel)
            self._state.set(rel, FileRecord(
                hash=remote_hash,
                size=0,
                mtime=0.0,
                s3_key=rel,
                status=FileStatus.FAILED,
                last_synced=_now_iso(),
                retry_count=retry_count,
                last_error=error,
            ))

    def _delete(self, rel: str) -> None:
        if self._s3.delete(rel):
            self._state.remove(rel)

    def _handle_conflict(self, rel: str, local_path: Path, local_hash: str) -> None:
        conflict = _conflict_path(local_path)
        if self._s3.download(rel, conflict):
            logger.warning("CONFLICT detected: %s", rel)
            logger.warning("  Remote version saved as: %s", conflict.name)
            stat = local_path.stat()
            self._state.set(rel, FileRecord(
                hash=local_hash,
                size=stat.st_size,
                mtime=stat.st_mtime,
                s3_key=rel,
                status=FileStatus.CONFLICT,
                last_synced=_now_iso(),
                conflict_copy=str(conflict),
            ))

    def _record_synced(self, rel: str, local_path: Path, file_hash: str) -> None:
        stat = local_path.stat()
        self._state.set(rel, FileRecord(
            hash=file_hash,
            size=stat.st_size,
            mtime=stat.st_mtime,
            s3_key=rel,
            status=FileStatus.SYNCED,
            last_synced=_now_iso(),
        ))

    def _scan_local(self) -> Set[str]:
        result: Set[str] = set()
        for abs_path in self._root.rglob("*"):
            if abs_path.is_file():
                rel = self._rel(abs_path)
                if not self._ignored(rel):
                    result.add(rel)
        return result

    def _rel(self, abs_path: Path) -> str:
        return str(abs_path.relative_to(self._root))

    def _ignored(self, rel_path: str) -> bool:
        parts = Path(rel_path).parts
        for pattern in self._config.ignore_patterns:
            for part in parts:
                if fnmatch.fnmatch(part, pattern):
                    return True
        return False

    def _report_failed(self) -> None:
        failures = self._state.failures()
        if not failures:
            return
        logger.warning("=" * 60)
        logger.warning("%d file(s) are persistently failing:", len(failures))
        for rel, rec in failures.items():
            logger.warning("  %s  (failed %d time(s): %s)",
                           rel, rec.retry_count, rec.last_error)
        logger.warning("Run with --failed for the full list.")
        logger.warning("These files will be retried on every reconciliation.")
        logger.warning("=" * 60)

    def _report_conflicts(self) -> None:
        conflicts = self._state.conflicts()
        if not conflicts:
            return
        logger.warning("=" * 60)
        logger.warning("%d unresolved conflict(s):", len(conflicts))
        for rel, rec in conflicts.items():
            logger.warning("  %s", rel)
            if rec.conflict_copy:
                logger.warning("    Remote copy -> %s", rec.conflict_copy)
        logger.warning("Delete the copy you don't want, then the conflict")
        logger.warning("will clear on the next reconciliation.")
        logger.warning("=" * 60)
