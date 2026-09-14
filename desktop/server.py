"""随桌面程序启动和停止的本地 FastAPI 服务。"""
from __future__ import annotations

import socket
import threading
import time
import urllib.request
from typing import Any

import uvicorn


def find_available_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


class LocalApiServer:
    def __init__(self, host: str = "127.0.0.1", port: int | None = None) -> None:
        self.host = host
        self.port = port or find_available_port(host)
        self.server: uvicorn.Server | None = None
        self.thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self) -> None:
        from backend.app.main import app

        # Windowed PyInstaller executables have no sys.stdout/sys.stderr. Uvicorn's
        # default logging dict points at those streams and fails before startup.
        config = uvicorn.Config(
            app,
            host=self.host,
            port=self.port,
            log_level="warning",
            access_log=False,
            log_config=None,
        )
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, name="local-fastapi", daemon=True)
        self.thread.start()

    def wait_until_ready(self, timeout: float = 20.0) -> tuple[bool, str]:
        deadline = time.monotonic() + timeout
        last_error = "本地服务未响应"
        while time.monotonic() < deadline:
            if self.thread is not None and not self.thread.is_alive():
                return False, "本地服务启动线程已退出"
            try:
                with urllib.request.urlopen(f"{self.base_url}/api/health", timeout=1.0) as response:
                    if response.status == 200:
                        return True, ""
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
            time.sleep(0.15)
        return False, last_error

    def stop(self, timeout: float = 8.0) -> None:
        if self.server is not None:
            self.server.should_exit = True
        if self.thread is not None and self.thread.is_alive():
            self.thread.join(timeout=timeout)

    def diagnostics(self) -> dict[str, Any]:
        return {"url": self.base_url, "running": bool(self.thread and self.thread.is_alive())}
