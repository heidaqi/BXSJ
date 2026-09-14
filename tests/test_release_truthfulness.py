from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.app.paut.realtime import RealtimeService
from backend.app import realtime_api
from backend.app.services.risk_assessment_service import assess_batch_risk


def test_missing_raw_waveform_never_returns_synthetic_data(tmp_path: Path) -> None:
    service = RealtimeService()
    service._frames["frame-1"] = {"frame_id": "frame-1", "source_path": str(tmp_path / "missing")}

    with pytest.raises(ValueError, match="避免使用模拟数据"):
        service.get_waveform("frame-1", 0, 0, 1200)


def test_focus_requires_real_probe_positions() -> None:
    service = RealtimeService()
    service._frames["frame-1"] = {"frame_id": "frame-1"}

    with pytest.raises(ValueError, match="未提供阵元位置"):
        service.inspect_focus("frame-1", 40.0, 20.0, 4)


def test_team_result_endpoint_rejects_unvalidated_package(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        realtime_api.realtime_service,
        "get_frame",
        lambda _: {"frame_id": "frame-1", "team_result": {"defects": []}},
    )

    with pytest.raises(HTTPException) as exc_info:
        realtime_api.get_realtime_team_result("frame-1")
    assert exc_info.value.status_code == 404


def test_unvalidated_empty_result_cannot_claim_no_recheck() -> None:
    result = assess_batch_risk([], [{"frame_id": "frame-1", "team_result": {"defects": [], "quality": {"status": "可信"}}}])
    assert result["detection_state"] == "insufficient_quality"
    assert result["review_requirement"] == "required"
