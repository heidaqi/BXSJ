from __future__ import annotations

import tempfile
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from .database import connect, init_db
from .services.tofd_service import TofdService


router = APIRouter(prefix="/api/tofd", tags=["tofd"])


class TofdDetectRequest(BaseModel):
    velocity_mps: float = Field(default=5900.0, gt=0)
    plate_thickness_mm: float = Field(default=6.3, gt=0)
    tofd_pcs_mm: float = Field(default=42.0, gt=0)
    tofd_tx_channel: int = Field(default=1, ge=1)
    tofd_rx_channel: int = Field(default=128, ge=1)
    tofd_header_bytes: int = Field(default=128, ge=0)


class TofdLocalDetectRequest(BaseModel):
    job_id: str = Field(min_length=1)
    batch_name: str = Field(default="TOFD本地检测")
    data_dir: str = Field(min_length=1)
    velocity_mps: float = Field(default=5900.0, gt=0)
    plate_thickness_mm: float = Field(default=6.3, gt=0)
    tofd_pcs_mm: float = Field(default=42.0, gt=0)
    tofd_tx_channel: int = Field(default=1, ge=1)
    tofd_rx_channel: int = Field(default=128, ge=1)
    tofd_header_bytes: int = Field(default=128, ge=0)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _safe_extract(archive: zipfile.ZipFile, destination: Path) -> None:
    root = destination.resolve()
    for member in archive.infolist():
        target = (destination / member.filename).resolve()
        if target != root and root not in target.parents:
            raise ValueError(f"ZIP 包含不安全路径：{member.filename}")
    archive.extractall(destination)


@router.post("/jobs/{job_id}/upload")
async def upload_tofd_data(
    job_id: str,
    file: UploadFile = File(...),
    batch_name: str = Form("TOFD检测批次"),
) -> dict[str, Any]:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".mat", ".zip"}:
        raise HTTPException(status_code=400, detail="请上传 Tx1-Rx128 紧凑 .mat 文件或原始 .zip 数据包")
    init_db()
    with connect() as conn:
        job = conn.execute("SELECT id FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not job:
            conn.execute(
                "INSERT INTO jobs (id, batch_name, status, created_at, updated_at, confidence, source_type) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (job_id, batch_name or "TOFD检测批次", "正在上传TOFD数据", _now(), _now(), 0.25, "tofd"),
            )
    with tempfile.TemporaryDirectory() as temporary:
        try:
            if suffix == ".mat":
                source = Path(temporary) / "tx1_rx128_raw_upload.mat"
                with source.open("wb") as target:
                    while chunk := await file.read(1024 * 1024):
                        target.write(chunk)
                result = TofdService().upload(job_id, source)
            else:
                archive_path = Path(temporary) / "upload.zip"
                with archive_path.open("wb") as target:
                    while chunk := await file.read(1024 * 1024):
                        target.write(chunk)
                extract_dir = Path(temporary) / "extracted"
                extract_dir.mkdir()
                with zipfile.ZipFile(archive_path) as archive:
                    _safe_extract(archive, extract_dir)
                result = TofdService().upload(job_id, extract_dir)
        except (ValueError, zipfile.BadZipFile) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    with connect() as conn:
        conn.execute("UPDATE jobs SET status = ?, updated_at = ? WHERE id = ?", ("TOFD数据已上传", _now(), job_id))
    return {**result, "job_id": job_id, "message": "TOFD 数据已上传，可以开始独立成像分析"}


@router.post("/jobs/{job_id}/detect")
def detect_tofd(job_id: str, payload: TofdDetectRequest | None = None) -> dict[str, Any]:
    with connect() as conn:
        if not conn.execute("SELECT id FROM jobs WHERE id = ?", (job_id,)).fetchone():
            raise HTTPException(status_code=404, detail="任务不存在")
    result = TofdService().detect(job_id, (payload or TofdDetectRequest()).model_dump())
    if not result.get("ok"):
        raise HTTPException(status_code=500, detail=result.get("error", "TOFD分析失败"))
    return result


@router.post("/local-detect")
def detect_tofd_local(payload: TofdLocalDetectRequest) -> dict[str, Any]:
    init_db()
    with connect() as conn:
        job = conn.execute("SELECT id FROM jobs WHERE id = ?", (payload.job_id,)).fetchone()
        if not job:
            conn.execute(
                "INSERT INTO jobs (id, batch_name, status, created_at, updated_at, confidence, source_type) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (payload.job_id, payload.batch_name, "正在分析本地TOFD数据", _now(), _now(), 0.25, "tofd"),
            )
    params = payload.model_dump(exclude={"job_id", "batch_name", "data_dir"})
    try:
        result = TofdService().detect_local(payload.job_id, payload.data_dir, params)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not result.get("ok"):
        raise HTTPException(status_code=500, detail=result.get("error", "TOFD分析失败"))
    return result


@router.get("/jobs/{job_id}/tofd-status")
def tofd_status(job_id: str) -> dict[str, Any]:
    with connect() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not job:
            raise HTTPException(status_code=404, detail="任务不存在")
        image_count = conn.execute("SELECT COUNT(*) FROM images WHERE job_id = ? AND scan_area = 'TOFD B扫'", (job_id,)).fetchone()[0]
    return {"ok": True, "job_id": job_id, "status": job["status"], "images": image_count, "data_type": "tofd"}
