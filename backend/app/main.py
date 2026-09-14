from __future__ import annotations
from .paut_api import router as paut_router   # 新增
import json
import os
import shutil
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel

from .config import settings
from .database import connect, ensure_storage, init_db, public_url_for, rows_to_dicts
from .services.adapter_service import normalize_calibration, to_physical_bbox
from .services.archive_service import create_job_archive
from .services.agent_service import agent_is_configured, analyze_job
from .services.image_annotation_service import draw_annotated_image
from .services.export_service import write_defects_csv
from .services.report_service import create_job_report
from .services.validated_result_service import validated_package
from .realtime_api import router as realtime_router
from .paut.realtime import realtime_service
from .services.realtime_cache_service import realtime_cache_service
from .app_api import router as app_router
from .tofd_api import router as tofd_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    realtime_cache_service.start()
    from .services.matlab_service import close_matlab_service

    try:
        yield
    finally:
        realtime_cache_service.stop()
        close_matlab_service()


app = FastAPI(title="工业图像缺陷智能检测系统", version="0.1.0", lifespan=lifespan)
app.include_router(realtime_router)
app.include_router(paut_router)   # 新增
app.include_router(app_router)
app.include_router(tofd_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url, "http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ensure_storage()
app.mount("/uploads", StaticFiles(directory=str(settings.upload_dir)), name="uploads")
app.mount("/outputs", StaticFiles(directory=str(settings.output_dir)), name="outputs")


class DefectUpdate(BaseModel):
    class_name: str | None = None
    review_status: str | None = None
    suggestion: str | None = None
    remark: str | None = None
    x_min: float | None = None
    y_min: float | None = None
    x_max: float | None = None
    y_max: float | None = None


class DefectCreate(BaseModel):
    class_name: str = "人工标注缺陷"
    confidence: float = 1.0
    x_min: float
    y_min: float
    x_max: float
    y_max: float
    review_status: str = "已确认"
    suggestion: str = ""
    remark: str = ""


class BatchDefectReviewUpdate(BaseModel):
    defect_ids: list[str]
    review_status: str


class CalibrationUpdate(BaseModel):
    scan_area: str = ""
    probe_or_channel: str = ""
    image_note: str = ""
    mm_per_pixel: float | None = None
    calibration_method: str = "未标定"
    calibration_note: str = ""


class JobCalibrationUpdate(CalibrationUpdate):
    source_image_id: str
    point_a_x: float | None = None
    point_a_y: float | None = None
    point_b_x: float | None = None
    point_b_y: float | None = None
    scale_distance_mm: float | None = None


class MetadataApplyResult(BaseModel):
    matched: int
    unmatched: list[str]
    invalid: list[str]


class ReportGenerateRequest(BaseModel):
    destination_path: str | None = None


class ExportGenerateRequest(BaseModel):
    destination_path: str | None = None


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _refresh_realtime_job_evidence(job_id: str, reason: str) -> dict[str, Any] | None:
    """Update job review state, synchronize frame records and invalidate stale analysis."""
    with connect() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not job:
            return None
        counts = conn.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN review_status = '待复核' THEN 1 ELSE 0 END) AS pending
            FROM defects WHERE job_id = ?
            """,
            (job_id,),
        ).fetchone()
        total = int(counts["total"] or 0)
        pending = int(counts["pending"] or 0)
        status = "待复核" if pending else ("已复核" if total else "无需复核")
        conn.execute("UPDATE jobs SET status = ?, report_path = '', updated_at = ? WHERE id = ?", (status, now(), job_id))
        job_data = dict(job)
    if job_data.get("source_type") != "realtime" or not job_data.get("source_batch_id"):
        return {"job_status": status}
    return realtime_service.invalidate_batch_analysis(
        str(job_data["source_batch_id"]),
        job_id,
        reason,
    )


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "env": settings.app_env,
        "analysis_source": "validated_paut",
    }


@app.get("/api/system/status")
def system_status() -> dict[str, Any]:
    init_db()
    path_checks = {
        "项目目录": settings.app_data_dir.parent,
        "数据目录": settings.app_data_dir,
        "上传目录": settings.upload_dir,
        "输出目录": settings.output_dir,
        "报告目录": settings.output_dir / "reports",
        "归档目录": settings.output_dir / "archives",
    }
    paths = []
    for label, path in path_checks.items():
        paths.append(
            {
                "label": label,
                "path": str(path),
                "exists": path.exists(),
                "writable": _is_writable(path),
            }
        )

    with connect() as conn:
        counts = {
            "jobs": conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0],
            "images": conn.execute("SELECT COUNT(*) FROM images").fetchone()[0],
            "defects": conn.execute("SELECT COUNT(*) FROM defects").fetchone()[0],
            "audit_logs": conn.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0],
        }

    return {
        "ok": all(item["exists"] and item["writable"] for item in paths if item["label"] != "项目目录"),
        "app_env": settings.app_env,
        "database": {
            "url": settings.database_url,
            "path": str(settings.database_path),
            "exists": settings.database_path.exists(),
            "size_bytes": settings.database_path.stat().st_size if settings.database_path.exists() else 0,
        },
        "agent": {
            "enabled": settings.agent_enabled,
            "configured": agent_is_configured(),
            "base_url": settings.openai_base_url,
            "model": settings.openai_model,
            "send_project_context": settings.agent_send_project_context,
            "web_search_enabled": settings.web_search_enabled,
            "web_search_send_project_context": settings.web_search_send_project_context,
            "web_search_provider": settings.web_search_provider,
        },
        "paths": paths,
        "counts": counts,
    }


@app.get("/api/metadata/template")
def metadata_template() -> dict[str, Any]:
    return {
        "scan_area": "焊缝A区",
        "probe_or_channel": "PAUT通道1",
        "image_note": "当前图片内坐标，不包含小车全局位置",
        "mm_per_pixel": 0.05,
        "calibration_method": "手动比例",
        "calibration_note": "如果没有比例尺，请把 mm_per_pixel 设为 null，系统只输出像素坐标。",
    }


@app.post("/api/jobs", include_in_schema=False)
async def create_job(
    files: Annotated[list[UploadFile], File(description="图片文件")],
    batch_name: Annotated[str, Form()] = "未命名批次",
    client_name: Annotated[str, Form()] = "",
    project_name: Annotated[str, Form()] = "",
    inspected_object: Annotated[str, Form()] = "",
    inspector: Annotated[str, Form()] = "",
    reviewer: Annotated[str, Form()] = "",
    approver: Annotated[str, Form()] = "",
    confidence: Annotated[float, Form()] = settings.yolo_confidence,
) -> dict[str, Any]:
    raise HTTPException(status_code=410, detail="图片模型检测流程已停用，请从实时监控选择 PAUT 原始数据")
    init_db()
    image_files = [f for f in files if _is_image(f.filename or "")]
    if not image_files:
        raise HTTPException(status_code=400, detail="请上传至少一张图片")

    job_id = uid("job")
    job_dir = settings.upload_dir / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    meta_by_stem: dict[str, dict[str, Any]] = {}

    for uploaded in files:
        filename = Path(uploaded.filename or "").name
        if filename.lower().endswith(".json"):
            path = job_dir / filename
            await _save_upload(uploaded, path)
            try:
                metadata = json.loads(path.read_text(encoding="utf-8-sig"))
                meta_by_stem[path.stem] = metadata if isinstance(metadata, dict) else {}
            except Exception:
                meta_by_stem[path.stem] = {}

    with connect() as conn:
        conn.execute(
            """
            INSERT INTO jobs
            (id, batch_name, client_name, project_name, inspected_object, inspector,
             reviewer, approver, confidence, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                batch_name,
                client_name,
                project_name,
                inspected_object,
                inspector,
                reviewer,
                approver,
                confidence,
                "已上传",
                now(),
                now(),
            ),
        )
        conn.execute(
            "INSERT INTO audit_logs (entity_type, entity_id, action, detail, created_at) VALUES (?, ?, ?, ?, ?)",
            ("job", job_id, "create", f"images={len(image_files)}", now()),
        )

        for uploaded in image_files:
            filename = Path(uploaded.filename or "image").name
            image_id = uid("img")
            stored_path = job_dir / f"{image_id}_{filename}"
            await _save_upload(uploaded, stored_path)
            with Image.open(stored_path) as image:
                width, height = image.size
            calibration = normalize_calibration(meta_by_stem.get(Path(filename).stem, {}))
            conn.execute(
                """
                INSERT INTO images
                (id, job_id, original_name, stored_path, width, height, area_label,
                 scan_area, probe_or_channel, image_note,
                 mm_per_pixel, mm_per_pixel_x, mm_per_pixel_y, origin_x_px, origin_y_px,
                 origin_x_mm, origin_y_mm, rotation_deg, weld_centerline_y_px,
                 coordinate_system, coordinate_direction, calibration_method, calibration_note, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    image_id,
                    job_id,
                    filename,
                    str(stored_path),
                    width,
                    height,
                    calibration["area_label"],
                    calibration["scan_area"],
                    calibration["probe_or_channel"],
                    calibration["image_note"],
                    calibration["mm_per_pixel"],
                    calibration["mm_per_pixel_x"],
                    calibration["mm_per_pixel_y"],
                    calibration["origin_x_px"],
                    calibration["origin_y_px"],
                    calibration["origin_x_mm"],
                    calibration["origin_y_mm"],
                    calibration["rotation_deg"],
                    calibration["weld_centerline_y_px"],
                    calibration["coordinate_system"],
                    calibration["coordinate_direction"],
                    calibration["calibration_method"],
                    calibration["calibration_note"],
                    now(),
                ),
            )

    return get_job(job_id)


@app.post("/api/jobs/{job_id}/metadata")
async def upload_job_metadata(job_id: str, files: Annotated[list[UploadFile], File(description="JSON 元数据文件")]) -> dict[str, Any]:
    job = _fetch_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    if not files:
        raise HTTPException(status_code=400, detail="请上传至少一个 JSON 元数据文件")

    metadata_dir = settings.upload_dir / job_id / "metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    metadata_by_stem: dict[str, dict[str, Any]] = {}
    invalid: list[str] = []

    for uploaded in files:
        filename = Path(uploaded.filename or "").name
        if not filename.lower().endswith(".json"):
            invalid.append(filename or "未命名文件")
            continue
        path = metadata_dir / filename
        await _save_upload(uploaded, path)
        try:
            metadata = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            invalid.append(filename)
            continue
        if isinstance(metadata, dict) and "images" in metadata and isinstance(metadata["images"], list):
            for item in metadata["images"]:
                if isinstance(item, dict):
                    key = str(item.get("image") or item.get("filename") or item.get("name") or "").strip()
                    if key:
                        metadata_by_stem[Path(key).stem] = item
        elif isinstance(metadata, dict):
            metadata_by_stem[path.stem] = metadata
        else:
            invalid.append(filename)

    matched, unmatched = _apply_metadata_to_images(job_id, metadata_by_stem)
    with connect() as conn:
        conn.execute(
            "INSERT INTO audit_logs (entity_type, entity_id, action, detail, created_at) VALUES (?, ?, ?, ?, ?)",
            ("job", job_id, "metadata_upload", f"matched={matched}, unmatched={len(unmatched)}, invalid={len(invalid)}", now()),
        )
    result = get_job(job_id)
    result["metadata_result"] = {"matched": matched, "unmatched": unmatched, "invalid": invalid}
    return result


@app.post("/api/jobs/{job_id}/detect", include_in_schema=False)
def run_detection(job_id: str) -> dict[str, Any]:
    raise HTTPException(status_code=410, detail="图片模型检测流程已停用，请使用校验后的 PAUT 检测结果")
    job = _fetch_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")

    with connect() as conn:
        images = rows_to_dicts(conn.execute("SELECT * FROM images WHERE job_id = ?", (job_id,)).fetchall())
        conn.execute("DELETE FROM defects WHERE job_id = ? AND source != ?", (job_id, "manual"))
        total_detections = 0
        for image in images:
            image_path = Path(image["stored_path"])
            try:
                detections = detect_image(image_path, float(job["confidence"]))
            except DetectionRuntimeError as exc:
                conn.execute("UPDATE jobs SET status = ?, updated_at = ? WHERE id = ?", ("检测失败", now(), job_id))
                conn.execute(
                    "INSERT INTO audit_logs (entity_type, entity_id, action, detail, created_at) VALUES (?, ?, ?, ?, ?)",
                    ("job", job_id, "detect_failed", str(exc), now()),
                )
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            for detection in detections:
                _insert_defect(conn, job_id, image, detection)
            total_detections += len(detections)
            _redraw_image_annotations(conn, image)
        conn.execute("UPDATE jobs SET status = ?, updated_at = ? WHERE id = ?", ("检测完成", now(), job_id))
        conn.execute(
            "INSERT INTO audit_logs (entity_type, entity_id, action, detail, created_at) VALUES (?, ?, ?, ?, ?)",
            ("job", job_id, "detect", f"images={len(images)}, model_detections={total_detections}, manual_reserved=true", now()),
        )

    return get_job(job_id)


@app.get("/api/jobs")
def list_jobs() -> dict[str, Any]:
    with connect() as conn:
        jobs = rows_to_dicts(conn.execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall())
    return {"items": jobs}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    job = _fetch_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    with connect() as conn:
        images = rows_to_dicts(conn.execute("SELECT * FROM images WHERE job_id = ? ORDER BY created_at", (job_id,)).fetchall())
        defects = rows_to_dicts(conn.execute("SELECT * FROM defects WHERE job_id = ? ORDER BY created_at", (job_id,)).fetchall())
        calibration_row = conn.execute("SELECT * FROM job_calibrations WHERE job_id = ?", (job_id,)).fetchone()
    for image in images:
        image["url"] = public_url_for(image["stored_path"])
        image["annotated_url"] = public_url_for(image["annotated_path"]) if image.get("annotated_path") else ""
        try:
            metadata = json.loads(image.get("source_metadata_json") or "{}")
        except json.JSONDecodeError:
            metadata = {}
        image["source_metadata"] = metadata
        image["thresholded_url"] = metadata.get("thresholded_url") or image["url"]
        image["original_url"] = metadata.get("original_url") or ""
        image["default_view"] = metadata.get("default_view") or "thresholded"
    if job.get("source_type") == "realtime" and not defects and images:
        trusted_empty = all(
            ((validated_package({"team_result": (image.get("source_metadata") or {}).get("team_result")}) or {}).get("quality") or {}).get("status") == "可信"
            for image in images
        )
        if trusted_empty:
            job = {**job, "status": "无需复核"}
    return {
        "job": job,
        "images": images,
        "defects": defects,
        "batch_calibration": dict(calibration_row) if calibration_row else None,
    }


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str) -> dict[str, Any]:
    job = _fetch_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")

    with connect() as conn:
        images = rows_to_dicts(conn.execute("SELECT * FROM images WHERE job_id = ?", (job_id,)).fetchall())
        conn.execute(
            "INSERT INTO audit_logs (entity_type, entity_id, action, detail, created_at) VALUES (?, ?, ?, ?, ?)",
            ("job", job_id, "delete", f"images={len(images)}", now()),
        )
        conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))

    removed = _cleanup_job_files(job_id, job, images)
    return {"ok": True, "removed": removed}


@app.patch("/api/defects/{defect_id}")
def update_defect(defect_id: str, payload: DefectUpdate) -> dict[str, Any]:
    fields = {k: v for k, v in payload.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(status_code=400, detail="没有可更新字段")
    job_id = ""
    with connect() as conn:
        defect = conn.execute("SELECT * FROM defects WHERE id = ?", (defect_id,)).fetchone()
        if not defect:
            raise HTTPException(status_code=404, detail="缺陷不存在")
        image = conn.execute("SELECT * FROM images WHERE id = ?", (defect["image_id"],)).fetchone()
        job_id = str(defect["job_id"])
        bbox_keys = {"x_min", "y_min", "x_max", "y_max"}
        should_redraw = "class_name" in fields or bool(bbox_keys & set(fields))
        if bbox_keys & set(fields):
            merged = dict(defect)
            merged.update(fields)
            if image:
                _validate_bbox(merged, dict(image))
                physical = _physical_bbox_for_image(
                    dict(image),
                    float(merged["x_min"]),
                    float(merged["y_min"]),
                    float(merged["x_max"]),
                    float(merged["y_max"]),
                )
                fields.update(
                    {
                        "center_x_px": (float(merged["x_min"]) + float(merged["x_max"])) / 2,
                        "center_y_px": (float(merged["y_min"]) + float(merged["y_max"])) / 2,
                        "width_px": float(merged["x_max"]) - float(merged["x_min"]),
                        "height_px": float(merged["y_max"]) - float(merged["y_min"]),
                        **physical,
                    }
                )
        assignments = ", ".join(f"{key} = ?" for key in fields)
        conn.execute(f"UPDATE defects SET {assignments}, updated_at = ? WHERE id = ?", (*fields.values(), now(), defect_id))
        conn.execute(
            "INSERT INTO audit_logs (entity_type, entity_id, action, detail, created_at) VALUES (?, ?, ?, ?, ?)",
            ("defect", defect_id, "update", str(fields), now()),
        )
        if image and should_redraw:
            _redraw_image_annotations(conn, dict(image))
    refresh = _refresh_realtime_job_evidence(job_id, "人工复核记录已更新")
    return {"ok": True, "evidence_refresh": refresh}


@app.patch("/api/jobs/{job_id}/defects/review")
def update_job_defect_reviews(job_id: str, payload: BatchDefectReviewUpdate) -> dict[str, Any]:
    defect_ids = list(dict.fromkeys(str(item).strip() for item in payload.defect_ids if str(item).strip()))
    if not defect_ids:
        raise HTTPException(status_code=400, detail="没有可复核的缺陷记录")
    if payload.review_status not in {"待复核", "已确认", "误检"}:
        raise HTTPException(status_code=400, detail="复核状态无效")

    placeholders = ",".join("?" for _ in defect_ids)
    with connect() as conn:
        job = conn.execute("SELECT id FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not job:
            raise HTTPException(status_code=404, detail="任务不存在")
        rows = conn.execute(
            f"SELECT id FROM defects WHERE job_id = ? AND id IN ({placeholders})",
            (job_id, *defect_ids),
        ).fetchall()
        found_ids = {str(row["id"]) for row in rows}
        missing_ids = [item for item in defect_ids if item not in found_ids]
        if missing_ids:
            raise HTTPException(status_code=409, detail=f"有 {len(missing_ids)} 条缺陷不属于当前批次或已被删除")
        conn.execute(
            f"UPDATE defects SET review_status = ?, updated_at = ? WHERE job_id = ? AND id IN ({placeholders})",
            (payload.review_status, now(), job_id, *defect_ids),
        )
        conn.execute(
            "INSERT INTO audit_logs (entity_type, entity_id, action, detail, created_at) VALUES (?, ?, ?, ?, ?)",
            ("job", job_id, "batch_review", f"status={payload.review_status}, count={len(defect_ids)}", now()),
        )
    refresh = _refresh_realtime_job_evidence(job_id, "批量人工复核记录已更新")
    return {"ok": True, "updated_count": len(defect_ids), "evidence_refresh": refresh}


@app.post("/api/images/{image_id}/defects")
def create_manual_defect(image_id: str, payload: DefectCreate) -> dict[str, Any]:
    with connect() as conn:
        image = conn.execute("SELECT * FROM images WHERE id = ?", (image_id,)).fetchone()
        if not image:
            raise HTTPException(status_code=404, detail="图片不存在")
        image_dict = dict(image)
        detection = {
            "class_name": payload.class_name,
            "confidence": payload.confidence,
            "x_min": payload.x_min,
            "y_min": payload.y_min,
            "x_max": payload.x_max,
            "y_max": payload.y_max,
            "source": "manual",
        }
        _validate_bbox(detection, image_dict)
        defect_id = _insert_defect(conn, image_dict["job_id"], image_dict, detection)
        conn.execute(
            """
            UPDATE defects
            SET review_status = ?, suggestion = ?, remark = ?, updated_at = ?
            WHERE id = ?
            """,
            (payload.review_status, payload.suggestion, payload.remark, now(), defect_id),
        )
        conn.execute(
            "INSERT INTO audit_logs (entity_type, entity_id, action, detail, created_at) VALUES (?, ?, ?, ?, ?)",
            ("defect", defect_id, "manual_create", payload.model_dump_json(), now()),
        )
        conn.execute(
            "UPDATE jobs SET status = ?, updated_at = ? WHERE id = ?",
            ("待复核" if payload.review_status == "待复核" else "已复核", now(), image_dict["job_id"]),
        )
        _redraw_image_annotations(conn, image_dict)
    refresh = _refresh_realtime_job_evidence(str(image_dict["job_id"]), "人工补录记录已更新")
    return {"ok": True, "defect_id": defect_id, "evidence_refresh": refresh}


@app.delete("/api/defects/{defect_id}")
def delete_defect(defect_id: str) -> dict[str, Any]:
    job_id = ""
    with connect() as conn:
        defect = conn.execute("SELECT * FROM defects WHERE id = ?", (defect_id,)).fetchone()
        if not defect:
            raise HTTPException(status_code=404, detail="缺陷不存在")
        image = conn.execute("SELECT * FROM images WHERE id = ?", (defect["image_id"],)).fetchone()
        job_id = str(defect["job_id"])
        conn.execute("DELETE FROM defects WHERE id = ?", (defect_id,))
        conn.execute(
            "INSERT INTO audit_logs (entity_type, entity_id, action, detail, created_at) VALUES (?, ?, ?, ?, ?)",
            ("defect", defect_id, "delete", "", now()),
        )
        if image:
            _redraw_image_annotations(conn, dict(image))
    refresh = _refresh_realtime_job_evidence(job_id, "缺陷记录已删除")
    return {"ok": True, "evidence_refresh": refresh}


@app.patch("/api/images/{image_id}/calibration")
def update_image_calibration(image_id: str, payload: CalibrationUpdate) -> dict[str, Any]:
    sync_result: dict[str, Any] | None = None
    with connect() as conn:
        image = conn.execute("SELECT * FROM images WHERE id = ?", (image_id,)).fetchone()
        if not image:
            raise HTTPException(status_code=404, detail="图片不存在")
        image_dict = dict(image)
        mm_per_pixel = payload.mm_per_pixel if payload.mm_per_pixel and payload.mm_per_pixel > 0 else None
        conn.execute(
            """
            UPDATE images
            SET area_label = ?, scan_area = ?, probe_or_channel = ?, image_note = ?,
                mm_per_pixel = ?, mm_per_pixel_x = ?, mm_per_pixel_y = ?,
                origin_x_px = ?, origin_y_px = ?, origin_x_mm = ?, origin_y_mm = ?,
                rotation_deg = ?, weld_centerline_y_px = ?, coordinate_system = ?,
                coordinate_direction = ?, calibration_method = ?, calibration_note = ?
            WHERE id = ?
            """,
            (
                payload.scan_area,
                payload.scan_area,
                payload.probe_or_channel,
                payload.image_note,
                mm_per_pixel,
                mm_per_pixel,
                mm_per_pixel,
                0,
                0,
                0,
                0,
                0,
                None,
                "image_xy",
                "图片左上角为原点，x向右，y向下",
                payload.calibration_method,
                payload.calibration_note,
                image_id,
            ),
        )
        defects = rows_to_dicts(conn.execute("SELECT * FROM defects WHERE image_id = ?", (image_id,)).fetchall())
        for defect in defects:
            physical = to_physical_bbox(
                defect["x_min"],
                defect["y_min"],
                defect["x_max"],
                defect["y_max"],
                mm_per_pixel,
            )
            conn.execute(
                """
                UPDATE defects
                SET center_x_mm = ?, center_y_mm = ?, width_mm = ?, height_mm = ?,
                    area_mm2 = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    physical["center_x_mm"],
                    physical["center_y_mm"],
                    physical["width_mm"],
                    physical["height_mm"],
                    physical["area_mm2"],
                    now(),
                    defect["id"],
                ),
            )
        job = conn.execute("SELECT * FROM jobs WHERE id = ?", (image_dict["job_id"],)).fetchone()
        job_dict = dict(job) if job else {}
    try:
        sync_result = _refresh_realtime_job_evidence(str(job_dict.get("id") or ""), "坐标标定已更新")
    except Exception as exc:  # noqa: BLE001
        sync_result = {"error": str(exc)}
    return {"ok": True, "sync": sync_result}


