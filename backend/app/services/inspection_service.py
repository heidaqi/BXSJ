"""桌面端统一文件夹检测入口。

将「拖入文件夹 -> 自动分析」的完整流程封装为可被桌面任务控制器直接调用的入口，
不通过 HTTP / FastAPI，也不依赖前端。处理顺序与 `analysis_service.py` 保持一致：

    scan_input -> classify_input -> create_job
      -> per-group: run_das -> run_ascan -> build_yolo_image -> detect_image
                    -> fuse_results -> render_annotated
      -> aggregate_job_result -> optional_agent_analysis -> create_report

普通图片目录进入图片 YOLO 检测分支；PAUT / 数字 .txt 目录进入数据集分析分支。
两者共享任务状态、输出目录、日志和报告接口。

结果以稳定的 `InspectionResult` / `GroupResult` 数据类表示，并提供 JSON 序列化，
供桌面层显示与历史读取，避免依赖网页 JSON 的偶然字段。
"""
from __future__ import annotations

import csv
import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from ..config import settings
from .analysis_service import (
    _axis_info,
    _find_data_groups,
    _find_newest,
    _fuse_defects,
    _isfinite_numeric_name,
    _load_das_mat,
    _peak_position,
    _read_ascan_csv,
    _render_annotated,
    _render_colormap,
    _yolo_to_mm,
)
from .detection_service import DetectionRuntimeError, detect_image, draw_annotated_image
from .paut_v2_adapter import output_files as paut_output_files, read_defect_report, summarize as summarize_paut
from .imaging_backend import get_imaging_backend


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

ProgressFn = Callable[[dict[str, Any]], None]


# ---------------------------------------------------------------------------
# 输入选项
# ---------------------------------------------------------------------------
@dataclass
class InspectionOptions:
    velocity_mps: float = 5900.0
    plate_thickness_mm: float = 40.0
    x_min_mm: float = 20.0
    x_max_mm: float = 80.0
    z_min_mm: float = 0.0
    z_max_mm: float = 40.0
    pixel_step_mm: float = 0.2
    dynamic_range_db: float = 20.0
    gaussian_sigma_mm: float = 0.4
    yolo_confidence: float = 0.25

    def matlab_params(self) -> dict[str, Any]:
        return {
            "velocity_mps": self.velocity_mps,
            "plate_thickness_mm": self.plate_thickness_mm,
            "x_min_mm": self.x_min_mm,
            "x_max_mm": self.x_max_mm,
            "z_min_mm": self.z_min_mm,
            "z_max_mm": self.z_max_mm,
            "pixel_step_mm": self.pixel_step_mm,
            "dynamic_range_db": self.dynamic_range_db,
            "gaussian_sigma_mm": self.gaussian_sigma_mm,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "InspectionOptions":
        data = data or {}

        def num(key: str, default: float) -> float:
            try:
                value = data.get(key, default)
                return float(value) if value is not None else default
            except (TypeError, ValueError):
                return default

        return cls(
            velocity_mps=num("velocity_mps", 5900.0),
            plate_thickness_mm=num("plate_thickness_mm", 40.0),
            x_min_mm=num("x_min_mm", 20.0),
            x_max_mm=num("x_max_mm", 80.0),
            z_min_mm=num("z_min_mm", 0.0),
            z_max_mm=num("z_max_mm", 40.0),
            pixel_step_mm=num("pixel_step_mm", 0.2),
            dynamic_range_db=num("dynamic_range_db", 20.0),
            gaussian_sigma_mm=num("gaussian_sigma_mm", 0.4),
            yolo_confidence=num("yolo_confidence", 0.25),
        )


# ---------------------------------------------------------------------------
# 输入分类
# ---------------------------------------------------------------------------
@dataclass
class InputClassification:
    input_type: str  # "data" | "images" | "invalid"
    data_groups: list[Path] = field(default_factory=list)
    image_files: list[Path] = field(default_factory=list)
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_type": self.input_type,
            "data_groups": [str(p) for p in self.data_groups],
            "image_files": [str(p) for p in self.image_files],
            "message": self.message,
        }


def _collect_image_files(directory: Path) -> list[Path]:
    files: list[Path] = []
    for child in sorted(directory.iterdir(), key=lambda p: p.name):
        if child.is_file() and child.suffix.lower() in IMAGE_EXTS:
            files.append(child)
    return files


