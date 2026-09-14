from __future__ import annotations

from typing import Any

from .validated_result_service import validated_package


RISK_ORDER = {"低": 0, "中": 1, "高": 2, "严重": 3}
RISK_SCHEME_NAME = "PAUT缺陷辅助风险评价体系 v2.0"


RISK_RUBRIC = {
    "purpose": "用于项目内风险排序、复核优先级和报告草稿，不替代GB/T、ISO、ASME等正式验收标准。",
    "score_range": "0-15+",
    "levels": {
        "低": {"score": "0-2", "meaning": "未见需立即处置的异常，或异常线索较弱", "action": "按既定检验计划管理"},
        "中": {"score": "3-5", "meaning": "异常较明确，但检测条件或结果可信度仍需确认", "action": "针对异常区域复检"},
        "高": {"score": "6-8", "meaning": "单帧证据较强、形态可疑或位置敏感，可能影响使用安全", "action": "维修或工程完整性评估"},
        "严重": {"score": ">=8且满足严重门槛", "meaning": "平面型强异常，二维尺寸占比较大或位于敏感区域", "action": "暂停使用并由专业人员评估"},
    },
    "severe_gate": [
        "疑似裂纹、未熔合、未焊透等平面型缺陷",
        "团队结果信噪比较高或旧流程A扫响应较强",
        "二维长度占板厚>=25%，或靠近表面/底面/关键受力区域",
    ],
}


