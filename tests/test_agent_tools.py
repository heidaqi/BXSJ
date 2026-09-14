from pathlib import Path
from unittest.mock import patch

import pytest

from backend.app.services.agent_graph import PautAgentGraph
from backend.app.services.agent_tools import prepare_report_content, render_report


def _trusted_empty_frame(frame_id: str = "frame-1") -> dict:
    return {
        "frame_id": frame_id,
        "shape": [601, 16, 16],
        "plate_thickness_mm": 40.0,
        "image": {"url": "/outputs/frame.png", "peak_x_mm": 40.0, "peak_z_mm": 20.0},
        "analysis": {"ascan": {"count": 0}, "yolo": {"count": 0, "detections": []}},
        "team_result": {
            "schema_version": "team-inspection-package/v1",
            "defects": [],
            "summary": {"pipeline": "team_paut_v2", "count": 0},
            "quality": {"status": "可信", "blocking": False, "issues": []},
        },
        "review": {"status": "无需复核"},
    }


def test_report_content_marks_trusted_empty_batch_as_no_review() -> None:
    result = prepare_report_content.invoke({
        "batch": {"batch_id": "batch-1"},
        "frames": [_trusted_empty_frame()],
        "agent_result": {},
    })
    disposition = result["disposition"]
    assert result["report_ready"] is True
    assert disposition["detection_state"] == "no_recordable_indication"
    assert disposition["review_requirement"] == "not_required"
    assert disposition["recommended_action"] == "routine_management"


def test_final_analysis_preserves_current_receipt_by_frame_id():
    frame = _trusted_empty_frame()
    frame['received_at'] = '2026-09-07T10:50:01+00:00'
    frame['scan_receipt'] = {'received_at': frame['received_at'], 'files': ['private-input']}
    with patch('backend.app.services.agent_tools.prepare_report_content') as report_tool:
        report_tool.invoke.return_value = {}
        result = PautAgentGraph._audit_report_evidence({
            'frames': [frame], 'output': {'frame_analyses': [
                {'frame_id': frame['frame_id'], 'received_at': 'stale'}
            ]}
        })['output']['frame_analyses'][0]
    assert result['received_at'] == frame['received_at']
    assert 'files' not in result['scan_receipt']


def test_consistency_guard_rejects_llm_recheck_for_trusted_empty_batch(tmp_path: Path) -> None:
    with patch("backend.app.services.agent_graph.collect_local_knowledge", return_value=[]), patch(
        "backend.app.services.agent_graph.find_similar_cases", return_value=[]
    ), patch(
        "backend.app.services.agent_graph.analyze_realtime_batch",
        return_value={"configured": True, "summary": "建议立即复检", "review_suggestions": ["复检"]},
    ):
        result = PautAgentGraph(tmp_path).run("batch-empty", [_trusted_empty_frame()])
    assert result["review_requirement"] == "not_required"
    assert result["recommended_action"] == "routine_management"
    assert "无需追加专项复检" in result["summary"]
    assert result["review_suggestions"] == ["按既定检验周期管理并保留本次检测记录。"]


def test_report_renderer_blocks_duplicate_frame_identity() -> None:
    frames = [_trusted_empty_frame("same"), _trusted_empty_frame("same")]
    context = prepare_report_content.invoke({
        "batch": {"batch_id": "batch-duplicate"},
        "frames": frames,
        "agent_result": {},
    })
    assert context["report_ready"] is False
    with pytest.raises(ValueError, match="frame_id重复"):
        render_report.invoke({
            "batch": {"batch_id": "batch-duplicate"},
            "frames": frames,
            "report_context": context,
            "destination_path": "",
        })


def test_report_renderer_blocks_missing_or_blocking_result_package() -> None:
    frame = _trusted_empty_frame()
    frame["team_result"]["quality"] = {
        "status": "结果冲突",
        "blocking": True,
        "issues": [{"message": "坐标超出成像范围"}],
    }
    context = prepare_report_content.invoke({
        "batch": {"batch_id": "batch-blocked"},
        "frames": [frame],
        "agent_result": {},
    })
    assert context["report_ready"] is False
    assert context["evidence_gate"]["minimum_quality_passed"] is False


def test_report_content_uses_current_review_instead_of_stale_agent_candidates() -> None:
    frame = _trusted_empty_frame()
    frame["team_result"]["defects"] = [{
        "defect_id": "def-1", "class_name": "气孔", "x_mm": 40.0, "z_mm": 12.0,
    }]
    frame["review"] = {
        "status": "误检",
        "defects": [{
            "id": "def-1", "class_name": "气孔", "center_x_mm": 40.0,
            "center_y_mm": 12.0, "review_status": "误检",
        }],
    }
    context = prepare_report_content.invoke({
        "batch": {"batch_id": "batch-reviewed"},
        "frames": [frame],
        "agent_result": {"fusion_candidates": [{"candidate_id": "stale"}]},
    })
    assert context["candidate_count"] == 0
    assert context["disposition"]["detection_state"] == "no_recordable_indication"


def test_report_content_never_falls_back_to_old_agent_or_image_candidates() -> None:
    frame = _trusted_empty_frame()
    frame["defects"] = [{"class_name": "裂纹", "X_mm": 99.0, "Z_mm": 99.0}]
    frame["analysis"]["yolo"] = {
        "count": 1,
        "detections": [{"class_name": "气孔", "x_mm": 12.0, "z_mm": 13.0}],
    }
    context = prepare_report_content.invoke({
        "batch": {"batch_id": "batch-no-fallback"},
        "frames": [frame],
        "agent_result": {"fusion_candidates": [{"candidate_id": "invented"}]},
    })
    assert context["candidate_count"] == 0
    assert context["disposition"]["detection_state"] == "no_recordable_indication"
