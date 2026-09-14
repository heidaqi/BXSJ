"""Read and normalize outputs produced by the team PAUT v2 pipeline.

The adapter preserves team values and only adds stable identifiers and aliases.
It must never infer a missing class, confidence, coordinate, or measurement.
"""
from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "team-inspection-package/v1"
ADAPTER_VERSION = "paut-v2-csv/v2-slag-quantification"
TYPE_NAMES = {
    "Crack": "裂纹",
    "LOF": "未熔合",
    "Pore": "气孔",
    "Slag": "夹渣",
    "Inclusion": "夹杂",
    "Unknown": "待复核",
}
EXPECTED_COLUMNS = {
    "DefectID", "Type", "ClassificationMethod", "CrackProbability",
    "CNNThreshold", "CenterX_mm", "CenterZ_mm", "PeakX_mm", "PeakZ_mm",
    "GlobalPeak_dB", "RawLength_mm", "CorrectedLength_mm", "Diameter_mm",
    "Area_mm2", "ImageAngle_deg", "AspectRatio", "SNR_dB",
    "QuantificationStatus", "Warning",
}


def _number(value: Any) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def output_files(output_dir: Path) -> dict[str, Path | None]:
    output_dir = Path(output_dir)
    paths = {
        "thresholded_image": output_dir / "das_image_thresholded.png",
        "original_image": output_dir / "das_image_full.png",
        "report_csv": output_dir / "defect_report.csv",
        "analysis_mat": output_dir / "analysis_result.mat",
    }
    return {name: path if path.is_file() else None for name, path in paths.items()}


def read_defect_report(output_dir: Path) -> list[dict[str, Any]] | None:
    report_path = Path(output_dir) / "defect_report.csv"
    if not report_path.is_file():
        return None
    with report_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("团队缺陷报告没有表头")
        missing = EXPECTED_COLUMNS - set(reader.fieldnames)
        if missing:
            raise ValueError("团队缺陷报告缺少字段：" + "、".join(sorted(missing)))
        return [normalize_row(row, row_index=index) for index, row in enumerate(reader, start=1)]


def normalize_row(row: dict[str, Any], row_index: int = 1) -> dict[str, Any]:
    defect_type = str(row.get("Type") or "Unknown").strip() or "Unknown"
    source_defect_id = str(row.get("DefectID") or "").strip()
    crack_probability = _number(row.get("CrackProbability"))
    raw_length = _number(row.get("RawLength_mm"))
    corrected_length = _number(row.get("CorrectedLength_mm"))
    diameter = _number(row.get("Diameter_mm"))
    slag_probability = _number(row.get("SlagProbability"))
    minor_axis_nominal = _number(row.get("MinorAxisNominal_mm"))

    # CrackProbability and SlagProbability are class-specific confidences;
    # neither may be reused for a different defect family.
    confidence = (
        crack_probability if defect_type == "Crack"
        else slag_probability if defect_type == "Slag"
        else None
    )
    # RawLength_mm is a pre-calibration trace value.  It remains available for
    # audit, but must never silently become the user-facing final measurement.
    primary_size = corrected_length if defect_type in {"Crack", "LOF", "Slag"} else diameter
    confidence_basis = (
        "crack_probability" if defect_type == "Crack" and confidence is not None
        else "slag_probability" if defect_type == "Slag" and confidence is not None
        else None
    )

    return {
        "defect_id": source_defect_id or f"row-{row_index:04d}",
        "source_defect_id": source_defect_id or None,
        "type": defect_type,
        "class_name": TYPE_NAMES.get(defect_type, defect_type),
        "classification_method": str(row.get("ClassificationMethod") or "").strip(),
        "confidence": confidence,
        "confidence_available": confidence is not None,
        "confidence_basis": confidence_basis,
        "crack_probability": crack_probability,
        "slag_probability": slag_probability,
        "lof_slag_suggestion": str(row.get("LOFSlagSuggestion") or "").strip(),
        "lof_slag_suggestion_status": str(row.get("LOFSlagSuggestionStatus") or "").strip(),
        "cnn_threshold": _number(row.get("CNNThreshold")),
        "x_mm": _number(row.get("CenterX_mm")),
        "z_mm": _number(row.get("CenterZ_mm")),
        "peak_x_mm": _number(row.get("PeakX_mm")),
        "peak_z_mm": _number(row.get("PeakZ_mm")),
        "global_peak_db": _number(row.get("GlobalPeak_dB")),
        "raw_length_mm": raw_length,
        "corrected_length_mm": corrected_length,
        "length_mm": primary_size if defect_type in {"Crack", "LOF", "Slag"} else None,
        "diameter_mm": diameter,
        "area_mm2": _number(row.get("Area_mm2")),
        "minor_axis_nominal_mm": minor_axis_nominal,
        "minor_axis_lower_mm": _number(row.get("MinorAxisLower_mm")),
        "minor_axis_upper_mm": _number(row.get("MinorAxisUpper_mm")),
        "area_lower_mm2": _number(row.get("AreaLower_mm2")),
        "area_upper_mm2": _number(row.get("AreaUpper_mm2")),
        "advisory_slag_major_mm": _number(row.get("AdvisorySlagMajor_mm")),
        "advisory_slag_short_lower_mm": _number(row.get("AdvisorySlagShortLower_mm")),
        "advisory_slag_short_upper_mm": _number(row.get("AdvisorySlagShortUpper_mm")),
        "advisory_slag_area_mm2": _number(row.get("AdvisorySlagArea_mm2")),
        "image_angle_deg": _number(row.get("ImageAngle_deg")),
        "aspect_ratio": _number(row.get("AspectRatio")),
        "snr_db": _number(row.get("SNR_dB")),
        "quantification_status": str(row.get("QuantificationStatus") or "").strip(),
        "warning": str(row.get("Warning") or "").strip(),
        "coordinate_system": "probe_surface_xz_mm",
        "coordinate_source": "team_paut_v2",
        "measurement_source": "team_paut_v2",
        "source": "paut_v2",
        "raw_team_values": dict(row),
        # Temporary aliases for existing database/UI consumers.
        "width_mm": minor_axis_nominal if defect_type == "Slag" else primary_size,
        "height_mm": minor_axis_nominal if defect_type == "Slag" else diameter,
        "X_mm": _number(row.get("CenterX_mm")),
        "Z_mm": _number(row.get("CenterZ_mm")),
        "MLProbability": confidence,
        "Score": confidence,
        "MLLevel": TYPE_NAMES.get(defect_type, defect_type),
    }


def summarize(defects: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    warnings = 0
    unavailable_confidence = 0
    for defect in defects:
        name = str(defect.get("class_name") or "待复核")
        counts[name] = counts.get(name, 0) + 1
        warnings += bool(defect.get("warning"))
        unavailable_confidence += defect.get("confidence") is None
    parts = [f"{name}{count}处" for name, count in counts.items() if count]
    return {
        "pipeline": "team_paut_v2",
        "adapter_version": ADAPTER_VERSION,
        "count": len(defects),
        "counts": counts,
        "warning_count": warnings,
        "confidence_unavailable_count": unavailable_confidence,
        "summary": "、".join(parts) if parts else "当前未形成满足阈值的缺陷记录",
    }
