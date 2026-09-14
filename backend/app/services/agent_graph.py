from __future__ import annotations

import json
import math
import re
import threading
from collections import Counter
from pathlib import Path
from typing import Any, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from ..config import settings
from .agent_service import analyze_realtime_batch
from .agent_tools import evaluate_batch_disposition, inspect_batch_evidence
from .defect_interpretation_service import build_defect_assessments
from .knowledge_service import collect_local_knowledge
from .priority_assessment_service import build_priority_assessment
from .simulation_case_service import find_similar_cases
from .validated_result_service import canonical_frame_candidates, validated_package


AGENT_SCHEMA_VERSION = "paut-agent-v6-validated-results"


class AgentGraphState(TypedDict, total=False):
    batch_id: str
    frames: list[dict[str, Any]]
    local_result: dict[str, Any]
    evidence_package: dict[str, Any]
    alignment: dict[str, Any]
    frame_features: list[dict[str, Any]]
    frame_analyses: list[dict[str, Any]]
    response_regions: list[dict[str, Any]]
    defect_instances: list[dict[str, Any]]
    global_defect_candidates: list[dict[str, Any]]
    auxiliary_image_findings: list[dict[str, Any]]
    fusion_candidates: list[dict[str, Any]]
    risk_assessment: dict[str, Any]
    similar_simulation_cases: list[dict[str, Any]]
    defect_assessments: list[dict[str, Any]]
    knowledge: list[dict[str, str]]
    model_result: dict[str, Any]
    output: dict[str, Any]
    guardrail_warnings: list[str]
    errors: list[str]
    completed_nodes: list[str]
    tool_audit: list[dict[str, Any]]


