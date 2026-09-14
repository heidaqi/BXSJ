from __future__ import annotations

import tempfile
import zipfile
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from .database import connect, rows_to_dicts
from .services.paut_service import PautService


router = APIRouter(prefix="/api/paut", tags=["paut"])


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


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
async def upload_paut_data(
    job_id: str,
    file: UploadFile = File(...),
    batch_name: str = Form("PAUT检测批次"),
) -> dict:
    """上传 PAUT 原始数据（ZIP 压缩包）"""
    from .database import init_db, connect
    
    init_db()
    
    # 检查任务是否存在，不存在则自动创建
    with connect() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not job:
            actual_batch_name = batch_name or f"PAUT检测-{datetime.now().strftime('%Y-%m-%d')}"
            conn.execute(
                """
                INSERT INTO jobs
                (id, batch_name, status, created_at, updated_at, confidence)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (job_id, actual_batch_name, "已上传", _now(), _now(), 0.25),
            )
            conn.execute(
                "INSERT INTO audit_logs (entity_type, entity_id, action, detail, created_at) VALUES (?, ?, ?, ?, ?)",
                ("job", job_id, "create", "auto_created_by_paut_upload", _now()),
            )
            print(f"[PAUT] 自动创建批次: {job_id} ({actual_batch_name})")
        else:
            actual_batch_name = job["batch_name"]

    # 原有逻辑继续...
    filename = file.filename or ""
    if not filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="请上传 .zip 压缩包")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir) / "upload.zip"
        with tmp_path.open("wb") as f:
            content = await file.read()
            f.write(content)

        extract_dir = Path(tmpdir) / "extracted"
        extract_dir.mkdir()
        try:
            with zipfile.ZipFile(tmp_path, "r") as zf:
                _safe_extract(zf, extract_dir)
        except (ValueError, zipfile.BadZipFile) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        txt_files = list(extract_dir.rglob("*.txt"))

        if not txt_files:
            raise HTTPException(status_code=400, detail="ZIP 中未找到任何 .txt 文件")

        top_level = [item for item in extract_dir.iterdir() if item.name != "__MACOSX"]
        if len(top_level) == 1 and top_level[0].is_dir():
            extract_dir = top_level[0]

        service = PautService()
        result = service.upload_paut_data(job_id, extract_dir)

        with connect() as conn:
            conn.execute(
                "UPDATE jobs SET status = ?, updated_at = ? WHERE id = ?",
                ("PAUT数据已上传", _now(), job_id),
            )

        return {
            "ok": True,
            "job_id": job_id,
            "txt_count": result.get("txt_count", 0),
            "message": "PAUT数据已上传，请点击'开始处理'执行检测",
        }


@router.post("/jobs/{job_id}/detect")
def run_paut_detection(job_id: str) -> dict:
    """执行 PAUT 检测（DAS + Ascan）"""
    with connect() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not job:
            raise HTTPException(status_code=404, detail="任务不存在")

    service = PautService()
    result = service.detect(job_id)

    if not result.get("ok"):
        raise HTTPException(status_code=500, detail=result.get("error", "PAUT检测失败"))

    return result


@router.get("/jobs/{job_id}/paut-status")
def get_paut_status(job_id: str) -> dict:
    """获取 PAUT 检测状态"""
    with connect() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not job:
            raise HTTPException(status_code=404, detail="任务不存在")

        images = rows_to_dicts(
            conn.execute(
                "SELECT * FROM images WHERE job_id = ? AND scan_area = 'PAUT B扫'",
                (job_id,)
            ).fetchall()
        )

        defects = rows_to_dicts(
            conn.execute(
                "SELECT * FROM defects WHERE job_id = ? AND source = 'paut'",
                (job_id,)
            ).fetchall()
        )

    return {
        "ok": True,
        "job_id": job_id,
        "status": job["status"],
        "bscan_images": len(images),
        "defects": len(defects),
    }