def assess_batch_risk(
    candidates: list[dict[str, Any]],
    frames: list[dict[str, Any]],
    auxiliary_image_findings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """给出可复现的辅助风险分级，不替代产品标准或工程完整性评定。"""

    thickness = _common_number(frames, "plate_thickness_mm")
    missing = _missing_conditions(frames, candidates, thickness)
    candidate_results = [
        _assess_candidate(candidate, thickness)
        for candidate in candidates
    ]

    quality_gate = _batch_quality_gate(frames)
    if candidate_results:
        overall = max(candidate_results, key=lambda item: RISK_ORDER[item["risk_level"]])
        risk_level = overall["risk_level"]
        recommendation = _recommendation(risk_level)
        reasons = _dedupe(
            reason
            for item in candidate_results
            if RISK_ORDER[item["risk_level"]] == RISK_ORDER[risk_level]
            for reason in item["reasons"]
        )[:8]
        potential_risks = _dedupe(
            risk for item in candidate_results for risk in item["potential_risks"]
        )[:8]
    else:
        risk_level = "低"
        recommendation = (
            "本次辅助检测未检出需记录异常，无需追加专项复检，按既定检验周期管理。"
            if quality_gate["passed"]
            else "当前数据不足以形成有效筛查结论，建议补充检测。"
        )
        reasons = [
            "检测流程完整结束，未形成需记录的异常候选。"
            if quality_gate["passed"]
            else "检测结果完整性或质量门控未通过，当前零记录不能作为有效筛查结论。"
        ]
        potential_risks = [] if quality_gate["passed"] else ["数据不完整可能增加漏检风险。"]

    if auxiliary_image_findings:
        reasons.append(f"另有{len(auxiliary_image_findings)}个图像提示缺少邻近A扫响应，需作为漏检或误检线索复核。")

    confidence = _batch_confidence(candidate_results, frames)
    disposition = _batch_disposition(candidates, candidate_results, frames, quality_gate, risk_level, confidence)
    return {
        "scheme": RISK_SCHEME_NAME,
        "rubric": RISK_RUBRIC,
        "risk_level": risk_level,
        "recommendation": recommendation,
        "confidence": confidence,
        "confidence_level": confidence,
        **disposition,
        "reasons": _dedupe(reasons),
        "potential_risks": potential_risks,
        "candidate_risks": candidate_results,
        "missing_conditions": missing,
        "decision_boundary": (
            "该等级表示软件对异常证据的复核和处置优先级，不是标准验收等级，也不构成自动判废。"
            "“维修”或“停用”均表示进入工程评估流程；最终决定应由具备资质的人员依据适用产品标准、"
            "校准记录、载荷和服役条件作出。"
        ),
        "basis": [
            "检测结果中的缺陷类别、信噪比和质量提示",
            "检测结果中的二维位置、尺寸及其相对板厚比例",
            "关键工程条件完整性与人工复核状态",
        ],
    }


def _batch_quality_gate(frames: list[dict[str, Any]]) -> dict[str, Any]:
    warnings: list[str] = []
    if not frames:
        return {"passed": False, "warnings": ["批次中没有已完成帧"]}
    for index, frame in enumerate(frames, start=1):
        team_result = validated_package(frame)
        if not team_result:
            warnings.append(f"第{index}帧缺少完整检测结果包")
            continue
        quality = team_result.get("quality") or {}
        if quality.get("status") != "可信" or quality.get("blocking"):
            warnings.append(f"第{index}帧结果质量为{quality.get('status') or '无法评价'}")
    return {"passed": not warnings, "warnings": warnings}


def _batch_disposition(
    candidates: list[dict[str, Any]],
    candidate_results: list[dict[str, Any]],
    frames: list[dict[str, Any]],
    quality_gate: dict[str, Any],
    risk_level: str,
    confidence: str,
) -> dict[str, Any]:
    if not quality_gate["passed"] and not candidate_results:
        return {
            "detection_state": "insufficient_quality",
            "review_requirement": "required",
            "recommended_action": "focused_recheck",
            "conclusion": "当前数据不足以形成有效筛查结论，建议补充检测。",
            "quality_gate": quality_gate,
        }
    if not candidate_results:
        return {
            "detection_state": "no_recordable_indication",
            "review_requirement": "not_required",
            "recommended_action": "routine_management",
            "conclusion": "本次辅助检测未检出需记录异常，无需追加专项复检，按既定检验周期管理。",
            "quality_gate": quality_gate,
        }
    statuses = [
        str(candidate.get("review_status") or (candidate.get("review") or {}).get("status") or "待复核")
        for candidate in candidates
    ]
    review_requirement = "completed" if statuses and all(status != "待复核" for status in statuses) else "required"
    action = {
        "低": "routine_management",
        "中": "focused_recheck",
        "高": "engineering_assessment",
        "严重": "suspend_and_assess",
    }[risk_level]
    conclusion = {
        "低": "已发现低风险异常线索，建议纳入常规跟踪并按既定检验计划管理。",
        "中": "已发现需要关注的异常线索，建议对相应区域开展针对性复检。",
        "高": "已发现高风险异常线索，建议开展维修或工程完整性评估。",
        "严重": "已发现严重风险线索，建议暂停使用并由专业人员开展评估。",
    }[risk_level]
    recommendation = {
        "低": "建议纳入常规跟踪并按既定检验周期管理",
        "中": "建议对异常区域开展针对性复检",
        "高": "建议开展维修或工程完整性评估",
        "严重": "建议暂停使用并由专业人员评估",
    }[risk_level]
    if review_requirement == "completed":
        if risk_level == "低":
            conclusion = "人工复核已完成，当前异常风险较低，建议按既定检验周期管理。"
            recommendation = "人工复核已完成，按既定检验周期管理"
        elif risk_level == "中":
            conclusion = "人工复核已完成，已记录需要关注的异常；建议按复核结论跟踪，必要时补充定量检测。"
            recommendation = "按复核结论跟踪，必要时补充定量检测"
    return {
        "detection_state": "indications_present",
        "review_requirement": review_requirement,
        "recommended_action": action,
        "recommendation": recommendation,
        "conclusion": conclusion,
        "quality_gate": quality_gate,
    }


def _assess_candidate(
    candidate: dict[str, Any],
    thickness_mm: float | None,
) -> dict[str, Any]:
    score = 0
    reasons: list[str] = []
    scoring_items: list[dict[str, Any]] = []
    signal_score = _number(candidate.get("ascan_score_median"))
    support = _number(candidate.get("ascan_support_median"))
    snr_db = _number(candidate.get("snr_db"))
    length = _number(candidate.get("corrected_length_mm"))
    if length is None:
        length = _number(candidate.get("diameter_mm"))
    response_span = _number(candidate.get("ascan_response_span_mm"))
    center = candidate.get("centroid_mm") or {}
    depth = _number(center.get("z"))
    defect_hint = _defect_hint(candidate)
    crack_like = any(term in defect_hint for term in ("裂纹", "未熔合", "未焊透", "线状", "平面"))

    scoring_items.append({
        "item": "帧间关系",
        "score": 0,
        "evidence": "帧与帧之间没有绝对空间关联，本体系不使用跨帧持续性加分",
    })
    reasons.append("当前仅按本帧校验后的二维检测结果评估，不推断跨帧连续缺陷。")

    if candidate.get("primary_evidence") in {"team_algorithm", "validated_team_result"}:
        reasons[-1] = "当前按单帧二维检测结果评估，不推断跨帧连续缺陷。"

    if signal_score is not None and signal_score >= 0.85:
        score += _add_score(scoring_items, "A扫响应强度", 2, f"中位评分{signal_score:.2f} >= 0.85")
        reasons.append(f"A扫候选中位评分为{signal_score:.2f}，属于当前算法中的强响应。")
    elif signal_score is not None and signal_score >= 0.60:
        score += _add_score(scoring_items, "A扫响应强度", 1, f"中位评分{signal_score:.2f} >= 0.60")
        reasons.append(f"A扫候选中位评分为{signal_score:.2f}，响应达到关注范围。")

    if signal_score is None and snr_db is not None:
        if snr_db >= 20:
            score += _add_score(scoring_items, "信噪比", 2, f"团队结果SNR={snr_db:.1f} dB")
            reasons.append(f"信噪比约{snr_db:.1f} dB，异常响应较清晰。")
        elif snr_db >= 10:
            score += _add_score(scoring_items, "信噪比", 1, f"团队结果SNR={snr_db:.1f} dB")
            reasons.append(f"信噪比约{snr_db:.1f} dB，异常响应需结合原始波形确认。")

    if support is not None and support >= 8:
        score += _add_score(scoring_items, "通道支撑", 2, f"中位通道支撑{support:.0f} >= 8")
        reasons.append(f"中位通道支撑为{support:.0f}，不是单通道孤立信号。")
    elif support is not None and support >= 4:
        score += _add_score(scoring_items, "通道支撑", 1, f"中位通道支撑{support:.0f} >= 4")
        reasons.append(f"中位通道支撑为{support:.0f}，具备一定通道一致性。")

    if crack_like:
        score += _add_score(scoring_items, "形态危险性", 2, f"形态线索偏向{defect_hint}")
        reasons.append(f"现有形态线索偏向{defect_hint}等平面型异常，需关注疲劳扩展可能性。")
    elif any(term in defect_hint for term in ("夹渣", "夹杂", "不规则")):
        score += _add_score(scoring_items, "形态危险性", 1, f"形态线索偏向{defect_hint}")
        reasons.append(f"现有形态线索偏向{defect_hint}，需关注局部应力集中。")
    elif any(term in defect_hint for term in ("气孔", "孔", "点状")):
        score += _add_score(scoring_items, "形态危险性", 1, f"形态线索偏向{defect_hint}")
        reasons.append(f"现有形态线索偏向{defect_hint}，需确认是否为孔洞聚集或孤立小孔。")

    geometry_value = length if length is not None else response_span
    geometry_name = "截面长度估计" if length is not None else "A扫响应范围"
    length_ratio = None
    if geometry_value is not None and thickness_mm and thickness_mm > 0:
        length_ratio = geometry_value / thickness_mm
        if length_ratio >= 0.25:
            score += _add_score(scoring_items, "尺寸占板厚比例", 2, f"{geometry_name}约为板厚的{length_ratio:.0%}")
            reasons.append(f"{geometry_name}约为板厚的{length_ratio:.0%}，二维影响范围较大。")
        elif length_ratio >= 0.10:
            score += _add_score(scoring_items, "尺寸占板厚比例", 1, f"{geometry_name}约为板厚的{length_ratio:.0%}")
            reasons.append(f"{geometry_name}约为板厚的{length_ratio:.0%}。")

    near_boundary = bool(
        depth is not None and thickness_mm and thickness_mm > 0
        and (depth / thickness_mm <= 0.10 or depth / thickness_mm >= 0.90)
    )
    if near_boundary:
        score += _add_score(scoring_items, "深度位置", 1, "靠近表面或底面10%厚度范围")
        reasons.append("候选靠近表面或底面敏感区域，应排除边界回波并重点复核。")
    if candidate.get("near_stressed_region") is True:
        score += _add_score(scoring_items, "工程敏感区域", 2, "工程元数据标记为关键区域")
        labels = "、".join(map(str, candidate.get("critical_region_labels") or []))
        reasons.append(
            f"工程元数据确认候选靠近{labels or '关键受力区域'}，应提高处置优先级。"
        )
    strong_evidence = (signal_score or 0) >= 0.85 or (support or 0) >= 8 or (snr_db or 0) >= 20
    severe_gate = crack_like and strong_evidence and (
        (length_ratio is not None and length_ratio >= 0.25) or near_boundary or candidate.get("near_stressed_region") is True
    )
    if severe_gate and score >= 8:
        level = "严重"
    elif score >= 6:
        level = "高"
    elif score >= 3:
        level = "中"
    else:
        level = "低"

    team_quality = candidate.get("team_quality") or {}
    if candidate.get("primary_evidence") in {"team_algorithm", "validated_team_result"}:
        confidence = "低" if team_quality.get("blocking") else (
            "中" if team_quality.get("status") in {"建议复核", "数据不足"} else "高"
        )
    elif candidate.get("primary_evidence") == "manual_review":
        confidence = "高"
    else:
        confidence = "中" if support is not None else "低"
    return {
        "candidate_id": candidate.get("candidate_id"),
        "risk_level": level,
        "score": score,
        "max_score_note": "分值用于项目内辅助排序，可能超过15；等级按阈值和严重门槛映射。",
        "scoring_items": scoring_items,
        "severe_gate_passed": severe_gate,
        "confidence": confidence,
        "recommendation": _recommendation(level),
        "defect_hint": defect_hint or "未分类异常",
        "reasons": reasons,
        "potential_risks": _potential_risks(defect_hint),
    }


def _recommendation(level: str) -> str:
    return {
        "低": "纳入常规跟踪，按既定检验计划管理",
        "中": "建议针对异常区域复检",
        "高": "建议开展维修或工程完整性评估",
        "严重": "建议暂停使用并由专业人员评估",
    }[level]


def _potential_risks(defect_hint: str) -> list[str]:
    if any(term in defect_hint for term in ("裂纹", "线状", "平面")):
        return ["在循环载荷下可能发生疲劳扩展。", "裂纹扩展可能导致局部承载能力下降或焊缝失效。"]
    if any(term in defect_hint for term in ("未熔合", "未焊透")):
        return ["有效承载截面和载荷传递能力可能降低。", "缺口效应可能促进疲劳裂纹萌生与扩展。"]
    if any(term in defect_hint for term in ("气孔", "孔")):
        return ["孔洞聚集可能造成局部应力集中。", "压力边界中若缺陷贯通或继续扩展，可能增加泄漏风险。"]
    if any(term in defect_hint for term in ("腐蚀", "减薄")):
        return ["截面减薄可能降低承载能力。", "压力边界中持续减薄可能增加泄漏风险。"]
    if any(term in defect_hint for term in ("夹渣", "夹杂")):
        return ["不规则夹杂可能形成局部应力集中。", "在循环载荷下可能成为疲劳裂纹萌生位置。"]
    return ["异常若为真实不连续，可能造成局部应力集中或承载能力下降。"]


def _defect_hint(candidate: dict[str, Any]) -> str:
    direct = str(candidate.get("class_name") or candidate.get("type") or "").strip()
    if direct not in {"", "未分类候选", "疑似缺陷", "图像候选", "待复核", "Unknown"}:
        return direct
    votes: dict[str, int] = {}
    for field in ("ascan_classifier_votes", "type_votes", "image_type_votes"):
        for name, count in (candidate.get(field) or {}).items():
            text = str(name)
            if text in {"", "未分类候选", "疑似缺陷", "图像候选"}:
                continue
            votes[text] = votes.get(text, 0) + int(count or 0)
    if votes:
        return max(votes, key=votes.get)
    return "未定型异常"


def _missing_conditions(
    frames: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    thickness_mm: float | None,
) -> list[str]:
    context = {}
    for frame in frames:
        context.update(frame.get("engineering_context") or {})
    checks = [
        (context.get("material"), "材料牌号及力学性能"),
        (thickness_mm, "可靠板厚或壁厚"),
        (context.get("load_condition"), "载荷类型、应力水平及循环次数"),
        (context.get("service_environment"), "服役环境、温度、腐蚀介质和压力条件"),
        (context.get("weld_geometry"), "焊缝形式、坡口和候选相对受力区域的位置"),
        (context.get("acceptance_level"), "检测等级、验收等级和适用产品标准"),
        (context.get("calibration_record"), "试块、灵敏度、增益及系统校准记录"),
    ]
    missing = [label for value, label in checks if value in (None, "", [], {})]
    if any(
        item.get("corrected_length_mm") is None
        and item.get("diameter_mm") is None
        and item.get("area_mm2") is None
        for item in candidates
    ):
        missing.append("部分候选缺少经验证的二维边界尺寸")
    missing.append("缺陷真实尺寸、取向及材料内部实际形貌")
    return _dedupe(missing)


def _coverage_is_confirmed(frames: list[dict[str, Any]]) -> bool:
    return bool(frames) and all(
        bool(
            (frame.get("ascan") or {}).get("coverage_complete")
            or (frame.get("analysis") or {}).get("ascan", {}).get("coverage_complete")
        )
        for frame in frames
    )


def _batch_confidence(candidate_results: list[dict[str, Any]], frames: list[dict[str, Any]]) -> str:
    if not candidate_results:
        return "低"
    values = [item["confidence"] for item in candidate_results]
    if "低" in values:
        return "低"
    if all(value == "高" for value in values) and len(frames) >= 3:
        return "高"
    return "中"


def _common_number(items: list[dict[str, Any]], key: str) -> float | None:
    values = {_number(item.get(key)) for item in items}
    values.discard(None)
    return next(iter(values)) if len(values) == 1 else None


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _dedupe(values) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if value))


def _add_score(items: list[dict[str, Any]], item: str, score: int, evidence: str) -> int:
    items.append({"item": item, "score": score, "evidence": evidence})
    return score
