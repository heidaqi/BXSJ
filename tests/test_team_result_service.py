from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.services.agent_graph import PautAgentGraph
from backend.app.services.paut_v2_adapter import EXPECTED_COLUMNS, normalize_row
from backend.app.services.team_result_service import build_team_result_package
from backend.app.services.validated_result_service import canonical_frame_candidates


def _row(**overrides):
    row = {name: "" for name in EXPECTED_COLUMNS}
    row.update({
        "DefectID": "1",
        "Type": "Crack",
        "ClassificationMethod": "CNN",
        "CrackProbability": "0.91",
        "CenterX_mm": "42.5",
        "CenterZ_mm": "18.0",
        "RawLength_mm": "8.2",
        "CorrectedLength_mm": "8.0",
        "Area_mm2": "3.2",
        "ImageAngle_deg": "25",
        "SNR_dB": "22",
        "QuantificationStatus": "完成",
    })
    row.update(overrides)
    return row


def _write_team_outputs(root: Path, rows: list[dict]) -> None:
    with (root / "defect_report.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=sorted(EXPECTED_COLUMNS))
        writer.writeheader()
        writer.writerows(rows)
    (root / "das_image_thresholded.png").write_bytes(b"png")
    (root / "das_image_full.png").write_bytes(b"png")
    (root / "analysis_result.mat").write_bytes(b"mat")


class TeamResultServiceTests(unittest.TestCase):
    def test_promoted_slag_quantities_survive_adapter_and_canonical_result(self) -> None:
        defect = normalize_row(_row(
            Type="Slag",
            ClassificationMethod="High-confidence LOF/Slag classifier",
            SlagProbability="0.94",
            CorrectedLength_mm="10.8",
            MinorAxisNominal_mm="1.9",
            MinorAxisLower_mm="1.5",
            MinorAxisUpper_mm="2.3",
            Area_mm2="16.12",
            AreaLower_mm2="12.72",
            AreaUpper_mm2="19.51",
        ))
        defect.update({"defect_id": "frame-1:team:1", "frame_id": "frame-1"})
        package = {
            "schema_version": "team-inspection-package/v1",
            "summary": {"pipeline": "team_paut_v2"},
            "defects": [defect],
            "quality": {"status": "可信", "issues": [], "blocking": False},
        }
        candidate = canonical_frame_candidates({"frame_id": "frame-1", "team_result": package})[0]
        self.assertEqual(candidate["class_name"], "夹渣")
        self.assertEqual(candidate["confidence_basis"], "slag_probability")
        self.assertEqual(candidate["corrected_length_mm"], 10.8)
        self.assertEqual(candidate["minor_axis_nominal_mm"], 1.9)
        self.assertEqual(candidate["minor_axis_lower_mm"], 1.5)
        self.assertEqual(candidate["minor_axis_upper_mm"], 2.3)
        self.assertEqual(candidate["area_mm2"], 16.12)
        self.assertEqual(candidate["slag_probability"], 0.94)

    def test_non_crack_does_not_infer_confidence_from_crack_probability(self) -> None:
        defect = normalize_row(_row(Type="Pore", CrackProbability="0.10"))
        self.assertEqual(defect["class_name"], "气孔")
        self.assertIsNone(defect["confidence"])
        self.assertFalse(defect["confidence_available"])

    def test_package_keeps_stable_ids_and_source_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write_team_outputs(root, [_row()])
            package = build_team_result_package(
                root,
                batch_id="batch-1",
                frame_id="frame-1",
                x_range_mm=[20, 80],
                z_range_mm=[0, 40],
                source_path=str(root),
            )
        self.assertIsNotNone(package)
        self.assertEqual(package["schema_version"], "team-inspection-package/v1")
        self.assertEqual(package["defects"][0]["defect_id"], "frame-1:team:1")
        self.assertEqual(package["defects"][0]["raw_team_values"]["CorrectedLength_mm"], "8.0")
        self.assertEqual(package["quality"]["status"], "可信")

    def test_quality_gate_blocks_out_of_range_and_negative_measurement(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write_team_outputs(root, [_row(CenterZ_mm="45", CorrectedLength_mm="-2")])
            package = build_team_result_package(
                root,
                batch_id="batch-1",
                frame_id="frame-1",
                x_range_mm=[20, 80],
                z_range_mm=[0, 40],
            )
        self.assertEqual(package["quality"]["status"], "结果冲突")
        self.assertTrue(package["quality"]["blocking"])
        codes = {item["code"] for item in package["quality"]["issues"]}
        self.assertIn("z_out_of_range", codes)
        self.assertIn("negative_measurement", codes)

    def test_empty_report_is_valid_when_required_assets_are_complete(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write_team_outputs(root, [])
            package = build_team_result_package(root, batch_id="batch-1", frame_id="frame-1")
        self.assertEqual(package["quality"]["status"], "可信")
        self.assertFalse(package["quality"]["issues"])
        self.assertIn("未形成需记录", package["quality"]["notices"][0])

    def test_agent_uses_team_result_and_review_override(self) -> None:
        defect = normalize_row(_row())
        defect.update({"defect_id": "frame-1:team:1", "source_defect_id": "1", "frame_id": "frame-1"})
        package = {
            "schema_version": "team-inspection-package/v1",
            "algorithm_version": "team-paut-v2",
            "defects": [defect],
            "summary": {"count": 1, "summary": "裂纹1处"},
            "quality": {"status": "可信", "issues": [], "blocking": False},
        }
        frame = {
            "frame_id": "frame-1",
            "source_group": "group-1",
            "plate_thickness_mm": 40.0,
            "image": {"url": "/outputs/frame.png", "x_range_mm": [20, 80], "z_range_mm": [0, 40]},
            "analysis": {"team_result": package},
            "team_result": package,
            "defects": [defect],
            "review": {"defects": [{
                "id": "frame-1:team:1", "review_status": "已确认", "class_name": "未熔合",
                "center_x_mm": 43.0, "center_y_mm": 19.0, "width_mm": 7.5,
            }]},
        }
        with tempfile.TemporaryDirectory() as temp:
            result = PautAgentGraph(checkpoint_root=Path(temp)).run("batch-1", [frame])
        candidate = result["defect_instances"][0]
        self.assertEqual(result["agent_schema_version"], "paut-agent-v6-validated-results")
        self.assertEqual(candidate["type"], "未熔合")
        self.assertEqual(candidate["centroid_mm"], {"x": 43.0, "z": 19.0})
        self.assertEqual(candidate["length_mm"], 7.5)
        self.assertTrue(candidate["review_override_applied"])

    def test_team_type_remains_consistent_when_llm_fails(self) -> None:
        defect = normalize_row(_row(Type="Pore", CrackProbability="0.01", Diameter_mm="2.5"))
        defect.update({"defect_id": "frame-1:team:1", "source_defect_id": "1", "frame_id": "frame-1"})
        package = {
            "schema_version": "team-inspection-package/v1",
            "algorithm_version": "team-paut-v2",
            "defects": [defect],
            "summary": {"count": 1, "counts": {"气孔": 1}, "warning_count": 1},
            "quality": {"status": "建议复核", "issues": [{"code": "measurement_boundary"}], "blocking": False},
        }
        frame = {
            "frame_id": "frame-1",
            "index": 0,
            "source_group": "group-1",
            "plate_thickness_mm": 40.0,
            "image": {"url": "/outputs/frame.png", "x_range_mm": [20, 80], "z_range_mm": [0, 40]},
            "analysis": {"team_result": package},
            "team_result": package,
            "defects": [defect],
        }
        with tempfile.TemporaryDirectory() as temp, patch(
            "backend.app.services.agent_graph.analyze_realtime_batch",
            side_effect=RuntimeError("402 Payment Required"),
        ):
            result = PautAgentGraph(checkpoint_root=Path(temp)).run("batch-1", [frame])

        self.assertEqual(result["status"], "大模型调用失败，本地摘要可用")
        self.assertEqual(result["frame_analyses"][0]["frame_type"], "气孔")
        self.assertEqual(result["frame_analyses"][0]["frame_type_confidence"], "检测结果")
        self.assertTrue(all("API" not in item and "Key" not in item for item in result["review_suggestions"]))


if __name__ == "__main__":
    unittest.main()
