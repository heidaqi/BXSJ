from backend.app.services.priority_assessment_service import build_priority_assessment
from backend.app.services.report_service import create_realtime_batch_report


def _risk(level: str = "高") -> dict:
    return {
        "risk_level": level,
        "recommendation": "建议开展维修或工程完整性评估",
        "candidate_risks": [{
            "candidate_id": "candidate-1",
            "risk_level": level,
            "score": 7,
            "recommendation": "建议开展维修或工程完整性评估",
        }],
    }


def test_priority_uses_vehicle_scan_distance_without_exposing_candidate_id() -> None:
    result = build_priority_assessment(
        [{"candidate_id": "candidate-1", "frame_id": "frame-1", "class_name": "裂纹", "x_mm": 42, "z_mm": 18}],
        [{"frame_id": "frame-1", "scan_receipt": {
            "received_at": "2026-09-09T17:24:45+08:00",
            "vehicle_speed_mm_s": 10,
            "estimated_scan_distance_mm": 50,
        }}],
        _risk(),
    )
    item = result["immediate_attention"][0]
    assert "车辆扫查路径约50.00 mm处" in item["location"]
    assert "candidate-1" not in str(result)


def test_priority_falls_back_to_receipt_time_without_valid_vehicle_distance() -> None:
    result = build_priority_assessment(
        [{"candidate_id": "candidate-1", "frame_id": "frame-1", "class_name": "气孔", "x_mm": 37, "z_mm": 10}],
        [{"frame_id": "frame-1", "received_at": "2026-09-09 17:24:45", "scan_receipt": {
            "vehicle_speed_mm_s": 0, "estimated_scan_distance_mm": 0,
        }}],
        _risk("中"),
    )
    assert "扫查时间2026-09-09 17:24:45" in result["follow_up_attention"][0]["location"]


def test_model_can_only_add_number_free_operator_wording() -> None:
    candidates = [{"candidate_id": "candidate-1", "frame_id": "frame-1", "class_name": "裂纹"}]
    frames = [{"frame_id": "frame-1"}]
    accepted = build_priority_assessment(
        candidates, frames, _risk(),
        {"items": [{"candidate_id": "candidate-1", "operator_note": "建议优先核对原始波形并安排专业人员评估。"}]},
    )
    assert accepted["model_language_review_applied"] is True
    rejected = build_priority_assessment(
        candidates, frames, _risk(),
        {"items": [{"candidate_id": "candidate-1", "operator_note": "长度为20毫米，可以直接判废。"}]},
    )
    assert rejected["model_language_review_applied"] is False


def test_report_renders_operator_priority_section(tmp_path) -> None:
    received_at = "2026-09-09T17:24:45+08:00"
    frame = {
        "frame_id": "frame-1",
        "received_at": received_at,
        "scan_receipt": {
            "mode": "simulation",
            "sequence": 1,
            "received_at": received_at,
            "vehicle_speed_mm_s": 10,
            "estimated_scan_distance_mm": 50,
        },
        "sample_rate_hz": 50_000_000,
        "velocity_mps": 5900,
        "plate_thickness_mm": 40,
        "team_result": {
            "schema_version": "team-inspection-package/v1",
            "summary": {"pipeline": "team_paut_v2"},
            "quality": {"status": "可信", "blocking": False, "issues": []},
            "defects": [{
                "defect_id": "candidate-1",
                "class_name": "裂纹",
                "x_mm": 42,
                "z_mm": 18,
                "corrected_length_mm": 5.5,
                "snr_db": 25,
            }],
        },
        "review": {"status": "待复核"},
    }
    risk = _risk()
    risk.update({
        "confidence_level": "中",
        "review_requirement": "required",
        "conclusion": "发现需要优先处置的异常线索。",
        "reasons": ["裂纹类异常应优先开展工程评估。"],
    })
    batch = {
        "batch_id": "batch-1",
        "status": "分析完成",
        "result": {},
        "report_context": {"disposition": risk},
    }

    output = create_realtime_batch_report(batch, [frame], tmp_path / "priority-report.pdf")

    assert output.exists()
    assert output.read_bytes().startswith(b"%PDF")
