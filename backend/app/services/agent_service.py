from __future__ import annotations

import json
from typing import Any

import requests

from ..config import settings
from .knowledge_service import collect_local_knowledge, collect_reference_material
from .standard_service import assess_standard_applicability
from .validated_result_service import canonical_frame_candidates, validated_package


AGENT_OUTPUT_FIELDS = [
    "summary", "object_assessment", "standard_applicability", "evidence_chain",
    "missing_conditions", "reasoning", "suggestions", "risks", "report_notes", "sources",
    "global_defect_candidates", "fusion_candidates", "uncertainty", "review_suggestions", "report_draft",
    "guardrail_warnings", "defect_assessments", "auxiliary_image_findings",
    "risk_assessment", "risk_level", "recommended_action", "potential_risks",
    "similar_simulation_cases", "frame_analyses", "response_regions", "defect_instances",
    "priority_assessment",
]


def agent_is_configured() -> bool:
    return bool(settings.agent_enabled and settings.openai_api_key and settings.openai_base_url and settings.openai_model)


def analyze_job(job: dict[str, Any], images: list[dict[str, Any]], defects: list[dict[str, Any]]) -> dict[str, Any]:
    if not agent_is_configured():
        result = _normalize_agent_result({
            "configured": False,
            "summary": "Agent 未启用或未配置 API Key。",
            "suggestions": [],
            "risks": ["请在 .env 中配置 AGENT_ENABLED=true、OPENAI_API_KEY、OPENAI_BASE_URL 和 OPENAI_MODEL。"],
        }, [])
        result["configured"] = False
        return result

    references = collect_reference_material(job, defects)
    payload = _build_payload(job, images, defects, references)
    payload["standard_assessment"] = assess_standard_applicability({
        "object_name": job.get("inspected_object"), "project_name": job.get("project_name"),
        "client_name": job.get("client_name"), "technique": "工业检测图片",
    })
    response = requests.post(
        f"{settings.openai_base_url}/chat/completions",
        headers={
            "Authorization": f"Bearer {settings.openai_api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": settings.openai_model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是工业无损检测图像缺陷识别系统中的分析 Agent。"
                        "按对象识别、标准适用性、证据一致性、缺失验收条件、结论边界、复核动作的顺序推理。"
                        "只能引用standard_assessment中列出的标准身份和适用范围；普通联网搜索不得作为验收阈值来源。"
                        "图像模型框是候选证据，人工补录必须区分来源，未标定坐标不得解释为工件坐标。"
                        "你不替代持证检测人员下最终验收结论，不编造标准条款、等级或限值。"
                        "输出必须为JSON，字段为summary、object_assessment、standard_applicability、evidence_chain、"
                        "missing_conditions、reasoning、suggestions、risks、report_notes、sources。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False),
                },
            ],
            "temperature": 0.2,
        },
        timeout=60,
    )
    response.raise_for_status()
    message = response.json()["choices"][0]["message"]
    content = message.get("content") or message.get("reasoning_content") or ""
    try:
        parsed = json.loads(_strip_json_fence(content))
    except json.JSONDecodeError:
        parsed = {
            "summary": content,
            "reasoning": [],
            "suggestions": [],
            "risks": [],
            "report_notes": [],
            "sources": references.get("sources", []),
        }
    parsed = _normalize_agent_result(parsed, references.get("sources", []))
    parsed["configured"] = True
    return parsed


