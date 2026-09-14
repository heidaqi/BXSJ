from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from backend.app.services.agent_graph import PautAgentGraph
from backend.app.services import report_service


def _validated_frame(frame_id: str, defects: list[dict] | None = None) -> dict:
    package = {
        "schema_version": "team-inspection-package/2.2",
        "algorithm_version": "test",
        "result_stage": "validated",
        "authoritative": True,
        "provenance": {"provider": "team_algorithm", "result_stage": "validated"},
        "summary": {"pipeline": "team_paut_v2"},
        "quality": {"status": "通过", "warnings": []},
        "defects": defects or [],
    }
    return {
        "frame_id": frame_id, "source_group": frame_id, "received_at": "2026-08-19 10:00:00",
        "shape": [601, 16, 16], "sample_rate_hz": 50_000_000,
        "plate_thickness_mm": 40.0, "velocity_mps": 5900.0,
        "image": {"url": f"/outputs/{frame_id}.png", "x_range_mm": [0, 80], "z_range_mm": [0, 40]},
        "team_result": package, "analysis": {"team_result": package},
        "defects": [{"X_mm": 77, "Z_mm": 38, "Score": 0.99}],
    }


def _defect(defect_id: str, kind: str, x: float, z: float, **extra) -> dict:
    return {
        "defect_id": defect_id, "class_name": kind, "confidence": 0.88,
        "x_mm": x, "z_mm": z, "peak_x_mm": x + 0.2, "peak_z_mm": z + 0.1,
        "snr_db": 32.0, **extra,
    }


class AgentGraphTests(unittest.TestCase):
    def _run(self, frames: list[dict]) -> dict:
        with tempfile.TemporaryDirectory() as temp, patch(
            "backend.app.services.agent_graph.collect_local_knowledge", return_value=[]
        ), patch(
            "backend.app.services.agent_graph.analyze_realtime_batch",
            side_effect=lambda items, local, references=None: {
                "configured": False, "status": "未配置", "summary": local["summary"],
                "uncertainty": [], "guardrail_warnings": [],
            },
        ):
            return PautAgentGraph(Path(temp)).run("batch_test", frames)

    def test_uses_only_validated_values_and_keeps_frames_independent(self) -> None:
        frames = [
            _validated_frame("f1", [_defect("d1", "裂纹", 10, 12, corrected_length_mm=4.2)]),
            _validated_frame("f2", [_defect("d2", "气孔", 10.4, 12.2, diameter_mm=2.5, area_mm2=4.91)]),
        ]
        candidates = self._run(frames)["global_defect_candidates"]
        self.assertEqual(len(candidates), 2)
        self.assertEqual({item["frame_id"] for item in candidates}, {"f1", "f2"})
        self.assertTrue(all(item["source"] == "validated_team_result" for item in candidates))

    def test_old_yolo_and_pre_validation_candidates_are_ignored(self) -> None:
        frame = _validated_frame("f1", [])
        frame["analysis"]["yolo"] = {"detections": [{"class_name": "裂纹", "x_mm": 1, "z_mm": 2}]}
        frame["analysis"]["ascan"] = {"candidates": [{"X_mm": 3, "Z_mm": 4}]}
        result = self._run([frame])
        self.assertEqual(result["defect_instances"], [])
        self.assertEqual(result["global_defect_candidates"], [])

    def test_missing_quantification_is_not_filled_from_raw_length(self) -> None:
        frame = _validated_frame("f1", [_defect("d1", "裂纹", 30, 20, raw_length_mm=9.9)])
        candidate = self._run([frame])["global_defect_candidates"][0]
        self.assertIsNone(candidate.get("length_mm"))
        self.assertIsNone(candidate.get("corrected_length_mm"))

    def test_unvalidated_frame_cannot_create_agent_candidate(self) -> None:
        frame = _validated_frame("f1", [])
        frame.pop("team_result")
        frame["analysis"] = {"ascan": {"pre_validation_candidates": [{"X_mm": 20, "Z_mm": 10}]}}
        result = self._run([frame])
        self.assertEqual(result["global_defect_candidates"], [])
        self.assertTrue(any("校验后" in item for item in result["guardrail_warnings"]))

    def test_confirmed_review_overrides_validated_type_and_position(self) -> None:
        frame = _validated_frame("f1", [_defect("d1", "裂纹", 10, 12, corrected_length_mm=4.2)])
        frame["review"] = {"defects": [{
            "id": "d1", "class_name": "气孔", "center_x_mm": 11.5,
            "center_y_mm": 13.0, "diameter_mm": 2.0, "review_status": "已确认",
        }]}
        candidate = self._run([frame])["global_defect_candidates"][0]
        self.assertEqual(candidate["type"], "气孔")
        self.assertEqual(candidate["x_mm"], 11.5)
        self.assertEqual(candidate["diameter_mm"], 2.0)
        self.assertEqual(candidate["source"], "manual_review")

    def test_realtime_report_uses_selected_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            Image.new("RGB", (640, 360), "white").save(root / "frame.png")
            frame = _validated_frame("f1", [_defect("d1", "裂纹", 10, 12, corrected_length_mm=4.2)])
            frame["image"]["url"] = "/outputs/frame.png"
            destination = root / "用户选择" / "二维检测报告.pdf"
            batch = {"batch_id": "batch_save_test", "status": "分析完成", "result": {"agent": {}}}
            with patch.object(report_service, "settings", SimpleNamespace(output_dir=root)):
                report = report_service.create_realtime_batch_report(batch, [frame], destination)
            self.assertEqual(report, destination.resolve())
            self.assertTrue(destination.exists())


if __name__ == "__main__":
    unittest.main()
