from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.services.agent_graph import PautAgentGraph
from backend.app.services.risk_assessment_service import assess_batch_risk
from backend.app.services.simulation_case_service import find_similar_cases, load_simulation_cases
from scripts.build_simulation_case_library import parse_case_name, parse_lof_case


def _candidate(**overrides):
    value = {
        "candidate_id": "candidate_001",
        "frame_count": 1,
        "ascan_score_median": 0.4,
        "ascan_support_median": 1,
        "image_support_count": 0,
        "centroid_mm": {"x": 40.0, "z": 20.0},
        "type_votes": {},
    }
    value.update(overrides)
    return value


class RiskAssessmentTests(unittest.TestCase):
    def test_weak_single_frame_is_low(self) -> None:
        result = assess_batch_risk([_candidate()], [{"plate_thickness_mm": 40.0}])
        self.assertEqual(result["risk_level"], "低")
        self.assertIn("常规跟踪", result["recommendation"])
        self.assertEqual(result["recommended_action"], "routine_management")
        self.assertIn("不构成自动判废", result["decision_boundary"])
        self.assertEqual(result["scheme"], "PAUT缺陷辅助风险评价体系 v2.0")
        self.assertIn("severe_gate", result["rubric"])
        self.assertIn("scoring_items", result["candidate_risks"][0])

    def test_persistent_strong_large_crack_is_severe(self) -> None:
        result = assess_batch_risk([_candidate(
            primary_evidence="validated_team_result",
            ascan_score_median=None,
            ascan_support_median=None,
            corrected_length_mm=12.0,
            type="裂纹",
            snr_db=25.0,
            near_stressed_region=True,
            team_quality={"status": "可信", "blocking": False},
        )], [{"plate_thickness_mm": 40.0}] * 4)
        self.assertEqual(result["risk_level"], "严重")
        self.assertEqual(result["recommendation"], "建议暂停使用并由专业人员评估")
        self.assertEqual(result["recommended_action"], "suspend_and_assess")
        self.assertTrue(result["candidate_risks"][0]["severe_gate_passed"])
        self.assertTrue(any("疲劳扩展" in item for item in result["potential_risks"]))

    def test_zero_candidate_requires_reinspection_without_coverage(self) -> None:
        result = assess_batch_risk([], [{"plate_thickness_mm": 40.0}])
        self.assertEqual(result["risk_level"], "低")
        self.assertEqual(result["detection_state"], "insufficient_quality")
        self.assertEqual(result["review_requirement"], "required")
        self.assertIn("数据不足", result["recommendation"])

    def test_zero_candidate_needs_no_extra_recheck_when_quality_passes(self) -> None:
        frame = {
            "frame_id": "frame-1",
            "plate_thickness_mm": 40.0,
            "team_result": {
                "schema_version": "team-inspection-package/v1",
                "defects": [], "quality": {"status": "可信", "blocking": False},
            },
        }
        result = assess_batch_risk([], [frame])
        self.assertEqual(result["detection_state"], "no_recordable_indication")
        self.assertEqual(result["review_requirement"], "not_required")
        self.assertEqual(result["recommended_action"], "routine_management")
        self.assertIn("无需追加专项复检", result["conclusion"])

    def test_completed_review_does_not_request_another_review(self) -> None:
        candidate = _candidate(review_status="已确认", review={"status": "已确认"})
        frame = {
            "frame_id": "frame-1",
            "plate_thickness_mm": 40.0,
            "team_result": {
                "schema_version": "team-inspection-package/v1",
                "defects": [candidate], "quality": {"status": "可信", "blocking": False},
            },
        }
        result = assess_batch_risk([candidate], [frame])
        self.assertEqual(result["review_requirement"], "completed")
        self.assertNotIn("建议复检", result["recommendation"])
        self.assertIn("复核已完成", result["conclusion"])

    def test_missing_engineering_conditions_are_explicit(self) -> None:
        result = assess_batch_risk([_candidate()], [{"plate_thickness_mm": 40.0}])
        self.assertIn("载荷类型、应力水平及循环次数", result["missing_conditions"])
        self.assertIn("检测等级、验收等级和适用产品标准", result["missing_conditions"])

    def test_confirmed_stressed_region_adds_auditable_reason(self) -> None:
        result = assess_batch_risk([_candidate(
            near_stressed_region=True, critical_region_labels=["焊趾受力区"]
        )], [{"plate_thickness_mm": 40.0}])
        self.assertTrue(any("工程元数据确认" in item for item in result["reasons"]))

    def test_response_region_count_does_not_add_multi_defect_score(self) -> None:
        separate = assess_batch_risk(
            [_candidate(candidate_id="a", frame_id="frame_a"), _candidate(candidate_id="b", frame_id="frame_b")],
            [{"plate_thickness_mm": 40.0}],
        )
        self.assertTrue(all(
            all(item["item"] != "同帧多缺陷" for item in risk["scoring_items"])
            for risk in separate["candidate_risks"]
        ))
        same_frame = assess_batch_risk(
            [_candidate(candidate_id="a", frame_id="frame_a"), _candidate(candidate_id="b", frame_id="frame_a")],
            [{"plate_thickness_mm": 40.0}],
        )
        self.assertTrue(all(
            all(item["item"] != "同帧多缺陷" for item in risk["scoring_items"])
            for risk in same_frame["candidate_risks"]
        ))

    def test_agent_cannot_override_local_risk(self) -> None:
        frames = []
        for index in range(4):
            frames.append({
                "frame_id": f"frame_{index}", "shape": [601, 16, 16],
                "plate_thickness_mm": 40.0, "image": {"url": "/outputs/test.png"},
                "analysis": {"ascan": {"count": 1}},
                "team_result": {
                    "schema_version": "team-inspection-package/v1",
                    "summary": {"pipeline": "team_paut_v2"},
                    "quality": {"status": "可信", "blocking": False, "issues": []},
                    "defects": [{
                        "defect_id": f"def-{index}", "class_name": "裂纹",
                        "x_mm": 40.0 + index * 0.1, "z_mm": 20.0,
                        "corrected_length_mm": 12.0, "snr_db": 25.0,
                    }],
                },
            })
        with tempfile.TemporaryDirectory() as temp, patch(
            "backend.app.services.agent_graph.collect_local_knowledge", return_value=[]
        ), patch(
            "backend.app.services.agent_graph.find_similar_cases", return_value=[]
        ), patch(
            "backend.app.services.agent_graph.analyze_realtime_batch",
            return_value={"summary": "模型说明", "risk_level": "严重", "recommended_action": "暂停使用"},
        ):
            result = PautAgentGraph(Path(temp)).run("risk_guard", frames)
        self.assertEqual(result["risk_level"], "高")
        self.assertEqual(result["recommended_action"], "engineering_assessment")
        self.assertIn("工程完整性评估", result["recommended_action_text"])


