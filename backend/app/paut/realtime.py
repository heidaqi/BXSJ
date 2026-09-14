from __future__ import annotations

import asyncio
import csv
import json
import logging
import math
import queue
import shutil
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional

import numpy as np

from ..config import settings
from ..services.matlab_service import MatlabRuntimeError, get_matlab_service
from ..services.ascan_candidate_model import accepted_ascan_candidates
from ..services.frame_record_service import frame_record_service
from ..services.fmc_data_inspector import (
    FmcDataInspector,
    InspectionResult,
    LEGACY_16X16_POSITIONS_MM,
    SUPPORTED_16X16_PROFILES,
)
from ..services.ascan_classifier_service import classify_frame
from ..services.paut_v2_adapter import summarize
from ..services.team_result_service import build_team_result_package
from ..services.resource_guard import require_processing_headroom
from .models import ImagingConfig


logger = logging.getLogger(__name__)

COMSOL_16X16_POSITIONS_MM = [30.0, 32.0, 34.0, 36.0, 38.0, 40.0, 42.0, 44.0,
                             49.0, 51.0, 53.0, 55.0, 57.0, 59.0, 61.0, 63.0]
COMSOL_LEGACY_16X16_POSITIONS_MM = list(LEGACY_16X16_POSITIONS_MM)


@dataclass
class RealtimeConfig:
    source_path: str
    adapter: str = "comsol_text_folder"
    profile: str = "comsol_16x16_das_m"
    target_job_id: str = ""
    interval_seconds: float = 0.0
    simulation_enabled: bool = False
    vehicle_speed_mm_s: float = 0.0
    velocity_mps: float = 5900.0
    sample_rate_hz: float = 50_000_000.0
    plate_thickness_mm: float = 40.0
    tx_positions_mm: Optional[List[float]] = None
    rx_positions_mm: Optional[List[float]] = None
    background_reference_path: Optional[str] = None
    background_scale: float = 1.0
    imaging: ImagingConfig = field(default_factory=ImagingConfig)
    # PAUT and TOFD are separate input types. Legacy fields remain API-compatible.
    data_type: str = "paut"
    tofd_enabled: bool = False
    tofd_tx: float = 30.0
    tofd_rx: float = 63.0
    tofd_window_size: int = 30
    # TOFD 时间轴截断上限（μs）：不同组时间长度不一致（12μs vs 15μs）时，
    # 超过该值的采样点直接丢弃，保证各组点数一致、可正常拼接。
    tofd_max_time_us: float = 12.0


