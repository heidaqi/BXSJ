from __future__ import annotations

import logging
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from .paut.models import ImagingConfig
from .paut.realtime import RealtimeConfig, RealtimeService, realtime_service
from .config import settings
from .services.agent_tools import prepare_report_content, render_report
from .services.realtime_cache_service import realtime_cache_service
from .services.frame_record_service import frame_record_service
from .services.validated_result_service import validated_package


router = APIRouter(prefix="/api/realtime", tags=["PAUT实时处理"])
logger = logging.getLogger(__name__)


def _compact_frame_record(record: dict[str, Any]) -> dict[str, Any]:
    """Bounded review-record payload（镜像帧级 compact）：去重复包、截断候选，完整证据仍在磁盘。

    帧记录是帧的深拷贝并增补复核包装，重复携带 team_result 与 analysis.ascan.features；
    此处与 compact_frame 保持一致的上限，避免濒危内存被大 JSON 响应拖垮。
    """
    compact = RealtimeService.compact_frame(record)
    ascan_block = dict(compact.get("ascan") or {})
    features = dict(ascan_block.get("features") or {})
    candidates = list(features.get("candidates") or [])
    features["candidates"] = candidates[:100]
    features["truncated"] = len(candidates) > 100
    ascan_block["features"] = features
    compact["ascan"] = ascan_block
    return compact


def _compact_batch_list_item(batch: dict[str, Any]) -> dict[str, Any]:
    """批量列表视图：保留列表 UI 用到的字段，去掉体积大的 Agent 详细载荷（完整详情走 /batches/{id}）。"""
    item = dict(batch)
    result = dict(item.get("result") or {})
    agent = dict(result.get("agent") or {})
    result["agent"] = {
        key: agent[key]
        for key in ("configured", "status", "summary", "guardrail_warnings", "frame_analyses")
        if key in agent
    }
    item["result"] = result
    return item


class RealtimeConfigRequest(BaseModel):
    source_path: str = Field(description="包含 tx_N 子目录的A扫数据目录")
    adapter: str = "comsol_text_folder"
    profile: str = "comsol_16x16_das_m"
    interval_seconds: float = Field(default=0.0, ge=0.0, le=3600)
    simulation_enabled: bool = False
    vehicle_speed_mm_s: float = Field(default=0, ge=0, le=10000)
    velocity_mps: float = Field(default=5900.0, gt=0)
    sample_rate_hz: float = Field(default=50_000_000.0, gt=0)
    plate_thickness_mm: float = Field(default=40.0, gt=0)
    tx_positions_mm: list[float] | None = None
    rx_positions_mm: list[float] | None = None
    background_reference_path: str | None = None
    background_scale: float = Field(default=1.0, ge=0.0, le=2.0)
    tofd_tx: float | None = None
    tofd_rx: float | None = None
    x_min_mm: float = 20.0
    x_max_mm: float = 80.0
    z_min_mm: float = 0.0
    z_max_mm: float = 40.0
    pixel_step_mm: float = Field(default=0.2, gt=0)
    dynamic_range_db: float = Field(default=20.0, gt=0)
    gaussian_smoothing_sigma_mm: float = Field(default=0.4, ge=0)
    data_type: str = "paut"


class FocusInspectionRequest(BaseModel):
    x_mm: float
    z_mm: float = Field(ge=0)
    top_k: int = Field(default=8, ge=1, le=32)


class RealtimeReviewRequest(BaseModel):
    status: str
    reviewer: str = Field(default="", max_length=80)
    note: str = Field(default="", max_length=1000)


class ReportGenerateRequest(BaseModel):
    destination_path: str | None = None


@router.get("/status")
def get_realtime_status() -> dict[str, Any]:
    return realtime_service.status()


@router.get("/cache/status")
def get_realtime_cache_status() -> dict[str, Any]:
    return realtime_cache_service.status()


@router.post("/cache/cleanup")
def cleanup_realtime_cache() -> dict[str, Any]:
    return realtime_cache_service.run_once()

