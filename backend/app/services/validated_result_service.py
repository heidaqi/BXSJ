"""Canonical user-facing view of validated PAUT results.

Only the final team result package and explicit human review may provide defect
type, position, peak or measurement values.  Pre-validation A-scan candidates,
DAS display peaks, image detections and language-model output are deliberately
excluded from this view.
"""
from __future__ import annotations

import math
from typing import Any


CANONICAL_RESULT_VERSION = "validated-paut-result/v1"


def validated_package(frame: dict[str, Any]) -> dict[str, Any] | None:
    package = frame.get("team_result") or (frame.get("analysis") or {}).get("team_result")
    if not isinstance(package, dict):
        return None
    defects = package.get("defects")
    if not isinstance(defects, list):
        return None
    provenance = package.get("provenance") or {}
    summary = package.get("summary") or {}
    if not (
        provenance.get("provider") == "team_algorithm"
        or summary.get("pipeline") == "team_paut_v2"
        or str(package.get("schema_version") or "").startswith("team-inspection-package/")
    ):
        return None
    return package


def canonical_frame_candidates(frame: dict[str, Any]) -> list[dict[str, Any]]:
    """Return current reviewed-over-validated results for one frame.

    A pending database row is only a materialized copy of the algorithm result;
    it must not replace the validated package.  Confirmed review fields override
    their matching result, false positives are removed, and explicit manual
    additions are appended.
    """
    package = validated_package(frame)
    if package is None:
        return []

    reviewed_rows = list((frame.get("review") or {}).get("defects") or [])
    review_by_id = {
        str(row.get("id") or row.get("defect_id") or ""): row
        for row in reviewed_rows
        if row.get("id") or row.get("defect_id")
    }
    results: list[dict[str, Any]] = []
    source_ids: set[str] = set()
    for index, defect in enumerate(package.get("defects") or [], start=1):
        source_id = str(defect.get("defect_id") or defect.get("id") or f"row-{index:04d}")
        source_ids.add(source_id)
        review = review_by_id.get(source_id)
        if review and str(review.get("review_status") or "") == "误检":
            continue
        reviewed = bool(review and str(review.get("review_status") or "") not in {"", "待复核"})
        results.append(_canonical_candidate(frame, defect, review if reviewed else None))

    for row in reviewed_rows:
        row_id = str(row.get("id") or row.get("defect_id") or "")
        if not row_id or row_id in source_ids or row.get("source") != "manual":
            continue
        if str(row.get("review_status") or "") == "误检":
            continue
        results.append(_manual_candidate(frame, row))
    return results


