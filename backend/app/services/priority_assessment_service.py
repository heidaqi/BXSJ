from __future__ import annotations

import re
from typing import Any


RISK_ORDER = {"低": 0, "中": 1, "高": 2, "严重": 3}


def build_priority_assessment(
    candidates: list[dict[str, Any]],
    frames: list[dict[str, Any]],
    risk_assessment: dict[str, Any],
    model_review: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build operator-facing priorities from frozen facts; the model may only polish notes."""
    frame_by_id = {str(frame.get("frame_id") or ""): frame for frame in frames}
    risk_by_id = {
        str(item.get("candidate_id") or ""): item
        for item in risk_assessment.get("candidate_risks") or []
        if isinstance(item, dict)
    }
    model_notes = _validated_model_notes(model_review)
    items: list[dict[str, Any]] = []
    for candidate in candidates:
        candidate_id = str(candidate.get("candidate_id") or candidate.get("defect_id") or "")
        risk = risk_by_id.get(candidate_id) or {}
        risk_level = str(risk.get("risk_level") or "低")
        frame = frame_by_id.get(str(candidate.get("frame_id") or ""), {})
        items.append({
            "location": _operator_location(frame, candidate),
            "defect_type": str(candidate.get("class_name") or candidate.get("type") or "未定型异常"),
            "risk_level": risk_level,
            "recommended_action": str(risk.get("recommendation") or risk_assessment.get("recommendation") or "按检测计划管理"),
            "review_status": str(candidate.get("review_status") or (candidate.get("review") or {}).get("status") or "待复核"),
            "operator_note": model_notes.get(candidate_id, ""),
            "_score": float(risk.get("score") or 0),
        })
    items.sort(key=lambda item: (RISK_ORDER.get(item["risk_level"], 0), item["_score"]), reverse=True)
    for item in items:
        item.pop("_score", None)

    urgent = [item for item in items if item["risk_level"] in {"严重", "高"}]
    attention = [item for item in items if item["risk_level"] == "中"]
    routine = [item for item in items if item["risk_level"] == "低"]
    if urgent:
        overall = f"本批次有{len(urgent)}处高优先级异常，应优先处理列表中的首项并开展工程评估。"
    elif attention:
        overall = f"本批次未见需要立即停用处置的异常，有{len(attention)}处需要重点关注并按建议复核。"
    elif routine:
        overall = f"本批次异常均为低风险线索，共{len(routine)}处，建议纳入常规跟踪。"
    else:
        overall = str(risk_assessment.get("conclusion") or "本批次未形成需记录的异常。")
    return {
        "overall_judgment": overall,
        "immediate_attention": urgent,
        "follow_up_attention": attention,
        "routine_observation": routine,
        "model_language_review_applied": bool(model_notes),
        "boundary": "处置优先级为辅助判断，不替代适用标准的验收结论或专业人员决定。",
    }


def _operator_location(frame: dict[str, Any], candidate: dict[str, Any]) -> str:
    receipt = frame.get("scan_receipt") or {}
    distance = _number(receipt.get("estimated_scan_distance_mm"))
    speed = _number(receipt.get("vehicle_speed_mm_s"))
    received_at = receipt.get("received_at") or frame.get("received_at")
    x_mm = _number(candidate.get("x_mm", (candidate.get("centroid_mm") or {}).get("x")))
    z_mm = _number(candidate.get("z_mm", (candidate.get("centroid_mm") or {}).get("z")))
    parts: list[str] = []
    if distance is not None and speed is not None and speed > 0:
        parts.append(f"车辆扫查路径约{distance:.2f} mm处")
    elif received_at:
        parts.append(f"扫查时间{received_at}")
    else:
        parts.append("当前检测帧")
    if x_mm is not None and z_mm is not None:
        parts.append(f"截面位置x={x_mm:.2f} mm、深度z={z_mm:.2f} mm")
    return "，".join(parts)


def _validated_model_notes(model_review: dict[str, Any] | None) -> dict[str, str]:
    notes: dict[str, str] = {}
    for item in (model_review or {}).get("items") or []:
        if not isinstance(item, dict):
            continue
        candidate_id = str(item.get("candidate_id") or "")
        note = re.sub(r"\s+", " ", str(item.get("operator_note") or "")).strip()
        if not candidate_id or not note or len(note) > 120:
            continue
        if re.search(r"\d", note) or any(term in note for term in ("合格", "判废", "确认报废", "无需检测")):
            continue
        notes[candidate_id] = note
    return notes


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None
