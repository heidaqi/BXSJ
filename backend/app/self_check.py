from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw

from .config import settings
from .database import connect, init_db
from .main import (
    DefectCreate,
    DefectUpdate,
    create_manual_defect,
    generate_archive,
    export_defects_csv,
    delete_job,
    now,
    uid,
    update_defect,
    _insert_defect,
)
from .services.detection_service import detect_image, draw_annotated_image
from .services.report_service import create_job_report


def run() -> None:
    init_db()
    test_dir = settings.upload_dir / "_self_check"
    test_dir.mkdir(parents=True, exist_ok=True)
    image_path = test_dir / "self_check_sample.jpg"
    image = Image.new("RGB", (960, 540), (230, 235, 240))
    draw = ImageDraw.Draw(image)
    draw.rectangle((380, 210, 560, 310), fill=(120, 130, 145))
    draw.line((0, 270, 960, 270), fill=(90, 100, 110), width=2)
    image.save(image_path, quality=95)

    job_id = uid("job")
    image_id = uid("img")
    confidence = settings.yolo_confidence
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with connect() as conn:
        conn.execute(
            """
            INSERT INTO jobs
            (id, batch_name, project_name, inspected_object, inspector, confidence, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (job_id, "系统自检批次", "自检项目", "测试图片", "系统", confidence, "已上传", created_at, created_at),
        )
        conn.execute(
            """
            INSERT INTO images
            (id, job_id, original_name, stored_path, width, height, area_label, mm_per_pixel, origin_x_mm, origin_y_mm, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (image_id, job_id, image_path.name, str(image_path), 960, 540, "自检区域", 0.1, 0, 0, created_at),
        )
        image_row = dict(conn.execute("SELECT * FROM images WHERE id = ?", (image_id,)).fetchone())
        detections = detect_image(image_path, confidence)
        annotated_path = settings.output_dir / "annotated" / job_id / f"{image_id}_annotated.jpg"
        draw_annotated_image(image_path, detections, annotated_path)
        conn.execute("UPDATE images SET annotated_path = ? WHERE id = ?", (str(annotated_path), image_id))
        for detection in detections:
            _insert_defect(conn, job_id, image_row, detection)
        conn.execute("UPDATE jobs SET status = ?, updated_at = ? WHERE id = ?", ("检测完成", now(), job_id))

    manual = create_manual_defect(
        image_id,
        DefectCreate(
            class_name="人工复核缺陷",
            x_min=120,
            y_min=120,
            x_max=260,
            y_max=210,
            suggestion="建议复查原始图像",
            remark="系统自检人工补录",
        ),
    )
    update_defect(
        manual["defect_id"],
        DefectUpdate(
            class_name="人工修正缺陷",
            x_min=130,
            y_min=130,
            x_max=275,
            y_max=220,
            suggestion="建议纳入报告",
            remark="系统自检编辑通过",
        ),
    )

    with connect() as conn:
        job = dict(conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone())
        images = [dict(row) for row in conn.execute("SELECT * FROM images WHERE job_id = ?", (job_id,)).fetchall()]
        defects = [dict(row) for row in conn.execute("SELECT * FROM defects WHERE job_id = ?", (job_id,)).fetchall()]

    report_path = create_job_report(job, images, defects)
    if not Path(report_path).exists():
        raise RuntimeError("Report was not created.")
    csv_result = export_defects_csv(job_id)
    csv_path = settings.output_dir / csv_result["csv_url"].replace("/outputs/", "")
    if not csv_path.exists():
        raise RuntimeError("CSV was not created.")
    archive = generate_archive(job_id)
    archive_path = settings.output_dir / archive["archive_url"].replace("/outputs/", "")
    if not archive_path.exists():
        raise RuntimeError("Archive was not created.")
    _assert_archive_contains(archive_path, "defects.csv")
    delete_result = _run_delete_check()
    if not delete_result["ok"]:
        raise RuntimeError("Delete check failed.")
    print(f"self check ok: job={job_id}, defects={len(defects)}, report={report_path}, archive={archive_path}")


def _assert_archive_contains(archive_path: Path, member_name: str) -> None:
    import zipfile

    with zipfile.ZipFile(archive_path) as zf:
        if member_name not in zf.namelist():
            raise RuntimeError(f"Archive missing {member_name}.")


def _run_delete_check() -> dict[str, bool]:
    job_id = uid("job")
    image_id = uid("img")
    job_upload_dir = settings.upload_dir / job_id
    job_upload_dir.mkdir(parents=True, exist_ok=True)
    image_path = job_upload_dir / "delete_check.jpg"
    Image.new("RGB", (200, 120), (210, 220, 230)).save(image_path, quality=90)
    annotated_dir = settings.output_dir / "annotated" / job_id
    annotated_dir.mkdir(parents=True, exist_ok=True)
    annotated_path = annotated_dir / f"{image_id}_annotated.jpg"
    Image.new("RGB", (200, 120), (220, 220, 220)).save(annotated_path, quality=90)
    report_path = settings.output_dir / "reports" / f"{job_id}_report.pdf"
    report_path.write_bytes(b"%PDF-1.4\n%delete-check\n")
    archive_path = settings.output_dir / "archives" / f"{job_id}_archive.zip"
    archive_path.write_bytes(b"zip-delete-check")

    with connect() as conn:
        conn.execute(
            """
            INSERT INTO jobs
            (id, batch_name, confidence, status, created_at, updated_at, report_path)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (job_id, "删除自检批次", 0.25, "报告已生成", now(), now(), str(report_path)),
        )
        conn.execute(
            """
            INSERT INTO images
            (id, job_id, original_name, stored_path, annotated_path, width, height, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (image_id, job_id, image_path.name, str(image_path), str(annotated_path), 200, 120, now()),
        )

    delete_job(job_id)
    with connect() as conn:
        remaining = conn.execute("SELECT COUNT(*) FROM jobs WHERE id = ?", (job_id,)).fetchone()[0]
    files_removed = not job_upload_dir.exists() and not annotated_dir.exists() and not report_path.exists() and not archive_path.exists()
    return {"ok": remaining == 0 and files_removed}


if __name__ == "__main__":
    run()
