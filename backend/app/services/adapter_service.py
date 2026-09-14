from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_sidecar_metadata(image_path: Path) -> dict[str, Any]:
    """读取与图片同名的 JSON 元数据，用于物理坐标换算。"""
    sidecar = image_path.with_suffix(".json")
    if not sidecar.exists():
        return {}
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def normalize_calibration(data: dict[str, Any]) -> dict[str, Any]:
    def as_float(key: str, default: float | None = None) -> float | None:
        value = data.get(key, default)
        if value in {"", None}:
            return default
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    mm_per_pixel = as_float("mm_per_pixel")
    return {
        "area_label": str(data.get("scan_area") or data.get("area_label") or data.get("area") or ""),
        "scan_area": str(data.get("scan_area") or data.get("area_label") or data.get("area") or ""),
        "probe_or_channel": str(data.get("probe_or_channel") or data.get("probe") or data.get("channel") or ""),
        "image_note": str(data.get("image_note") or data.get("note") or ""),
        "mm_per_pixel": mm_per_pixel,
        "mm_per_pixel_x": mm_per_pixel,
        "mm_per_pixel_y": mm_per_pixel,
        "origin_x_px": 0,
        "origin_y_px": 0,
        "origin_x_mm": 0,
        "origin_y_mm": 0,
        "rotation_deg": 0,
        "weld_centerline_y_px": None,
        "coordinate_system": "image_xy",
        "coordinate_direction": "图片左上角为原点，x向右，y向下",
        "calibration_method": str(data.get("calibration_method") or ("手动比例" if mm_per_pixel else "未标定")),
        "calibration_note": str(data.get("calibration_note") or ""),
    }


def to_physical_bbox(
    x_min: float,
    y_min: float,
    x_max: float,
    y_max: float,
    mm_per_pixel: float | None,
    origin_x_mm: float = 0,
    origin_y_mm: float = 0,
    mm_per_pixel_x: float | None = None,
    mm_per_pixel_y: float | None = None,
    origin_x_px: float = 0,
    origin_y_px: float = 0,
    rotation_deg: float = 0,
) -> dict[str, float | None]:
    scale = mm_per_pixel_x or mm_per_pixel_y or mm_per_pixel
    if not scale or scale <= 0:
        return {
            "center_x_mm": None,
            "center_y_mm": None,
            "width_mm": None,
            "height_mm": None,
            "area_mm2": None,
        }

    width_px = x_max - x_min
    height_px = y_max - y_min
    center_x_px = (x_min + x_max) / 2
    center_y_px = (y_min + y_max) / 2
    width_mm = width_px * scale
    height_mm = height_px * scale
    return {
        "center_x_mm": center_x_px * scale,
        "center_y_mm": center_y_px * scale,
        "width_mm": width_mm,
        "height_mm": height_mm,
        "area_mm2": width_mm * height_mm,
    }
