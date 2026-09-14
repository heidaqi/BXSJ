from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch

from backend.app.services.analysis_service import _find_data_groups
from backend.app.services.imaging_backend import MatlabRuntimeBackend
from backend.app import app_api
from desktop.bridge import DesktopBridge
from desktop.server import LocalApiServer, find_available_port


class DesktopMigrationTests(unittest.TestCase):
    def test_available_port_is_valid(self) -> None:
        port = find_available_port()
        self.assertGreater(port, 0)
        self.assertLessEqual(port, 65535)

    def test_server_uses_requested_port(self) -> None:
        server = LocalApiServer(port=38127)
        self.assertEqual(server.base_url, "http://127.0.0.1:38127")

    def test_server_disables_console_log_configuration(self) -> None:
        server = LocalApiServer(port=38127)
        with patch("uvicorn.Config") as config, patch("uvicorn.Server"), patch(
            "desktop.server.threading.Thread"
        ):
            server.start()
        self.assertIsNone(config.call_args.kwargs["log_config"])

    def test_report_save_dialog_returns_user_selected_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            selected = Path(temp) / "我的检测报告.pdf"
            bridge = DesktopBridge()
            with patch("desktop.bridge.QFileDialog.getSaveFileName", return_value=(str(selected), "PDF")):
                result = bridge.selectReportSavePath("批次:01")
            self.assertEqual(Path(result), selected.resolve())

    def test_numeric_and_tx_folders_are_discovered(self) -> None:
        with tempfile.TemporaryDirectory(prefix="中文数据_") as temp:
            root = Path(temp)
            tx = root / "tx_1"
            tx.mkdir()
            for index in range(16):
                (tx / f"{index + 1}.txt").write_text("0 0\n", encoding="ascii")
            self.assertEqual(_find_data_groups(root), [tx])

    def test_runtime_backend_reports_missing_component(self) -> None:
        backend = MatlabRuntimeBackend("missing_compiled_imaging_component")
        self.assertFalse(backend.is_available())
        self.assertFalse(backend.get_diagnostics()["available"])

    def test_runtime_backend_calls_compiled_contract(self) -> None:
        class RuntimeInstance:
            def run_imaging(self, source, output, params):
                return {"source": source, "output": output, "params": params}

            def run_ascan(self, source, output, params):
                return {"ascan": source}

            def terminate(self):
                return None

        class RuntimeModule:
            @staticmethod
            def initialize():
                return RuntimeInstance()

        backend = MatlabRuntimeBackend("compiled_component")
        with patch("importlib.import_module", return_value=RuntimeModule):
            self.assertTrue(backend.is_available())
            with tempfile.TemporaryDirectory() as temp:
                result = backend.run_imaging(Path(temp), Path(temp) / "out", {"x": 1})
            self.assertTrue(result["ok"])
            backend.close()

    def test_settings_do_not_write_api_key_to_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config_path = Path(temp) / "config.json"
            secrets_path = Path(temp) / "secrets.env"
            config_path.write_text("{}", encoding="utf-8")
            payload = app_api.AppSettingsUpdate(
                agent_enabled=True,
                openai_base_url="https://example.invalid/v1",
                openai_model="test-model",
                openai_api_key="secret-value",
            )
            with patch.object(app_api, "_config_path", return_value=config_path), patch.object(
                app_api, "_local_env_path", return_value=secrets_path
            ), patch.object(app_api, "reload_settings"):
                result = app_api.save_settings(payload)
            saved = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertFalse(result["restart_required"])
            self.assertNotIn("OPENAI_API_KEY", saved)
            self.assertIn('OPENAI_API_KEY="secret-value"', secrets_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
