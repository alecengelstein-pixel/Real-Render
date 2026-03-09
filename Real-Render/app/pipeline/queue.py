from __future__ import annotations

import queue
import threading
from typing import Callable

from .. import db
from .core import process_job


class JobQueue:
    def __init__(self) -> None:
        self._q: queue.Queue[str] = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="mcp-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def enqueue(self, job_id: str) -> None:
        self._q.put(job_id)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                job_id = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                process_job(job_id)
            except Exception as e:
                db.update_job(job_id, status="error", error=str(e))
            finally:
                self._q.task_done()


def start_worker_and_return_enqueuer() -> Callable[[str], None]:
    jq = JobQueue()
    jq.start()
    return jq.enqueue



