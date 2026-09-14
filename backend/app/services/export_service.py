from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any

from ..config import settings


CSV_HEADERS = [
    "序号",
    "数据帧",
    "区域/部位",
    "缺陷类型",
    "置信度",
    "来源",
    "复核状态",
    "位置X(mm)",
    "深度Z(mm)",
    "校正长度(mm)",
    "孔径(mm)",
    "面积(mm²)",
    "截面朝向(°)",
    "信噪比(dB)",
    "处理建议",
    "备注",
]


def build_defects_csv_text(images: list[dict[str, Any]], defects: list[dict[str, Any]]) -> str:
    image_by_id = {image["id"]: image for image in images}
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_HEADERS)
    for index, defect in enumerate(defects, start=1):
        image = image_by_id.get(defect["image_id"], {})
        writer.writerow(
            [
                index,
                image.get("original_name", ""),
                image.get("area_label", ""),
                defect.get("class_name", ""),
                defect.get("confidence", ""),
                _source_label(defect.get("source")),
                defect.get("review_status", ""),
                defect.get("center_x_mm", ""),
                defect.get("center_y_mm", ""),
                defect.get("corrected_length_mm", ""),
                defect.get("diameter_mm", ""),
                defect.get("area_mm2", ""),
                defect.get("image_angle_deg", ""),
                defect.get("snr_db", ""),
                defect.get("suggestion", ""),
                defect.get("remark", ""),
            ]
        )
    return "\ufeff" + buffer.getvalue()


def _source_label(value: Any) -> str:
    return {
        "manual": "人工补录",
        "manual_review": "人工复核",
        "validated_team_result": "校验后检测结果",
    }.get(str(value or ""), "历史检测结果")


def write_defects_csv(
    job_id: str,
    images: list[dict[str, Any]],
    defects: list[dict[str, Any]],
    destination: Path | None = None,
) -> Path:
    path = destination.expanduser().resolve() if destination else settings.output_dir / "exports" / f"{job_id}_defects.csv"
    if path.suffix.lower() != ".csv":
        path = path.with_suffix(".csv")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_defects_csv_text(images, defects), encoding="utf-8")
    return path
