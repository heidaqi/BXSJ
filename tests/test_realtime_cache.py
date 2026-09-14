from __future__ import annotations

import gzip
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from backend.app.config import settings
from backend.app.database import connect
from backend.app.services.realtime_cache_service import RealtimeCacheService
from backend.app.services.realtime_handoff_service import materialize_realtime_session


class RealtimeCacheTests(unittest.TestCase):
    def test_compaction_removes_only_regenerable_cache(self) -> None:
        names = ("app_data_dir", "upload_dir", "output_dir", "database_path")
        original = {name: getattr(settings, name) for name in names}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            try:
                object.__setattr__(settings, "app_data_dir", root)
                object.__setattr__(settings, "upload_dir", root / "uploads")
                object.__setattr__(settings, "output_dir", root / "outputs")
                object.__setattr__(settings, "database_path", root / "data" / "app.db")

                session_id = "session_cache_test"
                session_dir = settings.output_dir / "realtime" / "sessions" / session_id
                image_path = session_dir / "images" / "frame.png"
                image_path.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (20, 20), "white").save(image_path)
                intermediate = session_dir / "groups" / "group_0001" / "matrix.csv"
                intermediate.parent.mkdir(parents=True, exist_ok=True)
                intermediate.write_text("large-intermediate-data", encoding="utf-8")
                tofd_state = session_dir / "tofd_state" / "state.bin"
                tofd_state.parent.mkdir(parents=True, exist_ok=True)
                tofd_state.write_bytes(b"state")

                result = materialize_realtime_session(
                    session_id=session_id,
                    source_path="D:/source",
                    frames=[{
                        "frame_id": "frame_cache_test",
                        "source_group": "group_0001",
                        "image": {
                            "url": f"/outputs/realtime/sessions/{session_id}/images/frame.png",
                            "annotated_url": f"/outputs/realtime/sessions/{session_id}/images/frame.png",
                        },
                        "analysis": {"ascan": {"count": 0}, "yolo": {"detections": []}},
                    }],
                    batch_id="",
                )
                report = settings.output_dir / "reports" / "kept.pdf"
                report.parent.mkdir(parents=True, exist_ok=True)
                report.write_bytes(b"pdf")
                with connect() as conn:
                    conn.execute("UPDATE jobs SET report_path = ? WHERE id = ?", (str(report), result["job_id"]))

                cleanup = RealtimeCacheService().run_once(force=True)

                self.assertEqual(cleanup["compacted_jobs"], 1)
                self.assertFalse((session_dir / "groups").exists())
                self.assertFalse((session_dir / "tofd_state").exists())
                self.assertTrue(image_path.exists())
                self.assertTrue(report.exists())
                archive = settings.output_dir / "realtime" / "history" / f"{session_id}.json.gz"
                self.assertTrue(archive.exists())
                with gzip.open(archive, "rt", encoding="utf-8") as stream:
                    manifest = json.load(stream)
                self.assertEqual(manifest["statistics"]["image_count"], 1)
                with connect() as conn:
                    job = conn.execute("SELECT storage_state FROM jobs WHERE id = ?", (result["job_id"],)).fetchone()
                self.assertEqual(job["storage_state"], "compacted")
            finally:
                for name, value in original.items():
                    object.__setattr__(settings, name, value)


if __name__ == "__main__":
    unittest.main()
