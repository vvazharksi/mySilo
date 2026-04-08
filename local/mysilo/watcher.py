import logging
import threading
from typing import Dict

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from .syncer import Syncer

logger = logging.getLogger(__name__)

_DEBOUNCE_SECONDS = 1.0


class _Handler(FileSystemEventHandler):
    def __init__(self, syncer: Syncer) -> None:
        super().__init__()
        self._syncer = syncer
        self._timers: Dict[str, threading.Timer] = {}
        self._lock = threading.Lock()


    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._debounce(event.src_path, self._syncer.handle_event,
                           event.src_path, "created")

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._debounce(event.src_path, self._syncer.handle_event,
                           event.src_path, "modified")

    def on_deleted(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._debounce(event.src_path, self._syncer.handle_event,
                           event.src_path, "deleted")

    def on_moved(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._debounce(event.src_path, self._syncer.handle_move,
                           event.src_path, event.dest_path)

    def _debounce(self, key: str, fn, *args) -> None:
        """Cancel any pending call for *key* and schedule a fresh one."""
        with self._lock:
            existing = self._timers.get(key)
            if existing:
                existing.cancel()
            timer = threading.Timer(_DEBOUNCE_SECONDS, fn, args)
            self._timers[key] = timer
            timer.start()


class Watcher:
    def __init__(self, directory: str, syncer: Syncer) -> None:
        self._directory = directory
        self._observer = Observer()
        self._observer.schedule(_Handler(syncer), directory, recursive=True)

    def start(self) -> None:
        self._observer.start()
        logger.info("Watching %s", self._directory)

    def stop(self) -> None:
        self._observer.stop()
        self._observer.join()
        logger.info("Watcher stopped.")
