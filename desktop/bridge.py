"""React 前端可调用的少量原生桌面能力。"""
from __future__ import annotations

import os
import re
from pathlib import Path

from PySide6.QtCore import QObject, Slot
from PySide6.QtWidgets import QFileDialog

from backend.app.config import settings


class DesktopBridge(QObject):
    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._approved_paths: set[Path] = set()

    @Slot(result=str)
    def selectFolder(self) -> str:  # noqa: N802
        return QFileDialog.getExistingDirectory(None, "选择 PAUT 数据文件夹", str(Path.home())) or ""

    @Slot(result=str)
    def selectFmcFile(self) -> str:  # noqa: N802
        path, _ = QFileDialog.getOpenFileName(
            None,
            "选择单个 FMC 数据文件",
            str(Path.home()),
            "FMC 文本文件 (*.txt);;所有文件 (*.*)",
        )
        return path or ""

    @Slot(str, result=str)
    def selectReportSavePath(self, suggested_name: str) -> str:  # noqa: N802
        return self._select_save_path(suggested_name, ".pdf", "保存检测报告", "PDF 报告 (*.pdf)")

    @Slot(str, result=str)
    def selectCsvSavePath(self, suggested_name: str) -> str:  # noqa: N802
        return self._select_save_path(suggested_name, ".csv", "导出缺陷明细", "CSV 表格 (*.csv)")

    @Slot(str, result=str)
    def selectArchiveSavePath(self, suggested_name: str) -> str:  # noqa: N802
        return self._select_save_path(suggested_name, ".zip", "保存检测归档", "ZIP 归档 (*.zip)")

    def _select_save_path(self, suggested_name: str, suffix: str, title: str, file_filter: str) -> str:
        safe_name = re.sub(r'[<>:"/\\|?*]+', "_", suggested_name).strip(" .") or f"PAUT检测{suffix}"
        if not safe_name.lower().endswith(suffix):
            safe_name += suffix
        initial = Path.home() / "Documents" / safe_name
        path, _ = QFileDialog.getSaveFileName(None, title, str(initial), file_filter)
        if not path:
            return ""
        selected = Path(path).expanduser().resolve()
        if selected.suffix.lower() != suffix:
            selected = selected.with_suffix(suffix)
        self._approved_paths.add(selected)
        return str(selected)

    @Slot(str, result=bool)
    def openPath(self, raw_path: str) -> bool:  # noqa: N802
        try:
            path = Path(raw_path).expanduser().resolve()
            allowed = [settings.user_data_dir.resolve(), settings.output_dir.resolve()]
            if path not in self._approved_paths and not any(path == root or root in path.parents for root in allowed):
                return False
            if path.exists():
                os.startfile(str(path))  # type: ignore[attr-defined]
                return True
        except (OSError, ValueError):
            return False
        return False

    @Slot(result=bool)
    def openOutputDirectory(self) -> bool:  # noqa: N802
        try:
            settings.output_dir.mkdir(parents=True, exist_ok=True)
            os.startfile(str(settings.output_dir))  # type: ignore[attr-defined]
            return True
        except OSError:
            return False

    @Slot(result=str)
    def applicationInfo(self) -> str:  # noqa: N802
        return '{"desktop":true,"version":"0.2.0"}'
