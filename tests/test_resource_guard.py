from __future__ import annotations

import sys

import pytest

from backend.app.paut.realtime import RealtimeService
from backend.app.services import detection_service, resource_guard


def test_dependency_probe_does_not_import_torch() -> None:
    sys.modules.pop("torch", None)
    assert isinstance(detection_service._module_available("torch"), bool)
    assert "torch" not in sys.modules


def test_memory_guard_rejects_low_headroom(monkeypatch: pytest.MonkeyPatch) -> None:
    gib = 1024**3
    monkeypatch.setattr(
        resource_guard,
        "memory_snapshot",
        lambda: resource_guard.MemorySnapshot(16 * gib, 1 * gib, 32 * gib, 2 * gib),
    )
    with pytest.raises(RuntimeError, match="可用内存不足"):
        resource_guard.require_processing_headroom()


def test_compact_frame_removes_duplicate_team_payload_and_bounds_candidates() -> None:
    candidates = [{"id": index} for index in range(150)]
    compact = RealtimeService.compact_frame({
        "frame_id": "frame-1",
        "team_result": {"large": True},
        "analysis": {"team_result": {"large": True}, "ascan": {"candidates": candidates}},
        "defects": candidates,
    })
    assert "team_result" not in compact
    assert "team_result" not in compact["analysis"]
    assert len(compact["analysis"]["ascan"]["candidates"]) == 100
    assert compact["analysis"]["ascan"]["truncated"] is True
    assert len(compact["defects"]) == 100


def test_process_loop_stops_before_next_case_when_headroom_low(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    """逐案例内存门槛：余量不足时在调用 MATLAB 前停采，而不是让整批毒化继续。"""
    import backend.app.paut.realtime as realtime_mod

    svc = realtime_mod.RealtimeService()
    group = tmp_path / "case_a"
    group.mkdir()
    svc._config = realtime_mod.RealtimeConfig(source_path=tmp_path, interval_seconds=0.0)
    svc._session_id = "session_lowmem"
    svc._running = True
    svc._stop_event.clear()
    svc._write_session_manifest = lambda *args, **kwargs: None  # type: ignore[method-assign]
    svc._finalize_session = lambda: None  # type: ignore[method-assign]
    svc._find_txt_data_dirs = lambda source_path: [group]  # type: ignore[method-assign]

    def should_not_be_called(*args, **kwargs):
        raise AssertionError("低余量时应停采，不得发起 MATLAB 调用")

    svc._process_single_group = should_not_be_called  # type: ignore[method-assign]
    monkeypatch.setattr(
        realtime_mod,
        "require_processing_headroom",
        lambda: (_ for _ in ()).throw(RuntimeError("可用内存不足")),
    )

    svc._process_loop()

    assert svc._status["stop_reason"] == "内存不足"
    assert svc._status["state"] == "资源不足，已停止"
    assert svc._processed_groups == []
    assert svc._failed_groups == []
    assert svc._last_processed_groups == set()


def test_process_loop_records_single_group_failure_and_continues_to_next(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    """单组失败不拖垮整批：失败记入 failed_groups 后继续下一组。"""
    import backend.app.paut.realtime as realtime_mod

    svc = realtime_mod.RealtimeService()
    group_ok = tmp_path / "case_ok"
    group_ok.mkdir()
    group_bad = tmp_path / "case_bad"
    group_bad.mkdir()
    svc._config = realtime_mod.RealtimeConfig(source_path=tmp_path, interval_seconds=0.0)
    svc._session_id = "session_continue"
    svc._running = True
    svc._stop_event.clear()
    svc._write_session_manifest = lambda *args, **kwargs: None  # type: ignore[method-assign]
    svc._persist_frame = lambda frame: None  # type: ignore[method-assign]
    svc._finalize_session = lambda: None  # type: ignore[method-assign]
    svc._find_txt_data_dirs = lambda source_path: [group_bad, group_ok]  # type: ignore[method-assign]
    monkeypatch.setattr(
        realtime_mod,
        "require_processing_headroom",
        lambda: {"checked": True, "available_physical_gb": 8.0, "available_commit_gb": 16.0},
    )

    def fake_process(group_path):
        if group_path == group_bad:
            svc._status["last_error"] = {"stage": "MATLAB", "message": "引擎异常", "time": "2026-01-01"}
            return None
        svc._running = False  # 完成最后一组后让循环正常退出
        return {"frame_id": "frame-ok", "source_group": group_ok.name, "analysis": {}, "defects": []}

    svc._process_single_group = fake_process  # type: ignore[method-assign]

    svc._process_loop()

    assert [item["group"] for item in svc._failed_groups] == [group_bad.name]
    assert [item["reason"] for item in svc._failed_groups] == ["引擎异常"]
    assert svc._processed_groups == [group_ok.name]
    assert len(svc._group_resource_snapshots) == 2