def analyze_realtime_frame(frame: dict[str, Any]) -> dict[str, Any]:
    """Explain one frame without recalculating its validated measurements."""

    if not agent_is_configured():
        references = collect_local_knowledge("PAUT A扫 DAS 焊缝 验收 校准")
        standard_assessment = assess_standard_applicability({
            "object_name": "PAUT实时检测焊缝", "technique": "自动PAUT FMC DAS",
            "plate_thickness_mm": frame.get("plate_thickness_mm"),
        })
        result = _normalize_agent_result({
            "configured": False,
            "status": "未配置",
            "summary": "Agent未启用或未配置API；本地联合分析结果仍可正常使用。",
            "standard_applicability": [f"{item['code']}：{item['status']}" for item in standard_assessment["candidates"]],
            "missing_conditions": standard_assessment["missing_acceptance_conditions"],
            "reasoning": [],
            "suggestions": ["配置Agent后可对本帧A扫、DAS和图像模型结果进行综合说明。"],
            "risks": [],
            "sources": references,
        }, references)
        result.update(configured=False, status="未配置")
        return result

    references = collect_local_knowledge("PAUT A扫 DAS 焊缝 验收 校准")
    standard_assessment = assess_standard_applicability({
        "object_name": "PAUT实时检测焊缝", "technique": "自动PAUT FMC DAS",
        "plate_thickness_mm": frame.get("plate_thickness_mm"),
    })
    package = validated_package(frame)
    if package is None:
        result = _normalize_agent_result({
            "configured": True,
            "status": "数据不足",
            "summary": "当前帧缺少校验后的检测结果，未调用大模型生成缺陷结论。",
            "suggestions": ["请先完成检测算法计算和结果校验。"],
            "sources": references,
        }, references)
        result.update(configured=True, status="数据不足")
        return result
    compact_frame = {
        "frame_id": frame.get("frame_id"),
        "acquisition": {
            "shape": frame.get("shape"),
            "sample_rate_hz": frame.get("sample_rate_hz"),
            "scan_receipt": frame.get("scan_receipt") or {},
            "velocity_mps": frame.get("velocity_mps"),
            "plate_thickness_mm": frame.get("plate_thickness_mm"),
        },
        "validated_result": {
            "schema_version": package.get("schema_version"),
            "algorithm_version": package.get("algorithm_version"),
            "quality": package.get("quality") or {},
            "defects": canonical_frame_candidates(frame),
        },
        "authoritative_local_references": references,
        "standard_assessment": standard_assessment,
    }
    response = requests.post(
        f"{settings.openai_base_url}/chat/completions",
        headers={"Authorization": f"Bearer {settings.openai_api_key}", "Content-Type": "application/json"},
        json={
            "model": settings.openai_model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是PAUT检测结果解释Agent。validated_result是缺陷类型、位置、峰值和尺寸的唯一来源。"
                        "不得重新计算、补齐或修改其中数值；缺失字段必须保持缺失。只能结合给出的权威资料解释"
                        "可能成因、风险和复核建议，不得编造标准条款、验收阈值或缺陷类型。"
                        "输出JSON字段summary、object_assessment、standard_applicability、evidence_chain、"
                        "missing_conditions、reasoning、suggestions、risks、report_notes、sources。"
                    ),
                },
                {"role": "user", "content": json.dumps(compact_frame, ensure_ascii=False)},
            ],
            "temperature": 0.1,
        },
        timeout=60,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"].get("content") or ""
    try:
        parsed = json.loads(_strip_json_fence(content))
    except json.JSONDecodeError:
        parsed = {"summary": content, "reasoning": [], "suggestions": [], "risks": [], "sources": references}
    parsed = _normalize_agent_result(parsed, references)
    parsed["standard_applicability"] = [
        f"{item['code']}：{item['status']}" for item in standard_assessment["candidates"]
    ]
    parsed["missing_conditions"] = list(dict.fromkeys(
        [*parsed.get("missing_conditions", []), *standard_assessment["missing_acceptance_conditions"]]
    ))
    parsed.update(configured=True, status="分析完成")
    return parsed