@router.post("/config")
def configure_realtime(payload: RealtimeConfigRequest) -> dict[str, Any]:
    try:
        positions = payload.tx_positions_mm or []
        default_tofd_tx = min(positions) if positions else 30.0
        default_tofd_rx = max(positions) if positions else 63.0
        return realtime_service.configure(
            RealtimeConfig(
                source_path=payload.source_path,
                data_type="paut",
                adapter=payload.adapter,
                profile=payload.profile,
                interval_seconds=payload.interval_seconds,
                simulation_enabled=payload.simulation_enabled,
                vehicle_speed_mm_s=payload.vehicle_speed_mm_s,
                velocity_mps=payload.velocity_mps,
                sample_rate_hz=payload.sample_rate_hz,
                plate_thickness_mm=payload.plate_thickness_mm,
                tx_positions_mm=payload.tx_positions_mm,
                rx_positions_mm=payload.rx_positions_mm,
                background_reference_path=payload.background_reference_path,
                background_scale=payload.background_scale,
                tofd_enabled=False,
                tofd_tx=payload.tofd_tx if payload.tofd_tx is not None else default_tofd_tx,
                tofd_rx=payload.tofd_rx if payload.tofd_rx is not None else default_tofd_rx,
                imaging=ImagingConfig(
                    x_min_mm=payload.x_min_mm,
                    x_max_mm=payload.x_max_mm,
                    z_min_mm=payload.z_min_mm,
                    z_max_mm=payload.z_max_mm,
                    pixel_step_mm=payload.pixel_step_mm,
                    dynamic_range_db=payload.dynamic_range_db,
                    gaussian_smoothing_sigma_mm=payload.gaussian_smoothing_sigma_mm,
                ),
            )
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/start")
def start_realtime() -> dict[str, Any]:
    try:
        return realtime_service.start()
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/stop")
def stop_realtime() -> dict[str, Any]:
    return realtime_service.stop()


@router.get("/frames/latest")
def get_latest_frame() -> dict[str, Any]:
    frame = realtime_service.latest()
    if frame is None:
        raise HTTPException(status_code=404, detail="尚无已完成的实时帧")
    return realtime_service.compact_frame(frame)


@router.get("/frames")
def list_realtime_frames(limit: int = 50) -> dict[str, Any]:
    frames = realtime_service.list_frames(max(1, min(limit, 50)))
    return {"items": [realtime_service.compact_frame(frame) for frame in frames]}


@router.get("/frames/{frame_id}")
def get_realtime_frame(frame_id: str) -> dict[str, Any]:
    frame = realtime_service.get_frame(frame_id)
    if frame is None:
        raise HTTPException(status_code=404, detail="实时帧不存在")
    return frame


@router.get("/frames/{frame_id}/team-result")
def get_realtime_team_result(frame_id: str) -> dict[str, Any]:
    """Return the canonical team package used by review, Agent, and reports."""
    frame = realtime_service.get_frame(frame_id)
    if frame is None:
        raise HTTPException(status_code=404, detail="帧不存在")
    package = validated_package(frame)
    if not package:
        raise HTTPException(status_code=404, detail="该帧没有通过校验的检测结果，可能来自旧版兼容流程")
    return package


@router.get("/sessions")
def list_realtime_sessions(limit: int = 30) -> dict[str, Any]:
    return {"items": realtime_service.list_sessions(max(1, min(limit, 100)))}


@router.get("/sessions/{session_id}")
def get_realtime_session(session_id: str) -> dict[str, Any]:
    session = realtime_service.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="实时处理批次不存在")
    return session


@router.get("/sessions/{session_id}/frames")
def get_realtime_session_frames(session_id: str) -> dict[str, Any]:
    frames = realtime_service.get_session_frames(session_id)
    if frames is None:
        raise HTTPException(status_code=404, detail="实时处理批次不存在")
    return {"items": [realtime_service.compact_frame(frame) for frame in (frames or [])[-50:]]}


@router.get("/frames/{frame_id}/record")
def get_realtime_frame_record(frame_id: str) -> dict[str, Any]:
    record = frame_record_service.get_frame_record(frame_id, realtime_service.get_frame)
    if record is None:
        raise HTTPException(status_code=404, detail="实时帧不存在")
    return _compact_frame_record(record)


@router.get("/frames/{frame_id}/waveform")
def get_realtime_waveform(frame_id: str, tx: int = 0, rx: int = 0, max_points: int = 1200) -> dict[str, Any]:
    try:
        return realtime_service.get_waveform(frame_id, tx, rx, max(100, min(max_points, 5000)))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/frames/{frame_id}/focus")
def inspect_realtime_focus(frame_id: str, payload: FocusInspectionRequest) -> dict[str, Any]:
    try:
        return realtime_service.inspect_focus(frame_id, payload.x_mm, payload.z_mm, payload.top_k)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/frames/{frame_id}/review")
def update_realtime_review(frame_id: str, payload: RealtimeReviewRequest) -> dict[str, Any]:
    try:
        return realtime_service.update_review(frame_id, payload.status, payload.reviewer, payload.note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/batches")
def create_realtime_batch() -> dict[str, Any]:
    try:
        return realtime_service.create_session_batch()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc


@router.get("/batches")
def list_realtime_batches(limit: int = 30) -> dict[str, Any]:
    items = realtime_service.batch_manager.list(max(1, min(limit, 100)))
    return {"items": [_compact_batch_list_item(batch) for batch in items]}


@router.get("/validation/comsol16")
def get_comsol16_validation() -> dict[str, Any]:
    path = settings.app_data_dir / "validation" / "comsol_16x16_validation.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="尚未生成16发16收批量验证结果")
    try:
        import json

        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"验证结果读取失败：{exc}") from exc


@router.get("/batches/{batch_id}")
def get_realtime_batch(batch_id: str) -> dict[str, Any]:
    batch = realtime_service.batch_manager.get(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="分析批次不存在")
    return batch


