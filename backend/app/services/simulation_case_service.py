from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..config import settings


def load_simulation_cases(path: Path | None = None) -> list[dict[str, Any]]:
    library = path or settings.resource_dir / "knowledge" / "paut_simulation_cases.json"
    try:
        payload = json.loads(library.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return []
    cases = payload.get("cases") if isinstance(payload, dict) else None
    return cases if isinstance(cases, list) else []


def find_similar_cases(
    candidates: list[dict[str, Any]],
    frames: list[dict[str, Any]],
    limit: int = 4,
) -> list[dict[str, Any]]:
    cases = load_simulation_cases()
    if not cases or not candidates:
        return []
    shape = next((frame.get("shape") for frame in frames if frame.get("shape")), None)
    thickness = next((frame.get("plate_thickness_mm") for frame in frames if frame.get("plate_thickness_mm")), None)
    ranked: list[tuple[float, dict[str, Any]]] = []
    for case in cases:
        best_score = 0.0
        reasons: list[str] = []
        for candidate in candidates:
            score, candidate_reasons = _similarity(candidate, case, shape, thickness)
            if score > best_score:
                best_score, reasons = score, candidate_reasons
        if best_score <= 0:
            continue
        ranked.append((best_score, {
            "case_id": case.get("case_id"),
            "defect_family": case.get("defect_family"),
            "truth_geometry": case.get("truth_geometry"),
            "acquisition": case.get("acquisition"),
            "validation": case.get("validation"),
            "similarity_reasons": reasons,
            "evidence_level": case.get("evidence_level"),
            "use_boundary": "仿真案例只用于相似性解释和算法验证，不能直接决定真实工件风险或验收结论。",
        }))
    ranked.sort(key=lambda item: (-item[0], str(item[1].get("case_id"))))
    return [item[1] for item in ranked[: max(1, limit)]]


def _similarity(
    candidate: dict[str, Any],
    case: dict[str, Any],
    shape: Any,
    thickness_mm: Any,
) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    hint = _candidate_family(candidate)
    family = str(case.get("defect_family") or "")
    if hint and hint == family:
        score += 5
        reasons.append("候选形态类别一致")
    elif hint and {hint, family} <= {"crack", "lack_of_fusion"}:
        score += 3
        reasons.append("均属于平面型异常")

    case_shape = (case.get("acquisition") or {}).get("fmc_shape")
    if shape and case_shape and sorted(shape) == sorted(case_shape):
        score += 2
        reasons.append("FMC维度一致")

    center = candidate.get("centroid_mm") or {}
    depth = _number(center.get("z"))
    truth_depth = _truth_depth(case.get("truth_geometry") or {})
    if depth is not None and truth_depth is not None:
        denominator = _number(thickness_mm) or _number((case.get("acquisition") or {}).get("plate_thickness_mm")) or 40.0
        delta = abs(depth - truth_depth) / max(denominator, 1.0)
        if delta <= 0.10:
            score += 2
            reasons.append("归一化深度接近")
        elif delta <= 0.25:
            score += 1
            reasons.append("深度区间相近")

    observed_length = _number(candidate.get("section_length_estimate_mm")) or _number(candidate.get("ascan_response_span_mm"))
    truth_length = _truth_size(case.get("truth_geometry") or {})
    if observed_length is not None and truth_length is not None:
        ratio = min(observed_length, truth_length) / max(observed_length, truth_length, 1e-6)
        if ratio >= 0.75:
            score += 2
            reasons.append("二维尺度接近")
        elif ratio >= 0.40:
            score += 1
            reasons.append("二维尺度处于相近量级")
    return score, reasons


def _candidate_family(candidate: dict[str, Any]) -> str:
    names = " ".join(
        str(name) for field in ("type_votes", "image_type_votes")
        for name in (candidate.get(field) or {}).keys()
    )
    if "未熔合" in names or "未焊透" in names:
        return "lack_of_fusion"
    if "裂纹" in names:
        return "crack"
    if "气孔" in names or "孔" in names:
        return "pore"
    if "夹渣" in names or "夹杂" in names:
        return "inclusion"
    length = _number(candidate.get("section_length_estimate_mm"))
    width = _number(candidate.get("section_width_estimate_mm"))
    if length is not None and width is not None and width > 0 and length / width >= 3:
        return "crack"
    return ""


def _truth_depth(geometry: dict[str, Any]) -> float | None:
    centers = geometry.get("centers_mm") or []
    if centers:
        values = [_number(item[1]) for item in centers if isinstance(item, list) and len(item) >= 2]
        values = [value for value in values if value is not None]
        return sum(values) / len(values) if values else None
    segments = geometry.get("segments_mm") or []
    values = []
    for segment in segments:
        if isinstance(segment, list) and len(segment) == 2:
            values.extend(_number(point[1]) for point in segment if isinstance(point, list) and len(point) >= 2)
    values = [value for value in values if value is not None]
    return sum(values) / len(values) if values else None


def _truth_size(geometry: dict[str, Any]) -> float | None:
    lengths = [_number(value) for value in geometry.get("lengths_mm") or []]
    diameters = [_number(value) for value in geometry.get("diameters_mm") or []]
    values = [value for value in [*lengths, *diameters] if value is not None]
    return max(values) if values else None


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
