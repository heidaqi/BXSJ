"""桌面壳冒烟测试：启动本地 API，用 QtWebEngine 加载并验证 React 根界面。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("YOLO_DESKTOP_MODE", "1")
os.environ.setdefault("YOLO_USER_DATA_DIR", str(Path(tempfile.gettempdir()) / "YoloInspection-smoke-data"))
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication

from desktop.server import LocalApiServer


def main() -> int:
    app = QApplication(sys.argv)
    server = LocalApiServer()
    server.start()
    ready, error = server.wait_until_ready()
    if not ready:
        print(f"FAIL api: {error}")
        server.stop()
        return 1

    view = QWebEngineView()
    result = {"code": 1}

    def finish(ok: bool, detail: str) -> None:
        screenshot = Path(tempfile.gettempdir()) / "YoloInspection-desktop-smoke.png"
        view.grab().save(str(screenshot))
        print(f"{'PASS' if ok else 'FAIL'} ui: {detail}")
        print(f"screenshot: {screenshot}")
        result["code"] = 0 if ok else 1
        QTimer.singleShot(0, app.quit)

    def loaded(ok: bool) -> None:
        if not ok:
            finish(False, "QtWebEngine 页面加载失败")
            return
        view.page().runJavaScript(
            "Boolean(document.querySelector('.shell')) && document.body.innerText.includes('PAUT 缺陷智能检测系统')",
            lambda valid: QTimer.singleShot(
                1000,
                lambda: finish(bool(valid), "React 主界面已渲染" if valid else "未找到 React 主界面"),
            ),
        )

    view.resize(1440, 900)
    view.loadFinished.connect(loaded)
    view.load(QUrl(server.base_url))
    view.show()
    QTimer.singleShot(30000, lambda: finish(False, "等待页面超时"))
    app.exec()
    server.stop()
    return int(result["code"])


if __name__ == "__main__":
    raise SystemExit(main())
