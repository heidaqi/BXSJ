from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from backend.app.paut.realtime import COMSOL_16X16_POSITIONS_MM, RealtimeConfig, RealtimeService
from backend.app.paut import realtime


def _write_flat_group(path: Path, *, positions: list[float] | None = None) -> None:
    path.mkdir(parents=True, exist_ok=True)
    time_axis = np.arange(4, dtype=float) * 2.0e-8
    for position_mm in positions or COMSOL_16X16_POSITIONS_MM:
        tx_m = np.full(time_axis.shape, position_mm / 1000.0)
        channels = np.zeros((time_axis.size, 16), dtype=float)
        np.savetxt(path / f"{position_mm:g}.txt", np.column_stack([tx_m, time_axis, channels]))


def test_parent_folder_excludes_none_and_binds_it_as_background(tmp_path: Path) -> None:
    case = tmp_path / "case"
    background = tmp_path / "none"
    _write_flat_group(case)
    _write_flat_group(background)
    service = RealtimeService()

    status = service.configure(RealtimeConfig(source_path=str(tmp_path)))

    assert status["source_group_count"] == 1
    assert status["input_validation"]["contract"] == "comsol-flat-16x16/v1"
    assert status["input_validation"]["tx_column_unit"] == "m"
    assert status["background_reference"]["path"] == str(background.resolve())
    assert service._build_matlab_params()["background_input_path"] == str(background.resolve())


def test_uses_bundled_none_when_source_has_no_local_background(tmp_path: Path, monkeypatch) -> None:
    resource_root = tmp_path / "resources"
    source = tmp_path / "external" / "case"
    background = resource_root / "data" / "groups" / "none"
    _write_flat_group(source)
    _write_flat_group(background)
    monkeypatch.setattr(realtime.settings, "resource_dir", resource_root)
    service = RealtimeService()

    status = service.configure(RealtimeConfig(source_path=str(source)))

    assert status["background_reference"]["path"] == str(background.resolve())
    assert status["background_reference"]["source"] == "项目内置data/groups/none"
    assert service._build_matlab_params()["background_input_path"] == str(background.resolve())


def test_wrong_tx_sequence_is_rejected(tmp_path: Path) -> None:
    _write_flat_group(tmp_path, positions=COMSOL_16X16_POSITIONS_MM[:-1] + [65.0])
    service = RealtimeService()

    with pytest.raises(ValueError, match="Tx顺序"):
        service.configure(RealtimeConfig(source_path=str(tmp_path)))


def test_failed_reconfigure_preserves_last_valid_configuration(tmp_path: Path) -> None:
    valid = tmp_path / "valid"
    invalid = tmp_path / "invalid"
    _write_flat_group(valid)
    _write_flat_group(invalid, positions=COMSOL_16X16_POSITIONS_MM[:-1] + [65.0])
    service = RealtimeService()
    service.configure(RealtimeConfig(source_path=str(valid)))

    with pytest.raises(ValueError, match="Tx顺序"):
        service.configure(RealtimeConfig(source_path=str(invalid)))

    assert service._config is not None
    assert service._config.source_path == str(valid)


def test_new_session_clears_interrupted_session_state(tmp_path: Path) -> None:
    _write_flat_group(tmp_path)
    service = RealtimeService()
    first_id = service.configure(RealtimeConfig(source_path=str(tmp_path)))["session_id"]
    service._status.update(received_count=8, waiting_count=7, last_error={"message": "memory"})
    service._current_frame_id = "old-frame"
    service._session_frame_ids = ["old-frame"]
    second = service.configure(RealtimeConfig(source_path=str(tmp_path)))
    assert second["session_id"] != first_id
    assert second["waiting_count"] == 0
    assert second["received_count"] == 0
    assert second["last_error"] is None
    assert service._current_frame_id is None
    assert service._session_frame_ids == []