def classify_input(source_dir: Path) -> InputClassification:
    """判断拖入目录属于数据组、多组父目录、图片目录还是无法识别。"""
    source = Path(source_dir)
    if not source.exists():
        return InputClassification("invalid", message=f"目录不存在：{source}")
    if not source.is_dir():
        return InputClassification("invalid", message=f"请拖入文件夹，而不是单个文件：{source}")

    data_groups = _find_data_groups(source)
    if data_groups:
        return InputClassification(
            "data",
            data_groups=data_groups,
            message=f"识别到 {len(data_groups)} 个数据组。",
        )

    image_files = _collect_image_files(source)
    if image_files:
        return InputClassification(
            "images",
            image_files=image_files,
            message=f"识别到 {len(image_files)} 张图片（普通图片检测）。",
        )

    return InputClassification(
        "invalid",
        message=(
            f"无法识别该目录：{source}\n"
            "支持两种形态：① 单个数据组（≥16 个数字命名 .txt）；"
            "② 包含多个数据组子文件夹的父目录；③ 普通图片目录。"
        ),
    )


# ---------------------------------------------------------------------------
# 结果模型
# ---------------------------------------------------------------------------
@dataclass
class GroupResult:
    group_id: str
    group_name: str
    index: int
    input_type: str = "data"
    input_files: list[str] = field(default_factory=list)
    das_image_path: str | None = None
    das_mat_path: str | None = None
    ascan_csv_path: str | None = None
    clean_image_path: str | None = None
    annotated_image_path: str | None = None
    peak: dict[str, float] = field(default_factory=dict)
    axis: dict[str, Any] = field(default_factory=dict)
    ascan_candidates: list[dict[str, Any]] = field(default_factory=list)
    yolo_detections: list[dict[str, Any]] = field(default_factory=list)
    defects: list[dict[str, Any]] = field(default_factory=list)
    status: str = "pending"  # pending | completed | partial | failed
    failed_stage: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GroupResult":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class InspectionResult:
    job_id: str
    source_dir: str
    status: str = "pending"  # pending | running | completed | partial | failed | cancelled
    input_type: str = "unknown"
    total_groups: int = 0
    completed_groups: int = 0
    failed_groups: int = 0
    groups: list[GroupResult] = field(default_factory=list)
    defect_count: int = 0
    ascan_candidate_count: int = 0
    annotated_images: list[str] = field(default_factory=list)
    report_path: str | None = None
    report_csv_path: str | None = None
    summary: str = ""
    agent: dict[str, Any] | None = None
    errors: list[dict[str, Any]] = field(default_factory=list)
    started_at: float | None = None
    finished_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "InspectionResult":
        groups = [GroupResult.from_dict(g) for g in data.get("groups", [])]
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        kwargs = {k: v for k, v in data.items() if k in known and k != "groups"}
        kwargs["groups"] = groups
        return cls(**kwargs)


