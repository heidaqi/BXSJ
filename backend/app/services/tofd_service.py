"""Independent TOFD ingestion for compact v6 MAT files and legacy binaries."""
from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image

from ..config import settings
from ..database import connect, public_url_for
from .matlab_service import MatlabRuntimeError, get_matlab_service


def find_tofd_root(root: Path) -> Path | None:
    """Return a compact MAT file or a legacy Save_* acquisition root."""
    root = Path(root)
    if root.is_file() and root.suffix.lower() == ".mat":
        return root
    if not root.is_dir():
        return None

    compact_files = sorted(root.glob("tx1_rx128_raw*.mat"))
    if compact_files:
        return compact_files[0]

    candidates = [root, *[item for item in root.iterdir() if item.is_dir()]]
    for candidate in candidates:
        compact_files = sorted(candidate.glob("tx1_rx128_raw*.mat"))
        if compact_files:
            return compact_files[0]
        scan_dirs = [item for item in candidate.glob("Save_*") if item.is_dir()]
        if scan_dirs and all((item / "Param.mat").is_file() and any(item.glob("*.bin")) for item in scan_dirs):
            return candidate
    return None


class TofdService:
    def __init__(self) -> None:
        self.matlab = get_matlab_service()

    def upload(self, job_id: str, source_dir: Path) -> dict[str, Any]:
        source_root = find_tofd_root(Path(source_dir))
        if source_root is None:
            raise ValueError("未找到有效 TOFD 数据：请选择 Tx1-Rx128 紧凑 MAT，或包含 Save_* 的原始数据包")
        destination = settings.upload_dir / job_id / "tofd_raw"
        destination.mkdir(parents=True, exist_ok=True)
        if source_root.is_file():
            stored_file = destination / "tx1_rx128_raw.mat"
            shutil.copy2(source_root, stored_file)
            return {
                "ok": True,
                "dest_dir": str(destination),
                "source_file": str(stored_file),
                "source_format": "compact_mat_v6",
                "scan_count": None,
            }

        shutil.copytree(source_root, destination, dirs_exist_ok=True)
        scan_count = len([item for item in destination.glob("Save_*") if item.is_dir()])
        return {
            "ok": True,
            "dest_dir": str(destination),
            "source_format": "legacy_binary",
            "scan_count": scan_count,
        }

    def detect(self, job_id: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        raw_dir = settings.upload_dir / job_id / "tofd_raw"
        return self._detect(raw_dir, job_id, params)

    def detect_local(self, job_id: str, data_dir: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        source_root = find_tofd_root(Path(data_dir))
        if source_root is None:
            raise ValueError("未找到有效 TOFD 数据：请选择 Tx1-Rx128 紧凑 MAT，或包含 Save_* 的原始数据目录")
        return self._detect(source_root, job_id, params)

    def _detect(self, raw_dir: Path, job_id: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        source_root = find_tofd_root(raw_dir)
        if source_root is None:
            raise ValueError("TOFD 原始数据不存在或结构不完整")
        output_dir = settings.output_dir / "tofd" / job_id
        output_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.matlab.run_tofd(source_root, output_dir, output_dir, timeout=900, params=params)
        except MatlabRuntimeError as exc:
            return {"ok": False, "stage": "matlab", "error": str(exc)}

        image_path = output_dir / "tofd_analysis.png"
        summary_path = output_dir / "tofd_summary.json"
        if not image_path.is_file() or not summary_path.is_file():
            return {"ok": False, "stage": "outputs", "error": "TOFD 流程未生成完整的成像图和分析摘要"}
        summary = json.loads(summary_path.read_text(encoding="utf-8-sig"))
        image_id = f"tofd_{uuid.uuid4().hex[:12]}"
        with Image.open(image_path) as image:
            width, height = image.size
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO images
                (id, job_id, original_name, stored_path, width, height, scan_area,
                 probe_or_channel, image_note, calibration_method, source_metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    image_id, job_id, "TOFD成像与分析.png", str(image_path), width, height,
                    "TOFD B扫", f"Tx{summary.get('tx_channel', 1)}-Rx{summary.get('rx_channel', 128)}",
                    "TOFD 独立数据入口；不与 PAUT 成像混用。",
                    "TOFD紧凑MAT v6" if summary.get("source_format") == "compact_mat_v6" else "TOFD实际采集格式",
                    json.dumps(summary, ensure_ascii=False), self._now(),
                ),
            )
            conn.execute("UPDATE jobs SET status = ?, updated_at = ? WHERE id = ?", ("TOFD分析完成", self._now(), job_id))
            conn.execute(
                "INSERT INTO audit_logs (entity_type, entity_id, action, detail, created_at) VALUES (?, ?, ?, ?, ?)",
                ("job", job_id, "tofd_detect", f"scans={summary.get('scan_count', 0)}", self._now()),
            )
        return {
            "ok": True,
            "job_id": job_id,
            "image_id": image_id,
            "data_type": "tofd",
            "image_url": public_url_for(image_path),
            "analysis": summary,
            "output_dir": str(output_dir),
        }

    @staticmethod
    def _now() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