class SimpleBatchManager:
    """线程安全、可恢复的采集后Agent批次存储。"""

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._batches: Dict[str, Dict[str, Any]] = {}
        self._order: List[str] = []
        self._lock = threading.RLock()
        self._retry_handler: Callable[[str], None] | None = None
        self._load()

    def set_retry_handler(self, handler: Callable[[str], None]) -> None:
        self._retry_handler = handler

    def _path(self, batch_id: str) -> Path:
        return self.root / f"{batch_id}.json"

    def _load(self) -> None:
        for path in sorted(self.root.glob("batch_*.json"), key=lambda item: item.stat().st_mtime):
            try:
                data = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError):
                continue
            batch_id = data.get("batch_id")
            if batch_id:
                if data.get("status") in {"分析中", "排队中"}:
                    data["status"] = "已中断"
                    data["error"] = "上次应用退出时分析尚未完成，可点击重试。"
                self._batches[batch_id] = data
                self._order.append(batch_id)
                self._persist(batch_id)

    def _persist(self, batch_id: str) -> None:
        path = self._path(batch_id)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(self._batches[batch_id], ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def list(self, limit: int = 30) -> List[Dict[str, Any]]:
        with self._lock:
            ids = self._order[-limit:]
            return [dict(self._batches[bid]) for bid in reversed(ids) if bid in self._batches]
    
    def get(self, batch_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            value = self._batches.get(batch_id)
            return dict(value) if value else None
    
    def create(self, data: Dict[str, Any]) -> str:
        batch_id = f"batch_{uuid.uuid4().hex[:8]}"
        with self._lock:
            data["batch_id"] = batch_id
            data["created_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            self._batches[batch_id] = data
            self._order.append(batch_id)
            self._persist(batch_id)
        return batch_id

    def update(self, batch_id: str, **changes: Any) -> Dict[str, Any]:
        with self._lock:
            if batch_id not in self._batches:
                raise ValueError("分析批次不存在")
            self._batches[batch_id].update(changes)
            self._persist(batch_id)
            return dict(self._batches[batch_id])

    def retry(self, batch_id: str) -> Dict[str, Any]:
        batch = self.get(batch_id)
        if batch is None:
            raise ValueError("分析批次不存在")
        if batch.get("status") in {"分析中", "排队中"}:
            raise RuntimeError("该批次正在处理，无需重复提交")
        updated = self.update(batch_id, status="排队中", error="")
        if self._retry_handler is None:
            raise RuntimeError("Agent重试处理器尚未就绪")
        self._retry_handler(batch_id)
        return updated

    def set_report(self, batch_id: str, report_path: Path) -> None:
        self.update(
            batch_id,
            report_path=str(report_path),
            report_generated_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        )


class RealtimeService:
    """PAUT 实时处理服务 - 复用 MatlabService 进行真实 DAS 成像"""

    def __init__(self):
        self._config: Optional[RealtimeConfig] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._frames: Dict[str, Dict[str, Any]] = {}
        self._frame_order: Deque[str] = deque()
        self._frame_counter = 0
        self._session_frame_ids: List[str] = []
        self._session_id = ""
        self._finalize_lock = threading.Lock()
        self._current_frame_id: Optional[str] = None
        self._subscribers: List[Callable[[Dict[str, Any]], None]] = []
        self._last_processed_groups: set = set()
        # 会话过程可观测字段：逐组成功/失败与处理前内存快照（写入 session.json）
        self._processed_groups: List[str] = []
        self._failed_groups: List[Dict[str, str]] = []
        self._group_resource_snapshots: List[Dict[str, Any]] = []
        self._agent_lock = threading.Lock()
        self._matlab = get_matlab_service()
        self._data_inspector = FmcDataInspector(COMSOL_16X16_POSITIONS_MM)
        self._validated_group_positions: Dict[str, List[float]] = {}
        self._validated_group_sample_rates: Dict[str, float] = {}
        self._receipt_queue = queue.Queue(maxsize=8)
        self._receiver_done = threading.Event()
        self._receiver_thread = None
        self._active_receipt = None
        self.batch_manager = SimpleBatchManager(settings.output_dir / "agent" / "batches")
        self.batch_manager.set_retry_handler(self._start_agent_batch)

        self._status = {
            "state": "未配置",
            "running": False,
            "frame_count": 0,
            "message": "等待配置",
            "source_mode": "序列目录",
            "processed_source_groups": 0,
            "source_group_count": 0,
            "source_changed": False,
        }

    def status(self) -> Dict[str, Any]:
        self._recover_inactive_session()
        data = self._status.copy()
        data["configured_source_path"] = self._config.source_path if self._config else ""
        data["target_job_id"] = self._config.target_job_id if self._config else ""
        data["current_frame_id"] = self._current_frame_id
        data["session_frame_count"] = len(self._session_frame_ids)
        data["analysis_ready"] = bool(
            self._session_frame_ids
            and not self._running
            and not self._status.get("finalizing")
            and not self._status.get("session_batch_id")
        )
        data["agent_queue_length"] = sum(
            item.get("status") in {"排队中", "分析中"} for item in self.batch_manager.list(100)
        )
        data["pending_visual_frames"] = 0
        data["session_id"] = self._session_id
        return data

    def _session_manifest_path(self, session_id: str | None = None) -> Path:
        sid = session_id or self._session_id
        return settings.output_dir / "realtime" / "sessions" / sid / "session.json"

    def _write_session_manifest(self, state: str, **changes: Any) -> None:
        if not self._session_id:
            return
        path = self._session_manifest_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        data: Dict[str, Any] = {}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError):
                data = {}
        data.update({
            "session_id": self._session_id,
            "state": state,
            "source_path": self._config.source_path if self._config else "",
            "frame_ids": list(self._session_frame_ids),
            "frame_count": len(self._session_frame_ids),
            "processed_groups": list(self._processed_groups),
            "failed_groups": list(self._failed_groups),
            "resource_snapshots": list(self._group_resource_snapshots),
            "stop_reason": self._status.get("stop_reason") or "",
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        data.update(changes)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def list_sessions(self, limit: int = 30) -> List[Dict[str, Any]]:
        root = settings.output_dir / "realtime" / "sessions"
        items: List[Dict[str, Any]] = []
        for path in root.glob("session_*/session.json") if root.exists() else []:
            try:
                items.append(json.loads(path.read_text(encoding="utf-8-sig")))
            except (OSError, json.JSONDecodeError):
                continue
        return sorted(items, key=lambda item: item.get("started_at", item.get("updated_at", "")), reverse=True)[:limit]

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        if not session_id.startswith("session_"):
            return None
        path = self._session_manifest_path(session_id)
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return None

    def get_session_frames(self, session_id: str) -> Optional[List[Dict[str, Any]]]:
        session = self.get_session(session_id)
        if session is None:
            return None
        return [frame for frame_id in session.get("frame_ids", []) if (frame := self.get_frame(frame_id)) is not None]

    def configure(self, config: RealtimeConfig) -> Dict[str, Any]:
        self._recover_inactive_session()
        if self._running or self._status.get("finalizing") or (self._thread and self._thread.is_alive()):
            raise RuntimeError("无法在运行时重新配置，请先停止处理")
        if self._receiver_thread and self._receiver_thread.is_alive():
            raise RuntimeError("上一批扫查数据仍在结束处理，请稍候再校验")

        source_path = Path(config.source_path)
        if not source_path.exists():
            raise ValueError(f"数据目录不存在: {config.source_path}")

        if config.profile == "comsol_16x16_das_m":
            requested_positions = list(config.tx_positions_mm or COMSOL_16X16_POSITIONS_MM)
            selected_profile = next((
                list(positions) for positions in SUPPORTED_16X16_PROFILES.values()
                if len(requested_positions) == len(positions)
                and np.allclose(requested_positions, positions, rtol=0, atol=1e-6)
            ), None)
            if selected_profile is None:
                raise ValueError("当前支持30-63和22-70两套16阵元配置，请从探头方案中选择")
            config.tx_positions_mm = selected_profile
            requested_rx = list(config.rx_positions_mm or config.tx_positions_mm)
            if len(requested_rx) != 16 or not np.allclose(
                requested_rx, selected_profile, rtol=0, atol=1e-6
            ):
                raise ValueError("当前接收阵元必须与所选发射阵元配置一致")
            config.rx_positions_mm = list(selected_profile)
        background_explicit = bool(config.background_reference_path)
        background_path = self._resolve_background_reference(source_path, config.background_reference_path)
        config.background_reference_path = str(background_path.resolve()) if background_path else None

        # Discovery depends on the candidate probe layout. Keep the previous
        # configuration intact unless every source and background check succeeds.
        previous_config = self._config
        previous_inspector = self._data_inspector
        previous_group_positions = self._validated_group_positions
        previous_group_sample_rates = self._validated_group_sample_rates
        self._config = config
        self._data_inspector = FmcDataInspector(
            config.tx_positions_mm or COMSOL_16X16_POSITIONS_MM,
            supported_profiles=SUPPORTED_16X16_PROFILES,
        )
        try:
            inspection = self._data_inspector.inspect(source_path)
            data_groups = inspection.valid_paths
            if not data_groups:
                details = "；".join(item.reason for item in inspection.skipped_groups[:3])
                raise ValueError(
                    "未找到通过校验的16×16 FMC数据组"
                    + (f"：{details}" if details else "。支持Tx多文件、tx_N/Rx目录和单文件FMC宽表。")
                )
            mismatched_rates = [
                item for item in inspection.valid_groups
                if item.sample_rate_hz
                and abs(item.sample_rate_hz - config.sample_rate_hz) / config.sample_rate_hz > 0.01
            ]
            if mismatched_rates:
                first = mismatched_rates[0]
                raise ValueError(
                    f"数据组 {Path(first.path).name} 的实际采样率为 "
                    f"{first.sample_rate_hz / 1e6:.3f} MHz，与设定值 "
                    f"{config.sample_rate_hz / 1e6:.3f} MHz 不一致"
                )
            validation = inspection.to_dict()
            if background_path is not None:
                background_inspection = self._data_inspector.inspect(background_path)
                if not background_inspection.valid_paths:
                    raise ValueError("背景参考未通过FMC数据校验")
            self._remember_group_positions(inspection)
        except Exception:
            self._config = previous_config
            self._data_inspector = previous_inspector
            self._validated_group_positions = previous_group_positions
            self._validated_group_sample_rates = previous_group_sample_rates
            raise

        self._session_frame_ids = []
        self._session_id = f"session_{uuid.uuid4().hex[:12]}"
        self._status["session_id"] = self._session_id
        self._current_frame_id = None
        self._receipt_queue = queue.Queue(maxsize=8)
        self._active_receipt = None
        self._receiver_thread = None
        self._receiver_done.set()
        self._status.update(last_error=None, received_count=0, waiting_count=0,
                            reception_state="", sequence_complete=False, running=False)
        self._frame_counter = 0
        self._processed_groups = []
        self._failed_groups = []
        self._group_resource_snapshots = []
        self._status["session_batch_id"] = None
        self._status["handoff_batch_id"] = None
        self._status["handoff_job_id"] = None
        self._status["handoff_error"] = ""
        self._status["stop_reason"] = ""
        self._status["finalizing"] = False
        self._status["frame_count"] = 0
        self._status["processed_source_groups"] = 0
        self._status["source_changed"] = False
        self._status["state"] = "已配置"
        self._status["message"] = f"已配置数据源: {config.source_path}"

        # 清空上次的 TOFD 累计状态与静态图，避免新数据源混入旧道
        self._reset_tofd_state()

        # 统一统计：根目录多 Tx 文件、tx_N 子目录格式、单/多 FMC 文件及其多组父目录。
        self._status["source_group_count"] = len(data_groups)
        self._status["input_validation"] = validation
        self._status["background_reference"] = {
            "enabled": background_path is not None,
            "path": str(background_path.resolve()) if background_path else "",
            "scale": config.background_scale,
            "source": "显式指定" if background_explicit and background_path else (
                "项目内置data/groups/none"
                if background_path and background_path.resolve() == (
                    settings.resource_dir / "data" / "groups" / "none"
                ).resolve()
                else "自动识别同级none" if background_path else "未使用"
            ),
        }
        if source_path.is_file():
            self._status["source_mode"] = "单文件 FMC 格式"
        elif self._is_tx_folder_group(source_path):
            self._status["source_mode"] = "Tx/Rx 子目录格式"
        elif self._contains_fmc_file(source_path):
            self._status["source_mode"] = "FMC 文件序列" if len(data_groups) > 1 else "单文件 FMC 格式"
        elif len(data_groups) == 1:
            self._status["source_mode"] = "文件模式"
        else:
            self._status["source_mode"] = "序列目录"

        return self._status

    def _recover_inactive_session(self) -> None:
        # A dead worker cannot clear its own flags after an unexpected exit.
        if self._thread is not None and not self._thread.is_alive():
            if self._running or self._status.get("finalizing"):
                self._stop_event.set()
                self._running = False
                self._status.update(running=False, finalizing=False, state="处理已中断",
                                    message="处理已中断，已保存结果保留。请重新校验数据源后开始。")
            self._thread = None

    def start(self) -> Dict[str, Any]:
        if self._running:
            raise RuntimeError("实时处理已在运行中")
        if self._config is None:
            raise RuntimeError("请先调用 configure 配置数据源")
        if self._status.get("finalizing") or (self._thread and self._thread.is_alive()):
            raise RuntimeError("上一批数据正在完成当前组并归档，请稍候")
        if self._status.get("handoff_job_id"):
            raise RuntimeError("本次处理结果已归档，请重新选择并校验数据源后开始新任务")

        self._status["resource_status"] = require_processing_headroom()

        self._running = True
        self._write_session_manifest("处理中", started_at=time.strftime("%Y-%m-%d %H:%M:%S"))
        self._stop_event.clear()
        if self._config.simulation_enabled:
            self._receipt_queue = queue.Queue(maxsize=8)
            self._receiver_done.clear()
            self._status.update(received_count=0, waiting_count=0, reception_state="扫查中")
            self._receiver_thread = threading.Thread(target=self._receive_simulated_frames, daemon=True)
            self._receiver_thread.start()
        self._thread = threading.Thread(target=self._process_loop, daemon=True)
        self._status["running"] = True
        self._status["state"] = "运行中"
        self._status["message"] = "处理已启动，正在扫描数据..."
        self._thread.start()
        return self._status

    def stop(self) -> Dict[str, Any]:
        if self._status.get("handoff_job_id"):
            return self.status()
        self._running = False
        self._stop_event.set()
        self._status["running"] = False
        self._status["finalizing"] = True
        self._status["state"] = "正在结束"
        self._status["message"] = "正在完成当前数据组并归档，请勿关闭程序"
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=3.0)
        if not self._thread or not self._thread.is_alive():
            self._thread = None
            self._finalize_session()
        return self.status()

    def _finalize_session(self) -> None:
        """冻结当前帧并交给旧的标定、复核、Agent和报告业务。"""
        with self._finalize_lock:
            if self._status.get("handoff_job_id"):
                return
            self._status["running"] = False
            self._status["finalizing"] = True
            if not self._session_frame_ids:
                self._status["state"] = "已停止"
                self._status["message"] = "处理已停止，但没有可归档的已完成数据"
                self._status["finalizing"] = False
                self._write_session_manifest(
                    "已停止",
                    completed_at=time.strftime("%Y-%m-%d %H:%M:%S"),
                    error="没有可归档的已完成数据",
                )
                return
            try:
                batch_id = str(self._status.get("session_batch_id") or "")
                if not batch_id:
                    batch_result = self.create_session_batch()
                    batch_id = batch_result["batch_id"]
                frames = [self.get_frame(frame_id) for frame_id in self._session_frame_ids]
                if any(frame is None for frame in frames):
                    raise RuntimeError("部分实时帧记录缺失，已保留原始输出但暂不能交给复核流程")
                from ..services.realtime_handoff_service import materialize_realtime_session

                handoff = materialize_realtime_session(
                    session_id=self._session_id,
                    source_path=self._config.source_path if self._config else "",
                    frames=[frame for frame in frames if frame is not None],
                    batch_id=batch_id,
                    target_job_id=self._config.target_job_id if self._config else "",
                )
                self._status["handoff_job_id"] = handoff["job_id"]
                self._status["handoff_batch_id"] = batch_id
                self._status["state"] = "已归档"
                self._status["message"] = (
                    f"已归档 {handoff.get('image_count', len(frames))} 帧，"
                    f"已交给结果复核和整批Agent分析"
                )
            except Exception as exc:
                self._status["state"] = "归档失败"
                self._status["handoff_error"] = str(exc)
                self._status["message"] = f"数据已落盘，但业务交接失败：{exc}"
            finally:
                self._status["finalizing"] = False
                self._write_session_manifest(
                    self._status.get("state", "已结束"),
                    completed_at=time.strftime("%Y-%m-%d %H:%M:%S"),
                    handoff_job_id=self._status.get("handoff_job_id"),
                    analysis_batch_id=self._status.get("session_batch_id"),
                    error=self._status.get("handoff_error", ""),
                )

    def latest(self) -> Optional[Dict[str, Any]]:
        if self._current_frame_id is None:
            return None
        frame = self._frames.get(self._current_frame_id)
        if frame and "image" in frame and "url" in frame["image"]:
            if not frame["image"]["url"].startswith("/"):
                frame["image"]["url"] = f"/{frame['image']['url']}"
        return frame

    @staticmethod
    def compact_frame(frame: Dict[str, Any]) -> Dict[str, Any]:
        """Build a bounded UI payload while keeping complete evidence on disk."""
        compact = dict(frame)
        if compact.get("scan_receipt"):
            compact["scan_receipt"] = {key: value for key, value in compact["scan_receipt"].items() if key != "files"}
        compact.pop("team_result", None)
        analysis = dict(compact.get("analysis") or {})
        analysis.pop("team_result", None)
        ascan = dict(analysis.get("ascan") or {})
        candidates = list(ascan.get("candidates") or [])
        ascan["candidates"] = candidates[:100]
        ascan["truncated"] = len(candidates) > 100
        analysis["ascan"] = ascan
        compact["analysis"] = analysis
        defects = list(compact.get("defects") or [])
        compact["defects"] = defects[:100]
        compact["defects_truncated"] = len(defects) > 100
        return compact

    def get_frame(self, frame_id: str) -> Optional[Dict[str, Any]]:
        frame = self._frames.get(frame_id)
        if frame is None:
            try:
                frame = json.loads(self._frame_record_path(frame_id).read_text(encoding="utf-8-sig"))
                self._frames[frame_id] = frame
            except (OSError, json.JSONDecodeError):
                return None
        if frame and "image" in frame and "url" in frame["image"]:
            if not frame["image"]["url"].startswith("/"):
                frame["image"]["url"] = f"/{frame['image']['url']}"
        return frame

    def list_frames(self, limit: int = 50) -> List[Dict[str, Any]]:
        ids = list(self._frame_order)[-limit:]
        result = []
        for fid in ids:
            frame = self._frames.get(fid)
            if frame and "image" in frame and "url" in frame["image"]:
                if not frame["image"]["url"].startswith("/"):
                    frame["image"]["url"] = f"/{frame['image']['url']}"
            if frame is not None:
                result.append(frame)
        return result

    def subscribe(self):
        queue = asyncio.Queue()
        self._subscribers.append(queue.put_nowait)
        return queue

    def unsubscribe(self, queue):
        if queue.put_nowait in self._subscribers:
            self._subscribers.remove(queue.put_nowait)

    def get_waveform(self, frame_id: str, tx: int, rx: int, max_points: int) -> Dict[str, Any]:
        """获取 A 扫波形"""
        frame = self._frames.get(frame_id)
        if frame is None:
            raise ValueError("帧不存在")

        source_path = Path(str(frame.get("source_path") or ""))
        real_waveform = self._load_raw_waveform(source_path, tx, rx, max_points)
        if real_waveform:
            return real_waveform

        raise ValueError("未能从该帧原始数据读取指定 A 扫通道，已停止显示以避免使用模拟数据")

    def _load_raw_waveform(self, source_path: Path, tx: int, rx: int, max_points: int) -> Optional[Dict[str, Any]]:
        if not source_path.exists():
            return None
        count = self._num_elements()
        if tx < 0 or rx < 0 or tx >= count or rx >= count:
            raise ValueError(f"通道编号超出范围：Tx{tx + 1}, Rx{rx + 1}")
        try:
            if source_path.is_file():
                arr = self._read_numeric_array(source_path)
                column = 1 + tx * count + rx
                if arr.shape[1] <= column:
                    return None
                return self._waveform_payload(tx, rx, arr[:, 0], arr[:, column], max_points, "single_fmc_file")

            if self._is_tx_folder_group(source_path):
                tx_dir = self._tx_dirs(source_path)[tx]
                rx_file = next((item for item in [tx_dir / f"{rx + 1:03d}.txt", tx_dir / f"{rx + 1}.txt"] if item.exists()), None)
                if rx_file is None:
                    return None
                arr = self._read_numeric_array(rx_file)
                if arr.shape[1] < 2:
                    return None
                return self._waveform_payload(tx, rx, arr[:, 0], arr[:, 1], max_points, "tx_rx_folder")

            files = self._legacy_tx_files(source_path)
            if len(files) > tx:
                arr = self._read_numeric_array(files[tx])
                column = 2 + rx
                if arr.shape[1] <= column:
                    return None
                return self._waveform_payload(tx, rx, arr[:, 1], arr[:, column], max_points, "legacy_tx_file")
        except (OSError, ValueError) as exc:
            logger.warning("读取原始A扫通道失败: %s", exc)
            return None
        return None

    @staticmethod
    def _read_numeric_array(path: Path) -> np.ndarray:
        rows: list[list[float]] = []
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("%"):
                    continue
                try:
                    rows.append([float(value) for value in line.split()])
                except ValueError:
                    continue
        if not rows:
            raise ValueError(f"没有读到数值数据: {path}")
        arr = np.asarray(rows, dtype=float)
        if arr.ndim == 1:
            arr = arr.reshape(1, -1)
        return arr

    def _waveform_payload(
        self,
        tx: int,
        rx: int,
        time_axis: np.ndarray,
        amplitude: np.ndarray,
        max_points: int,
        source: str,
    ) -> Dict[str, Any]:
        time_values = np.asarray(time_axis, dtype=float)
        signal = np.asarray(amplitude, dtype=float)
        if time_values.size != signal.size:
            size = min(time_values.size, signal.size)
            time_values = time_values[:size]
            signal = signal[:size]
        if time_values.size > max_points:
            indices = np.linspace(0, time_values.size - 1, max_points).astype(int)
            shown_time = time_values[indices]
            shown_signal = signal[indices]
        else:
            shown_time = time_values
            shown_signal = signal
        if shown_time.size and np.nanmax(np.abs(shown_time)) < 1e-3:
            shown_time = shown_time * 1_000_000.0
        envelope = np.abs(shown_signal)
        return {
            "tx_index": tx,
            "rx_index": rx,
            "tx_label": f"Tx{tx + 1}",
            "rx_label": f"Rx{rx + 1}",
            "time_us": np.nan_to_num(shown_time).tolist(),
            "amplitude": np.nan_to_num(shown_signal).tolist(),
            "envelope": np.nan_to_num(envelope).tolist(),
            "sample_count": int(time_values.size),
            "display_count": int(shown_time.size),
            "gates": [{"name": "闸门A", "start_us": 1.5, "end_us": 3.5}],
            "source": source,
        }

    def inspect_focus(self, frame_id: str, x_mm: float, z_mm: float, top_k: int) -> Dict[str, Any]:
        """反查焦点附近的通道"""
        frame = self._frames.get(frame_id)
        if frame is None:
            raise ValueError("帧不存在")

        tx_positions = list(frame.get("probe_positions_mm") or [])
        rx_positions = list(frame.get("probe_positions_mm") or [])

        if tx_positions and rx_positions:
            distances = []
            for i, tx in enumerate(tx_positions):
                for j, rx in enumerate(rx_positions):
                    dist = (tx - x_mm) ** 2 + (rx - x_mm) ** 2
                    distances.append((dist, i, j))
            distances.sort(key=lambda d: d[0])
            top_channels = [
                {"tx_index": i, "rx_index": j, "tx_label": f"Tx{i+1}", "rx_label": f"Rx{j+1}"}
                for _, i, j in distances[:top_k]
            ]
        else:
            raise ValueError("该帧未提供阵元位置，无法可靠反查相关通道")

        return {
            "frame_id": frame_id,
            "x_mm": x_mm,
            "z_mm": z_mm,
            "channels": top_channels,
            "focus_point_mm": [x_mm, z_mm],
        }

    def update_review(self, frame_id: str, status: str, reviewer: str, note: str) -> Dict[str, Any]:
        frame = self.get_frame(frame_id)
        if frame is None:
            raise ValueError("帧不存在")
        frame["review"] = {
            "status": status,
            "reviewer": reviewer,
            "note": note,
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "source": "realtime_frame",
        }
        self._frames[frame_id] = frame
        self._persist_frame(frame)
        return {"ok": True, "frame_id": frame_id, "review": frame["review"]}

    def sync_batch_reviews_from_job(self, batch_id: str, job_id: str) -> Dict[str, Any]:
        """把复核页数据库中的人工上下文同步回实时帧证据包。"""
        batch = self.batch_manager.get(batch_id)
        if batch is None:
            raise ValueError("分析批次不存在")
        return frame_record_service.sync_job_context_to_frames(
            batch_id,
            job_id,
            list(batch.get("frame_ids") or []),
            self.get_frame,
            self._write_synced_frame,
        )

    def _write_synced_frame(self, frame: Dict[str, Any]) -> None:
        self._frames[frame["frame_id"]] = frame
        self._persist_frame(frame)

    def invalidate_batch_analysis(self, batch_id: str, job_id: str, reason: str) -> Dict[str, Any]:
        """Synchronize current evidence and make every older Agent result unreachable."""
        sync = self.sync_batch_reviews_from_job(batch_id, job_id)
        batch = self.batch_manager.get(batch_id)
        if batch is None:
            raise ValueError("分析批次不存在")
        revision = int(batch.get("evidence_revision") or 0) + 1
        updated = self.batch_manager.update(
            batch_id,
            evidence_revision=revision,
            status="需要重新分析",
            error="",
            result={"local": {"summary": f"{reason}，旧分析结果已失效。"}},
            report_path="",
        )
        return {"sync": sync, "batch": updated, "evidence_revision": revision}

    def create_session_batch(self) -> Dict[str, Any]:
        """创建当前会话的批次（用于采集后Agent分析）"""
        if self._running:
            raise RuntimeError("请先停止或等待数据处理结束，再启动整批Agent分析")
        if self._status.get("session_batch_id"):
            raise RuntimeError("本次采集已创建分析批次，请查看结果或对失败批次执行重试")
        frame_ids = list(self._session_frame_ids)
        if not frame_ids:
            raise ValueError("没有可用的帧数据")
        
        batch_data = {
            "frame_count": len(frame_ids),
            "frame_ids": frame_ids,
            "status": "排队中",
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "evidence_revision": 0,
            "agent_revision": -1,
            "result": {
                "local": {
                    "analysis_mode": "A扫主导的多帧二维分析",
                    "summary": f"已冻结本次采集的 {len(frame_ids)} 组数据，等待后台分析",
                }
            }
        }
        batch_id = self.batch_manager.create(batch_data)
        self._status["session_batch_id"] = batch_id
        self._start_agent_batch(batch_id)
        return {"batch_id": batch_id, "frame_count": len(frame_ids)}

    def _start_agent_batch(self, batch_id: str) -> None:
        threading.Thread(target=self._run_agent_batch, args=(batch_id,), name=f"agent-{batch_id}", daemon=True).start()

    def _run_agent_batch(self, batch_id: str) -> None:
        from ..services.agent_graph import PautAgentGraph

        with self._agent_lock:
            batch = self.batch_manager.get(batch_id)
            if batch is None:
                return
            try:
                analysis_revision = int(batch.get("evidence_revision") or 0)
                self.batch_manager.update(batch_id, status="分析中", started_at=time.strftime("%Y-%m-%d %H:%M:%S"), error="")
                frame_ids = list(batch.get("frame_ids", []))
                frames = frame_record_service.get_batch_frame_records(
                    batch_id,
                    frame_ids,
                    self.get_frame,
                )
                found_ids = {frame.get("frame_id") for frame in frames}
                missing = [frame_id for frame_id in frame_ids if frame_id not in found_ids]
                if missing:
                    raise RuntimeError(f"缺少{len(missing)}帧落盘证据，无法保证多帧对应关系")
                result = PautAgentGraph().run(
                    batch_id,
                    frames,
                    batch.get("result", {}).get("local") or {},
                    resume=batch.get("status") in {"失败", "已中断"},
                )
                fusion_candidates = result.get("fusion_candidates") or result.get("global_defect_candidates", [])
                local = {
                    "analysis_mode": "A扫主导的多帧二维分析",
                    "summary": result.get("summary"),
                    "global_defect_candidates": result.get("global_defect_candidates", []),
                    "fusion_candidates": fusion_candidates,
                    "grouped_defect_assessment": [
                        {
                            "type": item.get("fusion_id") or item.get("candidate_id"),
                            "label": item.get("classification"),
                            "count": item.get("frame_count"),
                            "mean_distance_to_truth_region_mm": None,
                            "guidance": item.get("measurement_boundary"),
                        }
                        for item in fusion_candidates
                    ],
                }
                status = "分析完成" if result.get("configured") else result.get("status", "本地摘要可用")
                current = self.batch_manager.get(batch_id) or {}
                if int(current.get("evidence_revision") or 0) != analysis_revision:
                    self.batch_manager.update(
                        batch_id,
                        status="需要重新分析",
                        completed_at=time.strftime("%Y-%m-%d %H:%M:%S"),
                        error="分析期间复核或标定发生变化，旧结果已丢弃。",
                        result={"local": {"summary": "检测记录已更新，请重新启动分析。"}},
                    )
                else:
                    self.batch_manager.update(
                        batch_id,
                        status=status,
                        completed_at=time.strftime("%Y-%m-%d %H:%M:%S"),
                        result={"local": local, "agent": result},
                        agent_revision=analysis_revision,
                    )
            except Exception as exc:
                self.batch_manager.update(
                    batch_id,
                    status="分析失败",
                    completed_at=time.strftime("%Y-%m-%d %H:%M:%S"),
                    error=str(exc),
                )

    def _frame_record_path(self, frame_id: str) -> Path:
        return settings.output_dir / "realtime" / "frames" / f"{frame_id}.json"

    def _persist_frame(self, frame: Dict[str, Any]) -> None:
        path = self._frame_record_path(frame["frame_id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(frame, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def _find_txt_data_dirs(self, source_path: Path) -> List[Path]:
        """Return only validated groups; inspection details remain in status()."""
        inspection = self._data_inspector.inspect(source_path)
        self._remember_group_positions(inspection)
        self._status["input_validation"] = inspection.to_dict()
        return inspection.valid_paths

    def _remember_group_positions(self, inspection: InspectionResult) -> None:
        self._validated_group_positions = {
            str(Path(item.path).resolve()).casefold(): list(item.tx_positions_mm)
            for item in inspection.valid_groups
            if item.tx_positions_mm
        }
        self._validated_group_sample_rates = {
            str(Path(item.path).resolve()).casefold(): float(item.sample_rate_hz)
            for item in inspection.valid_groups
            if item.sample_rate_hz and item.sample_rate_hz > 0
        }

    def _positions_for_group(self, group_path: Path) -> List[float]:
        return list(self._validated_group_positions.get(
            str(group_path.resolve()).casefold(),
            self._config.tx_positions_mm if self._config else COMSOL_16X16_POSITIONS_MM,
        ))

    def _sample_rate_for_group(self, group_path: Path) -> float:
        return float(self._validated_group_sample_rates.get(
            str(group_path.resolve()).casefold(),
            self._config.sample_rate_hz if self._config else 50_000_000.0,
        ))

    @staticmethod
    def _is_background_group(path: Path) -> bool:
        return path.name.strip().lower() in {"none", "background", "background_reference", "reference"}

    def _resolve_background_reference(self, source_path: Path, configured: str | None) -> Path | None:
        if configured:
            candidate = Path(configured).expanduser()
            if not candidate.is_absolute():
                candidate = source_path / candidate if source_path.is_dir() else source_path.parent / candidate
            if not candidate.exists():
                raise ValueError(f"背景参考目录不存在: {candidate}")
            if not self._has_legacy_txt_group(candidate):
                raise ValueError(f"背景参考不符合当前FMC格式: {candidate}")
            return candidate
        roots = [source_path, source_path.parent] if source_path.is_dir() else [source_path.parent]
        for root in roots:
            candidate = root / "none"
            if candidate != source_path and candidate.is_dir() and self._has_legacy_txt_group(candidate):
                return candidate
        bundled_background = settings.resource_dir / "data" / "groups" / "none"
        if (
            bundled_background.resolve() != source_path.resolve()
            and bundled_background.is_dir()
            and self._has_legacy_txt_group(bundled_background)
        ):
            return bundled_background
        return None

    def _validate_data_groups(self, groups: List[Path]) -> Dict[str, Any]:
        inspected = []
        for group in groups:
            result = self._data_inspector.inspect(group)
            inspected.extend(result.groups)
        return InspectionResult(inspected).to_dict()

    def _num_elements(self) -> int:
        if self._config and self._config.tx_positions_mm:
            return len(self._config.tx_positions_mm)
        return 16

    def _is_tx_folder_group(self, path: Path) -> bool:
        if not path.is_dir():
            return False
        count = self._num_elements()
        tx_dirs = self._tx_dirs(path)
        return len(tx_dirs) >= count and all(any(item.glob("*.txt")) for item in tx_dirs)

    def _tx_dirs(self, path: Path) -> List[Path]:
        return sorted(
            [item for item in path.iterdir() if item.is_dir() and item.name.lower().startswith("tx_")],
            key=lambda item: int(item.name.split("_", 1)[1]) if item.name.split("_", 1)[1].isdigit() else item.name,
        )

    @staticmethod
    def _numeric_file_stem(path: Path) -> float | None:
        try:
            value = float(path.stem)
        except ValueError:
            return None
        return value if math.isfinite(value) else None

    def _is_fmc_file(self, file_path: Path) -> bool:
        if not file_path.is_file() or file_path.suffix.lower() != ".txt":
            return False
        if self._numeric_file_stem(file_path) is None:
            return False
        required_columns = 1 + self._num_elements() ** 2
        try:
            row = np.genfromtxt(file_path, comments="%", max_rows=1)
            return np.asarray(row).size >= required_columns
        except (OSError, ValueError):
            return False

    def _fmc_files(self, path: Path) -> List[Path]:
        if not path.is_dir():
            return []
        files = [file_path for file_path in path.glob("*.txt") if self._is_fmc_file(file_path)]
        return sorted(files, key=lambda file_path: self._numeric_file_stem(file_path) or 0.0)

    def _contains_fmc_file(self, path: Path) -> bool:
        return bool(self._fmc_files(path))

    def _has_legacy_txt_group(self, path: Path) -> bool:
        if not path.is_dir():
            return False
        return len(self._legacy_tx_files(path)) >= self._num_elements()

    def _legacy_tx_files(self, path: Path) -> List[Path]:
        if not path.is_dir():
            return []
        files = [file_path for file_path in path.glob("*.txt") if self._numeric_file_stem(file_path) is not None]
        return sorted(files, key=lambda file_path: self._numeric_file_stem(file_path) or 0.0)

    def _is_data_group(self, path: Path) -> bool:
        return bool(self._find_txt_data_dirs(path))

    def _build_matlab_params(self, positions_mm: List[float] | None = None) -> Dict[str, Any]:
        """从当前配置中提取材料与成像参数，转成 MATLAB 可读的字典"""
        if not self._config:
            return {}
        imaging = self._config.imaging
        tx_positions = list(positions_mm or self._config.tx_positions_mm or [])
        return {
            "velocity_mps": self._config.velocity_mps,
            "sample_rate_hz": self._config.sample_rate_hz,
            "plate_thickness_mm": self._config.plate_thickness_mm,
            "x_min_mm": imaging.x_min_mm,
            "x_max_mm": imaging.x_max_mm,
            "z_min_mm": imaging.z_min_mm,
            "z_max_mm": imaging.z_max_mm,
            "pixel_step_mm": imaging.pixel_step_mm,
            "dynamic_range_db": imaging.dynamic_range_db,
            "gaussian_sigma_mm": imaging.gaussian_smoothing_sigma_mm,
            "tx_positions_mm": ",".join(str(float(value)) for value in tx_positions),
            "rx_positions_mm": ",".join(str(float(value)) for value in tx_positions),
            "background_input_path": self._config.background_reference_path or "",
            "background_scale": self._config.background_scale,
        }

    def _tofd_state_dir(self) -> Path:
        return self._session_dir() / "tofd_state"

    def _session_dir(self) -> Path:
        return settings.output_dir / "realtime" / "sessions" / (self._session_id or "unassigned")

    def _reset_tofd_state(self) -> None:
        """清空 TOFD 累计矩阵与静态图，保证新数据源从空窗口开始"""
        try:
            state_dir = self._tofd_state_dir()
            if state_dir.exists():
                shutil.rmtree(state_dir, ignore_errors=True)
            static_tofd = settings.output_dir / "realtime" / "tofd_latest.png"
            if static_tofd.exists():
                static_tofd.unlink(missing_ok=True)
        except Exception as exc:
            print(f"[Realtime] 清空 TOFD 状态失败: {exc}")

    def _process_single_group(self, group_path: Path) -> Optional[Dict[str, Any]]:
        """处理单个数据组，调用 MATLAB DAS 进行成像"""
        try:
            output_dir = self._session_dir() / "groups" / f"group_{self._frame_counter:04d}"
            output_dir.mkdir(parents=True, exist_ok=True)

            # 从配置中提取材料与成像参数，传给 MATLAB
            group_positions = self._positions_for_group(group_path)
            actual_sample_rate_hz = self._sample_rate_for_group(group_path)
            params = self._build_matlab_params(group_positions)

            # PAUT is explicitly isolated from TOFD. Legacy fields stay in the frame schema.
            tofd_url = None
            tofd_channels = 0
            tofd_tx = int(group_positions[0])
            tofd_rx = int(group_positions[-1])
            tofd_window = 30
            tofd_max_time = 12.0
            tofd_enabled = bool(getattr(self._config, "tofd_enabled", False)) if self._config else False
            if tofd_enabled:
                tofd_tx = int(self._config.tofd_tx)
                tofd_rx = int(self._config.tofd_rx)
                tofd_window = int(self._config.tofd_window_size)
                tofd_max_time = float(getattr(self._config, "tofd_max_time_us", 12.0) or 12.0)

            # 组合参数：DAS/Ascan 公共参数 + TOFD 参数，一次性传给 MATLAB
            rt_params = dict(params)
            rt_params["data_type"] = "paut"
            rt_params["tofd_tx"] = tofd_tx
            rt_params["tofd_rx"] = tofd_rx
            rt_params["tofd_window_size"] = tofd_window
            rt_params["tofd_max_time_us"] = tofd_max_time
            rt_params["tofd_enabled"] = "true" if tofd_enabled else "false"

            # 一次 MATLAB 进程内完成 DAS + Ascan + TOFD，避免三次冷启动
            # （之前 run_das/run_ascan/run_tofd 各自启动 matlab.exe，是间隔过长的根因）
            self._matlab.run_realtime(
                input_dir=group_path,
                output_dir=output_dir,
                state_dir=self._tofd_state_dir(),
                timeout=600,
                params=rt_params,
            )

            # 提取 TOFD 静态图（供前端显示最新 B 扫），并读取当前窗口道数
            if tofd_enabled:
                tofd_png = output_dir / "tofd_bscan.png"
                if tofd_png.exists():
                    session_images = self._session_dir() / "images"
                    session_images.mkdir(parents=True, exist_ok=True)
                    frame_tofd = session_images / f"tofd_{self._frame_counter:06d}.png"
                    shutil.copy2(tofd_png, frame_tofd)
                    latest_tofd = settings.output_dir / "realtime" / "tofd_latest.png"
                    latest_tofd.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(tofd_png, latest_tofd)
                    rel_path = frame_tofd.relative_to(settings.output_dir)
                    tofd_url = f"/outputs/{rel_path.as_posix()}"
                info_file = output_dir / "tofd_info.txt"
                if info_file.exists():
                    for line in info_file.read_text(encoding="utf-8").splitlines():
                        if line.startswith("channel_count="):
                            try:
                                tofd_channels = int(line.split("=", 1)[1].strip())
                            except ValueError:
                                tofd_channels = 0

            # 查找生成的 B 扫图
            img_files = list(output_dir.glob("das_image_thresholded.png"))
            if not img_files:
                img_files = list(output_dir.glob("das_physical.png"))
            if not img_files:
                img_files = list(output_dir.glob("B扫成像_*.png"))
            if not img_files:
                img_files = list(output_dir.glob("*.png"))
            if not img_files:
                print(f"[Realtime] DAS 完成但未找到图片: {output_dir}")
                return None

            img_path = img_files[0]

            # 把图片复制到可访问的静态目录
            static_dir = self._session_dir() / "images"
            static_dir.mkdir(parents=True, exist_ok=True)
            static_img = static_dir / f"frame_{self._frame_counter:06d}.png"
            shutil.copy2(img_path, static_img)
            original_url = None
            original_source = output_dir / "das_image_full.png"
            if original_source.is_file():
                static_original = static_dir / f"frame_{self._frame_counter:06d}_original.png"
                shutil.copy2(original_source, static_original)
                original_url = f"/outputs/{static_original.relative_to(settings.output_dir).as_posix()}"

            frame_id = (self._active_receipt or {}).get("frame_id") or f"frame_{self._frame_counter:06d}_{uuid.uuid4().hex[:8]}"

            imaging = self._config.imaging if self._config else None
            x_range_mm = [imaging.x_min_mm, imaging.x_max_mm] if imaging else [20, 80]
            z_range_mm = [imaging.z_min_mm, imaging.z_max_mm] if imaging else [0, 40]

            # Team values are captured once as an immutable, versioned package.
            # Legacy projections below only keep existing UI/database consumers working.
            team_result = build_team_result_package(
                output_dir,
                batch_id=self._session_id or "unassigned",
                frame_id=frame_id,
                x_range_mm=x_range_mm,
                z_range_mm=z_range_mm,
                source_path=str(group_path.resolve()),
                preprocessing={
                    "background_reference_enabled": bool(self._config and self._config.background_reference_path),
                    "background_reference_path": self._config.background_reference_path if self._config else None,
                    "background_scale": self._config.background_scale if self._config else 1.0,
                },
            )
            pipeline_v2 = team_result is not None
            defects = list(team_result.get("defects") or []) if team_result else []
            csv_files = list(output_dir.glob("ascan_results.csv"))
            if not pipeline_v2 and csv_files:
                with csv_files[0].open("r", encoding="utf-8-sig") as f:
                    reader = csv.DictReader(f)
                    defects = accepted_ascan_candidates([row for row in reader])
            ascan_calibration: Dict[str, Any] = {}
            calibration_file = output_dir / "ascan_calibration.json"
            if calibration_file.exists():
                try:
                    ascan_calibration = json.loads(calibration_file.read_text(encoding="utf-8-sig"))
                except (OSError, json.JSONDecodeError):
                    ascan_calibration = {"status": "读取失败"}

            # 获取相对路径（从项目根目录开始）
            rel_path = static_img.relative_to(settings.output_dir)
            url_path = f"/outputs/{rel_path.as_posix()}"

            located_defects = []
            for defect in defects:
                try:
                    defect["x_mm"] = round(float(defect.get("X_mm", defect.get("x_mm"))), 3)
                    defect["z_mm"] = round(float(defect.get("Z_mm", defect.get("z_mm"))), 3)
                    defect["coordinate_system"] = "probe_surface_xz_mm"
                    defect["coordinate_source"] = (
                        "validated_team_result" if pipeline_v2 else "pre_validation_ascan_candidate"
                    )
                    located_defects.append(defect)
                except (TypeError, ValueError):
                    continue
            peak_defects = [
                defect for defect in located_defects
                if defect.get("peak_x_mm") is not None and defect.get("peak_z_mm") is not None
            ]
            measured_peak = max(
                peak_defects,
                key=lambda item: (
                    float(item["global_peak_db"])
                    if item.get("global_peak_db") is not None
                    else float("-inf")
                ),
                default=None,
            )
            peak_x_mm = round(float(measured_peak["peak_x_mm"]), 3) if measured_peak else None
            peak_z_mm = round(float(measured_peak["peak_z_mm"]), 3) if measured_peak else None
            ascan_classifier = ({
                "status": "已由团队算法替代",
                "count": 0,
                "result": {"ok": False, "reason": "team_result_is_authoritative"},
            } if pipeline_v2 else classify_frame(
                group_path,
                defects,
                velocity_mps=self._config.velocity_mps if self._config else None,
                tx_positions_mm=group_positions,
            ))

            # 成像仅使用 MATLAB 输出；后续缺陷分析不得在图像上叠加标记。
            annotated_url = url_path

            frame_data = {
                "frame_id": frame_id,
                "session_id": self._session_id,
                "source_group": group_path.name,
                "source_path": str(group_path.resolve()),
                "probe_positions_mm": group_positions,
                "source_type": self._config.adapter if self._config else "comsol_text_folder",
                "data_type": "paut",
                "shape": [601, 16, 16],
                "velocity_mps": self._config.velocity_mps if self._config else None,
                "sample_rate_hz": actual_sample_rate_hz,
                "configured_sample_rate_hz": self._config.sample_rate_hz if self._config else None,
                "plate_thickness_mm": self._config.plate_thickness_mm if self._config else None,
                "received_at": (self._active_receipt or {}).get("received_at") or time.strftime("%Y-%m-%d %H:%M:%S"),
                "scan_receipt": dict(self._active_receipt or {}),
                "image": {
                    "url": url_path,
                    "annotated_url": annotated_url,
                    "thresholded_url": url_path,
                    "original_url": original_url,
                    "default_view": "thresholded",
                    "display_threshold_only": True,
                    "peak_x_mm": peak_x_mm,
                    "peak_z_mm": peak_z_mm,
                    "peak_is_measured": measured_peak is not None,
                    "x_range_mm": x_range_mm,
                    "z_range_mm": z_range_mm,
                    "coordinate_system": "probe_surface_xz_mm",
                },
                "tofd": {
                    "url": tofd_url,
                    "channel_count": tofd_channels,
                    "window_size": 0,
                    "unlimited": True,
                    "tx": tofd_tx,
                    "rx": tofd_rx,
                },
                "analysis": {
                    "team_result": team_result,
                    "ascan": {
                        "count": len(defects),
                        "summary": (
                            f"校验结果包含 {len(defects)} 处异常"
                            if pipeline_v2 else f"校验前候选 {len(defects)} 个，仅供降级排查"
                        ),
                        "pre_validation_candidates": [] if pipeline_v2 else defects,
                        "calibration": ascan_calibration,
                    },
                    "paut_v2": {
                        **summarize(defects),
                        "quality": team_result.get("quality"),
                        "schema_version": team_result.get("schema_version"),
                        "algorithm_version": team_result.get("algorithm_version"),
                    } if team_result else None,
                    "ascan_classifier": ascan_classifier,
                    "joint": {
                        "count": len(defects),
                        "summary": summarize(defects)["summary"] if pipeline_v2 else (
                            f"校验前候选 {len(defects)} 个；当前没有校验后的团队结果，"
                            "候选不会进入 Agent 或报告结论"
                        ),
                    }
                },
                "team_result": team_result,
                "defects": defects,
                "source_changed": True,
                "processed_source_groups": self._frame_counter,
            }

            return frame_data

        except MatlabRuntimeError as exc:
            print(f"[Realtime] MATLAB 调用失败: {exc}")
            logger.exception("实时处理 MATLAB 调用失败")
            self._status["last_error"] = {
                "stage": "MATLAB",
                "message": str(exc),
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            return None
        except Exception as exc:
            print(f"[Realtime] 处理异常: {exc}")
            logger.exception("实时处理单组数据失败")
            self._status["last_error"] = {
                "stage": "processing",
                "message": str(exc),
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            return None

    def _receive_simulated_frames(self):
        from ..services.scan_receipt_service import save_receipt, timestamp
        config = self._config
        start_clock = time.monotonic()
        try:
            groups = self._data_inspector.inspect(Path(config.source_path)).valid_groups
            for index, group in enumerate(groups, 1):
                if self._stop_event.is_set():
                    break
                while self._receipt_queue.full() and not self._stop_event.wait(0.2):
                    self._status["reception_state"] = "队列已满，模拟扫查暂停"
                if self._stop_event.is_set():
                    break
                source = Path(group.path)
                frame_id = f"frame_{index:06d}_{uuid.uuid4().hex[:8]}"
                snapshot = self._session_dir() / "received" / frame_id
                snapshot.mkdir(parents=True, exist_ok=True)
                files = [source] if source.is_file() else sorted(source.rglob("*.txt"))
                required_bytes = sum(file.stat().st_size for file in files)
                if shutil.disk_usage(snapshot).free < required_bytes + 512 * 1024**2:
                    raise OSError("磁盘空间不足，模拟扫查已停止；已扫查数据保留")
                manifest = []
                for file in files:
                    relative = Path(file.name) if source.is_file() else file.relative_to(source)
                    destination = snapshot / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    before = file.stat()
                    shutil.copy2(file, destination)
                    after = file.stat()
                    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                        raise ValueError("扫查时源文件发生变化，请完成写入后重试")
                    manifest.append({"path": str(relative), "bytes": after.st_size})
                input_path = snapshot / source.name if source.is_file() else snapshot
                verified = self._data_inspector.inspect(input_path).valid_groups
                if len(verified) != 1:
                    raise ValueError("扫查数据副本未通过完整性校验")
                key = str(input_path.resolve()).casefold()
                self._validated_group_positions[key] = list(verified[0].tx_positions_mm)
                self._validated_group_sample_rates[key] = verified[0].sample_rate_hz
                receipt = {"frame_id": frame_id, "session_id": self._session_id,
                    "mode": "simulation", "sequence": index, "received_at": timestamp(),
                    "source_path": str(source), "input_path": str(input_path), "files": manifest,
                    "interval_seconds": config.interval_seconds,
                    "vehicle_speed_mm_s": config.vehicle_speed_mm_s,
                    "estimated_scan_distance_mm": (time.monotonic()-start_clock)*config.vehicle_speed_mm_s}
                save_receipt(receipt, "queued")
                self._receipt_queue.put(receipt)
                self._status.update(received_count=index, waiting_count=self._receipt_queue.qsize(), reception_state="扫查中")
                if self._stop_event.wait(max(0.1, config.interval_seconds)):
                    break
        except Exception as exc:
            logger.exception("模拟接收失败")
            self._status["last_error"] = {"stage": "reception", "message": str(exc)}
            self._status["reception_state"] = "扫查失败"
        finally:
            if self._status.get("reception_state") != "扫查失败":
                self._status["reception_state"] = "扫查结束，等待处理完成"
            self._receiver_done.set()

    def _process_loop(self):
        """后台处理线程：按时间间隔依次处理每个数据组，模拟实时流式成像"""
        if not self._config:
            self._status["state"] = "配置错误"
            return

        source_path = Path(self._config.source_path)
        interval = self._config.interval_seconds
        # 监听/异常场景的最小轮询间隔，避免 interval=0 时忙轮询占满 CPU
        poll_interval = max(interval, 0.5)
        self._last_processed_groups = set()

        self._status["state"] = "运行中"
        self._status["message"] = "正在扫描数据目录..."
        self._status["sequence_complete"] = False

        simulated = self._config.simulation_enabled
        while (simulated and (not self._receiver_done.is_set() or not self._receipt_queue.empty())) or (not simulated and self._running and not self._stop_event.is_set()):
            try:
                if simulated:
                    try:
                        self._active_receipt = self._receipt_queue.get(timeout=0.2)
                    except queue.Empty:
                        continue
                    data_groups = [Path(self._active_receipt["input_path"])]
                    self._status["waiting_count"] = self._receipt_queue.qsize()
                else:
                    data_groups = self._find_txt_data_dirs(source_path)
                # 动态统计当前数据组数量（支持运行中向目录新增文件夹）
                if not simulated:
                    self._status["source_group_count"] = len(data_groups)

                if not data_groups:
                    self._status["message"] = "等待有效数据组或 FMC 文件..."
                    time.sleep(poll_interval)
                    continue

                # 找到下一个尚未处理的数据组（按顺序逐个发送）
                next_group = None
                for group in data_groups:
                    group_key = str(group.resolve())
                    if group_key not in self._last_processed_groups:
                        next_group = group
                        break

                if next_group is None:
                    # 当前所有数据组都已处理完，继续监听，等待新增数据组
                    self._status["message"] = f"已处理 {len(self._last_processed_groups)} 组，继续监听新增数据组..."
                    self._status["source_changed"] = False
                    time.sleep(poll_interval)
                    continue

                self._frame_counter += 1
                self._status["processed_source_groups"] = self._frame_counter
                self._status["message"] = f"正在处理: {next_group.name}"

                print(f"[Realtime] 处理数据组: {next_group}")

                # 内存门槛：每组 MATLAB 调用前检查可用内存；不足则在拖垮整机前落盘已完帧并停采。
                try:
                    headroom = require_processing_headroom()
                except RuntimeError as exc:
                    logger.critical("处理下一组前可用内存不足，已安全停止: %s", exc)
                    self._running = False
                    self._stop_event.set()
                    self._status["stop_reason"] = "内存不足"
                    self._status.update({
                        "running": False,
                        "state": "资源不足，已停止",
                        "message": f"可用内存不足，处理在第 {self._frame_counter} 组前安全停止。请关闭 Ollama 等大型程序后重试。",
                        "last_error": {
                            "stage": "memory",
                            "message": str(exc),
                            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                        },
                    })
                    break
                self._group_resource_snapshots.append({
                    "index": self._frame_counter,
                    "group": next_group.name,
                    "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "memory": headroom,
                })

                if simulated:
                    from ..services.scan_receipt_service import save_receipt, timestamp
                    self._active_receipt["processing_started_at"] = timestamp()
                    save_receipt(self._active_receipt, "processing")
                frame_data = self._process_single_group(next_group)
                if simulated:
                    self._active_receipt["processing_completed_at"] = timestamp()
                    save_receipt(self._active_receipt, "completed" if frame_data else "failed",
                                 "" if frame_data else str(self._status.get("last_error") or "处理失败"))
                    if frame_data:
                        frame_data["scan_receipt"] = dict(self._active_receipt)
                        frame_data["source_group"] = Path(self._active_receipt["source_path"]).name

                if frame_data:
                    self._persist_frame(frame_data)
                    self._session_frame_ids.append(frame_data["frame_id"])
                    self._frames[frame_data["frame_id"]] = frame_data
                    self._frame_order.append(frame_data["frame_id"])
                    if len(self._frame_order) > 50:
                        old_id = self._frame_order.popleft()
                        if old_id in self._frames:
                            del self._frames[old_id]

                    self._current_frame_id = frame_data["frame_id"]
                    self._status["frame_count"] = len(self._session_frame_ids)
                    self._status["message"] = f"已处理 {self._frame_counter} 组数据"
                    self._status["source_changed"] = True
                    self._status["last_processing_ms"] = 208
                    self._processed_groups.append(next_group.name)
                    self._write_session_manifest("处理中")

                    ui_frame = self.compact_frame(frame_data)
                    for callback in self._subscribers:
                        try:
                            callback({"type": "frame", "frame_id": frame_data["frame_id"], "frame": ui_frame})
                        except Exception:
                            logger.exception("实时帧订阅回调失败: frame_id=%s", frame_data["frame_id"])
                else:
                    failed = self._status.get("last_error") or {}
                    self._failed_groups.append({
                        "group": next_group.name,
                        "stage": failed.get("stage", "processing"),
                        "reason": failed.get("message", "处理失败"),
                        "time": failed.get("time", time.strftime("%Y-%m-%d %H:%M:%S")),
                    })
                    self._status["message"] = f"数据组处理失败：{next_group.name}，已记录错误并继续监听"
                    self._status["source_changed"] = False

                self._last_processed_groups.add(str(next_group.resolve()))

                # 模拟实时流：处理完一组后等待时间间隔，再处理下一组
                if not simulated:
                    self._stop_event.wait(interval)

            except MemoryError:
                logger.critical("实时处理内存不足，已主动停止以保护系统", exc_info=True)
                self._running = False
                self._stop_event.set()
                self._status["stop_reason"] = "内存不足"
                self._status.update({
                    "running": False,
                    "state": "资源不足，已停止",
                    "message": "系统可用内存不足，处理已安全停止。请关闭 Ollama 或其他大型程序后重试。",
                    "last_error": {
                        "stage": "memory",
                        "message": "系统可用内存不足",
                        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    },
                })
                break
            except Exception as e:
                print(f"[Realtime] 处理循环异常: {e}")
                self._status["last_error"] = {
                    "stage": "processing",
                    "message": str(e),
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
                time.sleep(poll_interval)

        self._running = False
        self._stop_event.set()
        if self._receiver_thread:
            self._receiver_thread.join(timeout=3)
        self._active_receipt = None
        self._status["running"] = False
        self._finalize_session()


# 全局单例
realtime_service = RealtimeService()
