from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image

from ..config import settings
from ..database import connect, init_db
from .validated_result_service import validated_package


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _output_path(url: str) -> Path:
    prefix = "/outputs/"
    if not url.startswith(prefix):
        raise ValueError(f"实时帧资源不是输出目录内文件：{url}")
    return settings.output_dir / url[len(prefix):]


def materialize_realtime_session(
    *,
    session_id: str,
    source_path: str,
    frames: list[dict[str, Any]],
    batch_id: str = "",
    target_job_id: str = "",
) -> dict[str, Any]:
    """将一次实时会话映射到旧业务使用的 jobs/images/defects 数据结构。"""

    init_db()
    if not frames:
        raise ValueError("本次处理没有已完成帧，不能进入复核流程")

    with connect() as conn:
        existing = conn.execute(
            "SELECT id FROM jobs WHERE source_type = 'realtime' AND source_session_id = ?",
            (session_id,),
        ).fetchone()
        if existing:
            if batch_id:
                conn.execute("UPDATE jobs SET source_batch_id = ?, updated_at = ? WHERE id = ?", (batch_id, _now(), existing["id"]))
            return {"job_id": existing["id"], "created": False}

        target = None
        if target_job_id:
            target = conn.execute("SELECT id FROM jobs WHERE id = ?", (target_job_id,)).fetchone()
        job_id = target["id"] if target else _uid("job")
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        source_name = Path(source_path).name or "实时数据"
        if target:
            conn.execute(
                """
                UPDATE jobs SET project_name = ?, inspected_object = ?, confidence = ?, status = ?,
                    updated_at = ?, source_type = ?, source_session_id = ?, source_batch_id = ?, storage_state = ?
                WHERE id = ?
                """,
                (
                    source_name, "PAUT实时采集数据", 0.0, "待复核", _now(),
                    "realtime", session_id, batch_id, "active", job_id,
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO jobs
                (id, batch_name, project_name, inspected_object, confidence, status,
                 created_at, updated_at, source_type, source_session_id, source_batch_id, storage_state)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id, f"实时检测-{stamp}", source_name, "PAUT实时采集数据",
                    0.0, "待复核", _now(), _now(),
                    "realtime", session_id, batch_id, "active",
                ),
            )

        image_count = 0
        defect_count = 0
        for frame in frames:
            frame_id = str(frame.get("frame_id") or "")
            image_info = frame.get("image") or {}
            stored_path = _output_path(str(image_info.get("url") or ""))
            if not stored_path.exists():
                raise ValueError(f"实时帧图像缺失：{frame_id}")
            annotated_url = str(image_info.get("annotated_url") or "")
            annotated_path = _output_path(annotated_url) if annotated_url else stored_path
            if not annotated_path.exists():
                annotated_path = stored_path
            with Image.open(stored_path) as image_file:
                width, height = image_file.size

            image_id = _uid("img")
            x_range = image_info.get("x_range_mm") or []
            z_range = image_info.get("z_range_mm") or []
            range_note = ""
            if len(x_range) == 2 and len(z_range) == 2:
                range_note = f"DAS显示范围：x={x_range[0]}~{x_range[1]} mm，z={z_range[0]}~{z_range[1]} mm。"
            metadata = {
                "frame_id": frame_id,
                "received_at": frame.get("received_at"),
                "scan_receipt": frame.get("scan_receipt") or {},
                "source_group": frame.get("source_group"),
                "source_path": frame.get("source_path"),
                "x_range_mm": x_range,
                "z_range_mm": z_range,
                "velocity_mps": frame.get("velocity_mps"),
                "sample_rate_hz": frame.get("sample_rate_hz"),
                "configured_sample_rate_hz": frame.get("configured_sample_rate_hz"),
                "plate_thickness_mm": frame.get("plate_thickness_mm"),
                "ascan": (frame.get("analysis") or {}).get("ascan"),
                "paut_v2": (frame.get("analysis") or {}).get("paut_v2"),
                "team_result": validated_package(frame),
                "tofd": frame.get("tofd"),
                "thresholded_url": image_info.get("thresholded_url") or image_info.get("url"),
                "original_url": image_info.get("original_url"),
                "default_view": image_info.get("default_view", "thresholded"),
                "display_threshold_only": image_info.get("display_threshold_only", False),
            }
            conn.execute(
                """
                INSERT INTO images
                (id, job_id, original_name, stored_path, annotated_path, width, height,
                 scan_area, probe_or_channel, image_note, coordinate_system,
                 coordinate_direction, calibration_method, calibration_note,
                 source_frame_id, source_metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    image_id,
                    job_id,
                    f"{frame.get('source_group') or frame_id}.png",
                    str(stored_path),
                    str(annotated_path),
                    width,
                    height,
                    str(frame.get("source_group") or ""),
                    "PAUT/DAS",
                    f"来源帧 {frame_id}。{range_note}",
                    "das_xz",
                    "x右z下",
                    "未标定",
                    "DAS图含坐标轴和边距，未自动按整张PNG换算；可在标定页人工校正。",
                    frame_id,
                    json.dumps(metadata, ensure_ascii=False),
                    _now(),
                ),
            )
            image_count += 1

            team_result = validated_package(frame)
            detections = list((team_result or {}).get("defects") or [])
            for detection in detections:
                try:
                    center_x = float(detection.get("x_mm"))
                    center_z = float(detection.get("z_mm"))
                    size_mm = max(float(detection.get("width_mm") or 1.0), 0.5)
                    x_span = max(float(x_range[1]) - float(x_range[0]), 1e-9)
                    z_span = max(float(z_range[1]) - float(z_range[0]), 1e-9)
                    center_x_px = (center_x - float(x_range[0])) / x_span * width
                    center_y_px = (center_z - float(z_range[0])) / z_span * height
                    width_px = max(size_mm / x_span * width, 2.0)
                    height_px = max((float(detection.get("height_mm") or 0.5)) / z_span * height, 2.0)
                    detection = {
                        **detection,
                        "x_min": center_x_px - width_px / 2,
                        "y_min": center_y_px - height_px / 2,
                        "x_max": center_x_px + width_px / 2,
                        "y_max": center_y_px + height_px / 2,
                        "x_mm": center_x,
                        "z_mm": center_z,
                        "projected_width_mm": detection.get("width_mm"),
                        "projected_height_mm": detection.get("height_mm"),
                    }
                except (TypeError, ValueError, IndexError):
                    continue
                try:
                    x_min = float(detection["x_min"])
                    y_min = float(detection["y_min"])
                    x_max = float(detection["x_max"])
                    y_max = float(detection["y_max"])
                except (KeyError, TypeError, ValueError):
                    continue
                if x_max <= x_min or y_max <= y_min:
                    continue
                center_x_mm = detection.get("x_mm")
                center_z_mm = detection.get("z_mm")
                width_mm = detection.get("mask_projected_major_mm", detection.get("projected_width_mm"))
                height_mm = detection.get("mask_projected_minor_mm", detection.get("projected_height_mm"))
                area_mm2 = detection.get("area_mm2")
                mask_note_parts = [
                    f"PAUT V2定量结果；来源帧 {frame_id}",
                    str(detection.get("quantification_status") or ""),
                    str(detection.get("warning") or ""),
                ]
                if detection.get("mask_projected_major_mm") is not None:
                    mask_note_parts.append(f"截面长约{detection.get('mask_projected_major_mm')}mm")
                if detection.get("mask_projected_minor_mm") is not None:
                    mask_note_parts.append(f"宽约{detection.get('mask_projected_minor_mm')}mm")
                if detection.get("projected_orientation_deg") is not None:
                    mask_note_parts.append(f"朝向约{detection.get('projected_orientation_deg')}°")
                record_id = str(detection.get("defect_id") or _uid("def"))
                conn.execute(
                    """
                    INSERT INTO defects
                    (id, job_id, image_id, class_name, confidence, x_min, y_min, x_max, y_max,
                     center_x_px, center_y_px, width_px, height_px, center_x_mm, center_y_mm,
                     width_mm, height_mm, area_mm2, review_status, remark, source, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record_id, job_id, image_id,
                        str(detection.get("class_name") or "疑似缺陷"),
                        float(detection["confidence"]) if detection.get("confidence") is not None else 0.0,
                        x_min, y_min, x_max, y_max,
                        (x_min + x_max) / 2, (y_min + y_max) / 2,
                        x_max - x_min, y_max - y_min,
                        center_x_mm, center_z_mm, width_mm, height_mm, area_mm2,
                        "待复核",
                        "；".join(mask_note_parts),
                        "validated_team_result",
                        _now(), _now(),
                    ),
                )
                conn.execute(
                        """
                        UPDATE defects SET classification_method = ?, raw_length_mm = ?,
                            corrected_length_mm = ?, diameter_mm = ?, minor_axis_nominal_mm = ?,
                            minor_axis_lower_mm = ?, minor_axis_upper_mm = ?, area_lower_mm2 = ?,
                            area_upper_mm2 = ?, slag_probability = ?, image_angle_deg = ?,
                            aspect_ratio = ?, snr_db = ?, quantification_status = ?, analysis_warning = ?,
                            source_defect_id = ?, result_schema_version = ?, algorithm_version = ?,
                            quality_status = ?, source_payload_json = ?, confidence_available = ?
                        WHERE id = ?
                        """,
                        (
                            detection.get("classification_method"), detection.get("raw_length_mm"),
                            detection.get("corrected_length_mm"), detection.get("diameter_mm"),
                            detection.get("minor_axis_nominal_mm"), detection.get("minor_axis_lower_mm"),
                            detection.get("minor_axis_upper_mm"), detection.get("area_lower_mm2"),
                            detection.get("area_upper_mm2"), detection.get("slag_probability"),
                            detection.get("image_angle_deg"), detection.get("aspect_ratio"),
                            detection.get("snr_db"), detection.get("quantification_status"),
                            detection.get("warning"), detection.get("source_defect_id"),
                            team_result.get("schema_version"), team_result.get("algorithm_version"),
                            (team_result.get("quality") or {}).get("status"),
                            json.dumps(detection.get("raw_team_values") or {}, ensure_ascii=False),
                            1 if detection.get("confidence") is not None else 0,
                            record_id,
                        ),
                    )
                defect_count += 1

        trusted_empty = defect_count == 0 and all(
            ((validated_package(frame) or {}).get("quality") or {}).get("status") == "可信"
            for frame in frames
        )
        conn.execute(
            "UPDATE jobs SET status = ?, updated_at = ? WHERE id = ?",
            ("无需复核" if trusted_empty else "待复核", _now(), job_id),
        )

        conn.execute(
            "INSERT INTO audit_logs (entity_type, entity_id, action, detail, created_at) VALUES (?, ?, ?, ?, ?)",
            (
                "job", job_id, "realtime_handoff",
                f"session={session_id}, batch={batch_id}, frames={image_count}, validated_candidates={defect_count}",
                _now(),
            ),
        )

    return {"job_id": job_id, "created": True, "image_count": image_count, "defect_count": defect_count}
