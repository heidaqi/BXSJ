from __future__ import annotations

import gzip
import json
import shutil
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..config import settings
from ..database import connect, init_db, rows_to_dicts


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _directory_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


class RealtimeCacheService:
    """定时压缩历史摘要并清理可再生的实时计算中间文件。"""

    def __init__(self) -> None:
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._last_result: dict[str, Any] = {
            "last_run_at": "",
            "compacted_jobs": 0,
            "released_bytes": 0,
            "errors": [],
        }

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, name="realtime-cache-cleanup", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        self._thread = None

    def status(self) -> dict[str, Any]:
        return {
            **self._last_result,
            "running": bool(self._thread and self._thread.is_alive()),
            "retention_days": settings.realtime_cache_retention_days,
            "cleanup_interval_hours": settings.realtime_cache_cleanup_hours,
            "policy": "保留任务、复核、展示图片和报告；压缩摘要并删除可再生中间文件",
        }

    def _loop(self) -> None:
        self.run_once()
        interval = settings.realtime_cache_cleanup_hours * 3600
        while not self._stop_event.wait(interval):
            self.run_once()

    def run_once(self, *, force: bool = False) -> dict[str, Any]:
        if not self._lock.acquire(blocking=False):
            return self.status()
        result: dict[str, Any] = {
            "last_run_at": _now(),
            "compacted_jobs": 0,
            "released_bytes": 0,
            "errors": [],
        }
        try:
            init_db()
            cutoff = datetime.now() - timedelta(days=settings.realtime_cache_retention_days)
            with connect() as conn:
                jobs = rows_to_dicts(
                    conn.execute(
                        """
                        SELECT * FROM jobs
                        WHERE source_type = 'realtime' AND source_session_id != ''
                          AND COALESCE(storage_state, 'active') != 'compacted'
                        ORDER BY updated_at
                        """
                    ).fetchall()
                )
            for job in jobs:
                try:
                    updated_at = datetime.strptime(job["updated_at"], "%Y-%m-%d %H:%M:%S")
                    if not force and updated_at > cutoff:
                        continue
                    if self._batch_is_running(str(job.get("source_batch_id") or "")):
                        continue
                    released = self._compact_job(job)
                    result["compacted_jobs"] += 1
                    result["released_bytes"] += released
                except Exception as exc:  # 单个历史批次失败不阻断其他批次
                    result["errors"].append(f"{job.get('id')}: {exc}")
            self._cleanup_temporary_files(cutoff)
            self._last_result = result
            return self.status()
        finally:
            self._lock.release()

    def _batch_is_running(self, batch_id: str) -> bool:
        if not batch_id:
            return False
        path = settings.output_dir / "agent" / "batches" / f"{batch_id}.json"
        try:
            status = json.loads(path.read_text(encoding="utf-8-sig")).get("status")
        except (OSError, json.JSONDecodeError):
            return False
        return status in {"排队中", "分析中"}

    def _compact_job(self, job: dict[str, Any]) -> int:
        session_id = str(job["source_session_id"])
        sessions_root = (settings.output_dir / "realtime" / "sessions").resolve()
        session_dir = (sessions_root / session_id).resolve()
        try:
            session_dir.relative_to(sessions_root)
        except ValueError as exc:
            raise RuntimeError("会话目录越界，拒绝清理") from exc

        with connect() as conn:
            images = rows_to_dicts(conn.execute("SELECT * FROM images WHERE job_id = ?", (job["id"],)).fetchall())
            defects = rows_to_dicts(conn.execute("SELECT * FROM defects WHERE job_id = ?", (job["id"],)).fetchall())

        manifest = {
            "schema": "realtime-history-summary-v1",
            "compacted_at": _now(),
            "job": {
                key: job.get(key)
                for key in ("id", "batch_name", "status", "created_at", "updated_at", "source_session_id", "source_batch_id")
            },
            "statistics": {
                "image_count": len(images),
                "defect_count": len(defects),
                "confirmed_count": sum(item.get("review_status") == "已确认" for item in defects),
                "false_positive_count": sum(item.get("review_status") == "误检" for item in defects),
            },
            "frames": [
                {
                    "frame_id": image.get("source_frame_id"),
                    "name": image.get("original_name"),
                    "scan_area": image.get("scan_area"),
                    "coordinate_system": image.get("coordinate_system"),
                }
                for image in images
            ],
        }
        history_dir = settings.output_dir / "realtime" / "history"
        history_dir.mkdir(parents=True, exist_ok=True)
        archive_path = history_dir / f"{session_id}.json.gz"
        temporary = archive_path.with_suffix(".json.gz.tmp")
        with gzip.open(temporary, "wt", encoding="utf-8") as stream:
            json.dump(manifest, stream, ensure_ascii=False, separators=(",", ":"))
        temporary.replace(archive_path)

        released = 0
        for name in ("groups", "tofd_state"):
            target = session_dir / name
            released += _directory_size(target)
            if target.exists():
                shutil.rmtree(target)

        with connect() as conn:
            conn.execute(
                """
                UPDATE jobs SET storage_state = 'compacted', history_archive_path = ?, compacted_at = ?
                WHERE id = ?
                """,
                (str(archive_path), _now(), job["id"]),
            )
            conn.execute(
                "INSERT INTO audit_logs (entity_type, entity_id, action, detail, created_at) VALUES (?, ?, ?, ?, ?)",
                ("job", job["id"], "cache_compact", f"released_bytes={released}", _now()),
            )
        return released

    def _cleanup_temporary_files(self, cutoff: datetime) -> None:
        root = settings.output_dir / "realtime"
        if not root.exists():
            return
        for path in root.rglob("*.tmp"):
            try:
                if datetime.fromtimestamp(path.stat().st_mtime) <= cutoff:
                    path.unlink()
            except OSError:
                continue


realtime_cache_service = RealtimeCacheService()