def analyze_realtime_batch(
    frames: list[dict[str, Any]],
    local_result: dict[str, Any],
    references: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Explain frozen team results; use the legacy evidence path only for old frames."""

    references = references or collect_local_knowledge("PAUT 自动 相控阵 A扫 DAS 多帧 裂纹 气孔 夹渣 未熔合 类型 走向 风险 等级 评分 验收 试块 校准")
    thicknesses = [frame.get("plate_thickness_mm") for frame in frames if frame.get("plate_thickness_mm") is not None]
    standard_assessment = assess_standard_applicability({
        "object_name": "PAUT实时检测焊缝", "technique": "自动PAUT FMC DAS",
        "plate_thickness_mm": thicknesses[0] if thicknesses and len(set(thicknesses)) == 1 else None,
    })
    if not agent_is_configured():
        result = _normalize_agent_result({
            "configured": False,
            "status": "未配置",
            "summary": local_result["summary"],
            "standard_applicability": [f"{item['code']}：{item['status']}" for item in standard_assessment["candidates"]],
            "missing_conditions": standard_assessment["missing_acceptance_conditions"],
            "reasoning": ["当前摘要由检测结果、质量检查和本地风险规则生成；帧间不做连续性推断。"],
            "suggestions": ["结合缺陷标注图、原始波形、质量提示和适用检测条件完成复核。"],
            "risks": local_result.get("potential_risks") or ["缺少适用标准和校准条件时，软件不能自动给出验收结论。"],
            "risk_assessment": local_result.get("risk_assessment") or {},
            "risk_level": local_result.get("risk_level"),
            "recommended_action": local_result.get("recommended_action"),
            "potential_risks": local_result.get("potential_risks") or [],
            "similar_simulation_cases": local_result.get("similar_simulation_cases") or [],
            "defect_assessments": local_result.get("defect_assessments") or [],
            "frame_analyses": local_result.get("frame_analyses") or [],
            "response_regions": local_result.get("response_regions") or [],
            "defect_instances": local_result.get("defect_instances") or [],
            "sources": references,
        }, references)
        result.update(configured=False, status="未配置")
        return result

    compact_frames = []
    for frame in frames:
        analysis = frame.get("analysis", {})
        team_result = validated_package(frame) or {}
        compact_team_defects = [
            {
                key: defect.get(key)
                for key in [
                    "defect_id", "class_name", "classification_method", "confidence",
                    "x_mm", "z_mm", "peak_x_mm", "peak_z_mm", "global_peak_db",
                    "corrected_length_mm", "diameter_mm", "area_mm2", "image_angle_deg",
                    "minor_axis_nominal_mm", "minor_axis_lower_mm", "minor_axis_upper_mm",
                    "area_lower_mm2", "area_upper_mm2", "slag_probability",
                    "snr_db", "quantification_status", "warning",
                ]
            }
            for defect in canonical_frame_candidates(frame)
        ]
        compact_frames.append(
            {
                "frame_id": frame.get("frame_id"),
                "source_group": frame.get("source_group"),
                "acquisition": {
                    "shape": frame.get("shape"),
                    "sample_rate_hz": frame.get("sample_rate_hz"),
                    "velocity_mps": frame.get("velocity_mps"),
                    "plate_thickness_mm": frame.get("plate_thickness_mm"),
                },
                "engineering_context": {
                    key: value
                    for key, value in (frame.get("engineering_context") or {}).items()
                    if key != "calibration"
                },
                "team_algorithm_result": {
                    "schema_version": team_result.get("schema_version"),
                    "algorithm_version": team_result.get("algorithm_version"),
                    "quality": team_result.get("quality"),
                    "defects": compact_team_defects,
                } if team_result else None,
                "review": frame.get("review"),
                "received_at": frame.get("received_at"),
                "scan_receipt": {
                    key: value for key, value in (frame.get("scan_receipt") or {}).items() if key != "files"
                },
            }
        )
    payload = {
        "analysis_rule": (
            "team_algorithm_result是校验后的类别、位置、峰值、尺寸和方向的唯一算法来源。"
            "模型不得重新计算、修改、合并或拆分检测记录，也不得根据文件名或任何校验前候选改写结论。"
            "任何缺失数值必须保持缺失，不得用校验前数值或语言模型估计值补齐。"
            "质量状态与风险等级由本地确定性规则生成，模型只能解释，不能修改。"
            "帧与帧之间没有绝对空间关联，不得推断跨帧连续长度、走向或同一缺陷轨迹。"
            "文件名真值不得进入分析结论，只能用于开发阶段离线验证。"
            "结合权威资料说明可能成因、潜在风险、缺失条件和复核动作，不要只复述数值。"
        ),
        "local_batch_assessment": _compact_local_assessment(local_result),
        "frames": compact_frames,
        "authoritative_local_references": references,
        "standard_assessment": standard_assessment,
    }
    response = requests.post(
        f"{settings.openai_base_url}/chat/completions",
        headers={"Authorization": f"Bearer {settings.openai_api_key}", "Content-Type": "application/json"},
        json={
            "model": settings.openai_model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是PAUT检测结果解释Agent，面向普通检测操作者用简洁中文输出。"
                        "上游检测结果是缺陷类别、物理坐标和二维定量值的唯一算法来源。"
                        "不得覆盖、重算、合并或拆分缺陷记录；人工复核结果优先于自动检测结果。"
                        "帧与帧之间没有绝对空间关联，不得使用跨帧连续性、跨帧长度或跨帧走向作为结论依据。"
                        "危险等级必须引用local_batch_assessment.risk_assessment和candidate_risks中的本地评分结果，"
                        "可以解释评分项，但不能改写风险等级、分值或处置建议。"
                        "不得把零缺陷记录写成工件无缺陷，不得编造类别、置信度、标准条款或验收阈值。"
                        "质量门控标记为结果冲突或无法评价时，必须明确提示先处理数据问题。"
                        "不得推断三维尺寸、跨帧轨迹、小车全局位置或工件绝对坐标。"
                        "必须保留本地风险等级对应的处置建议，用条件性措辞解释疲劳扩展、泄漏、承载下降、"
                        "腐蚀减薄或焊缝失效等潜在风险，不得将其写成已发生事实。"
                        "即使等级为严重，也必须说明不具备直接判废依据，建议停用待评估并由人员确认。"
                        "只输出JSON：summary为两至四句总体结论；defect_assessments为逐实例中文说明，字段仅含"
                        "candidate_id、indication、position、size_and_orientation、confidence、risk_note；"
                        "可保留一条brief_evidence和一条review_focus，不输出长篇算法过程；"
                        "risks、review_suggestions、missing_conditions均为简短中文列表；"
                        "report_draft仅含conclusion和review_focus。"
                        "另输出priority_assessment.items，每项只能包含candidate_id和operator_note。"
                        "operator_note只用于润色处置顺序，应为不含任何数字的简短操作句；不得新增位置、尺寸、"
                        "类别、风险等级、验收或判废结论。"
                    ),
                },
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            "temperature": 0.1,
        },
        timeout=90,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"].get("content") or ""
    try:
        parsed = json.loads(_strip_json_fence(content))
    except json.JSONDecodeError:
        parsed = {"summary": content, "reasoning": [], "suggestions": [], "risks": [], "sources": references}
    parsed = _normalize_agent_result(parsed, references)
    parsed["standard_applicability"] = [
        f"{item['code']}：{item['status']}" for item in standard_assessment["candidates"]
    ]
    parsed["missing_conditions"] = list(dict.fromkeys(
        [*parsed.get("missing_conditions", []), *standard_assessment["missing_acceptance_conditions"]]
    ))
    parsed.update(configured=True, status="分析完成")
    return parsed


def _compact_local_assessment(local_result: dict[str, Any]) -> dict[str, Any]:
    """Keep authoritative conclusions while removing compatibility/debug payloads."""
    candidates = local_result.get("fusion_candidates") or local_result.get("global_defect_candidates") or []
    compact_candidates = [
        {
            key: candidate.get(key)
            for key in [
                "candidate_id", "frame_id", "type", "class_name", "type_confidence",
                "centroid_mm", "length_mm", "width_mm", "diameter_mm", "area_mm2",
                "section_orientation_deg", "snr_db", "quantification_status", "warning",
                "review_status", "review_override_applied", "team_quality",
            ]
        }
        for candidate in candidates
        if isinstance(candidate, dict)
    ]
    return {
        "analysis_mode": local_result.get("analysis_mode"),
        "summary": local_result.get("summary"),
        "frame_count": local_result.get("frame_count"),
        "defects": compact_candidates,
        "risk_assessment": local_result.get("risk_assessment") or {},
        "defect_assessments": local_result.get("defect_assessments") or [],
        "missing_conditions": local_result.get("missing_conditions") or [],
        "similar_simulation_cases": local_result.get("similar_simulation_cases") or [],
    }


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    return [
        json.dumps(item, ensure_ascii=False) if isinstance(item, (dict, list)) else str(item)
        for item in values
        if item is not None
    ]


def _normalize_agent_result(parsed: dict[str, Any], fallback_sources: list[Any]) -> dict[str, Any]:
    result = {key: parsed.get(key) for key in AGENT_OUTPUT_FIELDS}
    result["summary"] = str(result.get("summary") or "未生成分析摘要")
    for key in ["object_assessment", "standard_applicability", "evidence_chain", "missing_conditions", "reasoning", "suggestions", "risks", "report_notes", "uncertainty", "review_suggestions", "guardrail_warnings"]:
        result[key] = _as_list(result.get(key))
    result["global_defect_candidates"] = result.get("global_defect_candidates") or []
    result["fusion_candidates"] = result.get("fusion_candidates") or result["global_defect_candidates"]
    result["frame_analyses"] = result.get("frame_analyses") or []
    result["response_regions"] = result.get("response_regions") or result.get("defect_instances") or result["fusion_candidates"]
    result["defect_instances"] = result.get("defect_instances") or result["fusion_candidates"]
    result["defect_assessments"] = result.get("defect_assessments") or []
    result["auxiliary_image_findings"] = result.get("auxiliary_image_findings") or []
    result["risk_assessment"] = result.get("risk_assessment") or {}
    result["risk_level"] = result.get("risk_level") or result["risk_assessment"].get("risk_level")
    result["recommended_action"] = result.get("recommended_action") or result["risk_assessment"].get("recommendation")
    result["potential_risks"] = _as_list(result.get("potential_risks"))
    result["similar_simulation_cases"] = result.get("similar_simulation_cases") or []
    result["report_draft"] = result.get("report_draft") or {}
    result["priority_assessment"] = result.get("priority_assessment") or {}
    # Sources are supplied by the controlled retrieval layer; never expose model-invented citations.
    result["sources"] = fallback_sources
    return result


def _strip_json_fence(content: str) -> str:
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()
    return text


def _build_payload(
    job: dict[str, Any],
    images: list[dict[str, Any]],
    defects: list[dict[str, Any]],
    references: dict[str, Any],
) -> dict[str, Any]:
    image_by_id = {image["id"]: image for image in images}
    compact_defects = []
    for defect in defects:
        image = image_by_id.get(defect["image_id"], {})
        compact_defects.append(
            {
                "image": image.get("original_name", ""),
                "area": image.get("area_label", ""),
                "class_name": defect.get("class_name", ""),
                "confidence": defect.get("confidence"),
                "source": defect.get("source"),
                "review_status": defect.get("review_status"),
                "pixel_center": [defect.get("center_x_px"), defect.get("center_y_px")],
                "physical_center_mm": [defect.get("center_x_mm"), defect.get("center_y_mm")],
                "physical_size_mm": [defect.get("width_mm"), defect.get("height_mm")],
                "calibrated": defect.get("center_x_mm") is not None and defect.get("center_y_mm") is not None,
                "suggestion": defect.get("suggestion", ""),
                "remark": defect.get("remark", ""),
            }
        )
    return {
        "language": settings.agent_language,
        "job": _job_context(job),
        "calibration": [
            {
                "image": image.get("original_name", "") if settings.agent_send_project_context else f"image_{index + 1}",
                "area": image.get("area_label", ""),
                "mm_per_pixel": image.get("mm_per_pixel"),
                "mm_per_pixel_x": image.get("mm_per_pixel_x"),
                "mm_per_pixel_y": image.get("mm_per_pixel_y"),
                "origin_px": [image.get("origin_x_px"), image.get("origin_y_px")],
                "origin_mm": [image.get("origin_x_mm"), image.get("origin_y_mm")],
                "rotation_deg": image.get("rotation_deg"),
                "weld_centerline_y_px": image.get("weld_centerline_y_px"),
                "method": image.get("calibration_method", ""),
                "note": image.get("calibration_note", ""),
            }
            for index, image in enumerate(images[:50])
        ],
        "counts": {
            "images": len(images),
            "defects": len(defects),
            "manual_defects": sum(1 for defect in defects if defect.get("source") == "manual"),
            "unreviewed": sum(1 for defect in defects if defect.get("review_status") == "待复核"),
        },
        "defects": compact_defects[:200],
        "external_references": references,
    }


def _job_context(job: dict[str, Any]) -> dict[str, Any]:
    if settings.agent_send_project_context:
        return {
            "id": job.get("id"),
            "batch_name": job.get("batch_name"),
            "project_name": job.get("project_name"),
            "client_name": job.get("client_name"),
            "inspected_object": job.get("inspected_object"),
            "status": job.get("status"),
        }
    return {
        "id": "redacted",
        "batch_name": "redacted",
        "project_name": "redacted",
        "client_name": "redacted",
        "inspected_object": "工业检测对象",
        "status": job.get("status"),
    }
