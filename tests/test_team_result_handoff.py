from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from backend.app.config import settings
from backend.app.database import connect
from backend.app.services.realtime_handoff_service import materialize_realtime_session


class TeamResultHandoffTests(unittest.TestCase):
    def test_team_result_provenance_is_persisted(self) -> None:
        original = {
            name: getattr(settings, name)
            for name in ["app_data_dir", "upload_dir", "output_dir", "database_path"]
        }
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            try:
                object.__setattr__(settings, "app_data_dir", root)
                object.__setattr__(settings, "upload_dir", root / "uploads")
                object.__setattr__(settings, "output_dir", root / "outputs")
                object.__setattr__(settings, "database_path", root / "data" / "app.db")
                image_path = settings.output_dir / "realtime" / "frame.png"
                image_path.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (100, 80), "white").save(image_path)
                defect = {
                    "defect_id": "frame-1:team:1",
                    "source_defect_id": "1",
                    "source": "paut_v2",
                    "class_name": "气孔",
                    "confidence": None,
                    "x_mm": 40.0,
                    "z_mm": 20.0,
                    "diameter_mm": 3.0,
                    "area_mm2": 7.1,
                    "quantification_status": "完成",
                    "raw_team_values": {"Type": "Pore"},
                }
                package = {
                    "schema_version": "team-inspection-package/v1",
                    "algorithm_version": "team-paut-v2",
                    "quality": {"status": "可信", "issues": [], "blocking": False},
                    "defects": [defect],
                }
                frame = {
                    "frame_id": "frame-1",
                    "source_group": "group-1",
                    "source_path": "D:/data/group-1",
                    "image": {
                        "url": "/outputs/realtime/frame.png",
                        "annotated_url": "/outputs/realtime/frame.png",
                        "x_range_mm": [20, 80],
                        "z_range_mm": [0, 40],
                    },
                    "analysis": {"team_result": package, "paut_v2": {}, "yolo": {}},
                    "team_result": package,
                    "defects": [defect],
                }
                result = materialize_realtime_session(
                    session_id="session-team",
                    source_path="D:/data",
                    frames=[frame],
                    batch_id="batch-team",
                )
                with connect() as conn:
                    stored = dict(conn.execute("SELECT * FROM defects WHERE id = ?", ("frame-1:team:1",)).fetchone())
                    job = dict(conn.execute("SELECT status FROM jobs WHERE id = ?", (result["job_id"],)).fetchone())
                self.assertTrue(result["created"])
                self.assertEqual(stored["source"], "validated_team_result")
                self.assertEqual(stored["source_defect_id"], "1")
                self.assertEqual(stored["result_schema_version"], "team-inspection-package/v1")
                self.assertEqual(stored["quality_status"], "可信")
                self.assertEqual(stored["confidence_available"], 0)
                self.assertEqual(stored["area_mm2"], 7.1)
                self.assertEqual(job["status"], "待复核")
            finally:
                for name, value in original.items():
                    object.__setattr__(settings, name, value)

    def test_trusted_empty_session_does_not_require_review(self) -> None:
        original = {
            name: getattr(settings, name)
            for name in ["app_data_dir", "upload_dir", "output_dir", "database_path"]
        }
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            try:
                object.__setattr__(settings, "app_data_dir", root)
                object.__setattr__(settings, "upload_dir", root / "uploads")
                object.__setattr__(settings, "output_dir", root / "outputs")
                object.__setattr__(settings, "database_path", root / "data" / "app.db")
                image_path = settings.output_dir / "realtime" / "empty.png"
                image_path.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (100, 80), "white").save(image_path)
                package = {
                    "schema_version": "team-inspection-package/v1",
                    "algorithm_version": "team-paut-v2",
                    "quality": {"status": "可信", "issues": [], "blocking": False},
                    "defects": [],
                }
                frame = {
                    "frame_id": "frame-empty",
                    "source_group": "group-empty",
                    "source_path": "D:/data/group-empty",
                    "image": {
                        "url": "/outputs/realtime/empty.png",
                        "x_range_mm": [20, 80],
                        "z_range_mm": [0, 40],
                    },
                    "analysis": {"team_result": package, "paut_v2": {}, "yolo": {}},
                    "team_result": package,
                    "defects": [],
                }
                result = materialize_realtime_session(
                    session_id="session-empty",
                    source_path="D:/data",
                    frames=[frame],
                    batch_id="batch-empty",
                )
                with connect() as conn:
                    job = dict(conn.execute("SELECT status FROM jobs WHERE id = ?", (result["job_id"],)).fetchone())
                self.assertEqual(job["status"], "无需复核")
                self.assertEqual(result["defect_count"], 0)
            finally:
                for name, value in original.items():
                    object.__setattr__(settings, name, value)


if __name__ == "__main__":
    unittest.main()
