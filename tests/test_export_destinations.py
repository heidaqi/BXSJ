from __future__ import annotations

import zipfile
import json
from pathlib import Path

from backend.app.services.archive_service import create_job_archive
from backend.app.services.export_service import write_defects_csv


def test_csv_export_uses_selected_destination_and_adds_suffix(tmp_path: Path) -> None:
    destination = tmp_path / "用户选择" / "缺陷明细"
    result = write_defects_csv("job-1", [], [], destination)
    assert result == destination.with_suffix(".csv").resolve()
    assert result.read_text(encoding="utf-8").startswith("\ufeff序号")


def test_archive_uses_selected_destination_and_adds_suffix(tmp_path: Path) -> None:
    destination = tmp_path / "用户选择" / "检测归档"
    result = create_job_archive({"id": "job-1"}, [], [], [], destination)
    assert result == destination.with_suffix(".zip").resolve()
    with zipfile.ZipFile(result) as archive:
        assert {"manifest.json", "defects.csv"}.issubset(archive.namelist())
        assert "audit_logs.json" not in archive.namelist()
        assert json.loads(archive.read("manifest.json"))["report_included"] is False


def test_archive_uses_unique_names_and_hides_local_paths(tmp_path: Path) -> None:
    first = tmp_path / "a" / "same.png"
    second = tmp_path / "b" / "same.png"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    result = create_job_archive(
        {"id": "job-1", "source_path": "D:/private/data"},
        [
            {"id": "i1", "source_frame_id": "f1", "stored_path": str(first)},
            {"id": "i2", "source_frame_id": "f2", "stored_path": str(second)},
        ],
        [],
        [{"detail": "D:/private/data"}],
        tmp_path / "archive.zip",
    )
    with zipfile.ZipFile(result) as archive:
        image_names = [name for name in archive.namelist() if name.startswith("images/original/")]
        assert len(image_names) == len(set(image_names)) == 2
        assert b"D:/private/data" not in archive.read("manifest.json")


def test_csv_uses_validated_paut_measurement_columns(tmp_path: Path) -> None:
    destination = tmp_path / "details.csv"
    write_defects_csv(
        "job-1",
        [{"id": "image-1", "original_name": "frame-1.png", "area_label": "焊缝"}],
        [{
            "image_id": "image-1", "class_name": "裂纹", "source": "validated_team_result",
            "center_x_mm": 42.0, "center_y_mm": 18.0, "corrected_length_mm": 5.5,
            "raw_length_mm": 99.0, "review_status": "已确认",
        }],
        destination,
    )
    content = destination.read_text(encoding="utf-8-sig")
    assert "校正长度(mm)" in content
    assert "校验后检测结果" in content
    assert "99.0" not in content
    assert "像素中心" not in content
