from __future__ import annotations

import uvicorn

from . import db
from .config import settings
from .services.inbox_watcher import start_inbox_watcher
from .pipeline.queue import start_worker_and_return_enqueuer
from .routes.web import create_app


def main() -> None:
    db.init_db()

    enqueue = start_worker_and_return_enqueuer()
    _stop_watcher = start_inbox_watcher(enqueue)

    app = create_app(enqueue)
    uvicorn.run(app, host=settings.mcp_host, port=settings.mcp_port, log_level="info")


if __name__ == "__main__":
    main()
