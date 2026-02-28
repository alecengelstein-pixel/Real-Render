from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from .config import settings
from .ingest import ingest_zip


class _ZipHandler(FileSystemEventHandler):
    def __init__(self, on_job: Callable[[str], None]) -> None:
        self._on_job = on_job

    def on_created(self, event):  # type: ignore[no-untyped-def]
        if event.is_directory:
            return
        self._maybe_ingest(Path(event.src_path))

    def on_moved(self, event):  # type: ignore[no-untyped-def]
        if event.is_directory:
            return
        self._maybe_ingest(Path(event.dest_path))

    def _maybe_ingest(self, path: Path) -> None:
        if path.name.startswith("."):
            return
        if path.suffix.lower() != ".zip":
            return
        try:
            job_id = ingest_zip(str(path))
            self._on_job(job_id)
        except Exception:
            # Swallow: watcher should never crash the app
            return


def start_inbox_watcher(on_job: Callable[[str], None]) -> Callable[[], None]:
    inbox = Path(settings.mcp_inbox_dir)
    inbox.mkdir(parents=True, exist_ok=True)

    observer = Observer()
    handler = _ZipHandler(on_job)
    observer.schedule(handler, str(inbox), recursive=False)
    observer.start()

    stop_evt = threading.Event()

    def _keepalive() -> None:
        while not stop_evt.is_set():
            time.sleep(0.5)

    t = threading.Thread(target=_keepalive, name="mcp-watcher-keepalive", daemon=True)
    t.start()

    def stop() -> None:
        stop_evt.set()
        observer.stop()
        observer.join(timeout=5)

    return stop