@app.put("/api/jobs/{job_id}/calibration")
def update_job_calibration(job_id: str, payload: JobCalibrationUpdate) -> dict[str, Any]:
    """Save one editable calibration record and apply it atomically to the whole batch."""
    mm_per_pixel = payload.mm_per_pixel if payload.mm_per_pixel and payload.mm_per_pixel > 0 else None
    if payload.calibration_method != "未标定" and mm_per_pixel is None:
        raise HTTPException(status_code=400, detail="已选择标定方式，但每像素毫米数无效")

    with connect() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not job:
            raise HTTPException(status_code=404, detail="任务不存在")
        source_image = conn.execute(
            "SELECT * FROM images WHERE id = ? AND job_id = ?",
            (payload.source_image_id, job_id),
        ).fetchone()
        if not source_image:
            raise HTTPException(status_code=400, detail="标定参考图不属于当前批次")
        images = rows_to_dicts(conn.execute("SELECT * FROM images WHERE job_id = ?", (job_id,)).fetchall())
        if not images:
            raise HTTPException(status_code=400, detail="当前批次没有可标定图片")

        conn.execute(
            """
            INSERT INTO job_calibrations
            (job_id, source_image_id, scan_area, probe_or_channel, image_note,
             mm_per_pixel, calibration_method, calibration_note,
             point_a_x, point_a_y, point_b_x, point_b_y, scale_distance_mm, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                source_image_id = excluded.source_image_id,
                scan_area = excluded.scan_area,
                probe_or_channel = excluded.probe_or_channel,
                image_note = excluded.image_note,
                mm_per_pixel = excluded.mm_per_pixel,
                calibration_method = excluded.calibration_method,
                calibration_note = excluded.calibration_note,
                point_a_x = excluded.point_a_x,
                point_a_y = excluded.point_a_y,
                point_b_x = excluded.point_b_x,
                point_b_y = excluded.point_b_y,
                scale_distance_mm = excluded.scale_distance_mm,
                updated_at = excluded.updated_at
            """,
            (
                job_id,
                payload.source_image_id,
                payload.scan_area,
                payload.probe_or_channel,
                payload.image_note,
                mm_per_pixel,
                payload.calibration_method,
                payload.calibration_note,
                payload.point_a_x,
                payload.point_a_y,
                payload.point_b_x,
                payload.point_b_y,
                payload.scale_distance_mm,
                now(),
            ),
        )
        conn.execute(
            """
            UPDATE images
            SET area_label = ?, scan_area = ?, probe_or_channel = ?, image_note = ?,
                mm_per_pixel = ?, mm_per_pixel_x = ?, mm_per_pixel_y = ?,
                origin_x_px = 0, origin_y_px = 0, origin_x_mm = 0, origin_y_mm = 0,
                rotation_deg = 0, weld_centerline_y_px = NULL, coordinate_system = 'image_xy',
                coordinate_direction = '图片左上角为原点，x向右，y向下',
                calibration_method = ?, calibration_note = ?
            WHERE job_id = ?
            """,
            (
                payload.scan_area,
                payload.scan_area,
                payload.probe_or_channel,
                payload.image_note,
                mm_per_pixel,
                mm_per_pixel,
                mm_per_pixel,
                payload.calibration_method,
                payload.calibration_note,
                job_id,
            ),
        )

        defects = rows_to_dicts(conn.execute("SELECT * FROM defects WHERE job_id = ?", (job_id,)).fetchall())
        for defect in defects:
            physical = to_physical_bbox(
                defect["x_min"], defect["y_min"], defect["x_max"], defect["y_max"], mm_per_pixel
            )
            conn.execute(
                """
                UPDATE defects
                SET center_x_mm = ?, center_y_mm = ?, width_mm = ?, height_mm = ?,
                    area_mm2 = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    physical["center_x_mm"],
                    physical["center_y_mm"],
                    physical["width_mm"],
                    physical["height_mm"],
                    physical["area_mm2"],
                    now(),
                    defect["id"],
                ),
            )

        dimensions = {(int(image.get("width") or 0), int(image.get("height") or 0)) for image in images}
        warning = ""
        if len(dimensions) > 1:
            warning = "本批次图片尺寸不完全一致，已按统一 mm/px 应用；请确认所有图片来自相同成像比例。"
        conn.execute(
            "INSERT INTO audit_logs (entity_type, entity_id, action, detail, created_at) VALUES (?, ?, ?, ?, ?)",
            (
                "job",
                job_id,
                "batch_calibration_update",
                f"source_image_id={payload.source_image_id}, images={len(images)}, mm_per_pixel={mm_per_pixel}",
                now(),
            ),
        )

    try:
        sync_result = _refresh_realtime_job_evidence(job_id, "整批坐标标定已更新")
    except Exception as exc:  # noqa: BLE001
        sync_result = {"error": str(exc)}
    return {
        "ok": True,
        "updated_images": len(images),
        "warning": warning,
        "sync": sync_result,
        "batch_calibration": get_job(job_id).get("batch_calibration"),
    }


@app.post("/api/jobs/{job_id}/report")
def generate_report(job_id: str, payload: ReportGenerateRequest | None = None) -> dict[str, Any]:
    job = _fetch_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    with connect() as conn:
        images = rows_to_dicts(conn.execute("SELECT * FROM images WHERE job_id = ?", (job_id,)).fetchall())
        defects = rows_to_dicts(conn.execute("SELECT * FROM defects WHERE job_id = ?", (job_id,)).fetchall())
        try:
            destination = Path(payload.destination_path) if payload and payload.destination_path else None
            report_path = create_job_report(job, images, defects, destination)
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"报告保存失败：{exc}") from exc
        conn.execute(
            "UPDATE jobs SET report_path = ?, status = ?, updated_at = ? WHERE id = ?",
            (str(report_path), "报告已生成", now(), job_id),
        )
        conn.execute(
            "INSERT INTO audit_logs (entity_type, entity_id, action, detail, created_at) VALUES (?, ?, ?, ?, ?)",
            ("job", job_id, "report", str(report_path), now()),
        )
    report_url = public_url_for(report_path) or None
    return {"ok": True, "report_url": report_url, "report_path": str(report_path)}


@app.post("/api/jobs/{job_id}/export/defects-csv")
def export_defects_csv(job_id: str, payload: ExportGenerateRequest | None = None) -> dict[str, Any]:
    job = _fetch_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    with connect() as conn:
        images = rows_to_dicts(conn.execute("SELECT * FROM images WHERE job_id = ?", (job_id,)).fetchall())
        defects = rows_to_dicts(conn.execute("SELECT * FROM defects WHERE job_id = ?", (job_id,)).fetchall())
        try:
            destination = Path(payload.destination_path) if payload and payload.destination_path else None
            csv_path = write_defects_csv(job_id, images, defects, destination)
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"明细保存失败：{exc}") from exc
        conn.execute(
            "INSERT INTO audit_logs (entity_type, entity_id, action, detail, created_at) VALUES (?, ?, ?, ?, ?)",
            ("job", job_id, "export_csv", str(csv_path), now()),
        )
    return {"ok": True, "csv_url": public_url_for(csv_path) or None, "csv_path": str(csv_path)}


@app.get("/api/jobs/{job_id}/audit")
def get_job_audit(job_id: str) -> dict[str, Any]:
    job = _fetch_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    with connect() as conn:
        logs = rows_to_dicts(
            conn.execute(
                """
                SELECT audit_logs.*
                FROM audit_logs
                LEFT JOIN defects ON defects.id = audit_logs.entity_id
                WHERE defects.job_id = ? OR audit_logs.entity_id = ?
                ORDER BY audit_logs.created_at DESC, audit_logs.id DESC
                """,
                (job_id, job_id),
            ).fetchall()
        )
    return {"items": logs}


def _realtime_job_agent_result(job: dict[str, Any]) -> dict[str, Any] | None:
    realtime_batch_id = str(job.get("source_batch_id") or "")
    if job.get("source_type") != "realtime" or not realtime_batch_id:
        return None
    batch = realtime_service.batch_manager.get(realtime_batch_id)
    if batch is None:
        raise HTTPException(status_code=409, detail="实时多帧分析批次不存在，请保留输出目录并联系开发人员")
    agent_result = (batch.get("result") or {}).get("agent")
    evidence_revision = int(batch.get("evidence_revision") or 0)
    agent_revision_value = batch.get("agent_revision")
    agent_revision = int(agent_revision_value if agent_revision_value is not None else (0 if agent_result else -1))
    if agent_result and agent_revision != evidence_revision:
        return {
            "configured": agent_is_configured(),
            "status": "需要重新分析",
            "batch_id": realtime_batch_id,
            "frame_count": batch.get("frame_count", 0),
            "summary": "检测记录已更新，旧分析结果已失效。请重新开始Agent分析。",
            "global_defect_candidates": [],
            "evidence_chain": [],
            "missing_conditions": [],
            "uncertainty": [],
            "review_suggestions": [],
            "sources": [],
            "guardrail_warnings": [],
        }
    if agent_result:
        result = dict(agent_result)
        result.setdefault("configured", agent_is_configured())
        result["status"] = batch.get("status")
        result["batch_id"] = realtime_batch_id
        result["frame_count"] = batch.get("frame_count", 0)
        if not _agent_result_schema_is_current(result):
            result["status"] = "需要重新分析"
            result["summary"] = "该批次使用旧版Agent字段生成，尚未接入团队统一结果包。请点击开始Agent分析重新计算。"
            result["guardrail_warnings"] = list(dict.fromkeys([
                *result.get("guardrail_warnings", []),
                "旧版结果未经过当前团队结果质量门控，需要重新分析。",
            ]))
        return result
    return {
        "configured": agent_is_configured(),
        "status": batch.get("status"),
        "batch_id": realtime_batch_id,
        "frame_count": batch.get("frame_count", 0),
        "summary": (batch.get("result") or {}).get("local", {}).get("summary")
        or batch.get("error")
        or "实时多帧证据已进入后台分析队列，请稍后查看结果。",
        "global_defect_candidates": [],
        "evidence_chain": [],
        "missing_conditions": [],
        "uncertainty": [],
        "review_suggestions": [],
        "sources": [],
        "guardrail_warnings": [],
    }


def _agent_result_schema_is_current(result: dict[str, Any]) -> bool:
    return result.get("agent_schema_version") == "paut-agent-v6-validated-results"


@app.get("/api/jobs/{job_id}/agent/result")
def get_agent_analysis_result(job_id: str) -> dict[str, Any]:
    job = _fetch_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    result = _realtime_job_agent_result(job)
    if result is None:
        raise HTTPException(status_code=404, detail="该任务没有可恢复的实时多帧Agent结果")
    return result


@app.post("/api/jobs/{job_id}/agent/analyze")
def run_agent_analysis(job_id: str) -> dict[str, Any]:
    job = _fetch_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    realtime_result = _realtime_job_agent_result(job)
    if realtime_result is not None:
        realtime_batch_id = str(job.get("source_batch_id") or "")
        if realtime_result.get("status") in {"排队中", "分析中"}:
            realtime_result.update({
                "summary": "实时多帧分析正在后台处理，请稍后查看结果。",
                "batch_id": realtime_batch_id,
            })
        elif (
            realtime_result.get("status") in {
                "已中断", "分析失败", "Agent失败，本地摘要可用", "大模型调用失败，本地摘要可用",
                "本地摘要可用", "未配置", "需要重新分析",
            }
            or not realtime_result.get("configured")
        ):
            try:
                sync_result = realtime_service.sync_batch_reviews_from_job(realtime_batch_id, job_id)
                retry_batch = realtime_service.batch_manager.retry(realtime_batch_id)
                realtime_result.update({
                    "status": retry_batch.get("status", "排队中"),
                    "summary": (
                        "已重新提交检测结果分析，将使用最新校验结果、人工复核记录和坐标标定重新计算。"
                        f" 本次同步 {sync_result.get('synced_frames', 0)} 帧、"
                        f"{sync_result.get('reviewed_defects', 0)} 条已复核缺陷、"
                        f"{sync_result.get('calibrated_frames', 0)} 帧已标定数据。"
                    ),
                    "batch_id": realtime_batch_id,
                    "frame_count": retry_batch.get("frame_count", realtime_result.get("frame_count", 0)),
                })
            except Exception as exc:  # noqa: BLE001
                realtime_result.update({
                    "status": "重试失败",
                    "summary": f"实时多帧分析重试失败：{exc}",
                    "batch_id": realtime_batch_id,
                })
        else:
            try:
                sync_result = realtime_service.sync_batch_reviews_from_job(realtime_batch_id, job_id)
                realtime_result["review_sync"] = sync_result
            except Exception as exc:  # noqa: BLE001
                realtime_result["review_sync_error"] = str(exc)
        with connect() as conn:
            conn.execute(
                "INSERT INTO audit_logs (entity_type, entity_id, action, detail, created_at) VALUES (?, ?, ?, ?, ?)",
                ("job", job_id, "agent_analyze", f"realtime_batch={realtime_batch_id}, status={realtime_result.get('status')}", now()),
            )
        return realtime_result

    with connect() as conn:
        images = rows_to_dicts(conn.execute("SELECT * FROM images WHERE job_id = ?", (job_id,)).fetchall())
        defects = rows_to_dicts(conn.execute("SELECT * FROM defects WHERE job_id = ?", (job_id,)).fetchall())
        result = analyze_job(job, images, defects)
        conn.execute(
            "INSERT INTO audit_logs (entity_type, entity_id, action, detail, created_at) VALUES (?, ?, ?, ?, ?)",
            ("job", job_id, "agent_analyze", "configured=" + str(result.get("configured")), now()),
        )
    return result


@app.post("/api/jobs/{job_id}/archive")
def generate_archive(job_id: str, payload: ExportGenerateRequest | None = None) -> dict[str, Any]:
    job = _fetch_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    with connect() as conn:
        images = rows_to_dicts(conn.execute("SELECT * FROM images WHERE job_id = ?", (job_id,)).fetchall())
        defects = rows_to_dicts(conn.execute("SELECT * FROM defects WHERE job_id = ?", (job_id,)).fetchall())
        try:
            destination = Path(payload.destination_path) if payload and payload.destination_path else None
            archive_path = create_job_archive(job, images, defects, [], destination)
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"归档保存失败：{exc}") from exc
        conn.execute(
            "INSERT INTO audit_logs (entity_type, entity_id, action, detail, created_at) VALUES (?, ?, ?, ?, ?)",
            ("job", job_id, "archive", str(archive_path), now()),
        )
    return {"ok": True, "archive_url": public_url_for(archive_path) or None, "archive_path": str(archive_path)}


async def _save_upload(uploaded: UploadFile, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as target:
        shutil.copyfileobj(uploaded.file, target)


def _is_image(filename: str) -> bool:
    return Path(filename).suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}


def _apply_metadata_to_images(job_id: str, metadata_by_stem: dict[str, dict[str, Any]]) -> tuple[int, list[str]]:
    if not metadata_by_stem:
        return 0, []
    matched = 0
    used: set[str] = set()
    with connect() as conn:
        images = rows_to_dicts(conn.execute("SELECT * FROM images WHERE job_id = ?", (job_id,)).fetchall())
        for image in images:
            stem = Path(image["original_name"]).stem
            metadata = metadata_by_stem.get(stem)
            if not metadata:
                continue
            used.add(stem)
            calibration = normalize_calibration(metadata)
            mm_per_pixel = calibration["mm_per_pixel"] if calibration["mm_per_pixel"] and calibration["mm_per_pixel"] > 0 else None
            conn.execute(
                """
                UPDATE images
                SET area_label = ?, scan_area = ?, probe_or_channel = ?, image_note = ?,
                    mm_per_pixel = ?, mm_per_pixel_x = ?, mm_per_pixel_y = ?,
                    origin_x_px = ?, origin_y_px = ?, origin_x_mm = ?, origin_y_mm = ?,
                    rotation_deg = ?, weld_centerline_y_px = ?, coordinate_system = ?,
                    coordinate_direction = ?, calibration_method = ?, calibration_note = ?
                WHERE id = ?
                """,
                (
                    calibration["scan_area"],
                    calibration["scan_area"],
                    calibration["probe_or_channel"],
                    calibration["image_note"],
                    mm_per_pixel,
                    mm_per_pixel,
                    mm_per_pixel,
                    0,
                    0,
                    0,
                    0,
                    0,
                    None,
                    "image_xy",
                    "图片左上角为原点，x向右，y向下",
                    calibration["calibration_method"],
                    calibration["calibration_note"],
                    image["id"],
                ),
            )
            defects = rows_to_dicts(conn.execute("SELECT * FROM defects WHERE image_id = ?", (image["id"],)).fetchall())
            for defect in defects:
                physical = to_physical_bbox(defect["x_min"], defect["y_min"], defect["x_max"], defect["y_max"], mm_per_pixel)
                conn.execute(
                    """
                    UPDATE defects
                    SET center_x_mm = ?, center_y_mm = ?, width_mm = ?, height_mm = ?,
                        area_mm2 = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        physical["center_x_mm"],
                        physical["center_y_mm"],
                        physical["width_mm"],
                        physical["height_mm"],
                        physical["area_mm2"],
                        now(),
                        defect["id"],
                    ),
                )
            matched += 1
    unmatched = sorted(set(metadata_by_stem) - used)
    return matched, unmatched