class SimulationLibraryTests(unittest.TestCase):
    def test_parses_multiple_cracks_and_pores(self) -> None:
        cracks = parse_case_name("(0.03,0.028)-(0.048,0.018)(0.05,0.02)-(0.06,0.01)")
        pores = parse_case_name("d0.5mm(0.04,0.02)d2mm(0.05,0.03)")
        self.assertEqual(len(cracks["truth_geometry"]["segments_mm"]), 2)
        self.assertEqual(pores["truth_geometry"]["diameters_mm"], [0.5, 2.0])

    def test_parses_lack_of_fusion_subtypes(self) -> None:
        bevel = parse_lof_case("bevel_10mm")
        root = parse_lof_case("root_20mm")
        interlayer = parse_lof_case("interlayer_5mm")
        self.assertEqual(bevel["truth_geometry"]["centers_mm"], [[50.0, 20.0]])
        self.assertEqual(root["truth_geometry"]["lengths_mm"], [20.0])
        self.assertEqual(interlayer["defect_family"], "lack_of_fusion")

    def test_project_library_contains_all_four_defect_families(self) -> None:
        root = Path(__file__).resolve().parents[1]
        cases = load_simulation_cases(root / "knowledge" / "paut_simulation_cases.json")
        families = {case.get("defect_family") for case in cases}
        self.assertTrue({"crack", "pore", "lack_of_fusion", "inclusion"} <= families)
        self.assertEqual(len(cases), 13)

    def test_similar_case_is_explanatory_only(self) -> None:
        cases = [{
            "case_id": "case_crack", "defect_family": "crack",
            "truth_geometry": {"segments_mm": [[[30.0, 25.0], [45.0, 20.0]]], "lengths_mm": [15.8]},
            "acquisition": {"fmc_shape": [16, 16, 601], "plate_thickness_mm": 40.0},
            "validation": {"status": "success"}, "evidence_level": "仿真真值案例",
        }]
        candidate = _candidate(type_votes={"裂纹": 3}, section_length_estimate_mm=14.0)
        with patch("backend.app.services.simulation_case_service.load_simulation_cases", return_value=cases):
            result = find_similar_cases([candidate], [{"shape": [601, 16, 16], "plate_thickness_mm": 40.0}])
        self.assertEqual(result[0]["case_id"], "case_crack")
        self.assertIn("不能直接决定", result[0]["use_boundary"])

    def test_matches_lack_of_fusion_and_inclusion_separately(self) -> None:
        cases = [
            {"case_id": "lof", "defect_family": "lack_of_fusion", "truth_geometry": {"lengths_mm": [10]}, "acquisition": {}, "validation": {}},
            {"case_id": "slag", "defect_family": "inclusion", "truth_geometry": {"lengths_mm": [3]}, "acquisition": {}, "validation": {}},
        ]
        with patch("backend.app.services.simulation_case_service.load_simulation_cases", return_value=cases):
            lof = find_similar_cases([_candidate(type_votes={"未熔合": 2})], [])
            slag = find_similar_cases([_candidate(type_votes={"夹渣": 2})], [])
        self.assertEqual(lof[0]["case_id"], "lof")
        self.assertEqual(slag[0]["case_id"], "slag")


if __name__ == "__main__":
    unittest.main()
