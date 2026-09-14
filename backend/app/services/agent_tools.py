from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.tools import tool

from .risk_assessment_service import assess_batch_risk
from .validated_result_service import (
    canonical_batch_candidates,
    validated_frame_count,
    validated_package,
)


TOOLSET_VERSION = "paut-controlled-tools-v2-validated"


@tool
def inspect_batch_evidence(frames: list[dict[str, Any]]) -> dict[str, Any]:
    """Check frame identity and deterministic result-package quality before reasoning."""
    warnings: list[str] = []
    frame_ids = [str(frame.get("frame_id") or "") for frame in frames]
    if not frames:
        warnings.append("批次中没有已完成帧")
    if any(not frame_id for frame_id in frame_ids):
        warnings.append("存在缺少frame_id的帧")
    if len(frame_ids) != len(set(frame_ids)):
        warnings.append("frame_id重复")
    quality_passed = bool(frames)
    minimum_quality_passed = bool(frames)
    for index, frame in enumerate(frames, start=1):
        team_result = validated_package(frame)
        if not team_result:
            quality_passed = False
            minimum_quality_passed = False
            warnings.append(f"第{index}帧缺少检测结果包")
            continue
        quality = team_result.get("quality") or {}
        if quality.get("status") != "可信" or quality.get("blocking"):
            quality_passed = False
            warnings.append(f"第{index}帧结果质量为{quality.get('status') or '无法评价'}")
        if quality.get("blocking"):
            minimum_quality_passed = False
    identity_passed = bool(frames) and all(frame_ids) and len(frame_ids) == len(set(frame_ids))
    return {
        "toolset_version": TOOLSET_VERSION,
        "frame_count": len(frames),
        "identity_passed": identity_passed,
        "quality_passed": quality_passed,
        "minimum_quality_passed": minimum_quality_passed,
        "passed": identity_passed and minimum_quality_passed,
        "warnings": list(dict.fromkeys(warnings)),
    }


@tool
def evaluate_batch_disposition(
    candidates: list[dict[str, Any]],
    frames: list[dict[str, Any]],
    auxiliary_image_findings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Calculate auditable confidence, risk, review requirement and recommended action."""
    return assess_batch_risk(candidates, frames, auxiliary_image_findings or [])


@tool
def prepare_report_content(
    batch: dict[str, Any],
    frames: list[dict[str, Any]],
    agent_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Freeze canonical report facts and determine whether a truthful report can be rendered."""
    candidates = _frame_candidates(frames)
    disposition = evaluate_batch_disposition.invoke({
        "candidates": candidates,
        "frames": frames,
        "auxiliary_image_findings": [],
    })
    evidence = inspect_batch_evidence.invoke({"frames": frames})
    from .scan_receipt_service import audit_scan_frames
    scan_audit = audit_scan_frames(frames)
    if not scan_audit["passed"]:
        evidence["passed"] = False
        evidence["warnings"].extend(scan_audit["errors"])
    blocking = not evidence["passed"]
    return {
        "toolset_version": TOOLSET_VERSION,
        "batch_id": batch.get("batch_id"),
        "frame_count": len(frames),
        "candidate_count": len(candidates),
        "validated_frame_count": validated_frame_count(frames),
        "disposition": disposition,
        "quality_gate": disposition.get("quality_gate") or {},
        "evidence_gate": evidence,
        "scan_audit": scan_audit,
        "report_ready": not blocking,
        "blocking_reasons": evidence["warnings"] if blocking else [],
    }


@tool
def render_report(
    batch: dict[str, Any],
    frames: list[dict[str, Any]],
    report_context: dict[str, Any],
    destination_path: str = "",
) -> dict[str, Any]:
    """Render a PDF only after an explicit API request and a successful evidence identity gate."""
    if not report_context.get("report_ready"):
        reasons = "；".join(report_context.get("blocking_reasons") or ["证据门控未通过"])
        raise ValueError(f"报告证据不完整：{reasons}")
    from .report_service import create_realtime_batch_report
    from .scan_receipt_service import audit_scan_frames
    scan_audit = audit_scan_frames(frames)
    if not scan_audit["passed"]:
        raise ValueError("报告采集记录审查失败：" + "；".join(scan_audit["errors"]))

    report_batch = dict(batch)
    report_batch["report_context"] = report_context
    path = create_realtime_batch_report(
        report_batch,
        frames,
        Path(destination_path) if destination_path else None,
    )
    return {
        "ok": True,
        "report_path": str(path),
        "toolset_version": TOOLSET_VERSION,
        "quality_gate": report_context.get("quality_gate") or {},
    }


def _frame_candidates(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return canonical_batch_candidates(frames)


CONTROLLED_AGENT_TOOLS = (
    inspect_batch_evidence,
    evaluate_batch_disposition,
    prepare_report_content,
    render_report,
)
