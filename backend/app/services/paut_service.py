from __future__ import annotations

import csv
import json
import shutil
import uuid
from pathlib import Path
from typing import Any

from PIL import Image

from ..config import settings
from ..database import connect, public_url_for, rows_to_dicts
from .matlab_service import MatlabRuntimeError, get_matlab_service
from .paut_v2_adapter import output_files, read_defect_report, summarize
from .team_result_service import build_team_result_package


class PautService:
    """PAUT V2 service while retaining the existing upload/detect contract."""

    def __init__(self):
        self.matlab = get_matlab_service()

    def upload_paut_data(self, job_id: str, source_dir: Path) -> dict[str, Any]:
        """
        Preserve flat, nested tx_* and single-FMC layouts from the uploaded package.
        """
        dest_dir = settings.upload_dir / job_id / "paut_raw"
        dest_dir.mkdir(parents=True, exist_ok=True)

        txt_files = list(source_dir.rglob("*.txt"))
        if not txt_files:
            raise ValueError(f"源目录中没有找到 .txt 文件：{source_dir}")

        shutil.copytree(source_dir, dest_dir, dirs_exist_ok=True)
        copied = [str(path.relative_to(source_dir)) for path in txt_files]

        return {
            "ok": True,
            "dest_dir": str(dest_dir),
            "txt_count": len(copied),
            "txt_files": copied,
        }

    def detect(self, job_id: str) -> dict[str, Any]:
        """
        执行 PAUT 检测（DAS + Ascan），将结果写入数据库
        """
        # 1. 定位原始数据目录
        raw_dir = settings.upload_dir / job_id / "paut_raw"
        if not raw_dir.exists():
            raise ValueError(f"PAUT 原始数据目录不存在：{raw_dir}")

        # 2. 创建输出目录
        output_dir = settings.output_dir / "paut" / job_id
        output_dir.mkdir(parents=True, exist_ok=True)

        print("\n[PAUT] 执行 turn V2 成像、分类与定量分析...")
        try:
            matlab_result = self.matlab.run_paut(
                input_dir=raw_dir,
                output_dir=output_dir,
                timeout=600,
            )
        except MatlabRuntimeError as exc:
            return {
                "ok": False,
                "stage": "matlab",
                "error": str(exc),
            }

        files = output_files(output_dir)
        bscan_image = files["thresholded_image"] or self._find_latest_image(output_dir)
        if not bscan_image:
            return {
                "ok": False,
                "stage": "find_image",
                "error": "没有找到 B 扫图片文件",
            }

        try:
            team_result = build_team_result_package(
                output_dir,
                batch_id=job_id,
                frame_id=f"paut:{job_id}",
                source_path=str(raw_dir.resolve()),
            )
        except (OSError, ValueError, csv.Error) as exc:
            return {
                "ok": False,
                "stage": "load_results",
                "error": f"团队结果包解析失败：{exc}",
            }
        defects = list(team_result.get("defects") or []) if team_result else self._load_ascan_results(output_dir)
        if defects is None:
            return {"ok": False, "stage": "load_results", "error": "没有找到团队结果或兼容结果文件"}

        # 7. 写入数据库：images 表（B 扫图）
        image_id = self._insert_bscan_image(job_id, bscan_image, files, team_result)

        # 8. 写入数据库：defects 表（A 扫缺陷）
        self._insert_defects(job_id, image_id, defects, team_result)

        # 9. 更新任务状态
        with connect() as conn:
            conn.execute(
                "UPDATE jobs SET status = ?, updated_at = ? WHERE id = ?",
                ("PAUT检测完成", self._now(), job_id),
            )
            conn.execute(
                "INSERT INTO audit_logs (entity_type, entity_id, action, detail, created_at) VALUES (?, ?, ?, ?, ?)",
                ("job", job_id, "paut_detect", f"defects={len(defects)}", self._now()),
            )

        analysis = summarize(defects)
        return {
            "ok": True,
            "job_id": job_id,
            "image_id": image_id,
            "defect_count": len(defects),
            "bscan_url": public_url_for(bscan_image),
            "bscan_thresholded_url": public_url_for(bscan_image),
            "bscan_original_url": public_url_for(files["original_image"]) if files["original_image"] else None,
            "analysis": analysis,
            "team_result": team_result,
            "quality": team_result.get("quality") if team_result else None,
            "defects": defects,
            "data_type": "paut",
            "output_dir": str(output_dir),
        }

    # =========================================================
    # 辅助方法
    # =========================================================

    def _find_latest_image(self, output_dir: Path) -> Path | None:
        """查找输出目录中最新的 PNG 图片"""
        candidates = []
        for pattern in ("*.png", "*.jpg", "*.jpeg"):
            candidates.extend(output_dir.glob(pattern))
        if not candidates:
            return None
        return max(candidates, key=lambda p: p.stat().st_mtime)

    def _load_ascan_results(self, output_dir: Path) -> list[dict[str, Any]] | None:
        """Load V2 output first, retaining the legacy CSV fallback."""
        try:
            current = read_defect_report(output_dir)
            if current is not None:
                return current
        except (OSError, ValueError, csv.Error) as exc:
            print(f"[PAUT] 读取 V2 defect_report.csv 失败：{exc}")
            return None
        csv_files = list(output_dir.glob("ascan_results.csv"))
        if not csv_files:
            return None

        csv_path = csv_files[0]
        try:
            with csv_path.open("r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                return [dict(row) for row in reader]
        except Exception as e:
            print(f"[PAUT] 读取 CSV 失败：{e}")
            return None

    def _insert_bscan_image(
        self,
        job_id: str,
        image_path: Path,
        files: dict[str, Path | None],
        team_result: dict[str, Any] | None = None,
    ) -> str:
        """将 B 扫图插入 images 表"""
        image_id = f"paut_{uuid.uuid4().hex[:12]}"
        with Image.open(image_path) as img:
            width, height = img.size

        # 将图片复制到 uploads 目录下（以便通过 /uploads 访问）
        dest_path = settings.upload_dir / job_id / f"{image_id}_bscan.png"
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(image_path, dest_path)

        with connect() as conn:
            conn.execute(
                """
                INSERT INTO images
                (id, job_id, original_name, stored_path, width, height,
                 scan_area, probe_or_channel, calibration_method, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    image_id,
                    job_id,
                    "B扫成像.png",
                    str(dest_path),
                    width,
                    height,
                    "PAUT B扫",
                    "DAS成像",
                    "PAUT-DAS",
                    self._now(),
                ),
            )
            conn.execute(
                "UPDATE images SET source_metadata_json = ?, image_note = ? WHERE id = ?",
                (
                    json.dumps({
                        "pipeline": "turn_paut_v2",
                        "default_view": "thresholded",
                        "thresholded_url": public_url_for(files["thresholded_image"]) if files["thresholded_image"] else "",
                        "original_url": public_url_for(files["original_image"]) if files["original_image"] else "",
                        "display_threshold_only": True,
                        "team_result": team_result,
                    }, ensure_ascii=False),
                    "默认显示阈值截断图；原始未截断图可切换复核。分析基于原始数据。",
                    image_id,
                ),
            )

        return image_id

    def _insert_defects(
        self,
        job_id: str,
        image_id: str,
        defects: list[dict[str, Any]],
        team_result: dict[str, Any] | None = None,
    ) -> None:
        """将 Ascan 缺陷写入 defects 表"""
        with connect() as conn:
            for row in defects:
                x_mm = float(row.get("x_mm", row.get("X_mm", 0)) or 0)
                z_mm = float(row.get("z_mm", row.get("Z_mm", 0)) or 0)
                score = float(row["confidence"]) if row.get("confidence") is not None else 0.0
                if row.get("class_name") == "夹渣":
                    bbox_width = float(row.get("corrected_length_mm") or 1.0)
                    bbox_height = float(row.get("minor_axis_nominal_mm") or 1.0)
                else:
                    bbox_width = float(row.get("width_mm") or row.get("diameter_mm") or 1.0)
                    bbox_height = float(row.get("height_mm") or row.get("diameter_mm") or bbox_width)
                half_width = max(bbox_width / 2.0, 0.5)
                half_height = max(bbox_height / 2.0, 0.5)

                x_min = x_mm - half_width
                y_min = z_mm - half_height
                x_max = x_mm + half_width
                y_max = z_mm + half_height

                defect_id = str(row.get("defect_id") or f"paut_def_{uuid.uuid4().hex[:12]}")
                conn.execute(
                    """
                    INSERT INTO defects
                    (id, job_id, image_id, class_name, confidence,
                    x_min, y_min, x_max, y_max,
                    center_x_px, center_y_px, width_px, height_px,
                    center_x_mm, center_y_mm, width_mm, height_mm,
                    source, review_status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        defect_id,           # id
                        job_id,              # job_id
                        image_id,            # image_id
                        row.get("class_name", "PAUT缺陷"),
                        score,               # confidence
                        x_min,               # x_min
                        y_min,               # y_min
                        x_max,               # x_max
                        y_max,               # y_max
                        x_mm,                # center_x_px
                        z_mm,                # center_y_px
                        x_max - x_min,       # width_px
                        y_max - y_min,       # height_px
                        x_mm,                # center_x_mm
                        z_mm,                # center_y_mm
                        x_max - x_min,       # width_mm
                        y_max - y_min,       # height_mm
                        "paut_v2" if row.get("source") == "paut_v2" else "paut",  # source
                        "待复核",            # review_status
                        self._now(),         # created_at
                        self._now(),         # updated_at
                    ),
                )
                conn.execute(
                    """
                    UPDATE defects SET area_mm2 = ?, suggestion = ?, remark = ?,
                        classification_method = ?, raw_length_mm = ?, corrected_length_mm = ?,
                        diameter_mm = ?, minor_axis_nominal_mm = ?, minor_axis_lower_mm = ?,
                        minor_axis_upper_mm = ?, area_lower_mm2 = ?, area_upper_mm2 = ?,
                        slag_probability = ?, image_angle_deg = ?, aspect_ratio = ?, snr_db = ?,
                        quantification_status = ?, analysis_warning = ?, source_defect_id = ?,
                        result_schema_version = ?, algorithm_version = ?, quality_status = ?,
                        source_payload_json = ?, confidence_available = ?
                    WHERE id = ?
                    """,
                    (
                        row.get("area_mm2"),
                        "建议结合原始未截断图进行人工复核",
                        row.get("warning", ""),
                        row.get("classification_method", ""),
                        row.get("raw_length_mm"),
                        row.get("corrected_length_mm"),
                        row.get("diameter_mm"),
                        row.get("minor_axis_nominal_mm"),
                        row.get("minor_axis_lower_mm"),
                        row.get("minor_axis_upper_mm"),
                        row.get("area_lower_mm2"),
                        row.get("area_upper_mm2"),
                        row.get("slag_probability"),
                        row.get("image_angle_deg"),
                        row.get("aspect_ratio"),
                        row.get("snr_db"),
                        row.get("quantification_status", ""),
                        row.get("warning", ""),
                        row.get("source_defect_id"),
                        (team_result or {}).get("schema_version") or "",
                        (team_result or {}).get("algorithm_version") or "",
                        ((team_result or {}).get("quality") or {}).get("status") or "",
                        json.dumps(row.get("raw_team_values") or {}, ensure_ascii=False),
                        1 if row.get("confidence") is not None else 0,
                        defect_id,
                    ),
                )

    @staticmethod
    def _now() -> str:
        from datetime import datetime
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
