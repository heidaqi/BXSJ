"""桌面端入口：启动本地 API，并在 QtWebEngine 中加载 React。"""
from __future__ import annotations

import logging
import multiprocessing
import os
import sys
import traceback
from pathlib import Path


def _setup_paths() -> None:
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))


def _setup_logging() -> Path:
    from backend.app.config import ensure_user_directories, settings

    ensure_user_directories(settings.user_data_dir)
    log_path = settings.user_data_dir / "logs" / "desktop.log"
    logging.basicConfig(filename=log_path, level=logging.INFO, encoding="utf-8", format="%(asctime)s %(levelname)s %(name)s %(message)s")

    def excepthook(exc_type, exc_value, exc_tb) -> None:
        logging.getLogger("desktop").critical("未捕获异常", exc_info=(exc_type, exc_value, exc_tb))
        traceback.print_exception(exc_type, exc_value, exc_tb)

    sys.excepthook = excepthook
    return log_path


def main() -> int:
    _setup_paths()
    os.environ.setdefault("YOLO_DESKTOP_MODE", "1")
    _setup_packaged_child_process_env()
    from backend.app.config import ensure_user_config, ensure_user_directories, settings

    ensure_user_config(settings.user_data_dir)
    ensure_user_directories(settings.user_data_dir)
    log_path = _setup_logging()

    from PySide6.QtCore import QThread, Signal
    from PySide6.QtWidgets import QApplication
    from desktop.app_window import AppWindow
    from desktop.server import LocalApiServer

    app = QApplication(sys.argv)
    app.setApplicationName("工业图像缺陷智能检测系统")
    app.setOrganizationName("PAUT Research")
    server = LocalApiServer()
    window = AppWindow(server.base_url)
    window.show()

    class StartupWorker(QThread):
        ready = Signal(bool, str)

        def run(self) -> None:  # noqa: D102
            try:
                server.start()
                self.ready.emit(*server.wait_until_ready())
            except Exception as exc:  # noqa: BLE001
                logging.exception("本地服务启动失败")
                self.ready.emit(False, str(exc))

    startup_worker = StartupWorker(window)

    def on_server_ready(ok: bool, error: str) -> None:
        if ok:
            window.load_application()
        else:
            window.show_startup_error(f"本地 API 启动失败：{error}", log_path)

    startup_worker.ready.connect(on_server_ready)
    startup_worker.start()

    def shutdown() -> None:
        if startup_worker.isRunning():
            startup_worker.wait(3000)
        server.stop()
        try:
            from backend.app.services.imaging_backend import close_imaging_backend
            close_imaging_backend()
        except Exception:  # noqa: BLE001
            logging.exception("关闭成像后端失败")

    app.aboutToQuit.connect(shutdown)
    return app.exec()


def _setup_packaged_child_process_env() -> None:
    """打包后先固定第三方库可写目录，避免后台子进程写到随机位置。"""
    if not getattr(sys, "frozen", False):
        return
    runtime_root = Path(sys.executable).resolve().parent
    matplotlib_config_dir = runtime_root / "data" / "matplotlib"
    matplotlib_config_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(matplotlib_config_dir))


def _package_self_test() -> int:
    """Validate resources that are loaded dynamically and can be missed by PyInstaller."""
    _setup_paths()
    try:
        import joblib

        from backend.app.config import settings
        from backend.app.services.ascan_classifier_service import _available_model_path

        model_path = _available_model_path()
        if model_path is None:
            return 2
        payload = joblib.load(model_path)
        pipeline = payload.get("pipeline") if isinstance(payload, dict) else payload
        paut_root = settings.resource_dir / "models" / "paut_v2"
        required = [
            paut_root / "crack_classifier.pt",
            paut_root / "crack_length_model.mat",
            paut_root / "refined_crack_model.mat",
            paut_root / "lof_length_model.mat",
            paut_root / "pore_v22_model.mat",
            settings.resource_dir / "algorithms" / "matlab" / "TOFD_complete_pipeline.m",
        ]
        return 0 if pipeline is not None and all(path.is_file() for path in required) else 3
    except Exception:
        return 1


def _run_paut_inference() -> int:
    """Run the bundled PAUT CNN when MATLAB launches this EXE as a child."""
    _setup_paths()
    _setup_packaged_child_process_env()
    try:
        from algorithms.python.paut_v2.predict_crack_batch import main as inference_main

        marker = sys.argv.index("--paut-inference")
        inference_main(sys.argv[marker + 1:])
        return 0
    except Exception:  # MATLAB captures stderr and includes it in the pipeline error.
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    multiprocessing.freeze_support()
    if "--package-self-test" in sys.argv:
        raise SystemExit(_package_self_test())
    if "--paut-inference" in sys.argv:
        raise SystemExit(_run_paut_inference())
    raise SystemExit(main())
