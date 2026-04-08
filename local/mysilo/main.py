"""
mySilo — CLI entry point.

Commands:
    mysilo start       Start the sync daemon
    mysilo reconcile   Run one full reconciliation and exit
    mysilo conflicts   List unresolved conflicts and exit
    mysilo failed      List persistently failing files and exit
"""

import logging
import signal
import sys
import threading
import time

import click
from apscheduler.events import EVENT_JOB_MAX_INSTANCES
from apscheduler.schedulers.background import BackgroundScheduler

from mysilo.config import Config
from mysilo.syncer import Syncer
from mysilo.watcher import Watcher

logger = logging.getLogger(__name__)


def _setup_logging(config: Config) -> None:
    """Configure root logger: console + log file inside the sync folder."""
    config.log_file.parent.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(fmt)

    file_handler = logging.FileHandler(config.log_file)
    file_handler.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(stream_handler)
    root.addHandler(file_handler)


def _start_remote_poller(config: Config, syncer: Syncer, stop: threading.Event) -> threading.Thread:
    """Poll the remote state file ETag every N seconds.

    A single HEAD call — if the ETag changed since last check it means
    Lambda wrote a new version (another machine uploaded/deleted something).
    Triggers an immediate reconcile so we don't wait for the next interval.
    """
    def _poll() -> None:
        last_etag = None
        while not stop.is_set():
            try:
                etag = syncer._s3.get_remote_state_etag(config.remote_state_key)
                if etag is not None and etag != last_etag:
                    if last_etag is not None:
                        logger.info(
                            "Remote state changed (ETag %s → %s) — triggering reconcile.",
                            last_etag, etag,
                        )
                        syncer.reconcile()
                    last_etag = etag
            except Exception as exc:
                logger.warning("Remote poller error: %s", exc)
            stop.wait(config.remote_poll_seconds)

    t = threading.Thread(target=_poll, name="remote-poller", daemon=True)
    t.start()
    return t


_config_option = click.option(
    "--config", "-c",
    default="config.yaml",
    show_default=True,
    help="Path to config file.",
)


def _load(config_path: str) -> tuple[Config, Syncer]:
    config = Config.load(config_path)
    config.root.mkdir(parents=True, exist_ok=True)
    _setup_logging(config)
    return config, Syncer(config)


@click.group()
def cli():
    """mySilo — personal S3 sync daemon."""


@cli.command()
@_config_option
def start(config: str):
    """Start the sync daemon."""
    cfg, syncer = _load(config)

    logger.info("mySilo starting.  Local: %s  Bucket: %s", cfg.local_directory, cfg.bucket)

    syncer.reconcile()

    watcher = Watcher(cfg.local_directory, syncer)
    watcher.start()

    scheduler = BackgroundScheduler()
    scheduler.add_job(
        syncer.reconcile,
        "interval",
        minutes=cfg.sync_interval_minutes,
        id="reconcile",
        coalesce=True,
        max_instances=1,
    )

    def _on_reconcile_skipped(_):
        logger.warning(
            "Scheduled reconciliation skipped — previous run still in progress. "
            "Will retry at the next interval."
        )

    scheduler.add_listener(_on_reconcile_skipped, EVENT_JOB_MAX_INSTANCES)
    scheduler.start()

    stop_poller = threading.Event()
    _start_remote_poller(cfg, syncer, stop_poller)
    logger.info(
        "Remote state poller started (interval: %ds, key: %s).",
        cfg.remote_poll_seconds,
        cfg.remote_state_key,
    )

    def _shutdown(sig, _):
        logger.info("Shutting down (signal %s)…", sig)
        stop_poller.set()
        watcher.stop()
        scheduler.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    logger.info("Daemon running. Press Ctrl+C to stop.")
    while True:
        time.sleep(1)


@cli.command()
@_config_option
def reconcile(config: str):
    """Run one full reconciliation and exit."""
    _, syncer = _load(config)
    syncer.reconcile()


@cli.command()
@_config_option
def conflicts(config: str):
    """List all unresolved conflicts."""
    _, syncer = _load(config)
    items = syncer._state.conflicts()
    if not items:
        click.echo("No conflicts.")
        return
    for rel, rec in items.items():
        click.echo(click.style("CONFLICT", fg="yellow", bold=True) + f"  {rel}")
        if rec.conflict_copy:
            click.echo(f"          remote copy → {rec.conflict_copy}")


@cli.command()
@_config_option
def failed(config: str):
    """List all persistently failing files."""
    _, syncer = _load(config)
    items = syncer._state.failures()
    if not items:
        click.echo("No failed files.")
        return
    for rel, rec in items.items():
        click.echo(
            click.style("FAILED", fg="red", bold=True)
            + f"  {rel}  (retried {rec.retry_count}x — {rec.last_error})"
        )


def main():
    cli()


if __name__ == "__main__":
    main()
