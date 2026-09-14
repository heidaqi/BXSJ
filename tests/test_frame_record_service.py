from backend.app.services.frame_record_service import FrameRecordService


def test_frame_review_stays_pending_until_every_defect_is_reviewed() -> None:
    review = FrameRecordService._review_from_defects([
        {"id": "confirmed", "class_name": "气孔", "review_status": "已确认"},
        {"id": "pending", "class_name": "裂纹", "review_status": "待复核"},
    ])

    assert review["status"] == "待复核"
    assert review["confirmed_count"] == 1
    assert review["reviewed_defect_count"] == 1


def test_frame_review_is_complete_when_no_defect_is_pending() -> None:
    review = FrameRecordService._review_from_defects([
        {"id": "confirmed", "class_name": "气孔", "review_status": "已确认"},
        {"id": "false-positive", "class_name": "伪影", "review_status": "误检"},
    ])

    assert review["status"] == "已确认"
    assert review["reviewed_defect_count"] == 2


def test_frame_sampling_uses_archived_metadata_when_frame_field_is_missing() -> None:
    service = FrameRecordService()
    context = service._sampling_context(
        {"frame_id": "frame-1"},
        {"source_metadata_json": '{"sample_rate_hz": 50000000}'},
    )

    assert context["sample_rate_hz"] == 50_000_000
    assert context["source"] == "frame_or_archive"