@router.get("/batches/{batch_id}/records")
def get_realtime_batch_records(batch_id: str) -> dict[str, Any]:
    batch = realtime_service.batch_manager.get(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="分析批次不存在")
    records = frame_record_service.get_batch_frame_records(
        batch_id,
        list(batch.get("frame_ids") or []),
        realtime_service.get_frame,
    )
    return {
        "batch_id": batch_id,
        "items": [_compact_frame_record(record) for record in records],
        "count": len(records),
    }


@router.post("/batches/{batch_id}/retry")
def retry_realtime_batch(batch_id: str) -> dict[str, Any]:
    try:
        return realtime_service.batch_manager.retry(batch_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc


@router.post("/batches/{batch_id}/report")
def generate_realtime_batch_report(
    batch_id: str,
    payload: ReportGenerateRequest | None = None,
) -> dict[str, Any]:
    batch = realtime_service.batch_manager.get(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="分析批次不存在")
    job_id = None
    try:
        from .database import connect
        with connect() as conn:
            row = conn.execute(
                "SELECT id FROM jobs WHERE source_type = 'realtime' AND source_batch_id = ?",
                (batch_id,),
            ).fetchone()
        if row:
            job_id = row["id"]
            realtime_service.sync_batch_reviews_from_job(batch_id, job_id)
    except Exception:
        # 报告生成不能因为复核同步失败而中断；后续帧表仍会显示已有快照。
        logger.exception("生成实时报告前同步人工复核失败: batch_id=%s", batch_id)
    frames = frame_record_service.get_batch_frame_records(
        batch_id,
        list(batch["frame_ids"]),
        realtime_service.get_frame,
        job_id=job_id,
    )
    if len(frames) != len(batch["frame_ids"]):
        raise HTTPException(status_code=409, detail="批次中存在缺失帧，无法保证报告证据对应关系")
    try:
        destination = payload.destination_path if payload and payload.destination_path else ""
        stored_result = batch.get("result") or {}
        evidence_revision = int(batch.get("evidence_revision") or 0)
        agent_revision_value = batch.get("agent_revision")
        agent_revision = int(
            agent_revision_value
            if agent_revision_value is not None
            else (0 if stored_result.get("agent") else -1)
        )
        agent_result = (
            stored_result.get("agent")
            if agent_revision == evidence_revision
            else {}
        ) or stored_result.get("local") or {}
        report_context = prepare_report_content.invoke({
            "batch": batch,
            "frames": frames,
            "agent_result": agent_result,
        })
        rendered = render_report.invoke({
            "batch": batch,
            "frames": frames,
            "report_context": report_context,
            "destination_path": destination,
        })
        report_path = Path(rendered["report_path"])
        realtime_service.batch_manager.set_report(batch_id, report_path)
        realtime_service.batch_manager.update(
            batch_id,
            report_tool_audit={
                "toolset_version": report_context.get("toolset_version"),
                "quality_gate": report_context.get("quality_gate"),
                "evidence_gate": report_context.get("evidence_gate"),
            },
        )
        if job_id:
            from .database import connect
            with connect() as conn:
                conn.execute(
                    "INSERT INTO audit_logs (entity_type, entity_id, action, detail, created_at) VALUES (?, ?, ?, ?, ?)",
                    (
                        "job", job_id, "agent_report_tools",
                        json.dumps({
                            "batch_id": batch_id,
                            "toolset_version": report_context.get("toolset_version"),
                            "quality_gate": report_context.get("quality_gate"),
                        }, ensure_ascii=False),
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    ),
                )
        report_url = None if destination else f"/outputs/realtime_reports/{report_path.name}"
        return {
            "ok": True,
            "report_url": report_url,
            "report_path": str(report_path),
            "quality_gate": report_context.get("quality_gate"),
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"报告参数无效：{exc}") from exc
    except OSError as exc:
        raise HTTPException(status_code=507, detail=f"报告写入失败：{exc}") from exc


@router.websocket("/ws")
async def realtime_websocket(websocket: WebSocket) -> None:
    await websocket.accept()
    queue = realtime_service.subscribe()
    await websocket.send_json({"type": "status", "status": realtime_service.status()})
    try:
        while True:
            await websocket.send_json(await queue.get())
    except WebSocketDisconnect:
        pass
    finally:
        realtime_service.unsubscribe(queue)

@router.post("/config/job/{job_id}")
def configure_from_job(job_id: str) -> dict[str, Any]:
    """将实时监控配置为使用已上传的 PAUT 数据"""
    from .database import connect
    from pathlib import Path
    from .config import settings
    
    with connect() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not job:
            raise HTTPException(status_code=404, detail="任务不存在")
    
    raw_dir = settings.upload_dir / job_id / "paut_raw"
    if not raw_dir.exists():
        raise HTTPException(status_code=400, detail="该任务尚未上传PAUT数据")
    
    # 使用默认配置
    config = RealtimeConfig(
        source_path=str(raw_dir),
        data_type="paut",
        tofd_enabled=False,
        interval_seconds=0.0,
        target_job_id=job_id,
    )
    try:
        result = realtime_service.configure(config)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    
    # 同时更新前端状态
    return {
        "ok": True,
        "job_id": job_id,
        "source_path": str(raw_dir),
        "status": result,
    }
