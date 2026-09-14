from backend.app.services.validated_result_service import canonical_frame_candidates


def _frame() -> dict:
    return {
        "frame_id": "frame-1",
        "source_group": "group-1",
        "team_result": {
            "schema_version": "team-inspection-package/v1",
            "summary": {"pipeline": "team_paut_v2"},
            "quality": {"status": "可信", "blocking": False},
            "defects": [{
                "defect_id": "def-1",
                "class_name": "裂纹",
                "x_mm": 42.7,
                "z_mm": 28.44,
                "peak_x_mm": 43.1,
                "peak_z_mm": 28.8,
                "raw_length_mm": 9.9,
                "corrected_length_mm": 5.99,
                "image_angle_deg": 24.19,
            }],
        },
        "defects": [{"class_name": "气孔", "X_mm": 1.0, "Z_mm": 2.0}],
        "analysis": {"yolo": {"detections": [{"class_name": "夹渣", "x_mm": 3.0, "z_mm": 4.0}]}},
        "review": {"status": "待复核", "defects": []},
    }


def test_uses_only_validated_values_and_keeps_center_distinct_from_peak() -> None:
    candidate = canonical_frame_candidates(_frame())[0]
    assert candidate["type"] == "裂纹"
    assert candidate["x_mm"] == 42.7
    assert candidate["z_mm"] == 28.44
    assert candidate["peak_x_mm"] == 43.1
    assert candidate["peak_z_mm"] == 28.8
    assert candidate["length_mm"] == 5.99
    assert "raw_length_mm" not in candidate


def test_missing_corrected_length_is_not_filled_from_raw_length() -> None:
    frame = _frame()
    frame["team_result"]["defects"][0]["corrected_length_mm"] = None
    candidate = canonical_frame_candidates(frame)[0]
    assert candidate["length_mm"] is None
    assert candidate["geometry"]["length_mm"] is None


def test_confirmed_review_overrides_matching_result_but_pending_copy_does_not() -> None:
    frame = _frame()
    frame["review"] = {"status": "已确认", "defects": [{
        "id": "def-1", "review_status": "已确认", "class_name": "未熔合",
        "center_x_mm": 44.0, "center_y_mm": 27.0, "width_mm": 6.5,
    }]}
    candidate = canonical_frame_candidates(frame)[0]
    assert candidate["type"] == "未熔合"
    assert candidate["x_mm"] == 44.0
    assert candidate["length_mm"] == 6.5
    assert candidate["source"] == "manual_review"


def test_unvalidated_frame_does_not_emit_candidates() -> None:
    frame = _frame()
    frame.pop("team_result")
    assert canonical_frame_candidates(frame) == []
