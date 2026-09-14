from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from backend.app.paut.realtime import RealtimeConfig
from backend.app.services.matlab_service import MatlabRuntimeError, MatlabService
from backend.app.services.paut_v2_adapter import output_files, read_defect_report, summarize
from backend.app.services.tofd_service import find_tofd_root


class TurnMigrationTests(unittest.TestCase):
    def test_matlab_engine_call_honors_timeout_and_invalidates_session(self) -> None:
        service = MatlabService(use_engine=True)
        future = Mock()
        future.result.side_effect = TimeoutError("timeout")
        engine = Mock()
        engine.feval.return_value = future
        service._eng = engine
        service._engine_ready = True
        with self.assertRaises(MatlabRuntimeError):
            service._run_via_engine(
                engine,
                "Paut.m",
                Path("Paut.m"),
                {},
                Path("input"),
                Path("output"),
                {},
                1,
            )
        future.cancel.assert_called_once()
        engine.quit.assert_not_called()
        self.assertIsNone(service._eng)
        self.assertIsNone(service._engine_ready)
        self.assertIsNone(service._eng)
        self.assertIsNone(service._engine_ready)

    def test_paut_v2_report_is_normalized_for_existing_consumers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "defect_report.csv").write_text(
                "DefectID,Type,ClassificationMethod,CrackProbability,CNNThreshold,CenterX_mm,CenterZ_mm,PeakX_mm,PeakZ_mm,GlobalPeak_dB,RawLength_mm,CorrectedLength_mm,Diameter_mm,Area_mm2,ImageAngle_deg,AspectRatio,SNR_dB,QuantificationStatus,Warning\n"
                "1,Crack,CNN,0.997,0.98,42.1,18.2,42.0,18.0,-1.2,5.1,5.4,NaN,NaN,72,3.1,35.2,Crack length calibrated,\n"
                "2,Pore,shape rule,0.02,0.98,50,20,50,20,-2,NaN,NaN,1.2,1.13,0,1.1,28,Pore diameter/area calibrated,low SNR\n",
                encoding="utf-8",
            )
            defects = read_defect_report(root)
            self.assertIsNotNone(defects)
            self.assertEqual(defects[0]["class_name"], "裂纹")
            self.assertEqual(defects[0]["corrected_length_mm"], 5.4)
            self.assertEqual(defects[0]["X_mm"], 42.1)
            self.assertEqual(defects[1]["class_name"], "气孔")
            self.assertEqual(defects[1]["area_mm2"], 1.13)
            self.assertEqual(summarize(defects)["warning_count"], 1)

    def test_thresholded_image_is_the_explicit_default_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "das_image_thresholded.png").touch()
            (root / "das_image_full.png").touch()
            files = output_files(root)
            self.assertEqual(files["thresholded_image"].name, "das_image_thresholded.png")
            self.assertEqual(files["original_image"].name, "das_image_full.png")

    def test_paut_realtime_never_enables_tofd_by_default(self) -> None:
        config = RealtimeConfig(source_path="C:/data")
        self.assertEqual(config.data_type, "paut")
        self.assertFalse(config.tofd_enabled)

    def test_tofd_requires_actual_save_param_and_binary_layout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scan = root / "Save_test_1"
            scan.mkdir()
            self.assertIsNone(find_tofd_root(root))
            (scan / "Param.mat").touch()
            (scan / "sample.bin").touch()
            self.assertEqual(find_tofd_root(root), root)

    def test_run_paut_injects_bundled_runtime_resources(self) -> None:
        service = MatlabService(use_engine=False)
        service.run_script = Mock(return_value={"ok": True})
        service.run_paut(Path("input"), Path("output"), params={"pixel_step_mm": 0.2})
        kwargs = service.run_script.call_args.kwargs
        self.assertEqual(kwargs["script_name"], "Paut.m")
        self.assertEqual(kwargs["params"]["pixel_step_mm"], 0.2)
        self.assertTrue(kwargs["params"]["crack_cnn_model"].endswith("crack_classifier.pt"))
        self.assertIn(kwargs["params"]["inference_mode"], {"script", "frozen"})


if __name__ == "__main__":
    unittest.main()
