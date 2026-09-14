from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..database import connect, rows_to_dicts
from .fmc_data_inspector import FmcDataInspector


FrameLoader = Callable[[str], dict[str, Any] | None]
FrameWriter = Callable[[dict[str, Any]], None]


@dataclass
class DbFrameContext:
    job: dict[str, Any] | None
    image_by_frame: dict[str, dict[str, Any]]
    defects_by_frame: dict[str, list[dict[str, Any]]]


class FrameRecordService:
    """统一帧记录视图。

    数据库是人工标定、复核、补录和报告路径的主来源；实时帧 JSON 是
    A扫、DAS和TOFD初始证据快照。该服务负责合并两边信息，
    让 Agent、报告和导出读取同一个结构。
    """

    version = "frame-record-v2-validated"

    def __init__(self) -> None:
        self._sampling_inspector = FmcDataInspector()

    def find_realtime_job_for_batch(self, batch_id: str) -> dict[str, Any] | None:
        with connect() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE source_type = 'realtime' AND source_batch_id = ?",
                (batch_id,),
            ).fetchone()
        return dict(row) if row else None

    def find_job_for_frame(self, frame_id: str) -> dict[str, Any] | None:
        with connect() as conn:
            row = conn.execute(
                """
                SELECT jobs.*
                FROM jobs
                JOIN images ON images.job_id = jobs.id
                WHERE images.source_frame_id = ?
                ORDER BY images.created_at DESC
                LIMIT 1
                """,
                (frame_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_frame_record(self, frame_id: str, frame_loader: FrameLoader) -> dict[str, Any] | None:
        frame = frame_loader(frame_id)
        if frame is None:
            return None
        job = self.find_job_for_frame(frame_id)
        context = self.load_db_context(job.get("id") if job else None)
        return self.build_frame_record(frame, context)

    def load_db_context(self, job_id: str | None) -> DbFrameContext:
        if not job_id:
            return DbFrameContext(job=None, image_by_frame={}, defects_by_frame={})
        with connect() as conn:
            job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            image_rows = rows_to_dicts(conn.execute(
                """
                SELECT *
                FROM images
                WHERE job_id = ? AND source_frame_id != ''
                ORDER BY created_at, id
                """,
                (job_id,),
            ).fetchall())
            defects = rows_to_dicts(conn.execute(
                """
                SELECT *
                FROM defects
                WHERE job_id = ?
                ORDER BY created_at, id
                """,
                (job_id,),
            ).fetchall())

        image_by_id = {row["id"]: row for row in image_rows}
        image_by_frame = {row["source_frame_id"]: row for row in image_rows}
        defects_by_frame: dict[str, list[dict[str, Any]]] = {}
        for defect in defects:
            image = image_by_id.get(defect.get("image_id"))
            frame_id = image.get("source_frame_id") if image else None
            if frame_id:
                defects_by_frame.setdefault(frame_id, []).append(defect)
        return DbFrameContext(
            job=dict(job) if job else None,
            image_by_frame=image_by_frame,
            defects_by_frame=defects_by_frame,
        )

    def get_batch_frame_records(
        self,
        batch_id: str,
        frame_ids: list[str],
        frame_loader: FrameLoader,
        *,
        job_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if not job_id:
            job = self.find_realtime_job_for_batch(batch_id)
            job_id = job.get("id") if job else None
        context = self.load_db_context(job_id)
        records: list[dict[str, Any]] = []
        for frame_id in frame_ids:
            frame = frame_loader(frame_id)
            if frame is None:
                continue
            records.append(self.build_frame_record(frame, context))
        return records

    def build_frame_record(self, frame: dict[str, Any], context: DbFrameContext | None = None) -> dict[str, Any]:
        context = context or DbFrameContext(job=None, image_by_frame={}, defects_by_frame={})
        record = copy.deepcopy(frame)
        frame_id = str(record.get("frame_id") or "")
        image_row = context.image_by_frame.get(frame_id)
        db_defects = context.defects_by_frame.get(frame_id, [])

        calibration = self._calibration_from_image(image_row)
        review = self._review_from_defects(db_defects)
        image = record.get("image") or {}
        analysis = record.get("analysis") or {}
        sampling = self._sampling_context(record, image_row)
        if sampling["sample_rate_hz"]:
            record["sample_rate_hz"] = sampling["sample_rate_hz"]
        if sampling["configured_sample_rate_hz"]:
            record["configured_sample_rate_hz"] = sampling["configured_sample_rate_hz"]

        record["frame_record_version"] = self.version
        record["source"] = {
            "frame_id": frame_id,
            "job_id": context.job.get("id") if context.job else "",
            "batch_id": context.job.get("source_batch_id") if context.job else "",
            "source_path": record.get("source_path"),
            "source_group": record.get("source_group"),
            "source_type": record.get("source_type"),
        }
        record["acquisition"] = {
            "shape": record.get("shape"),
            "sample_rate_hz": record.get("sample_rate_hz"),
            "configured_sample_rate_hz": record.get("configured_sample_rate_hz"),
            "sample_rate_source": sampling["source"],
            "velocity_mps": record.get("velocity_mps"),
            "plate_thickness_mm": record.get("plate_thickness_mm"),
            "received_at": record.get("received_at"),
        }
        record["imaging"] = {
            "das_image_url": image.get("url"),
            "annotated_image_url": image.get("annotated_url"),
            "tofd_image_url": (record.get("tofd") or {}).get("url"),
            "x_range_mm": image.get("x_range_mm"),
            "z_range_mm": image.get("z_range_mm"),
            "peak_x_mm": image.get("peak_x_mm"),
            "peak_z_mm": image.get("peak_z_mm"),
            "peak_is_measured": image.get("peak_is_measured"),
        }
        record["ascan"] = {
            "features": analysis.get("ascan"),
            # Candidate points here precede the final team calibration and are
            # retained only for waveform tracing, never for Agent/report facts.
            "pre_validation_candidates": record.get("defects") or [],
        }
        record["manual_context"] = {
            "job": context.job or {},
            "image": image_row or {},
            "defects": db_defects,
        }
        record["calibration"] = calibration
        engineering_context = dict(record.get("engineering_context") or {})
        engineering_context["calibration"] = calibration
        record["engineering_context"] = engineering_context
        record["review"] = review
        return record

    def _sampling_context(
        self,
        record: dict[str, Any],
        image: dict[str, Any] | None,
    ) -> dict[str, Any]:
        actual = record.get("sample_rate_hz")
        configured = record.get("configured_sample_rate_hz")
        metadata: dict[str, Any] = {}
        if image and image.get("source_metadata_json"):
            try:
                metadata = json.loads(image["source_metadata_json"])
            except (TypeError, ValueError, json.JSONDecodeError):
                metadata = {}
        actual = actual or metadata.get("sample_rate_hz")
        configured = configured or metadata.get("configured_sample_rate_hz")
        if self._positive_number(actual):
            return {
                "sample_rate_hz": float(actual),
                "configured_sample_rate_hz": float(configured) if self._positive_number(configured) else None,
                "source": "frame_or_archive",
            }

        source_path = record.get("source_path") or metadata.get("source_path")
        if source_path:
            try:
                inspection = self._sampling_inspector.inspect(Path(source_path))
                rates = [
                    float(item.sample_rate_hz)
                    for item in inspection.valid_groups
                    if self._positive_number(item.sample_rate_hz)
                ]
                if rates and max(rates) - min(rates) <= max(rates) * 1e-6:
                    return {
                        "sample_rate_hz": rates[0],
                        "configured_sample_rate_hz": float(configured) if self._positive_number(configured) else None,
                        "source": "recovered_from_fmc_time_axis",
                    }
            except (OSError, ValueError):
                pass
        return {
            "sample_rate_hz": None,
            "configured_sample_rate_hz": float(configured) if self._positive_number(configured) else None,
            "source": "unavailable",
        }

    @staticmethod
    def _positive_number(value: Any) -> bool:
        try:
            return float(value) > 0
        except (TypeError, ValueError):
            return False

    def sync_job_context_to_frames(
        self,
        batch_id: str,
        job_id: str,
        frame_ids: list[str],
        frame_loader: FrameLoader,
        frame_writer: FrameWriter,
    ) -> dict[str, Any]:
        context = self.load_db_context(job_id)
        synced = 0
        reviewed_defects = 0
        calibrated_frames = 0
        for frame_id in frame_ids:
            frame = frame_loader(frame_id)
            if frame is None:
                continue
            record = self.build_frame_record(frame, context)
            reviewed_defects += int((record.get("review") or {}).get("reviewed_defect_count") or 0)
            if (record.get("calibration") or {}).get("mm_per_pixel"):
                calibrated_frames += 1
            frame["review"] = record.get("review")
            frame["calibration"] = record.get("calibration")
            frame["engineering_context"] = record.get("engineering_context")
            frame["frame_record_version"] = self.version
            frame["sample_rate_hz"] = record.get("sample_rate_hz")
            frame["configured_sample_rate_hz"] = record.get("configured_sample_rate_hz")
            frame_writer(frame)
            synced += 1
        return {
            "batch_id": batch_id,
            "job_id": job_id,
            "synced_frames": synced,
            "reviewed_defects": reviewed_defects,
            "calibrated_frames": calibrated_frames,
        }

    @staticmethod
    def _calibration_from_image(image: dict[str, Any] | None) -> dict[str, Any]:
        if not image:
            return {
                "calibration_method": "未标定",
                "coordinate_system": "image_xy",
                "coordinate_direction": "图片左上角为原点，x向右，y向下",
                "source": "none",
            }
        return {
            "scan_area": image.get("scan_area") or image.get("area_label") or "",
            "probe_or_channel": image.get("probe_or_channel") or "",
            "image_note": image.get("image_note") or "",
            "mm_per_pixel": image.get("mm_per_pixel"),
            "mm_per_pixel_x": image.get("mm_per_pixel_x"),
            "mm_per_pixel_y": image.get("mm_per_pixel_y"),
            "origin_x_px": image.get("origin_x_px"),
            "origin_y_px": image.get("origin_y_px"),
            "origin_x_mm": image.get("origin_x_mm"),
            "origin_y_mm": image.get("origin_y_mm"),
            "rotation_deg": image.get("rotation_deg"),
            "weld_centerline_y_px": image.get("weld_centerline_y_px"),
            "coordinate_system": image.get("coordinate_system") or "image_xy",
            "coordinate_direction": image.get("coordinate_direction") or "图片左上角为原点，x向右，y向下",
            "calibration_method": image.get("calibration_method") or "未标定",
            "calibration_note": image.get("calibration_note") or "",
            "source": "image_calibration_database",
        }

    @staticmethod
    def _review_from_defects(defects: list[dict[str, Any]]) -> dict[str, Any]:
        if not defects:
            return {
                "status": "无需复核",
                "note": "本帧未形成需记录的异常候选",
                "reviewed_defect_count": 0,
                "defect_count": 0,
                "confirmed_count": 0,
                "false_positive_count": 0,
                "manual_added_count": 0,
                "defects": [],
                "source": "review_database",
            }
        reviewed = [
            defect for defect in defects
            if defect.get("review_status") and defect.get("review_status") != "待复核"
        ]
        statuses = [str(defect.get("review_status") or "待复核") for defect in defects]
        if any(status == "待复核" for status in statuses):
            status = "待复核"
        elif any(status == "已确认" for status in statuses):
            status = "已确认"
        elif statuses and all(status == "误检" for status in statuses):
            status = "误检"
        else:
            status = "待复核"
        notes = []
        for defect in defects[:8]:
            parts = [
                str(defect.get("class_name") or "缺陷"),
                str(defect.get("review_status") or "待复核"),
            ]
            if defect.get("suggestion"):
                parts.append(f"建议:{defect.get('suggestion')}")
            if defect.get("remark"):
                parts.append(f"备注:{defect.get('remark')}")
            notes.append(" / ".join(parts))
        return {
            "status": status,
            "note": "；".join(notes) if notes else "",
            "reviewed_defect_count": len(reviewed),
            "defect_count": len(defects),
            "confirmed_count": sum(1 for defect in defects if defect.get("review_status") == "已确认"),
            "false_positive_count": sum(1 for defect in defects if defect.get("review_status") == "误检"),
            "manual_added_count": sum(1 for defect in defects if defect.get("source") == "manual"),
            "defects": defects,
            "source": "review_database",
        }


frame_record_service = FrameRecordService()