def _fetch_job(job_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def _cleanup_job_files(job_id: str, job: dict[str, Any], images: list[dict[str, Any]]) -> list[str]:
    candidates: list[Path] = [
        settings.upload_dir / job_id,
        settings.output_dir / "annotated" / job_id,
        settings.output_dir / "archives" / f"{job_id}_archive.zip",
        settings.output_dir / "exports" / f"{job_id}_defects.csv",
    ]
    if job.get("report_path"):
        candidates.append(Path(job["report_path"]))
    for image in images:
        if image.get("annotated_path"):
            candidates.append(Path(image["annotated_path"]))

    removed: list[str] = []
    for candidate in candidates:
        path = candidate.resolve()
        if not path.exists() or not _is_safe_data_path(path):
            continue
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        removed.append(str(path))
    return removed


def _is_safe_data_path(path: Path) -> bool:
    try:
        path.resolve().relative_to(settings.app_data_dir.resolve())
        return True
    except ValueError:
        return False


def _is_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        test_file = path / ".write_test"
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def _insert_defect(conn, job_id: str, image: dict[str, Any], detection: dict[str, Any]) -> str:
    x_min = float(detection["x_min"])
    y_min = float(detection["y_min"])
    x_max = float(detection["x_max"])
    y_max = float(detection["y_max"])
    width_px = x_max - x_min
    height_px = y_max - y_min
    center_x_px = (x_min + x_max) / 2
    center_y_px = (y_min + y_max) / 2
    physical = _physical_bbox_for_image(image, x_min, y_min, x_max, y_max)
    defect_id = uid("def")
    conn.execute(
        """
        INSERT INTO defects
        (id, job_id, image_id, class_name, confidence, x_min, y_min, x_max, y_max,
         center_x_px, center_y_px, width_px, height_px, center_x_mm, center_y_mm,
         width_mm, height_mm, area_mm2, source, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            defect_id,
            job_id,
            image["id"],
            detection["class_name"],
            detection["confidence"],
            x_min,
            y_min,
            x_max,
            y_max,
            center_x_px,
            center_y_px,
            width_px,
            height_px,
            physical["center_x_mm"],
            physical["center_y_mm"],
            physical["width_mm"],
            physical["height_mm"],
            physical["area_mm2"],
            detection.get("source", "model"),
            now(),
            now(),
        ),
    )
    return defect_id


def _physical_bbox_for_image(
    image: dict[str, Any],
    x_min: float,
    y_min: float,
    x_max: float,
    y_max: float,
) -> dict[str, float | None]:
    """Use manual calibration first, then the native DAS x-z range stored with the frame."""
    if image.get("mm_per_pixel") or image.get("mm_per_pixel_x") or image.get("mm_per_pixel_y"):
        return to_physical_bbox(
            x_min,
            y_min,
            x_max,
            y_max,
            image.get("mm_per_pixel"),
            image.get("origin_x_mm") or 0,
            image.get("origin_y_mm") or 0,
            image.get("mm_per_pixel_x"),
            image.get("mm_per_pixel_y"),
            image.get("origin_x_px") or 0,
            image.get("origin_y_px") or 0,
            image.get("rotation_deg") or 0,
        )
    try:
        metadata = json.loads(image.get("source_metadata_json") or "{}")
        x_range = metadata.get("x_range_mm") or []
        z_range = metadata.get("z_range_mm") or []
        image_width = float(image.get("width") or 0)
        image_height = float(image.get("height") or 0)
        if len(x_range) != 2 or len(z_range) != 2 or image_width <= 0 or image_height <= 0:
            raise ValueError
        x_scale = (float(x_range[1]) - float(x_range[0])) / image_width
        z_scale = (float(z_range[1]) - float(z_range[0])) / image_height
        width_mm = (x_max - x_min) * x_scale
        height_mm = (y_max - y_min) * z_scale
        return {
            "center_x_mm": float(x_range[0]) + ((x_min + x_max) / 2) * x_scale,
            "center_y_mm": float(z_range[0]) + ((y_min + y_max) / 2) * z_scale,
            "width_mm": width_mm,
            "height_mm": height_mm,
            "area_mm2": width_mm * height_mm,
        }
    except (TypeError, ValueError, json.JSONDecodeError):
        return to_physical_bbox(x_min, y_min, x_max, y_max, None)


def _validate_bbox(detection: dict[str, Any], image: dict[str, Any]) -> None:
    x_min = float(detection["x_min"])
    y_min = float(detection["y_min"])
    x_max = float(detection["x_max"])
    y_max = float(detection["y_max"])
    width = float(image.get("width") or 0)
    height = float(image.get("height") or 0)
    if x_min < 0 or y_min < 0 or x_max <= x_min or y_max <= y_min:
        raise HTTPException(status_code=400, detail="缺陷框坐标无效")
    if width and x_max > width:
        raise HTTPException(status_code=400, detail="缺陷框超出图片宽度")
    if height and y_max > height:
        raise HTTPException(status_code=400, detail="缺陷框超出图片高度")


def _redraw_image_annotations(conn, image: dict[str, Any]) -> None:
    defects = rows_to_dicts(conn.execute("SELECT * FROM defects WHERE image_id = ?", (image["id"],)).fetchall())
    detections = [
        {
            "class_name": item["class_name"],
            "confidence": item["confidence"],
            "x_min": item["x_min"],
            "y_min": item["y_min"],
            "x_max": item["x_max"],
            "y_max": item["y_max"],
        }
        for item in defects
    ]
    annotated_path = settings.output_dir / "annotated" / image["job_id"] / f"{image['id']}_annotated.jpg"
    draw_annotated_image(Path(image["stored_path"]), detections, annotated_path)
    conn.execute("UPDATE images SET annotated_path = ? WHERE id = ?", (str(annotated_path), image["id"]))


# 必须位于所有 API 路由之后，避免 SPA 回退吞掉 /api 请求。
frontend_dist = settings.resource_dir / "frontend" / "dist"
frontend_assets = frontend_dist / "assets"
if frontend_assets.is_dir():
    app.mount("/assets", StaticFiles(directory=str(frontend_assets)), name="frontend-assets")


@app.get("/{spa_path:path}", include_in_schema=False)
def serve_frontend(spa_path: str):
    if spa_path.startswith(("api/", "uploads/", "outputs/")):
        raise HTTPException(status_code=404, detail="资源不存在")
    index = frontend_dist / "index.html"
    if index.exists():
        return FileResponse(index)
    return HTMLResponse(
        "<meta charset='utf-8'><h2>前端尚未构建</h2>"
        "<p>开发人员请运行 frontend 目录中的 npm run build。</p>",
        status_code=503,
    )
