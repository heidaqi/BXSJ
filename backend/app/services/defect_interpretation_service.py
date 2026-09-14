from __future__ import annotations

from typing import Any


FAMILY_LABELS = {
    "crack": "裂纹",
    "pore": "气孔",
    "inclusion": "夹渣/夹杂",
    "lack_of_fusion": "未熔合/未焊透",
    "unknown": "未定型异常",
}


def build_defect_assessments(
    candidates: list[dict[str, Any]],
    risk_assessment: dict[str, Any] | None = None,
    similar_cases: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Explain frame classification and region geometry without reclassifying it."""

    risk_by_id = {
        item.get("candidate_id"): item
        for item in (risk_assessment or {}).get("candidate_risks", [])
        if isinstance(item, dict)
    }
    has_reference_cases = bool(similar_cases)
    return [
        _region_assessment(candidate, risk_by_id.get(candidate.get("candidate_id")), has_reference_cases)
        for candidate in candidates
    ]


def _region_assessment(
    candidate: dict[str, Any],
    risk: dict[str, Any] | None,
    has_reference_cases: bool,
) -> dict[str, Any]:
    family = str(candidate.get("family") or "unknown")
    if family not in FAMILY_LABELS:
        family = "unknown"
    label = str(candidate.get("type") or FAMILY_LABELS[family])
    confidence_value = _number(candidate.get("type_confidence"))
    confidence = f"{confidence_value:.0%}" if confidence_value is not None else "待复核"
    center = candidate.get("centroid_mm") or candidate.get("center_mm") or {}
    geometry = candidate.get("geometry") or {}
    score = _number(candidate.get("ascan_score_median"))
    support = _number(candidate.get("ascan_support_median"))

    is_team_result = candidate.get("primary_evidence") in {"team_algorithm", "validated_team_result", "manual_review"}
    basis = [str(candidate.get("type_basis_short") or ("校验后的检测结果" if is_team_result else "整帧FMC分类结果"))]
    if has_reference_cases:
        basis.append("仿真实例仅用于复核参考，不参与类别改写")

    response_basis: list[str] = []
    if is_team_result:
        snr = _number(candidate.get("snr_db"))
        response_basis.append(f"信噪比约{snr:.1f} dB" if snr is not None else "信噪比待复核")
        if candidate.get("warning"):
            response_basis.append("当前结果附有质量提示")
    if score is not None:
        response_basis.append(f"A扫响应评分约{score:.2f}")
    if support is not None:
        response_basis.append(f"约{support:.0f}个通道支持")

    causes, potential_risks = _family_cause_and_risks(family)
    region_index = _region_number(candidate)
    return {
        "candidate_id": candidate.get("candidate_id"),
        "region_id": candidate.get("region_id") or candidate.get("candidate_id"),
        "probable_type": label,
        "type_scope": "frame_primary_response",
        "type_confidence": confidence,
        "type_basis": basis,
        "possible_causes": causes,
        "indication": (
            (f"检测结果提示为{label}。" if is_team_result else
             f"本帧主要响应倾向{label}；当前为疑似缺陷{region_index}。检测记录数量需经人工复核。")
        ),
        "position": f"x={_fmt(center.get('x'))} mm，深度z={_fmt(center.get('z'))} mm",
        "size_and_orientation": _size_text(family, geometry, candidate),
        "ascan_basis": "；".join(response_basis),
        "measurement_source": "人工复核" if candidate.get("primary_evidence") == "manual_review" else "校验后的检测结果",
        "confidence": confidence,
        "risk_note": _risk_text(risk, potential_risks),
        "review_focus": _review_focus(family),
    }


def _size_text(family: str, geometry: dict[str, Any], candidate: dict[str, Any]) -> str:
    diameter = _number(candidate.get("diameter_mm", geometry.get("diameter_mm")))
    length = _number(candidate.get("length_mm", geometry.get("length_mm")))
    width = _number(candidate.get("width_mm", geometry.get("width_mm")))
    orientation = _number(candidate.get("orientation_deg", geometry.get("orientation_deg")))
    area = _number(geometry.get("area_mm2"))
    if family == "pore" and diameter is not None:
        text = f"区域面积等效直径约{diameter:.2f} mm"
        if length is not None and width is not None:
            text += f"，主轴/次轴约{length:.2f}/{width:.2f} mm"
        if orientation is not None:
            text += f"，主轴朝向约{orientation:.1f}°"
        return text
    if length is not None or width is not None:
        name = "截面中心线长度" if family in {"crack", "lack_of_fusion"} else "区域主轴尺寸"
        text = f"{name}约{_fmt(length)} mm，宽约{_fmt(width)} mm"
        if orientation is not None:
            text += f"，朝向约{orientation:.1f}°"
        return text
    if area is not None:
        return f"区域面积约{area:.2f} mm²"
    return "校验结果未提供尺寸或朝向"


def _family_cause_and_risks(family: str) -> tuple[list[str], list[str]]:
    if family == "crack":
        return (["可能与焊接残余应力、热影响区脆化或疲劳扩展有关。"], ["可能发生疲劳扩展", "可能降低局部承载能力"])
    if family == "lack_of_fusion":
        return (["可能与热输入不足、坡口清理或层间熔合不充分有关。"], ["有效承载截面可能降低", "缺口效应可能促进裂纹萌生"])
    if family == "pore":
        return (["可能与保护气体、湿度、污染物或气体逸出不足有关。"], ["孔洞聚集可能造成局部应力集中", "压力边界需关注泄漏风险"])
    if family == "inclusion":
        return (["可能与层间清理不足、焊渣或夹杂物卷入有关。"], ["可能形成局部应力集中", "循环载荷下可能成为裂纹萌生位置"])
    return (["现有证据不足以稳定追溯成因。"], ["若为真实不连续，可能造成局部应力集中或承载能力下降"])


def _risk_text(risk: dict[str, Any] | None, fallback_risks: list[str]) -> str:
    if not risk:
        return "风险未分级；" + "；".join(fallback_risks[:2])
    return f"辅助风险{risk.get('risk_level', '未评估')}，{risk.get('recommendation', '建议人工复核')}"


def _review_focus(family: str) -> list[str]:
    focus = ["核对本帧原始A扫、闸门及相邻通道响应。"]
    if family in {"crack", "lack_of_fusion"}:
        focus.append("复核线状响应及其与焊缝坡口、根部或层间位置的关系。")
    elif family == "pore":
        focus.append("复核区域边界与回波是否一致，不以圆度单独判定气孔。")
    elif family == "inclusion":
        focus.append("复核不规则或条带状响应是否与焊道位置一致。")
    return focus


def _region_number(candidate: dict[str, Any]) -> str:
    value = str(candidate.get("region_id") or candidate.get("candidate_id") or "")
    suffix = value.rsplit("_", 1)[-1]
    try:
        return str(int(suffix))
    except ValueError:
        return suffix or "-"


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _fmt(value: Any) -> str:
    number = _number(value)
    return "-" if number is None else f"{number:.2f}"
