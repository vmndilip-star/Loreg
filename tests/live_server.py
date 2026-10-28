"""A real uvicorn server on a free port, in a thread, for browser and streaming tests."""

import socket
import threading
import time
from pathlib import Path

import httpx
import uvicorn

from app.api.main import create_app


class LiveServer:
    def __init__(self, db_path: Path, as_of: str = "2026-10-26", **app_kwargs):
        self.app = create_app(db_path, as_of=as_of, **app_kwargs)
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self._server = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="error"))
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def __enter__(self):
        self._thread.start()
        for _ in range(200):
            try:
                if httpx.get(self.url + "/api/health").status_code == 200:
                    return self
            except httpx.HTTPError:
                time.sleep(0.05)
        raise RuntimeError("the test server did not start")

    def __exit__(self, *exc):
        self._server.should_exit = True
        self._thread.join(timeout=10)
