from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from ..database import connect


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def save_receipt(receipt: dict[str, Any], state: str, error: str = "") -> None:
    with connect() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS scan_receipts (
            frame_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
            received_at TEXT NOT NULL, state TEXT NOT NULL,
            evidence_json TEXT NOT NULL, error TEXT NOT NULL DEFAULT ''
        )""")
        conn.execute("""INSERT INTO scan_receipts
            (frame_id, session_id, received_at, state, evidence_json, error)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(frame_id) DO UPDATE SET state=excluded.state,
            evidence_json=excluded.evidence_json, error=excluded.error""",
            (receipt["frame_id"], receipt["session_id"], receipt["received_at"],
             state, json.dumps(receipt, ensure_ascii=False), error))


def audit_scan_frames(frames: list[dict[str, Any]]) -> dict[str, Any]:
    errors: list[str] = []
    receipts = []
    for frame in frames:
        scan = frame.get("scan_receipt") or {}
        if not scan:
            continue
        receipts.append(scan)
        if scan.get("frame_id") != frame.get("frame_id") or scan.get("received_at") != frame.get("received_at"):
            errors.append("采集记录与帧身份或扫查时间不一致")
        try:
            received = datetime.fromisoformat(scan["received_at"])
            started = datetime.fromisoformat(scan["processing_started_at"])
            completed = datetime.fromisoformat(scan["processing_completed_at"])
            if not received <= started <= completed:
                errors.append("扫查与处理时间顺序异常")
        except (KeyError, TypeError, ValueError):
            errors.append("采集或处理时间缺失")
    return {"passed": not errors, "errors": list(dict.fromkeys(errors)),
            "mode": "模拟采集" if receipts else "未记录采集模式",
            "received_frame_count": len(receipts),
            "position_basis": "速度与扫查时间估算的扫查距离，不是编码器位置；不能据此合并帧间缺陷"}
