from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from backend.app.services import ascan_classifier_service as service


class FrameClassifierTests(unittest.TestCase):
    def test_classifier_runs_once_for_whole_frame(self) -> None:
        defects = [
            {"X_mm": 20.0, "Z_mm": 10.0, "Score": 0.9, "Support": 10},
            {"X_mm": 21.0, "Z_mm": 10.5, "Score": 0.8, "Support": 8},
            {"X_mm": 55.0, "Z_mm": 25.0, "Score": 0.5, "Support": 2},
        ]
        prediction = {
            "ok": True,
            "model_name": "test",
            "prediction": {
                "label": "pore", "label_cn": "气孔", "confidence": 0.82,
                "probabilities": {"pore": 0.82}, "recommended_review": False,
            },
            "localization": {"method": "provided_candidate", "x_mm": 20.5, "z_mm": 10.2},
            "features": {}, "explanation": {}, "warnings": [], "errors": [],
        }
        with patch.object(
            service,
            "load_fmc_from_source",
            return_value=(np.zeros((16, 16, 32)), np.arange(32) * 2e-8, np.arange(16) * 2.0),
        ), patch.object(
            service, "_available_model_path", return_value=Path("model.joblib")
        ), patch.object(
            service, "_predict_with_velocity_ensemble", return_value=prediction
        ) as predict:
            result = service.classify_frame("unused", defects, velocity_mps=5900.0)

        self.assertEqual(predict.call_count, 1)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["analysis_unit"], "single_fmc_frame")
        self.assertEqual(result["frame_prediction"]["label_cn"], "气孔")
        self.assertEqual(result["anchor"]["selection_method"], "dominant_ascan_cluster")
        self.assertTrue(all("ascan_classifier" not in defect for defect in defects))

    def test_no_candidate_uses_tfm_peak_fallback(self) -> None:
        x_mm, z_mm, metadata = service._select_frame_anchor([])
        self.assertIsNone(x_mm)
        self.assertIsNone(z_mm)
        self.assertEqual(metadata["selection_method"], "tfm_peak_fallback")


if __name__ == "__main__":
    unittest.main()
