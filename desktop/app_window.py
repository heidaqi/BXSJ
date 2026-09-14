"""承载 React 前端的桌面主窗口。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QMainWindow

from .bridge import DesktopBridge


class AppWebPage(QWebEnginePage):
    """应用内链接留在窗口中，外部链接交给系统浏览器。"""

    def acceptNavigationRequest(self, url: QUrl, nav_type, is_main_frame: bool) -> bool:  # noqa: N802
        if url.scheme() in {"http", "https"} and url.host() not in {"127.0.0.1", "localhost"}:
            QDesktopServices.openUrl(url)
            return False
        return super().acceptNavigationRequest(url, nav_type, is_main_frame)


class AppWindow(QMainWindow):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url.rstrip("/")
        self.setWindowTitle("工业图像缺陷智能检测系统")
        self.resize(1440, 900)
        self.setMinimumSize(1024, 700)

        self.web_view = QWebEngineView(self)
        self.web_view.setPage(AppWebPage(self.web_view))
        self.web_view.settings().setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True
        )
        self.setCentralWidget(self.web_view)

        self.bridge = DesktopBridge(self)
        self.channel = QWebChannel(self.web_view.page())
        self.channel.registerObject("desktopBridge", self.bridge)
        self.web_view.page().setWebChannel(self.channel)
        self.web_view.loadFinished.connect(self._on_load_finished)
        self.show_loading("正在启动本地服务，请稍候...")

    def show_loading(self, message: str) -> None:
        html = f"""
        <!doctype html><html lang="zh-CN"><meta charset="utf-8">
        <style>
          body{{margin:0;background:#edf1f5;color:#132238;font-family:'Microsoft YaHei',sans-serif}}
          main{{height:100vh;display:grid;place-items:center}}
          section{{width:min(560px,80vw);background:white;border:1px solid #d5dde6;padding:32px}}
          h1{{font-size:22px;margin:0 0 14px}}p{{color:#526579;line-height:1.7}}
        </style><main><section><h1>工业图像缺陷智能检测系统</h1><p>{message}</p></section></main></html>
        """
        self.web_view.setHtml(html)

    def show_startup_error(self, message: str, log_path: Path | None = None) -> None:
        detail = str(message).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        log_hint = f"<p>日志：{log_path}</p>" if log_path else ""
        html = f"""
        <!doctype html><html lang="zh-CN"><meta charset="utf-8">
        <style>
          body{{margin:0;background:#edf1f5;color:#132238;font-family:'Microsoft YaHei',sans-serif}}
          main{{height:100vh;display:grid;place-items:center}}
          section{{width:min(680px,82vw);background:white;border-left:5px solid #b42318;padding:32px}}
          h1{{font-size:22px;margin:0 0 14px}}p{{color:#526579;line-height:1.7;word-break:break-all}}
        </style><main><section><h1>应用启动失败</h1><p>{detail}</p>{log_hint}
        <p>请关闭程序后重试；如仍失败，请将日志交给开发人员。</p></section></main></html>
        """
        self.web_view.setHtml(html)

    def load_application(self) -> None:
        self.web_view.load(QUrl(self.base_url))

    @Slot(bool)
    def _on_load_finished(self, ok: bool) -> None:
        if not ok and self.web_view.url().scheme() in {"http", "https"}:
            self.show_startup_error("前端页面加载失败，本地服务可能尚未就绪。")
