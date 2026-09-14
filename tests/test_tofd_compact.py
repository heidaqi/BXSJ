from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.config import settings
from backend.app.services.tofd_service import TofdService
from backend.app.services.tofd_service import find_tofd_root


class CompactTofdDiscoveryTests(unittest.TestCase):
    def test_direct_compact_mat_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            compact = Path(temporary) / "tx1_rx128_raw_20260831.mat"
            compact.touch()
            self.assertEqual(find_tofd_root(compact), compact)

    def test_compact_mat_is_found_inside_selected_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            compact = root / "tx1_rx128_raw_20260831.mat"
            compact.touch()
            self.assertEqual(find_tofd_root(root), compact)

    def test_legacy_binary_layout_remains_supported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scan = root / "Save_test_001"
            scan.mkdir()
            (scan / "Param.mat").touch()
            (scan / "0.bin").touch()
            self.assertEqual(find_tofd_root(root), root)

    def test_unrelated_mat_inside_directory_is_not_selected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "tofd_analysis.mat").touch()
            self.assertIsNone(find_tofd_root(root))

    def test_upload_copies_compact_mat_to_job_input(self) -> None:
        original_upload_dir = settings.upload_dir
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "tx1_rx128_raw_source.mat"
            source.write_bytes(b"compact-tofd-test")
            settings.upload_dir = root / "uploads"
            try:
                service = TofdService.__new__(TofdService)
                result = service.upload("job_compact", source)
            finally:
                settings.upload_dir = original_upload_dir

            stored = root / "uploads" / "job_compact" / "tofd_raw" / "tx1_rx128_raw.mat"
            self.assertTrue(stored.is_file())
            self.assertEqual(stored.read_bytes(), source.read_bytes())
            self.assertEqual(result["source_format"], "compact_mat_v6")


if __name__ == "__main__":
    unittest.main()
