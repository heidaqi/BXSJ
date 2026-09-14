from pathlib import Path

import numpy as np
import pytest

from backend.app.services.fmc_data_inspector import (
    DEFAULT_16X16_POSITIONS_MM,
    LEGACY_16X16_POSITIONS_MM,
    FmcDataInspector,
)


def _write_legacy(root: Path, positions: list[float]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    time_axis = np.arange(4, dtype=float) * 2e-8
    for position in positions:
        matrix = np.column_stack([
            np.full(time_axis.size, position / 1000.0),
            time_axis,
            *[np.full(time_axis.size, index + position) for index in range(16)],
        ])
        np.savetxt(root / f"{position:g}.txt", matrix)


def _write_nested(root: Path) -> None:
    time_axis = np.arange(4, dtype=float) * 2e-8
    for tx in range(1, 17):
        tx_dir = root / f"tx_{tx}"
        tx_dir.mkdir(parents=True, exist_ok=True)
        for rx in range(1, 17):
            np.savetxt(tx_dir / f"{rx:03d}.txt", np.column_stack([time_axis, np.full(4, tx + rx)]))


def _write_wide(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    time_axis = np.arange(4, dtype=float) * 2e-8
    np.savetxt(path, np.column_stack([time_axis, np.ones((4, 256))]))


def test_discovers_all_supported_formats_recursively(tmp_path: Path) -> None:
    _write_legacy(tmp_path / "cases" / "legacy" / "data", DEFAULT_16X16_POSITIONS_MM)
    _write_nested(tmp_path / "cases" / "nested" / "data")
    _write_wide(tmp_path / "cases" / "wide" / "1.txt")

    result = FmcDataInspector().inspect(tmp_path)

    assert len(result.valid_groups) == 3
    assert {item.format for item in result.valid_groups} == {
        "legacy_tx_table", "tx_rx_directory", "wide_fmc",
    }
    assert result.to_dict()["status"] == "通过"
    assert all(item.sample_rate_hz == pytest.approx(50_000_000.0) for item in result.valid_groups)


def test_accepts_both_supported_probe_layouts(tmp_path: Path) -> None:
    _write_legacy(tmp_path / "valid", DEFAULT_16X16_POSITIONS_MM)
    _write_legacy(tmp_path / "old", LEGACY_16X16_POSITIONS_MM)

    result = FmcDataInspector().inspect(tmp_path)

    assert len(result.valid_groups) == 2
    assert not result.skipped_groups
    assert {item.probe_profile for item in result.valid_groups} == {"30-63", "22-70"}
    assert result.to_dict()["tx_positions_mm"] == "mixed"


def test_skips_unsupported_probe_layout_but_keeps_valid_groups(tmp_path: Path) -> None:
    _write_legacy(tmp_path / "valid", DEFAULT_16X16_POSITIONS_MM)
    _write_legacy(tmp_path / "unsupported", list(range(16)))

    result = FmcDataInspector().inspect(tmp_path)

    assert len(result.valid_groups) == 1
    assert len(result.skipped_groups) == 1
    assert "不属于受支持的30-63或22-70配置" in result.skipped_groups[0].reason
    assert result.to_dict()["status"] == "部分通过"


def test_rejects_incomplete_nested_group(tmp_path: Path) -> None:
    _write_nested(tmp_path / "data")
    (tmp_path / "data" / "tx_16" / "016.txt").unlink()

    result = FmcDataInspector().inspect(tmp_path)

    assert not result.valid_groups
    assert result.to_dict()["status"] == "未通过"
    assert "Rx文件应为16个" in result.skipped_groups[0].reason
