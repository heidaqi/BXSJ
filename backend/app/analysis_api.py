"""数据集统一分析 API。

POST /api/analysis/run           启动数据集分析（异步）
GET  /api/analysis               列出最近的分析任务
GET  /api/analysis/{id}          查询单个任务状态与结果
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from .services.analysis_service import dataset_analysis_service

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


@router.post("/run")
def run_analysis(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        analysis_id = dataset_analysis_service.start_analysis(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"analysis_id": analysis_id, "state": "排队中"}


@router.get("")
def list_analyses(limit: int = Query(50, ge=1, le=200)) -> list[dict[str, Any]]:
    return dataset_analysis_service.list_jobs(limit=limit)


@router.get("/{analysis_id}")
def get_analysis(analysis_id: str) -> dict[str, Any]:
    job = dataset_analysis_service.get_job(analysis_id)
    if job is None:
        raise HTTPException(status_code=404, detail="分析任务不存在")
    return job
