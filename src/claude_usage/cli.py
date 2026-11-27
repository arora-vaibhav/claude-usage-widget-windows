"""Command line and GUI entry point.

``claude-usage`` starts the app. ``--version``, ``--json`` and ``--reindex``
run once and exit, without Qt.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Sequence

from claude_usage import __version__
from claude_usage.config import load_config, user_config_path
from claude_usage.logging_setup import get_logger, setup_logging

RANGES = ("24h", "7d", "30d", "90d", "all")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="claude-usage", description="Claude Code usage: the app by default, or one-off commands.")
    p.add_argument("--version", action="store_true", help="Print the version and exit.")
    p.add_argument("--json", action="store_true",
                   help="Update the store from your transcripts, fetch the limits and print the snapshot as JSON.")
    p.add_argument("--range", choices=RANGES, default="7d", help="Range for --json (default: 7d).")
    p.add_argument("--reindex", action="store_true", help="Read every transcript again, then exit.")
    return p


def _open_store(config: dict, reindex: bool = False):
    from claude_usage import ingest, store

    conn = store.connect()
    if conn.execute("PRAGMA user_version").fetchone()[0] > store.SCHEMA_VERSION:
        sys.exit(f"{store.default_path()} was made by a newer Claude Usage; update this one first.")
    try:
        store.import_legacy(conn, config["claude_dir"])
    except Exception as exc:  # the old daily totals are optional; a bad row must not block the store
        print(f"WARNING: could not import the old usage files: {exc}", file=sys.stderr)
    if reindex:
        with conn:  # forget every read offset; re-reading is safe, calls are upserted
            conn.execute("DELETE FROM files")
    return conn, ingest.Ingestor(conn, os.path.join(config["claude_dir"], "projects"))


def run_cli(argv: Sequence[str]) -> int:
    """Run one CLI command and return its exit code, or -1 when the GUI should start."""
    args = build_parser().parse_args(list(argv))
    if args.version:
        print(__version__)
        return 0
    if not (args.json or args.reindex):
        return -1
    config = load_config(user_config_path())
    conn, ing = _open_store(config, reindex=args.reindex)

    def progress(done: int, total: int) -> None:
        if total >= 100 and (done % 100 == 0 or done == total):  # only a long pass, like the first one
            print(f"\rIndexed {done} of {total} transcripts", end="", file=sys.stderr, flush=True)

    result = ing.index(progress)
    if result["todo"] >= 100 or args.reindex:
        print(f"\n{result['calls']} API calls read from {result['read']} transcripts in {result['ms'] / 1000:.1f} s",
              file=sys.stderr)
    if args.json:
        from claude_usage import collector, snapshot

        t = config.get("notify_thresholds") or [0.75, 0.90]
        snap = snapshot.build(conn, args.range, None, time.time(), collector.fetch_rate_limits(config["claude_dir"]),
                              updated_at=time.time(), thresholds=(float(t[0]), float(t[1])))
        json.dump(snap, sys.stdout, indent=2)
        print()
    conn.close()
    return 0


def _launch_gui() -> int:
    import signal
    import threading

    signal.signal(signal.SIGINT, signal.SIG_DFL)

    from PySide6.QtCore import QLockFile, QtMsgType, qInstallMessageHandler
    from PySide6.QtWidgets import QApplication, QMessageBox

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("claude-usage")
    app.setQuitOnLastWindowClosed(False)

    config = load_config(user_config_path())
    setup_logging(config["claude_dir"])
    log = get_logger()
    # The launcher hides the console, so Qt's own warnings go to the log file.
    qInstallMessageHandler(lambda mode, _ctx, msg: None if mode in (QtMsgType.QtDebugMsg, QtMsgType.QtInfoMsg)
                           else log.warning("qt: %s", msg))
    # Likewise errors raised in a Qt slot (PySide hands them to sys.excepthook) or in a thread.
    sys.excepthook = lambda *exc: log.error("uncaught error", exc_info=exc)
    threading.excepthook = lambda a: log.error("uncaught error in thread %s", a.thread and a.thread.name,
                                               exc_info=(a.exc_type, a.exc_value, a.exc_traceback))
    log.info("claude-usage %s starting", __version__)

    from claude_usage.widget import Controller, ask_running_instance

    # One instance only: concurrent copies would each refresh the rotating OAuth token
    # and invalidate each other. QLockFile also clears a lock left by a crash.
    os.makedirs(config["claude_dir"], exist_ok=True)  # else tryLock fails and we'd claim "already running"
    lock = QLockFile(os.path.join(config["claude_dir"], ".claude-usage.lock"))
    if not lock.tryLock(100):
        for _ in range(20):  # the running copy listens only once it has started, which can take seconds
            if ask_running_instance():
                log.info("already running; asked it to open its window")
                return 0
            time.sleep(0.5)
        log.warning("another claude-usage instance holds the lock and did not answer; exiting")
        # The launcher hides the console, so a message on stderr would never be seen.
        QMessageBox.information(None, "Claude Usage", "Claude Usage is already running but did not respond. "
                                "If an older version is running, quit it from its tray icon, then start "
                                "Claude Usage again.")
        return 0

    controller = Controller(config)
    controller.start()
    rc = app.exec()
    del controller, lock  # keep both alive until the event loop ends
    return rc


def main() -> int:
    """Entry point of the ``claude-usage`` console script."""
    if sys.version_info < (3, 10):
        print("ERROR: Python 3.10+ is required.", file=sys.stderr)
        return 1
    rc = run_cli(sys.argv[1:])
    return rc if rc >= 0 else _launch_gui()


if __name__ == "__main__":
    sys.exit(main())