# ---------------------------------------------------------------------------
# 执行器
# ---------------------------------------------------------------------------
class FolderInspectionRunner:
    """后台执行文件夹检测：数据组走 DAS+A扫+YOLO 融合，图片走 YOLO 检测。"""

    def __init__(self) -> None:
        self._imaging = get_imaging_backend()

    def run(
        self,
        source_dir: Path,
        options: InspectionOptions | None = None,
        on_progress: ProgressFn | None = None,
        stop_event: Any = None,
    ) -> InspectionResult:
        options = options or InspectionOptions()
        source = Path(source_dir).resolve()
        job_id = uuid.uuid4().hex[:12]
        result = InspectionResult(job_id=job_id, source_dir=str(source), status="running")
        result.started_at = time.time()

        job_dir = settings.output_dir / "jobs" / job_id
        job_dir.mkdir(parents=True, exist_ok=True)

        def emit(stage: str, **kwargs: Any) -> None:
            if on_progress is not None:
                on_progress({"stage": stage, **kwargs})

        def cancelled() -> bool:
            return stop_event is not None and bool(getattr(stop_event, "is_set", lambda: False)())

        try:
            emit("scanning", message=f"正在扫描：{source}")
            classification = classify_input(source)
            result.input_type = classification.input_type
            emit("classified", **classification.to_dict())

            if classification.input_type == "invalid":
                result.status = "failed"
                result.finished_at = time.time()
                result.errors.append({
                    "job_id": job_id, "group_id": None, "stage": "scanning",
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"), "input_path": str(source),
                    "error": classification.message, "suggestion": "请拖入包含 ≥16 个数字 .txt 的数据组、多组父目录或图片目录。",
                })
                emit("failed", error=classification.message)
                return result

            self._write_manifest(job_dir, source, classification)
            result.total_groups = (
                len(classification.data_groups)
                if classification.input_type == "data"
                else len(classification.image_files)
            )

            if classification.input_type == "data":
                self._run_data_groups(job_dir, source, classification.data_groups, options, result, emit, cancelled)
            else:
                self._run_images(job_dir, classification.image_files, options, result, emit, cancelled)

            if cancelled():
                result.status = "cancelled"
            else:
                result.status = "completed" if result.failed_groups == 0 else "partial"

            result.summary = self._build_summary(result)
            result.agent = self._run_agent(result)

            emit("reporting", message="正在生成报告…")
            self._write_result_json(job_dir, result)
            self._write_csv(job_dir, result)
            result.report_path = self._write_report(job_dir, result)
            if result.report_path:
                self._write_result_json(job_dir, result)

            result.finished_at = time.time()
            emit("completed", result=result.to_dict())
        except Exception as exc:  # noqa: BLE001
            result.status = "failed"
            result.finished_at = time.time()
            result.errors.append({
                "job_id": job_id, "group_id": None, "stage": "run",
                "time": time.strftime("%Y-%m-%d %H:%M:%S"), "input_path": str(source),
                "error": str(exc), "suggestion": "请查看日志定位失败原因。",
            })
            try:
                self._write_result_json(job_dir, result)
            except Exception:  # noqa: BLE001
                pass
            emit("failed", error=str(exc))
        finally:
            result.finished_at = result.finished_at or time.time()
        return result

    # ------------------------------------------------------------------
    # 数据组分支
    # ------------------------------------------------------------------
    def _run_data_groups(
        self,
        job_dir: Path,
        source: Path,
        groups: list[Path],
        options: InspectionOptions,
        result: InspectionResult,
        emit: ProgressFn,
        cancelled: Callable[[], bool],
    ) -> None:
        total = len(groups)
        for idx, group_dir in enumerate(groups):
            if cancelled():
                break

            group_id = f"group_{idx:04d}"
            grp = GroupResult(group_id=group_id, group_name=group_dir.name, index=idx)
            grp.input_files = sorted(
                p.name for p in group_dir.glob("*.txt") if _isfinite_numeric_name(p)
            )
            out_dir = job_dir / group_id
            out_dir.mkdir(parents=True, exist_ok=True)
            params = options.matlab_params()

            try:
                emit("paut_v2", group_index=idx, total_groups=total, group_name=group_dir.name,
                     message=f"第 {idx + 1}/{total} 组：新版 PAUT 成像与定量分析")
                self._imaging.run_paut(group_dir, out_dir, parameters=params)

                v2_defects = read_defect_report(out_dir)
                if v2_defects is not None:
                    files = paut_output_files(out_dir)
                    grp.das_image_path = str(files["thresholded_image"]) if files["thresholded_image"] else None
                    grp.clean_image_path = str(files["original_image"]) if files["original_image"] else None
                    grp.annotated_image_path = grp.das_image_path
                    grp.ascan_csv_path = str(files["report_csv"]) if files["report_csv"] else None
                    grp.ascan_candidates = v2_defects
                    grp.defects = v2_defects
                    grp.status = "completed"
                    result.completed_groups += 1
                    result.groups.append(grp)
                    emit("group_done", group_index=idx, total_groups=total,
                         group_result=grp.to_dict(), annotated_image=grp.annotated_image_path)
                    continue

                das_png = _find_newest(out_dir, "B扫成像_*.png")
                das_mat = _find_newest(out_dir, "B扫数据_*.mat")
                ascan_csv = out_dir / "ascan_results.csv"
                grp.das_image_path = str(das_png) if das_png else None
                grp.das_mat_path = str(das_mat) if das_mat else None
                grp.ascan_csv_path = str(ascan_csv) if ascan_csv.exists() else None

                candidates = _read_ascan_csv(ascan_csv)
                grp.ascan_candidates = candidates

                mat = _load_das_mat(das_mat)
                peak: dict[str, float] = {"peak_x_mm": 0.0, "peak_z_mm": 0.0, "peak_db": 0.0}
                axis: dict[str, Any] = {
                    "x_min_mm": options.x_min_mm, "x_max_mm": options.x_max_mm,
                    "z_min_mm": options.z_min_mm, "z_max_mm": options.z_max_mm,
                    "x_step_mm": options.pixel_step_mm, "z_step_mm": options.pixel_step_mm,
                    "n_x": 0, "n_z": 0,
                }
                yolo_detections: list[dict[str, Any]] = []
                defects: list[dict[str, Any]] = []

                if mat is not None:
                    img_db, x_img, z_img = mat
                    peak = _peak_position(img_db, x_img, z_img)
                    axis = _axis_info(x_img, z_img)
                    rgb = _render_colormap(img_db)
                    clean_png = out_dir / "das_clean.png"
                    Image.fromarray(rgb).convert("RGB").save(clean_png)
                    grp.clean_image_path = str(clean_png)

                    emit("yolo", group_index=idx, total_groups=total, group_name=group_dir.name,
                         message=f"第 {idx + 1}/{total} 组：YOLO 识别")
                    try:
                        raw = detect_image(clean_png, options.yolo_confidence)
                        yolo_detections = [_yolo_to_mm(d, axis) for d in raw]
                    except DetectionRuntimeError as exc:
                        # YOLO 失败不阻断：保留 A 扫/DAS 结果，标记为部分完成
                        grp.status = "partial"
                        grp.failed_stage = "yolo"
                        result.errors.append({
                            "job_id": result.job_id, "group_id": group_id, "stage": "yolo",
                            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                            "input_path": str(group_dir), "error": str(exc),
                            "suggestion": "检查 YOLO 模型与依赖后重试；本组已保留 A 扫/DAS 结果。",
                        })

                    grp.yolo_detections = yolo_detections
                    emit("fusion", group_index=idx, total_groups=total, group_name=group_dir.name,
                         message=f"第 {idx + 1}/{total} 组：结果融合")
                    defects = _fuse_defects(candidates, yolo_detections, img_db, x_img, z_img)

                    annotated_png = out_dir / "das_annotated.png"
                    _render_annotated(rgb, defects, axis, annotated_png, peak)
                    grp.annotated_image_path = str(annotated_png)
                else:
                    # 无 .mat：退化为仅 A 扫候选点
                    defects = [
                        {
                            "index": i + 1, "source": "ascan", "class_name": "待复核",
                            "confidence": None, "is_demo": False,
                            "x_mm": round(c["x_mm"], 2), "z_mm": round(c["z_mm"], 2),
                            "width_mm": 0.0, "height_mm": 0.0, "size_method": "unknown",
                            "ascan_score": c["score"], "ascan_support": c["support"],
                            "ascan_avg_error_us": c["avg_error_us"], "match_distance_mm": None,
                        }
                        for i, c in enumerate(candidates)
                    ]

                grp.peak = peak
                grp.axis = axis
                grp.defects = defects
                if grp.status != "partial":
                    grp.status = "completed"
                result.completed_groups += 1
            except Exception as exc:  # noqa: BLE001
                grp.status = "failed"
                grp.failed_stage = grp.failed_stage or "processing"
                grp.error = str(exc)
                result.failed_groups += 1
                result.errors.append({
                    "job_id": result.job_id, "group_id": group_id, "stage": grp.failed_stage,
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"), "input_path": str(group_dir),
                    "error": str(exc), "suggestion": "请查看该组输出目录与日志定位原因。",
                })

            result.groups.append(grp)
            emit("group_done", group_index=idx, total_groups=total,
                 group_result=grp.to_dict(), annotated_image=grp.annotated_image_path)

    # ------------------------------------------------------------------
    # 图片分支
    # ------------------------------------------------------------------
    def _run_images(
        self,
        job_dir: Path,
        image_files: list[Path],
        options: InspectionOptions,
        result: InspectionResult,
        emit: ProgressFn,
        cancelled: Callable[[], bool],
    ) -> None:
        total = len(image_files)
        out_dir = job_dir / "images"
        out_dir.mkdir(parents=True, exist_ok=True)
        for idx, image_path in enumerate(image_files):
            if cancelled():
                break
            group_id = f"image_{idx:04d}"
            grp = GroupResult(group_id=group_id, group_name=image_path.name, index=idx, input_type="images")
            grp.input_files = [image_path.name]
            grp.das_image_path = str(image_path)
            annotated = out_dir / f"{image_path.stem}_annotated.png"
            try:
                emit("yolo", group_index=idx, total_groups=total, group_name=image_path.name,
                     message=f"第 {idx + 1}/{total} 张：YOLO 识别")
                detections = detect_image(image_path, options.yolo_confidence)
                draw_annotated_image(image_path, detections, annotated)
                grp.annotated_image_path = str(annotated)
                grp.yolo_detections = [
                    {
                        "class_name": d.get("class_name", "缺陷"),
                        "confidence": float(d.get("confidence", 0.0)),
                        "source": d.get("source", "model"),
                        "is_demo": d.get("source", "") == "demo",
                        "x_min": float(d.get("x_min", 0.0)),
                        "y_min": float(d.get("y_min", 0.0)),
                        "x_max": float(d.get("x_max", 0.0)),
                        "y_max": float(d.get("y_max", 0.0)),
                    }
                    for d in detections
                ]
                grp.defects = [
                    {
                        "index": i + 1, "source": "yolo", "class_name": d["class_name"],
                        "confidence": d["confidence"], "is_demo": d.get("is_demo", False),
                        "x_px": round((d["x_min"] + d["x_max"]) / 2.0, 1),
                        "y_px": round((d["y_min"] + d["y_max"]) / 2.0, 1),
                        "width_px": round(d["x_max"] - d["x_min"], 1),
                        "height_px": round(d["y_max"] - d["y_min"], 1),
                    }
                    for i, d in enumerate(grp.yolo_detections)
                ]
                grp.status = "completed"
                result.completed_groups += 1
            except Exception as exc:  # noqa: BLE001
                grp.status = "failed"
                grp.failed_stage = "yolo"
                grp.error = str(exc)
                result.failed_groups += 1
                result.errors.append({
                    "job_id": result.job_id, "group_id": group_id, "stage": "yolo",
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"), "input_path": str(image_path),
                    "error": str(exc), "suggestion": "检查 YOLO 模型与依赖。",
                })
            result.groups.append(grp)
            emit("group_done", group_index=idx, total_groups=total,
                 group_result=grp.to_dict(), annotated_image=grp.annotated_image_path)

    # ------------------------------------------------------------------
    # 汇总 / Agent / 报告 / 序列化
    # ------------------------------------------------------------------
    def _build_summary(self, result: InspectionResult) -> str:
        all_defects = [
            {**d, "group_name": g.group_name}
            for g in result.groups for d in g.defects
        ]
        result.defect_count = len(all_defects)
        result.ascan_candidate_count = sum(len(g.ascan_candidates) for g in result.groups)
        result.annotated_images = [g.annotated_image_path for g in result.groups if g.annotated_image_path]

        if result.input_type == "images":
            return (
                f"共分析 {result.total_groups} 张图片，"
                f"检出缺陷候选 {result.defect_count} 处（AI 图像识别）。"
            )

        paut_v2 = [d for d in all_defects if d.get("source") == "paut_v2"]
        if paut_v2:
            analysis = summarize_paut(paut_v2)
            return f"共分析 {result.total_groups} 组 PAUT 数据，检出 {result.defect_count} 处缺陷：{analysis['summary']}。"

        joint = [d for d in all_defects if d.get("source") == "joint"]
        ascan = [d for d in all_defects if d.get("source") == "ascan"]
        yolo = [d for d in all_defects if d.get("source") == "yolo"]
        lines = [
            f"共分析 {result.total_groups} 组数据。",
            f"检出缺陷候选 {result.defect_count} 处：联合（A扫+AI）{len(joint)} 处、"
            f"A扫独立 {len(ascan)} 处、AI 独立 {len(yolo)} 处。",
        ]
        if joint:
            first = joint[0]
            lines.append(
                f"首要联合缺陷位于 x={first['x_mm']}mm, z={first['z_mm']}mm，"
                f"尺寸约 {first['width_mm']}×{first['height_mm']}mm。"
            )
        return "".join(lines)

    def _run_agent(self, result: InspectionResult) -> dict[str, Any] | None:
        try:
            from .agent_service import analyze_realtime_batch

            frames: list[dict[str, Any]] = []
            for group in result.groups:
                if group.status not in {"completed", "partial"}:
                    continue
                peak = group.peak or {}
                axis = group.axis or {}
                yolo = group.yolo_detections or []
                frames.append({
                    "frame_id": group.group_name,
                    "source_group": group.group_name,
                    "shape": [axis.get("n_z"), axis.get("n_x")],
                    "sample_rate_hz": None,
                    "velocity_mps": 5900.0,
                    "plate_thickness_mm": None,
                    "image": {
                        "x_range_mm": [axis.get("x_min_mm"), axis.get("x_max_mm")],
                        "z_range_mm": [axis.get("z_min_mm"), axis.get("z_max_mm")],
                        "shape": [axis.get("n_z"), axis.get("n_x")],
                        "peak_x_mm": peak.get("peak_x_mm"),
                        "peak_z_mm": peak.get("peak_z_mm"),
                        "peak_db": peak.get("peak_db"),
                    },
                    "analysis": {
                        "ascan": group.ascan_candidates,
                        "yolo": {
                            "status": "ok" if yolo else "empty",
                            "is_demo": any(d.get("is_demo") for d in yolo),
                            "count": len(yolo),
                            "detections": yolo,
                            "error": group.error if group.failed_stage == "yolo" else None,
                        },
                        "joint": group.defects,
                    },
                })
            if not frames:
                return None
            local_result = {
                "summary": result.summary,
                "group_count": result.total_groups,
                "defect_count": result.defect_count,
            }
            return analyze_realtime_batch(frames, local_result)
        except Exception as exc:  # noqa: BLE001
            print(f"[Inspection] Agent 分析失败：{exc}")
            return {
                "configured": False, "status": "失败",
                "summary": result.summary, "error": str(exc),
            }

    def _write_manifest(self, job_dir: Path, source: Path, classification: InputClassification) -> None:
        entries = classification.data_groups if classification.input_type == "data" else classification.image_files
        groups = [
            {"group_id": f"{classification.input_type}_{i:04d}", "name": p.name, "path": str(p)}
            for i, p in enumerate(entries)
        ]
        manifest = {
            "job_id": job_dir.name,
            "source_dir": str(source),
            "input_type": classification.input_type,
            "groups": groups,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        (job_dir / "input_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _write_result_json(self, job_dir: Path, result: InspectionResult) -> None:
        (job_dir / "result.json").write_text(
            json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _write_csv(self, job_dir: Path, result: InspectionResult) -> None:
        rows = [["组", "序号", "来源", "类型", "x(mm)", "z(mm)", "宽(mm)", "高(mm)", "置信度"]]
        for group in result.groups:
            for defect in group.defects:
                if result.input_type == "images":
                    rows.append([
                        group.group_name, defect.get("index"), _source_label(defect.get("source")),
                        defect.get("class_name"), defect.get("x_px", ""), defect.get("y_px", ""),
                        defect.get("width_px", ""), defect.get("height_px", ""),
                        _fmt_conf(defect.get("confidence")),
                    ])
                else:
                    rows.append([
                        group.group_name, defect.get("index"), _source_label(defect.get("source")),
                        defect.get("class_name"), defect.get("x_mm", ""), defect.get("z_mm", ""),
                        defect.get("width_mm", ""), defect.get("height_mm", ""),
                        _fmt_conf(defect.get("confidence")),
                    ])
        path = job_dir / "report.csv"
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerows(rows)
        result.report_csv_path = str(path)

    def _write_report(self, job_dir: Path, result: InspectionResult) -> str | None:
        try:
            from .report_service import create_inspection_report

            return str(create_inspection_report(result.to_dict()))
        except Exception as exc:  # noqa: BLE001
            result.errors.append({
                "job_id": result.job_id, "group_id": None, "stage": "reporting",
                "time": time.strftime("%Y-%m-%d %H:%M:%S"), "input_path": result.source_dir,
                "error": f"报告生成失败：{exc}", "suggestion": "结果仍保存在 result.json 与 report.csv。",
            })
            return None


def _source_label(source: Any) -> str:
    return {"joint": "A扫+AI", "yolo": "AI", "ascan": "A扫"}.get(source, str(source))


def _fmt_conf(value: Any) -> str:
    if value is None:
        return ""
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return str(value)


inspection_runner = FolderInspectionRunner()


def run_folder_inspection(
    source_dir: Path,
    options: InspectionOptions | None = None,
    on_progress: ProgressFn | None = None,
    stop_event: Any = None,
) -> InspectionResult:
    """统一文件夹检测入口：供桌面任务控制器直接调用。"""
    return inspection_runner.run(source_dir, options=options, on_progress=on_progress, stop_event=stop_event)