class PautAgentGraph:
    """PAUT批次证据图。数值聚合由本地节点完成，LLM只解释冻结证据。"""

    NODE_ORDER = (
        "collect_window_evidence",
        "validate_alignment",
        "extract_frame_features",
        "cluster_global_defects",
            "match_simulation_cases",
            "assess_risk",
            "interpret_defect_types",
            "retrieve_authoritative_knowledge",
        "reason_defect_hypotheses",
        "consistency_guard",
        "draft_window_summary",
        "audit_report_evidence",
    )

    def __init__(self, checkpoint_root: Path | None = None) -> None:
        self.checkpoint_root = checkpoint_root or settings.output_dir / "agent" / "checkpoints"
        self.checkpoint_root.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.Lock()
        builder = StateGraph(AgentGraphState)
        nodes = {
            "collect_window_evidence": self._collect_window_evidence,
            "validate_alignment": self._validate_alignment,
            "extract_frame_features": self._extract_frame_features,
            "cluster_global_defects": self._cluster_global_defects,
            "match_simulation_cases": self._match_simulation_cases,
            "assess_risk": self._assess_risk,
            "interpret_defect_types": self._interpret_defect_types,
            "retrieve_authoritative_knowledge": self._retrieve_authoritative_knowledge,
            "reason_defect_hypotheses": self._reason_defect_hypotheses,
            "consistency_guard": self._consistency_guard,
            "draft_window_summary": self._draft_window_summary,
            "audit_report_evidence": self._audit_report_evidence,
        }
        for name, node in nodes.items():
            builder.add_node(name, self._checkpointed(name, node))
        builder.add_edge(START, self.NODE_ORDER[0])
        for current, following in zip(self.NODE_ORDER, self.NODE_ORDER[1:]):
            builder.add_edge(current, following)
        builder.add_edge(self.NODE_ORDER[-1], END)
        self.graph = builder.compile(checkpointer=InMemorySaver())

    def run(
        self,
        batch_id: str,
        frames: list[dict[str, Any]],
        local_result: dict[str, Any] | None = None,
        *,
        resume: bool = False,
    ) -> dict[str, Any]:
        state: AgentGraphState = {
            "batch_id": batch_id,
            "frames": frames,
            "local_result": local_result or {},
            "errors": [],
            "completed_nodes": [],
            "tool_audit": [],
        }
        if resume:
            saved = self._load_checkpoint(batch_id)
            if saved:
                saved["frames"] = frames
                saved["local_result"] = local_result or saved.get("local_result", {})
                state.update(saved)
        result = self.graph.invoke(state, {"configurable": {"thread_id": batch_id}})
        self._save_checkpoint(batch_id, result)
        return result.get("output") or result.get("model_result") or {}

    def _checkpointed(self, name: str, node):
        def wrapped(state: AgentGraphState) -> dict[str, Any]:
            if name in state.get("completed_nodes", []):
                return {}
            update = node(state)
            completed = [*state.get("completed_nodes", []), name]
            update["completed_nodes"] = completed
            self._save_checkpoint(state["batch_id"], {**state, **update})
            return update

        return wrapped

    @staticmethod
    def _audit_report_evidence(state: AgentGraphState) -> dict[str, Any]:
        from .agent_tools import prepare_report_content
        output = dict(state.get("output") or {})
        frames_by_id = {frame.get("frame_id"): frame for frame in state.get("frames") or []}
        output["frame_analyses"] = [
            {**item,
             "received_at": frames_by_id.get(item.get("frame_id"), {}).get("received_at"),
             "scan_receipt": {key: value for key, value in (frames_by_id.get(item.get("frame_id"), {}).get("scan_receipt") or {}).items() if key != "files"}}
            for item in output.get("frame_analyses") or []
        ]
        audit = prepare_report_content.invoke({
            "batch": {"batch_id": state.get("batch_id")},
            "frames": state.get("frames") or [], "agent_result": output,
        })
        output["report_audit"] = audit
        output["scan_summary"] = audit.get("scan_audit")
        output["guardrail_warnings"] = list(dict.fromkeys(
            list(output.get("guardrail_warnings") or []) + audit.get("blocking_reasons", [])))
        return {"output": output}

    def _checkpoint_path(self, batch_id: str) -> Path:
        safe_id = re.sub(r"[^0-9A-Za-z_-]", "_", batch_id)
        return self.checkpoint_root / f"{safe_id}.json"

    def _save_checkpoint(self, batch_id: str, state: dict[str, Any]) -> None:
        path = self._checkpoint_path(batch_id)
        temporary = path.with_suffix(".json.tmp")
        with self._write_lock:
            temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            temporary.replace(path)

    def _load_checkpoint(self, batch_id: str) -> dict[str, Any] | None:
        try:
            value = json.loads(self._checkpoint_path(batch_id).read_text(encoding="utf-8-sig"))
            return value if isinstance(value, dict) else None
        except (OSError, json.JSONDecodeError):
            return None

    @staticmethod
    def _collect_window_evidence(state: AgentGraphState) -> dict[str, Any]:
        frames = state.get("frames", [])
        package_frames = []
        for index, frame in enumerate(frames):
            image = frame.get("image") or {}
            analysis = frame.get("analysis") or {}
            package_frames.append({
                "index": index,
                "frame_id": frame.get("frame_id"),
                "source_group": frame.get("source_group"),
                "received_at": frame.get("received_at"),
                "scan_receipt": {key: value for key, value in (frame.get("scan_receipt") or {}).items() if key != "files"},
                "source_path": frame.get("source_path"),
                "shape": frame.get("shape"),
                "plate_thickness_mm": frame.get("plate_thickness_mm"),
                "velocity_mps": frame.get("velocity_mps"),
                "engineering_context": frame.get("engineering_context") or {},
                "das": {
                    "url": image.get("url"),
                    "annotated_url": image.get("annotated_url"),
                    "peak_x_mm": image.get("peak_x_mm"),
                    "peak_z_mm": image.get("peak_z_mm"),
                    "peak_is_measured": image.get("peak_is_measured", False),
                    "x_range_mm": image.get("x_range_mm"),
                    "z_range_mm": image.get("z_range_mm"),
                },
                "ascan": analysis.get("ascan") or {},
                "ascan_classifier": analysis.get("ascan_classifier") or {},
                "team_result": validated_package(frame),
                "review": frame.get("review"),
            })
        return {"evidence_package": {
            "batch_id": state["batch_id"],
            "frame_count": len(package_frames),
            "frames": package_frames,
            "evidence_boundary": (
                "每个frame_id只绑定本帧原始超声证据和校验后的检测结果；帧与帧之间没有绝对空间关联，"
                "不得推断跨帧连续缺陷或同一缺陷轨迹。仿真文件名真值仅用于开发验证。"
            ),
        }}

    @staticmethod
    def _validate_alignment(state: AgentGraphState) -> dict[str, Any]:
        frames = state.get("evidence_package", {}).get("frames", [])
        ids = [frame.get("frame_id") for frame in frames]
        warnings: list[str] = []
        if not frames:
            warnings.append("批次没有可分析帧。")
        if any(not frame_id for frame_id in ids):
            warnings.append("存在缺失frame_id的数据，无法完整追溯。")
        if len(ids) != len(set(ids)):
            warnings.append("frame_id重复，批次证据可能发生错配。")
        team_frames = [frame for frame in frames if frame.get("team_result")]
        missing = {
            "das": [frame.get("frame_id") for frame in frames if not frame.get("das", {}).get("url")],
            "ascan": [frame.get("frame_id") for frame in frames if not frame.get("ascan")],
            "validated_result": [frame.get("frame_id") for frame in frames if not frame.get("team_result")],
        }
        if missing["das"]:
            warnings.append(f"{len(missing['das'])}帧缺少DAS图像。")
        if missing["ascan"]:
            warnings.append(f"{len(missing['ascan'])}帧缺少A扫特征。")
        if missing["validated_result"]:
            warnings.append(f"{len(missing['validated_result'])}帧缺少校验后的检测结果，未用于缺陷结论。")
        blocking_team_frames = [
            frame.get("frame_id") for frame in team_frames
            if (frame.get("team_result", {}).get("quality") or {}).get("blocking")
        ]
        for frame in team_frames:
            quality = frame.get("team_result", {}).get("quality") or {}
            for issue in quality.get("issues") or []:
                warnings.append(f"{frame.get('frame_id')}：{issue.get('message')}")
        if team_frames:
            primary_ok = (
                len(team_frames) == len(frames)
                and not blocking_team_frames
                and len(ids) == len(set(ids))
                and all(ids)
            )
        else:
            primary_ok = False
        evidence_gate = inspect_batch_evidence.invoke({"frames": state.get("frames", [])})
        warnings.extend(evidence_gate.get("warnings") or [])
        return {
            "alignment": {"ok": primary_ok and evidence_gate.get("identity_passed", False), "missing": missing},
            "guardrail_warnings": list(dict.fromkeys(warnings)),
            "tool_audit": [
                *state.get("tool_audit", []),
                {"tool": "inspect_batch_evidence", "result": evidence_gate},
            ],
        }

    @staticmethod
    def _extract_frame_features(state: AgentGraphState) -> dict[str, Any]:
        features: list[dict[str, Any]] = []
        for frame in state.get("evidence_package", {}).get("frames", []):
            das = frame.get("das") or {}
            features.append({
                "frame_id": frame.get("frame_id"),
                "received_at": frame.get("received_at"),
                "scan_receipt": frame.get("scan_receipt") or {},
                "frame_index": frame.get("index"),
                "source_group": frame.get("source_group"),
                "das_peak": {
                    "x_mm": _number(das.get("peak_x_mm")),
                    "z_mm": _number(das.get("peak_z_mm")),
                    "measured": bool(das.get("peak_is_measured")),
                },
                "team_result": frame.get("team_result"),
                "validated_candidates": canonical_frame_candidates(frame),
                "review": frame.get("review") or {},
            })
        return {"frame_features": features}

    @staticmethod
    def _cluster_global_defects(state: AgentGraphState) -> dict[str, Any]:
        team_features = [item for item in state.get("frame_features", []) if item.get("team_result")]
        if team_features:
            candidates: list[dict[str, Any]] = []
            frame_analyses: list[dict[str, Any]] = []
            for feature in team_features:
                frame_candidates = list(feature.get("validated_candidates") or [])
                candidates.extend(frame_candidates)
                defect_types = list(dict.fromkeys(
                    str(candidate.get("type") or "待复核") for candidate in frame_candidates
                ))
                frame_type = defect_types[0] if len(defect_types) == 1 else (
                    "、".join(defect_types) if defect_types else "未形成缺陷记录"
                )
                frame_analyses.append({
                    "frame_id": feature.get("frame_id"),
                    "received_at": feature.get("received_at"),
                    "scan_receipt": feature.get("scan_receipt") or {},
                    "frame_index": feature.get("frame_index"),
                    "source_group": feature.get("source_group"),
                    "analysis_source": "team_algorithm",
                    "frame_type": frame_type,
                    "frame_type_confidence": "检测结果",
                    "defect_count": len(frame_candidates),
                    "response_regions": frame_candidates,
                    "defect_instances": frame_candidates,
                    "quality": (feature.get("team_result") or {}).get("quality") or {},
                    "summary": (feature.get("team_result") or {}).get("summary") or {},
                })
            local = dict(state.get("local_result") or {})
            local.update({
                "analysis_mode": "检测结果质量审查与解释",
                "frame_count": len(team_features),
                "frame_analyses": frame_analyses,
                "response_regions": candidates,
                "defect_instances": candidates,
                "global_defect_candidates": candidates,
                "fusion_candidates": candidates,
                "auxiliary_image_findings": [],
                "summary": f"本批次共形成{len(candidates)}条疑似缺陷记录，已完成质量检查。",
            })
            return {
                "frame_analyses": frame_analyses,
                "response_regions": candidates,
                "defect_instances": candidates,
                "global_defect_candidates": candidates,
                "fusion_candidates": candidates,
                "auxiliary_image_findings": [],
                "local_result": local,
            }
        local = dict(state.get("local_result") or {})
        local.update({
            "analysis_mode": "校验结果缺失",
            "frame_count": len(state.get("frame_features", [])),
            "frame_analyses": [],
            "response_regions": [],
            "defect_instances": [],
            "global_defect_candidates": [],
            "fusion_candidates": [],
            "auxiliary_image_findings": [],
            "summary": "当前缺少校验后的检测结果，未使用校验前候选生成缺陷结论。",
        })
        return {
            "frame_analyses": [],
            "response_regions": [],
            "defect_instances": [],
            "global_defect_candidates": [],
            "fusion_candidates": [],
            "auxiliary_image_findings": [],
            "local_result": local,
        }

    @staticmethod
    def _match_simulation_cases(state: AgentGraphState) -> dict[str, Any]:
        cases = find_similar_cases(
            state.get("fusion_candidates") or state.get("global_defect_candidates", []),
            state.get("evidence_package", {}).get("frames", []),
        )
        local = dict(state.get("local_result") or {})
        local["similar_simulation_cases"] = cases
        return {"similar_simulation_cases": cases, "local_result": local}

    @staticmethod
    def _assess_risk(state: AgentGraphState) -> dict[str, Any]:
        risk = evaluate_batch_disposition.invoke({
            "candidates": state.get("fusion_candidates") or state.get("global_defect_candidates", []),
            "frames": state.get("evidence_package", {}).get("frames", []),
            "auxiliary_image_findings": state.get("auxiliary_image_findings", []),
        })
        local = dict(state.get("local_result") or {})
        local.update({
            "risk_assessment": risk,
            "risk_level": risk["risk_level"],
            "recommended_action": risk["recommended_action"],
            "recommended_action_text": risk["recommendation"],
            "potential_risks": risk["potential_risks"],
            "missing_conditions": risk["missing_conditions"],
        })
        return {
            "risk_assessment": risk,
            "local_result": local,
            "tool_audit": [
                *state.get("tool_audit", []),
                {"tool": "evaluate_batch_disposition", "result": {
                    "detection_state": risk.get("detection_state"),
                    "review_requirement": risk.get("review_requirement"),
                    "risk_level": risk.get("risk_level"),
                    "recommended_action": risk.get("recommended_action"),
                }},
            ],
        }

    @staticmethod
    def _interpret_defect_types(state: AgentGraphState) -> dict[str, Any]:
        assessments = build_defect_assessments(
            state.get("fusion_candidates") or state.get("global_defect_candidates", []),
            state.get("risk_assessment", {}),
            state.get("similar_simulation_cases", []),
        )
        local = dict(state.get("local_result") or {})
        local["defect_assessments"] = assessments
        local["defect_type_summary"] = _type_summary(assessments)
        return {"defect_assessments": assessments, "local_result": local}

    @staticmethod
    def _retrieve_authoritative_knowledge(state: AgentGraphState) -> dict[str, Any]:
        return {"knowledge": collect_local_knowledge(
            "PAUT 焊缝 裂纹 气孔 夹渣 未熔合 位置 尺寸 信噪比 质量告警 风险 疲劳 泄漏 承载 复核 标准 校准"
        )}

    @staticmethod
    def _reason_defect_hypotheses(state: AgentGraphState) -> dict[str, Any]:
        if not state.get("alignment", {}).get("ok"):
            local = state.get("local_result", {})
            return {"model_result": {
                "configured": False,
                "status": "数据质量未通过",
                "summary": "结果包存在缺失、冲突或对应关系问题，已停止大模型推理，请先完成数据复核。",
                "global_defect_candidates": state.get("global_defect_candidates", []),
                "fusion_candidates": state.get("fusion_candidates", []),
                "frame_analyses": state.get("frame_analyses", []),
                "response_regions": state.get("response_regions", []),
                "defect_instances": state.get("defect_instances", []),
                "defect_assessments": state.get("defect_assessments", []),
                "risk_assessment": state.get("risk_assessment", {}),
                "risk_level": state.get("risk_assessment", {}).get("risk_level"),
                "recommended_action": state.get("risk_assessment", {}).get("recommendation"),
                "potential_risks": state.get("risk_assessment", {}).get("potential_risks", []),
                "missing_conditions": local.get("missing_conditions") or [],
                "uncertainty": ["结果质量门控未通过，当前不生成大模型扩展结论。"],
                "review_suggestions": ["先处理结果包质量提示，再重新启动Agent分析。"],
                "sources": state.get("knowledge", []),
                "guardrail_warnings": state.get("guardrail_warnings", []),
            }}
        try:
            result = analyze_realtime_batch(
                state.get("frames", []),
                state.get("local_result", {}),
                references=state.get("knowledge", []),
            )
            return {"model_result": result}
        except Exception as exc:  # API失败时仍输出本地结果
            local = state.get("local_result", {})
            error_message = str(exc)
            return {
                "model_result": {
                    "configured": True,
                    "status": "大模型调用失败，本地摘要可用",
                    "agent_error": error_message,
                    "summary": local.get("summary") or "外部Agent调用失败，已保留本地证据聚合结果。",
                    "global_defect_candidates": state.get("global_defect_candidates", []),
                    "fusion_candidates": state.get("fusion_candidates", []),
                    "frame_analyses": state.get("frame_analyses", []),
                    "response_regions": state.get("response_regions", []),
                    "defect_instances": state.get("defect_instances", []),
                    "defect_assessments": state.get("defect_assessments", []),
                    "auxiliary_image_findings": state.get("auxiliary_image_findings", []),
                    "risk_assessment": state.get("risk_assessment", {}),
                    "risk_level": state.get("risk_assessment", {}).get("risk_level"),
                    "recommended_action": state.get("risk_assessment", {}).get("recommendation"),
                    "potential_risks": state.get("risk_assessment", {}).get("potential_risks", []),
                    "similar_simulation_cases": state.get("similar_simulation_cases", []),
                    "evidence_chain": ["团队结果包校验", "质量门控", "风险规则", "权威知识检索"],
                    "missing_conditions": state.get("risk_assessment", {}).get("missing_conditions", []),
                    "uncertainty": [f"外部模型未完成推理：{error_message}"],
                    "review_suggestions": [
                        "复核疑似缺陷位置与原始DAS图、A扫响应是否对应。",
                        "结合校准记录、工件信息和适用验收标准确认处置结论。",
                    ],
                    "sources": state.get("knowledge", []),
                    "guardrail_warnings": [],
                },
                "errors": [*state.get("errors", []), str(exc)],
            }

    @staticmethod
    def _consistency_guard(state: AgentGraphState) -> dict[str, Any]:
        result = dict(state.get("model_result") or {})
        warnings = list(state.get("guardrail_warnings") or [])
        frames = state.get("frame_features", [])
        summary = str(result.get("summary") or "")
        summary = summary.replace("未发现任何缺陷", "未形成明确缺陷结论")
        summary = summary.replace("确认无缺陷", "当前证据未形成缺陷确认结论")
        result["summary"] = summary or state.get("local_result", {}).get("summary")
        result["global_defect_candidates"] = state.get("global_defect_candidates", [])
        result["fusion_candidates"] = state.get("fusion_candidates", result["global_defect_candidates"])
        result["frame_analyses"] = state.get("frame_analyses", [])
        result["response_regions"] = state.get("response_regions", [])
        result["defect_instances"] = state.get("defect_instances", result["fusion_candidates"])
        result["defect_assessments"] = state.get("defect_assessments", [])
        result["auxiliary_image_findings"] = state.get("auxiliary_image_findings", [])
        risk = state.get("risk_assessment") or {}
        result["risk_assessment"] = risk
        result["risk_level"] = risk.get("risk_level")
        result["confidence_level"] = risk.get("confidence_level") or risk.get("confidence")
        result["detection_state"] = risk.get("detection_state")
        result["review_requirement"] = risk.get("review_requirement")
        result["recommended_action"] = risk.get("recommended_action")
        result["recommended_action_text"] = risk.get("recommendation")
        result["conclusion"] = risk.get("conclusion")
        result["quality_gate"] = risk.get("quality_gate") or {}
        result["potential_risks"] = risk.get("potential_risks", [])
        result["priority_assessment"] = build_priority_assessment(
            state.get("fusion_candidates") or state.get("global_defect_candidates", []),
            state.get("frames") or [],
            risk,
            result.get("priority_assessment") if isinstance(result.get("priority_assessment"), dict) else None,
        )
        result["similar_simulation_cases"] = state.get("similar_simulation_cases", [])
        result["missing_conditions"] = list(dict.fromkeys([
            *_string_list(result.get("missing_conditions")),
            *_string_list(risk.get("missing_conditions")),
        ]))
        warnings.append("辅助风险等级由本地可审计规则生成，不是产品标准验收等级，也不构成自动判废。")
        result["guardrail_warnings"] = list(dict.fromkeys(warnings + list(result.get("guardrail_warnings") or [])))
        result.setdefault("uncertainty", [])
        result["uncertainty"] = _string_list(result["uncertainty"])
        result["uncertainty"].extend([
            "当前只评估校验结果给出的x-z垂直截面二维位置和定量值，不推断三维尺寸或空间倾角。",
            "帧与帧之间没有绝对空间关联，批次汇总不代表同一缺陷的连续长度、走向或扩展轨迹。",
        ])
        result["uncertainty"] = list(dict.fromkeys(result["uncertainty"]))
        if risk.get("detection_state") == "no_recordable_indication":
            result["summary"] = risk.get("conclusion")
            result["review_suggestions"] = ["按既定检验周期管理并保留本次检测记录。"]
        elif risk.get("detection_state") == "insufficient_quality":
            result["summary"] = risk.get("conclusion")
            result["review_suggestions"] = ["检查缺失数据和质量提示，补充检测后重新分析。"]
        elif risk.get("review_requirement") == "completed":
            result["review_suggestions"] = ["人工复核已完成，请按确认结果和辅助风险等级执行后续处置。"]
        else:
            result.setdefault("review_suggestions", result.get("suggestions") or [])
        result["tool_audit"] = state.get("tool_audit", [])
        return {"model_result": result, "guardrail_warnings": result["guardrail_warnings"]}

    @staticmethod
    def _draft_window_summary(state: AgentGraphState) -> dict[str, Any]:
        result = dict(state.get("model_result") or {})
        result["agent_schema_version"] = AGENT_SCHEMA_VERSION
        result.setdefault("summary", state.get("local_result", {}).get("summary") or "未生成摘要")
        result.setdefault("evidence_chain", [])
        result["evidence_chain"] = _string_list(result["evidence_chain"])
        result["evidence_chain"].extend([
            f"结果对应关系：{'完整' if state.get('alignment', {}).get('ok') else '存在缺项'}",
            f"参与分析的数据帧：{len(state.get('frame_features', []))}帧",
            f"团队缺陷记录：{len(state.get('response_regions') or state.get('defect_instances', []))}条",
        ])
        result["evidence_chain"] = list(dict.fromkeys(result["evidence_chain"]))
        draft = dict(result.get("report_draft") or {})
        draft.setdefault("title", "PAUT检测结果辅助分析草稿")
        draft.setdefault("summary", result["summary"])
        draft["conclusion"] = result.get("conclusion") or result["summary"]
        if result.get("review_requirement") == "not_required":
            draft["review_focus"] = "无需追加专项复检，按既定检验周期管理。"
        draft["response_region_count"] = len(state.get("response_regions") or state.get("defect_instances", []))
        draft["risk_level"] = state.get("risk_assessment", {}).get("risk_level")
        draft["recommended_action"] = state.get("risk_assessment", {}).get("recommendation")
        draft["conclusion_boundary"] = "仅供检测人员复核，不构成自动验收或缺陷定性结论。"
        result["report_draft"] = draft
        return {"output": result}


def _number(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    return [str(item) for item in values if item is not None]


def _type_summary(assessments: list[dict[str, Any]]) -> str:
    if not assessments:
        return "未形成可解释的缺陷类型假设。"
    counts = Counter(str(item.get("probable_type") or "未分类异常") for item in assessments)
    parts = [f"{name}{count}个" for name, count in counts.most_common()]
    return "疑似类型分布：" + "，".join(parts) + "。"
