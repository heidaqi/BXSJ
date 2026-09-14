"""Build versioned team result packages and perform deterministic quality gates."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .paut_v2_adapter import ADAPTER_VERSION, SCHEMA_VERSION, output_files, read_defect_report, summarize


QUALITY_ORDER = {"可信": 0, "建议复核": 1, "数据不足": 2, "结果冲突": 3, "无法评价": 4}


def build_team_result_package(
    output_dir: Path,
    *,
    batch_id: str,
    frame_id: str,
    algorithm_version: str = "team-paut-v2",
    x_range_mm: list[float] | None = None,
    z_range_mm: list[float] | None = None,
    source_path: str = "",
    preprocessing: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Return None when this is not a PAUT v2 output directory."""
    output_dir = Path(output_dir)
    assets = output_files(output_dir)
    if assets["report_csv"] is None:
        return None

    defects = read_defect_report(output_dir) or []
    stable_defects = []
    for index, defect in enumerate(defects, start=1):
        source_id = str(defect.get("defect_id") or f"row-{index:04d}")
        stable_defects.append({
            **defect,
            "source_defect_id": defect.get("source_defect_id") or source_id,
            "defect_id": f"{frame_id}:team:{source_id}",
            "frame_id": frame_id,
        })

    quality = assess_team_result_quality(
        stable_defects,
        assets=assets,
        x_range_mm=x_range_mm,
        z_range_mm=z_range_mm,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "result_stage": "validated",
        "authoritative": True,
        "adapter_version": ADAPTER_VERSION,
        "algorithm_version": algorithm_version,
        "batch_id": batch_id,
        "frame_id": frame_id,
        "source_path": source_path,
        "source_fingerprint": _source_fingerprint(source_path),
        "preprocessing": dict(preprocessing or {}),
        "assets": {name: str(path.resolve()) if path else None for name, path in assets.items()},
        "coordinate_system": {
            "name": "probe_surface_xz_mm",
            "x_range_mm": x_range_mm,
            "z_range_mm": z_range_mm,
            "x_direction": "沿探头阵元排列方向",
            "z_direction": "由工件表面向内",
        },
        "defects": stable_defects,
        "summary": summarize(stable_defects),
        "quality": quality,
        "provenance": {
            "provider": "team_algorithm",
            "pipeline": "paut_v2",
            "result_stage": "validated",
            "authoritative_fields": [
                "class_name", "x_mm", "z_mm", "raw_length_mm", "corrected_length_mm",
                "diameter_mm", "area_mm2", "image_angle_deg", "quantification_status",
                "minor_axis_nominal_mm", "minor_axis_lower_mm", "minor_axis_upper_mm",
                "area_lower_mm2", "area_upper_mm2", "slag_probability",
            ],
        },
    }


def assess_team_result_quality(
    defects: list[dict[str, Any]],
    *,
    assets: dict[str, Path | None],
    x_range_mm: list[float] | None,
    z_range_mm: list[float] | None,
) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    _require_asset(issues, assets, "report_csv", "无法评价", "缺少团队缺陷报告")
    _require_asset(issues, assets, "thresholded_image", "数据不足", "缺少团队标注/阈值图")
    _require_asset(issues, assets, "analysis_mat", "建议复核", "缺少完整MAT分析结果，无法深入追溯")

    seen_ids: set[str] = set()
    for index, defect in enumerate(defects, start=1):
        defect_id = str(defect.get("defect_id") or "")
        if not defect_id or defect_id in seen_ids:
            _issue(issues, "duplicate_defect_id", "结果冲突", f"第{index}条缺陷ID缺失或重复", defect_id)
        seen_ids.add(defect_id)
        x = defect.get("x_mm")
        z = defect.get("z_mm")
        if x is None or z is None:
            _issue(issues, "missing_position", "数据不足", "缺陷缺少物理坐标", defect_id)
        else:
            if not _in_range(x, x_range_mm):
                _issue(issues, "x_out_of_range", "结果冲突", f"x坐标{x} mm超出成像范围", defect_id)
            if not _in_range(z, z_range_mm):
                _issue(issues, "z_out_of_range", "结果冲突", f"深度{z} mm超出成像范围", defect_id)
        for field, label in (
            ("raw_length_mm", "原始长度"), ("corrected_length_mm", "修正长度"),
            ("diameter_mm", "直径"), ("area_mm2", "面积"),
            ("minor_axis_nominal_mm", "短轴名义值"),
            ("minor_axis_lower_mm", "短轴下限"),
            ("minor_axis_upper_mm", "短轴上限"),
        ):
            value = defect.get(field)
            if value is not None and value < 0:
                _issue(issues, "negative_measurement", "结果冲突", f"{label}不能为负数", defect_id)
        confidence = defect.get("confidence")
        if confidence is not None and not 0 <= confidence <= 1:
            _issue(issues, "invalid_confidence", "结果冲突", "类别置信度超出0至1", defect_id)
        if defect.get("class_name") == "夹渣":
            lower = defect.get("minor_axis_lower_mm")
            nominal = defect.get("minor_axis_nominal_mm")
            upper = defect.get("minor_axis_upper_mm")
            if None not in {lower, nominal, upper} and not lower <= nominal <= upper:
                _issue(issues, "invalid_slag_minor_axis_range", "结果冲突", "夹渣短轴名义值不在输出范围内", defect_id)
        if defect.get("warning"):
            _issue(issues, "team_warning", "建议复核", str(defect["warning"]), defect_id)
        if not defect.get("quantification_status"):
            _issue(issues, "missing_quantification_status", "建议复核", "缺少定量状态说明", defect_id)

    status = max((item["status"] for item in issues), key=QUALITY_ORDER.get, default="可信")
    return {
        "status": status,
        "issues": issues,
        "issue_count": len(issues),
        "blocking": QUALITY_ORDER[status] >= QUALITY_ORDER["结果冲突"],
        "meaning": "仅评价结果包完整性和内部一致性，不代替检测验收结论",
        "notices": (["本帧未形成需记录的缺陷候选"] if not defects else []),
    }


def _require_asset(
    issues: list[dict[str, Any]],
    assets: dict[str, Path | None],
    name: str,
    status: str,
    message: str,
) -> None:
    if not assets.get(name):
        _issue(issues, f"missing_asset:{name}", status, message)


def _issue(
    issues: list[dict[str, Any]],
    code: str,
    status: str,
    message: str,
    defect_id: str = "",
) -> None:
    issues.append({"code": code, "status": status, "message": message, "defect_id": defect_id or None})


def _in_range(value: float, bounds: list[float] | None) -> bool:
    return not bounds or len(bounds) != 2 or min(bounds) <= value <= max(bounds)


def _source_fingerprint(source_path: str) -> str | None:
    if not source_path:
        return None
    path = Path(source_path)
    try:
        if path.is_file():
            stat = path.stat()
            payload = f"{path.name}|{stat.st_size}|{stat.st_mtime_ns}"
        elif path.is_dir():
            rows = []
            for child in sorted(path.iterdir(), key=lambda item: item.name.lower()):
                if child.is_file():
                    stat = child.stat()
                    rows.append(f"{child.name}|{stat.st_size}|{stat.st_mtime_ns}")
            payload = "\n".join(rows)
        else:
            return None
    except OSError:
        return None
    return hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()
