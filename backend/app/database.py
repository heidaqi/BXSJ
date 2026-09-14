from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

from .config import settings


def ensure_storage() -> None:
    for path in [
        settings.app_data_dir,
        settings.upload_dir,
        settings.output_dir,
        settings.output_dir / "annotated",
        settings.output_dir / "reports",
        settings.output_dir / "archives",
        settings.output_dir / "exports",
        settings.database_path.parent,
        settings.yolo_model_path.parent,
    ]:
        path.mkdir(parents=True, exist_ok=True)


@contextmanager
def connect():
    ensure_storage()
    conn = sqlite3.connect(settings.database_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    ensure_storage()
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                batch_name TEXT NOT NULL,
                client_name TEXT DEFAULT '',
                project_name TEXT DEFAULT '',
                inspected_object TEXT DEFAULT '',
                inspector TEXT DEFAULT '',
                reviewer TEXT DEFAULT '',
                approver TEXT DEFAULT '',
                confidence REAL NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                report_path TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS images (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                original_name TEXT NOT NULL,
                stored_path TEXT NOT NULL,
                annotated_path TEXT DEFAULT '',
                width INTEGER DEFAULT 0,
                height INTEGER DEFAULT 0,
                area_label TEXT DEFAULT '',
                scan_area TEXT DEFAULT '',
                probe_or_channel TEXT DEFAULT '',
                image_note TEXT DEFAULT '',
                mm_per_pixel REAL,
                mm_per_pixel_x REAL,
                mm_per_pixel_y REAL,
                origin_x_px REAL DEFAULT 0,
                origin_y_px REAL DEFAULT 0,
                origin_x_mm REAL DEFAULT 0,
                origin_y_mm REAL DEFAULT 0,
                rotation_deg REAL DEFAULT 0,
                weld_centerline_y_px REAL,
                coordinate_system TEXT DEFAULT 'image_xy',
                coordinate_direction TEXT DEFAULT 'x右y下',
                calibration_method TEXT DEFAULT '',
                calibration_note TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS defects (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                image_id TEXT NOT NULL,
                class_name TEXT NOT NULL,
                confidence REAL NOT NULL,
                x_min REAL NOT NULL,
                y_min REAL NOT NULL,
                x_max REAL NOT NULL,
                y_max REAL NOT NULL,
                center_x_px REAL NOT NULL,
                center_y_px REAL NOT NULL,
                width_px REAL NOT NULL,
                height_px REAL NOT NULL,
                center_x_mm REAL,
                center_y_mm REAL,
                width_mm REAL,
                height_mm REAL,
                area_mm2 REAL,
                review_status TEXT NOT NULL DEFAULT '待复核',
                suggestion TEXT DEFAULT '',
                remark TEXT DEFAULT '',
                source TEXT NOT NULL DEFAULT 'model',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE,
                FOREIGN KEY(image_id) REFERENCES images(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS job_calibrations (
                job_id TEXT PRIMARY KEY,
                source_image_id TEXT NOT NULL,
                scan_area TEXT DEFAULT '',
                probe_or_channel TEXT DEFAULT '',
                image_note TEXT DEFAULT '',
                mm_per_pixel REAL,
                calibration_method TEXT DEFAULT '未标定',
                calibration_note TEXT DEFAULT '',
                point_a_x REAL,
                point_a_y REAL,
                point_b_x REAL,
                point_b_y REAL,
                scale_distance_mm REAL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE,
                FOREIGN KEY(source_image_id) REFERENCES images(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                action TEXT NOT NULL,
                detail TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );
            """
        )
        _ensure_columns(
            conn,
            "jobs",
            {
                "source_type": "TEXT DEFAULT 'upload'",
                "source_session_id": "TEXT DEFAULT ''",
                "source_batch_id": "TEXT DEFAULT ''",
                "storage_state": "TEXT DEFAULT 'active'",
                "history_archive_path": "TEXT DEFAULT ''",
                "compacted_at": "TEXT DEFAULT ''",
            },
        )
        _ensure_columns(
            conn,
            "images",
            {
                "mm_per_pixel_x": "REAL",
                "mm_per_pixel_y": "REAL",
                "scan_area": "TEXT DEFAULT ''",
                "probe_or_channel": "TEXT DEFAULT ''",
                "image_note": "TEXT DEFAULT ''",
                "origin_x_px": "REAL DEFAULT 0",
                "origin_y_px": "REAL DEFAULT 0",
                "rotation_deg": "REAL DEFAULT 0",
                "weld_centerline_y_px": "REAL",
                "coordinate_system": "TEXT DEFAULT 'image_xy'",
                "coordinate_direction": "TEXT DEFAULT 'x右y下'",
                "calibration_method": "TEXT DEFAULT ''",
                "calibration_note": "TEXT DEFAULT ''",
                "source_frame_id": "TEXT DEFAULT ''",
                "source_metadata_json": "TEXT DEFAULT ''",
            },
        )
        _ensure_columns(
            conn,
            "defects",
            {
                "classification_method": "TEXT DEFAULT ''",
                "raw_length_mm": "REAL",
                "corrected_length_mm": "REAL",
                "diameter_mm": "REAL",
                "minor_axis_nominal_mm": "REAL",
                "minor_axis_lower_mm": "REAL",
                "minor_axis_upper_mm": "REAL",
                "area_lower_mm2": "REAL",
                "area_upper_mm2": "REAL",
                "slag_probability": "REAL",
                "image_angle_deg": "REAL",
                "aspect_ratio": "REAL",
                "snr_db": "REAL",
                "quantification_status": "TEXT DEFAULT ''",
                "analysis_warning": "TEXT DEFAULT ''",
                "source_defect_id": "TEXT DEFAULT ''",
                "result_schema_version": "TEXT DEFAULT ''",
                "algorithm_version": "TEXT DEFAULT ''",
                "quality_status": "TEXT DEFAULT ''",
                "source_payload_json": "TEXT DEFAULT ''",
                "confidence_available": "INTEGER DEFAULT 1",
            },
        )


def _ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def public_url_for(path_value: str | Path) -> str:
    path = Path(path_value)
    try:
        rel_upload = path.resolve().relative_to(settings.upload_dir)
        return f"/uploads/{rel_upload.as_posix()}"
    except ValueError:
        pass
    try:
        rel_output = path.resolve().relative_to(settings.output_dir)
        return f"/outputs/{rel_output.as_posix()}"
    except ValueError:
        return ""
