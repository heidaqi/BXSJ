from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..config import settings


def load_standard_catalog() -> list[dict[str, Any]]:
    path = settings.resource_dir / "knowledge" / "cn_paut_standard_catalog.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return []
    return payload.get("standards", [])


def assess_standard_applicability(context: dict[str, Any]) -> dict[str, Any]:
    text = " ".join(str(context.get(key) or "") for key in ["object_name", "project_name", "client_name"]).lower()
    technique = str(context.get("technique") or "").lower()
    thickness = _float_or_none(context.get("plate_thickness_mm"))
    is_paut = "paut" in technique or "相控阵" in technique or "相控阵" in text
    is_automatic = any(term in technique + text for term in ["自动", "实时", "编码器", "fmc", "das"])
    is_weld = "焊" in text or context.get("assume_weld", False)
    is_pressure = any(term in text for term in ["承压", "压力容器", "压力管道", "锅炉"])
    material = str(context.get("material") or "").lower()
    full_penetration = context.get("full_penetration")
    candidates: list[dict[str, Any]] = []
    for standard in load_standard_catalog():
        code = standard["code"]
        status = "支撑性引用"
        reasons: list[str] = []
        missing: list[str] = []
        if code == "GB/T 40733-2021" and is_paut and is_automatic and is_weld:
            status, reasons = "方法候选", ["对象为焊缝", "流程为自动/实时PAUT"]
        elif code == "GB/T 40734-2021" and is_paut and is_weld and (thickness is None or thickness >= 6):
            status, reasons = "验收候选", ["对象为焊缝PAUT", "板厚未提供或不小于6mm"]
        elif code in {"GB/T 43320-2023", "GB/T 44051-2024"} and is_paut and is_weld and thickness is not None and 3.2 <= thickness <= 8.0:
            status, reasons = ("薄壁方法候选" if code == "GB/T 43320-2023" else "薄壁验收候选"), ["板厚处于3.2mm至8.0mm"]
        elif code == "NB/T 47013.15-2021" and is_paut and is_pressure:
            status, reasons = "行业方法候选", ["检测对象描述包含承压设备场景"]
        elif code == "GB/T 11345-2023" and is_paut:
            status, reasons = "非直接适用", ["当前为PAUT，标准公开范围主要为手工常规UT"]
        elif code in {"GB/T 32563-2016", "GB/T 41114-2021", "GB/T 42399.1-2023/GB/T 42399.2-2023/GB/T 42399.3-2023", "GB/T 12604.1-2020"}:
            reasons = ["提供通用方法、校准、设备性能或术语支撑"]
        else:
            continue
        if "验收候选" in status:
            if not material:
                missing.append("材料牌号或材料类别")
            if full_penetration is None:
                missing.append("是否为全熔透焊缝")
            missing.extend(["项目指定验收等级", "灵敏度与试块校准记录", "指示长度/高度/幅度评定数据"])
        candidates.append({
            "code": code, "name": standard["name"], "role": standard["role"], "status": status,
            "reasons": reasons, "missing_conditions": list(dict.fromkeys(missing)), "scope": standard["scope"],
            "agent_rule": standard["agent_rule"], "url": standard["url"],
        })
    priority = {"薄壁方法候选": 0, "薄壁验收候选": 1, "方法候选": 2, "验收候选": 3, "行业方法候选": 4, "支撑性引用": 5, "非直接适用": 6}
    candidates.sort(key=lambda item: priority.get(item["status"], 9))
    all_missing = list(dict.fromkeys(item for candidate in candidates for item in candidate["missing_conditions"]))
    return {
        "context": {"is_paut": is_paut, "is_automatic": is_automatic, "is_weld": is_weld, "is_pressure_equipment": is_pressure, "plate_thickness_mm": thickness},
        "candidates": candidates,
        "missing_acceptance_conditions": all_missing,
        "acceptance_ready": bool(candidates) and not all_missing and any("验收候选" in item["status"] for item in candidates),
        "conclusion_boundary": "当前资料不足以自动给出标准验收等级或合格性结论。" if all_missing else "仍需按标准原文和批准的检测工艺由持证人员评定。",
    }


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
