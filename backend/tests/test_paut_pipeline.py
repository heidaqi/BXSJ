from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.paut.models import ImagingConfig
from backend.app.paut.realtime import RealtimeService
from backend.app.config import settings
from backend.app.database import connect, init_db
from backend.app.services.frame_record_service import frame_record_service
from backend.app.services.analysis_service import _find_data_groups
from backend.app.services.inspection_service import classify_input


class CurrentPautPipelineTests(unittest.TestCase):
    """覆盖团队当前 MATLAB 版实际保留的数据发现与状态逻辑。"""

    @staticmethod
    def _write_numeric_files(directory: Path, count: int = 16) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        for index in range(count):
            (directory / f"{index + 1}.txt").write_text("0 0\n1 1\n", encoding="ascii")

    def test_direct_numeric_dataset_is_classified(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._write_numeric_files(root)
            result = classify_input(root)
            self.assertEqual(result.input_type, "data")
            self.assertEqual(result.data_groups, [root])

    def test_tx_folders_are_discovered_as_ordered_groups(self) -> None:
        with tempfile.TemporaryDirectory(prefix="中文路径_") as temp:
            root = Path(temp)
            tx2 = root / "tx_2"
            tx1 = root / "tx_1"
            self._write_numeric_files(tx2)
            self._write_numeric_files(tx1)
            self.assertEqual(_find_data_groups(root), [tx1, tx2])

    def test_parent_directory_discovers_multiple_groups(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = root / "group_a"
            second = root / "group_b"
            self._write_numeric_files(first)
            self._write_numeric_files(second)
            self.assertEqual(_find_data_groups(root), [first, second])

    def test_incomplete_dataset_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._write_numeric_files(root, count=15)
            result = classify_input(root)
            self.assertEqual(result.input_type, "invalid")

    def test_realtime_service_starts_disconnected(self) -> None:
        service = RealtimeService()
        status = service.status()
        self.assertFalse(status["running"])
        self.assertEqual(status["frame_count"], 0)

    def test_imaging_defaults_match_current_matlab_workflow(self) -> None:
        config = ImagingConfig()
        self.assertLess(config.x_min_mm, config.x_max_mm)
        self.assertLess(config.z_min_mm, config.z_max_mm)
        self.assertGreater(config.pixel_step_mm, 0)

    def test_sampling_rate_is_explicit_and_missing_report_value_is_not_zero(self) -> None:
        from backend.app.paut.realtime import RealtimeConfig
        from backend.app.services.report_service import _sample_rate_mhz

        config = RealtimeConfig(source_path="C:/data", sample_rate_hz=25_000_000)
        self.assertEqual(config.sample_rate_hz, 25_000_000)
        self.assertEqual(_sample_rate_mhz({"sample_rate_hz": 25_000_000}), "25.000")
        self.assertEqual(_sample_rate_mhz({}), "未提供")
        self.assertEqual(_sample_rate_mhz({"sample_rate_hz": 0}), "未提供")

    def test_manual_box_uses_native_das_coordinates_without_yolo(self) -> None:
        import json
        from backend.app.main import _physical_bbox_for_image

        image = {
            "width": 600,
            "height": 400,
            "source_metadata_json": json.dumps({"x_range_mm": [20, 80], "z_range_mm": [0, 40]}),
        }
        result = _physical_bbox_for_image(image, 100, 100, 200, 200)
        self.assertAlmostEqual(result["center_x_mm"], 35.0)
        self.assertAlmostEqual(result["center_y_mm"], 15.0)
        self.assertAlmostEqual(result["width_mm"], 10.0)
        self.assertAlmostEqual(result["height_mm"], 10.0)

    def test_realtime_review_sync_uses_review_database(self) -> None:
        old_output_dir = settings.output_dir
        old_app_data_dir = settings.app_data_dir
        old_upload_dir = settings.upload_dir
        old_database_path = settings.database_path
        old_database_url = settings.database_url
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings.output_dir = root / "outputs"
            settings.app_data_dir = root / "data"
            settings.upload_dir = root / "uploads"
            settings.database_path = root / "data" / "app.db"
            settings.database_url = f"sqlite:///{settings.database_path}"
            try:
                init_db()
                service = RealtimeService()
                frame_id = "frame_test_001"
                service._frames[frame_id] = {"frame_id": frame_id, "analysis": {}, "image": {}}
                batch_id = service.batch_manager.create({
                    "frame_ids": [frame_id],
                    "frame_count": 1,
                    "status": "分析完成",
                    "result": {"local": {"summary": "test"}},
                })
                with connect() as conn:
                    conn.execute(
                        """
                        INSERT INTO jobs
                        (id, batch_name, confidence, status, created_at, updated_at,
                         source_type, source_batch_id)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        ("job_test", "batch", 0.25, "待复核", "now", "now", "realtime", batch_id),
                    )
                    conn.execute(
                        """
                        INSERT INTO images
                        (id, job_id, original_name, stored_path, width, height,
                         scan_area, probe_or_channel, image_note, mm_per_pixel,
                         calibration_method, calibration_note, source_frame_id, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            "img_test", "job_test", "frame.png", str(root / "frame.png"), 100, 100,
                            "焊缝A区", "PAUT通道1", "人工标定", 0.05,
                            "手动比例", "1px=0.05mm", frame_id, "now",
                        ),
                    )
                    conn.execute(
                        """
                        INSERT INTO defects
                        (id, job_id, image_id, class_name, confidence, x_min, y_min, x_max, y_max,
                         center_x_px, center_y_px, width_px, height_px, review_status, suggestion,
                         remark, source, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            "def_test", "job_test", "img_test", "裂纹", 0.9,
                            1, 2, 11, 12, 6, 7, 10, 10, "已确认",
                            "建议复检", "人工确认", "manual", "now", "now",
                        ),
                    )
                result = service.sync_batch_reviews_from_job(batch_id, "job_test")
                self.assertEqual(result["synced_frames"], 1)
                self.assertEqual(result["reviewed_defects"], 1)
                self.assertEqual(result["calibrated_frames"], 1)
                frame = service.get_frame(frame_id)
                self.assertEqual(frame["review"]["status"], "已确认")
                self.assertIn("人工确认", frame["review"]["note"])
                self.assertEqual(frame["calibration"]["mm_per_pixel"], 0.05)
                self.assertEqual(frame["engineering_context"]["calibration"]["scan_area"], "焊缝A区")
                record = frame_record_service.get_frame_record(frame_id, service.get_frame)
                self.assertEqual(record["frame_record_version"], "frame-record-v2-validated")
                self.assertEqual(record["source"]["job_id"], "job_test")
                self.assertEqual(record["review"]["confirmed_count"], 1)
                self.assertEqual(record["review"]["manual_added_count"], 1)
                self.assertEqual(record["calibration"]["probe_or_channel"], "PAUT通道1")
                self.assertEqual(record["manual_context"]["defects"][0]["class_name"], "裂纹")
            finally:
                settings.output_dir = old_output_dir
                settings.app_data_dir = old_app_data_dir
                settings.upload_dir = old_upload_dir
                settings.database_path = old_database_path
                settings.database_url = old_database_url

    def test_batch_calibration_is_persisted_and_applied_atomically(self) -> None:
        from backend.app.main import (
            BatchDefectReviewUpdate,
            JobCalibrationUpdate,
            get_job,
            update_job_calibration,
            update_job_defect_reviews,
        )

        old_output_dir = settings.output_dir
        old_app_data_dir = settings.app_data_dir
        old_upload_dir = settings.upload_dir
        old_database_path = settings.database_path
        old_database_url = settings.database_url
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings.output_dir = root / "outputs"
            settings.app_data_dir = root / "data"
            settings.upload_dir = root / "uploads"
            settings.database_path = root / "data" / "app.db"
            settings.database_url = f"sqlite:///{settings.database_path}"
            try:
                init_db()
                with connect() as conn:
                    conn.execute(
                        """
                        INSERT INTO jobs
                        (id, batch_name, confidence, status, created_at, updated_at, source_type)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        ("job_cal", "batch", 0.25, "待复核", "now", "now", "upload"),
                    )
                    for image_id, width in (("img_a", 100), ("img_b", 120)):
                        conn.execute(
                            """
                            INSERT INTO images
                            (id, job_id, original_name, stored_path, width, height, created_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                            (image_id, "job_cal", f"{image_id}.png", str(root / f"{image_id}.png"), width, 80, "now"),
                        )
                    conn.execute(
                        """
                        INSERT INTO defects
                        (id, job_id, image_id, class_name, confidence, x_min, y_min, x_max, y_max,
                         center_x_px, center_y_px, width_px, height_px, source, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        ("def_cal", "job_cal", "img_a", "裂纹", 0.9, 10, 20, 30, 40, 20, 30, 20, 20, "model", "now", "now"),
                    )

                result = update_job_calibration(
                    "job_cal",
                    JobCalibrationUpdate(
                        source_image_id="img_a",
                        scan_area="焊缝A区",
                        mm_per_pixel=0.1,
                        calibration_method="两点比例尺",
                        point_a_x=10,
                        point_a_y=10,
                        point_b_x=60,
                        point_b_y=10,
                        scale_distance_mm=5,
                    ),
                )
                self.assertEqual(result["updated_images"], 2)
                self.assertTrue(result["warning"])
                job_data = get_job("job_cal")
                self.assertEqual(job_data["batch_calibration"]["source_image_id"], "img_a")
                self.assertEqual(job_data["batch_calibration"]["mm_per_pixel"], 0.1)
                self.assertTrue(all(image["mm_per_pixel"] == 0.1 for image in job_data["images"]))
                self.assertEqual(job_data["defects"][0]["center_x_mm"], 2.0)
                self.assertEqual(job_data["defects"][0]["width_mm"], 2.0)

                update_job_calibration(
                    "job_cal",
                    JobCalibrationUpdate(
                        source_image_id="img_b",
                        mm_per_pixel=0.2,
                        calibration_method="手动比例",
                    ),
                )
                updated = get_job("job_cal")
                self.assertEqual(updated["batch_calibration"]["source_image_id"], "img_b")
                self.assertEqual(updated["defects"][0]["center_x_mm"], 4.0)

                review_result = update_job_defect_reviews(
                    "job_cal",
                    BatchDefectReviewUpdate(defect_ids=["def_cal"], review_status="已确认"),
                )
                self.assertEqual(review_result["updated_count"], 1)
                self.assertEqual(get_job("job_cal")["defects"][0]["review_status"], "已确认")
            finally:
                settings.output_dir = old_output_dir
                settings.app_data_dir = old_app_data_dir
                settings.upload_dir = old_upload_dir
                settings.database_path = old_database_path
                settings.database_url = old_database_url


if __name__ == "__main__":
    unittest.main()