def canonical_batch_candidates(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [candidate for frame in frames for candidate in canonical_frame_candidates(frame)]


def validated_frame_count(frames: list[dict[str, Any]]) -> int:
    return sum(validated_package(frame) is not None for frame in frames)


def _canonical_candidate(
    frame: dict[str, Any],
    defect: dict[str, Any],
    review: dict[str, Any] | None,
) -> dict[str, Any]:
    effective_class = _review_value(review, "class_name", defect.get("class_name") or defect.get("type"))
    x_mm = _number(_review_value(review, "center_x_mm", defect.get("x_mm")))
    z_mm = _number(_review_value(review, "center_y_mm", defect.get("z_mm")))
    corrected_length = _number(_review_value(review, "corrected_length_mm", defect.get("corrected_length_mm")))
    if review and review.get("corrected_length_mm") is None and review.get("width_mm") is not None:
        corrected_length = _number(review.get("width_mm"))
    diameter = _number(_review_value(review, "diameter_mm", defect.get("diameter_mm")))
    area = _number(_review_value(review, "area_mm2", defect.get("area_mm2")))
    minor_axis_nominal = _number(defect.get("minor_axis_nominal_mm"))
    minor_axis_lower = _number(defect.get("minor_axis_lower_mm"))
    minor_axis_upper = _number(defect.get("minor_axis_upper_mm"))
    orientation = _number(_review_value(review, "image_angle_deg", defect.get("image_angle_deg")))
    class_name = str(effective_class or "待复核")
    family = _family(class_name)
    length = corrected_length if family in {"crack", "lack_of_fusion", "inclusion"} else None
    width = minor_axis_nominal if family == "inclusion" else diameter if family == "pore" else None
    defect_id = str(defect.get("defect_id") or defect.get("id") or "")
    review_status = str((review or {}).get("review_status") or "待复核")
    return {
        "canonical_result_version": CANONICAL_RESULT_VERSION,
        "candidate_id": defect_id,
        "defect_id": defect_id,
        "source_defect_id": defect.get("source_defect_id"),
        "frame_id": frame.get("frame_id"),
        "source_group": frame.get("source_group"),
        "source": "manual_review" if review else "validated_team_result",
        "primary_evidence": "manual_review" if review else "validated_team_result",
        "class_name": class_name,
        "type": class_name,
        "family": family,
        "confidence": _number(defect.get("confidence")),
        "type_confidence": _number(defect.get("confidence")),
        "confidence_basis": defect.get("confidence_basis"),
        "x_mm": x_mm,
        "z_mm": z_mm,
        "centroid_mm": {"x": x_mm, "z": z_mm},
        "peak_x_mm": _number(defect.get("peak_x_mm")),
        "peak_z_mm": _number(defect.get("peak_z_mm")),
        "global_peak_db": _number(defect.get("global_peak_db")),
        "corrected_length_mm": length,
        "length_mm": length,
        "diameter_mm": diameter,
        "width_mm": width,
        "area_mm2": area,
        "minor_axis_nominal_mm": minor_axis_nominal,
        "minor_axis_lower_mm": minor_axis_lower,
        "minor_axis_upper_mm": minor_axis_upper,
        "area_lower_mm2": _number(defect.get("area_lower_mm2")),
        "area_upper_mm2": _number(defect.get("area_upper_mm2")),
        "slag_probability": _number(defect.get("slag_probability")),
        "image_angle_deg": orientation,
        "section_orientation_deg": orientation,
        "snr_db": _number(defect.get("snr_db")),
        "quantification_status": defect.get("quantification_status") or "",
        "warning": defect.get("warning") or "",
        "review_status": review_status,
        "review": {"status": review_status},
        "review_override_applied": bool(review),
        "team_quality": (validated_package(frame) or {}).get("quality") or {},
        "geometry": {
            "length_mm": length,
            "width_mm": width,
            "diameter_mm": diameter,
            "area_mm2": area,
            "minor_axis_nominal_mm": minor_axis_nominal,
            "minor_axis_range_mm": [minor_axis_lower, minor_axis_upper],
            "orientation_deg": orientation,
            "method": "校验后的检测算法定量结果" if not review else "人工复核后的检测结果",
        },
        "measurement_boundary": "数值来自校验后的检测结果；缺失项不进行推测。",
    }


def _manual_candidate(frame: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    class_name = str(row.get("class_name") or "人工补录缺陷")
    family = _family(class_name)
    length = _number(row.get("corrected_length_mm"))
    if length is None and family in {"crack", "lack_of_fusion"}:
        length = _number(row.get("width_mm"))
    diameter = _number(row.get("diameter_mm"))
    return {
        "canonical_result_version": CANONICAL_RESULT_VERSION,
        "candidate_id": str(row.get("id") or row.get("defect_id") or ""),
        "defect_id": str(row.get("id") or row.get("defect_id") or ""),
        "frame_id": frame.get("frame_id"),
        "source_group": frame.get("source_group"),
        "source": "manual_review",
        "primary_evidence": "manual_review",
        "class_name": class_name,
        "type": class_name,
        "family": family,
        "confidence": _number(row.get("confidence")),
        "type_confidence": _number(row.get("confidence")),
        "x_mm": _number(row.get("center_x_mm")),
        "z_mm": _number(row.get("center_y_mm")),
        "centroid_mm": {"x": _number(row.get("center_x_mm")), "z": _number(row.get("center_y_mm"))},
        "peak_x_mm": None,
        "peak_z_mm": None,
        "global_peak_db": None,
        "corrected_length_mm": length,
        "length_mm": length,
        "diameter_mm": diameter,
        "width_mm": diameter if family == "pore" else None,
        "area_mm2": _number(row.get("area_mm2")),
        "image_angle_deg": _number(row.get("image_angle_deg")),
        "section_orientation_deg": _number(row.get("image_angle_deg")),
        "snr_db": _number(row.get("snr_db")),
        "quantification_status": row.get("quantification_status") or "人工补录",
        "warning": "",
        "review_status": str(row.get("review_status") or "已确认"),
        "review": {"status": str(row.get("review_status") or "已确认")},
        "review_override_applied": True,
        "team_quality": (validated_package(frame) or {}).get("quality") or {},
        "geometry": {
            "length_mm": length,
            "width_mm": diameter if family == "pore" else None,
            "diameter_mm": diameter,
            "area_mm2": _number(row.get("area_mm2")),
            "orientation_deg": _number(row.get("image_angle_deg")),
            "method": "人工补录",
        },
        "measurement_boundary": "人工补录数值；缺失项不进行推测。",
    }


def _review_value(review: dict[str, Any] | None, key: str, fallback: Any) -> Any:
    if review is not None and review.get(key) is not None:
        return review.get(key)
    return fallback


def _number(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _family(class_name: str) -> str:
    return {
        "裂纹": "crack",
        "气孔": "pore",
        "夹渣": "inclusion",
        "夹杂": "inclusion",
        "未熔合": "lack_of_fusion",
        "未焊透": "lack_of_fusion",
    }.get(class_name, "unknown")
