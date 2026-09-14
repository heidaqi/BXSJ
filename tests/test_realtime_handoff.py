from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from backend.app.config import settings
from backend.app.database import connect
from backend.app.services.realtime_handoff_service import materialize_realtime_session


class RealtimeHandoffTests(unittest.TestCase):
    def test_handoff_is_idempotent_and_preserves_frame_mapping(self) -> None:
        original_values = {
            "app_data_dir": settings.app_data_dir,
            "upload_dir": settings.upload_dir,
            "output_dir": settings.output_dir,
            "database_path": settings.database_path,
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            try:
                object.__setattr__(settings, "app_data_dir", root)
                object.__setattr__(settings, "upload_dir", root / "uploads")
                object.__setattr__(settings, "output_dir", root / "outputs")
                object.__setattr__(settings, "database_path", root / "data" / "app.db")
                image_path = settings.output_dir / "realtime" / "frame.png"
                image_path.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (100, 80), "white").save(image_path)
                frame = {
                    "frame_id": "frame_test_1",
                    "source_group": "group_1",
                    "source_path": "D:/data/group_1",
                    "velocity_mps": 5900,
                    "plate_thickness_mm": 40,
                    "image": {
                        "url": "/outputs/realtime/frame.png",
                        "annotated_url": "/outputs/realtime/frame.png",
                        "x_range_mm": [20, 80],
                        "z_range_mm": [0, 40],
                    },
                    "team_result": {
                        "schema_version": "team-inspection-package/2.2",
                        "provenance": {"provider": "team_algorithm"},
                        "summary": {"pipeline": "team_paut_v2"},
                        "quality": {"status": "通过", "warnings": []},
                        "defects": [{
                            "defect_id": "team-defect-1",
                            "class_name": "裂纹",
                            "confidence": 0.8,
                            "x_mm": 35,
                            "z_mm": 15,
                            "corrected_length_mm": 12,
                        }],
                    },
                }

                first = materialize_realtime_session(
                    session_id="session_test", source_path="D:/data", frames=[frame], batch_id="batch_test"
                )
                second = materialize_realtime_session(
                    session_id="session_test", source_path="D:/data", frames=[frame], batch_id="batch_test"
                )

                self.assertTrue(first["created"])
                self.assertFalse(second["created"])
                self.assertEqual(first["job_id"], second["job_id"])
                with connect() as conn:
                    self.assertEqual(conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 1)
                    image = conn.execute("SELECT * FROM images").fetchone()
                    defect = conn.execute("SELECT * FROM defects").fetchone()
                    self.assertEqual(image["source_frame_id"], "frame_test_1")
                    self.assertEqual(image["calibration_method"], "未标定")
                    self.assertEqual(defect["source"], "validated_team_result")
                    self.assertEqual(defect["center_x_mm"], 35)
                    self.assertEqual(defect["center_y_mm"], 15)
            finally:
                for name, value in original_values.items():
                    object.__setattr__(settings, name, value)


if __name__ == "__main__":
    unittest.main()
