from __future__ import annotations

import json
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import settings
from .export_service import build_defects_csv_text


def create_job_archive(
    job: dict[str, Any],
    images: list[dict[str, Any]],
    defects: list[dict[str, Any]],
    _audit_logs: list[dict[str, Any]],
    destination: Path | None = None,
) -> Path:
    archive_path = destination.expanduser().resolve() if destination else settings.output_dir / "archives" / f"{job['id']}_archive.zip"
    if archive_path.suffix.lower() != ".zip":
        archive_path = archive_path.with_suffix(".zip")
    archive_path.parent.mkdir(parents=True, exist_ok=True)

    manifest = {
        "archive_created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "batch": _public_job(job),
        "images": _portable_paths(images),
        "defect_count": len(defects),
        "report_included": bool(job.get("report_path") and Path(job["report_path"]).exists()),
    }
    public_defects = [_public_defect(item) for item in defects]

    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        zf.writestr("defects.json", json.dumps(public_defects, ensure_ascii=False, indent=2))
        zf.writestr("defects.csv", build_defects_csv_text(images, defects).encode("utf-8"))

        report_path = Path(job.get("report_path") or "")
        if report_path.exists():
            zf.write(report_path, f"reports/{report_path.name}")

        used_names: set[str] = set()
        for index, image in enumerate(images, start=1):
            prefix = str(image.get("source_frame_id") or image.get("id") or f"frame_{index:04d}")
            stored_path = Path(image.get("stored_path") or "")
            if stored_path.exists():
                name = _unique_name(used_names, f"{prefix}_{stored_path.name}")
                zf.write(stored_path, f"images/original/{name}")
            annotated_path = Path(image.get("annotated_path") or "")
            if annotated_path.exists():
                name = _unique_name(used_names, f"{prefix}_{annotated_path.name}")
                zf.write(annotated_path, f"images/annotated/{name}")

    return archive_path


def _portable_paths(images: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for image in images:
        item = {
            "frame_id": image.get("source_frame_id") or image.get("id"),
            "name": image.get("original_name") or "",
            "area": image.get("area_label") or image.get("scan_area") or "",
            "received_at": image.get("created_at") or "",
        }
        result.append(item)
    return result


def _public_job(job: dict[str, Any]) -> dict[str, Any]:
    keys = ("id", "batch_name", "project_name", "inspected_object", "inspector", "reviewer", "status", "created_at", "updated_at")
    return {key: job.get(key) for key in keys if job.get(key) not in (None, "")}


def _public_defect(defect: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "id", "image_id", "class_name", "confidence", "review_status", "center_x_mm",
        "center_y_mm", "corrected_length_mm", "diameter_mm", "area_mm2",
        "image_angle_deg", "snr_db", "suggestion", "remark",
    )
    return {key: defect.get(key) for key in keys if defect.get(key) not in (None, "")}


def _unique_name(used: set[str], proposed: str) -> str:
    safe = "".join(char if char.isalnum() or char in "._-" else "_" for char in proposed)
    candidate = safe or "image"
    stem, suffix = Path(candidate).stem, Path(candidate).suffix
    number = 2
    while candidate.lower() in used:
        candidate = f"{stem}_{number}{suffix}"
        number += 1
    used.add(candidate.lower())
    return candidate
