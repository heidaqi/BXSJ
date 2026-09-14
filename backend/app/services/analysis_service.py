"""数据集统一分析服务。

将整个工作流串联为一个端到端流程：

    上传数据集文件夹
      → 逐组调用 DAS.m（成像）+ Ascan.m（A 扫 TOFD 类缺陷检测）
      → 读取 ascan_results.csv（A 扫候选点）
      → 从 DAS .mat 结果重建图像，调用 YOLO 做 AI 识别
      → 综合 A 扫 + AI + DAS 峰值做缺陷位置与尺寸融合
      → 在重建图像上标注缺陷分析结果
      → 汇总为结构化结果（可选调用 Agent 做综合说明）

该模块不依赖旧的“上传图片→YOLO 检测”流程，也不依赖 ZIP 上传 / 实时批次流程。
"""
from __future__ import annotations

import csv
import math
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from ..config import settings
from .ascan_candidate_model import accepted_ascan_candidates


def _isfinite_numeric_name(path: Path) -> bool:
    """判断文件名是否为有限数字（过滤 readme.txt / inf.txt 等非数据文件）。"""
    try:
        value = float(path.stem)
    except ValueError:
        return False
    return math.isfinite(value)


def _find_data_groups(source_path: Path) -> list[Path]:
    """查找数据组。

    支持两种结构：
      1. 源目录本身即一组（目录内 >= 16 个数字命名 txt）。
      2. 源目录包含多个子目录，每个子目录是一组（data/groups 的结构）。
    """
    source_path = Path(source_path)
    if not source_path.exists():
        return []

    # 源目录本身是否为一组
    txt_files = [p for p in source_path.glob("*.txt") if _isfinite_numeric_name(p)]
    if len(txt_files) >= 16:
        return [source_path]

    # 子目录模式
    groups: list[Path] = []
    for child in sorted(source_path.iterdir(), key=lambda p: p.name):
        if not child.is_dir():
            continue
        txt_files = [p for p in child.glob("*.txt") if _isfinite_numeric_name(p)]
        if len(txt_files) >= 16:
            groups.append(child)
    return groups


def _find_newest(output_dir: Path, pattern: str) -> Path | None:
    matches = sorted(output_dir.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return matches[0] if matches else None


def _read_ascan_csv(csv_path: Path) -> list[dict[str, Any]]:
    if not csv_path.exists():
        return []
    candidates: list[dict[str, Any]] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        for row in accepted_ascan_candidates([dict(item) for item in reader]):
            try:
                candidates.append(
                    {
                        "x_mm": float(row.get("X_mm", 0.0)),
                        "z_mm": float(row.get("Z_mm", 0.0)),
                        "score": float(row.get("Score", 0.0)),
                        "support": int(float(row.get("Support", 0.0))),
                        "avg_error_us": float(row.get("AvgError_us", 0.0)),
                        "candidate_tier": row.get("CandidateTier", "primary"),
                        "ml_probability": float(row.get("MLProbability", row.get("Score", 0.0))),
                        "ml_level": row.get("MLLevel", "物理候选"),
                    }
                )
            except (TypeError, ValueError):
                continue
    return candidates


def _load_das_mat(mat_path: Path) -> tuple[Any, Any, Any] | None:
    """读取 DAS.m 保存的 .mat（v7 格式），返回 (img_db, x_img, z_img)。"""
    if mat_path is None or not mat_path.exists():
        return None
    try:
        from scipy.io import loadmat

        data = loadmat(str(mat_path))
        img_db = data.get("img_db")
        x_img = data.get("x_img")
        z_img = data.get("z_img")
        if img_db is None or x_img is None or z_img is None:
            return None
        import numpy as np

        return np.asarray(img_db), np.asarray(x_img).ravel(), np.asarray(z_img).ravel()
    except Exception as exc:  # 读取失败不阻断流程
        print(f"[Analysis] 读取 .mat 失败：{exc}")
        return None


def _peak_position(img_db: Any, x_img: Any, z_img: Any) -> dict[str, float]:
    import numpy as np

    idx = int(np.nanargmax(img_db))
    n_x = img_db.shape[1]
    iz, ix = divmod(idx, n_x)
    return {
        "peak_x_mm": float(x_img[ix]),
        "peak_z_mm": float(z_img[iz]),
        "peak_db": float(img_db[iz, ix]),
    }


def _jet_colormap(n: int = 256) -> Any:
    import numpy as np

    t = np.linspace(0.0, 1.0, n)
    r = np.clip(1.5 - np.abs(4.0 * t - 3.0), 0.0, 1.0)
    g = np.clip(1.5 - np.abs(4.0 * t - 2.0), 0.0, 1.0)
    b = np.clip(1.5 - np.abs(4.0 * t - 1.0), 0.0, 1.0)
    return np.stack([r, g, b], axis=1)


def _render_colormap(img_db: Any) -> Any:
    import numpy as np

    finite = np.isfinite(img_db)
    vmin = float(np.min(img_db[finite])) if finite.any() else 0.0
    vmax = float(np.max(img_db[finite])) if finite.any() else 1.0
    norm = np.zeros_like(img_db, dtype=float)
    norm[finite] = (img_db[finite] - vmin) / (vmax - vmin + 1e-12)
    cmap = _jet_colormap(256)
    idx = np.clip((norm * 255.0).astype(np.int64), 0, 255)
    rgb = cmap[idx]  # (nz, nx, 3)
    return (rgb * 255.0).astype(np.uint8)


def _axis_info(x_img: Any, z_img: Any) -> dict[str, float]:
    x_step = float(x_img[1] - x_img[0]) if len(x_img) > 1 else 1.0
    z_step = float(z_img[1] - z_img[0]) if len(z_img) > 1 else 1.0
    return {
        "x_min_mm": float(x_img[0]),
        "x_max_mm": float(x_img[-1]),
        "z_min_mm": float(z_img[0]),
        "z_max_mm": float(z_img[-1]),
        "x_step_mm": x_step,
        "z_step_mm": z_step,
        "n_x": int(len(x_img)),
        "n_z": int(len(z_img)),
    }


def _mm_to_px(x_mm: float, z_mm: float, axis: dict[str, float]) -> tuple[int, int]:
    px = int(round((x_mm - axis["x_min_mm"]) / axis["x_step_mm"]))
    py = int(round((z_mm - axis["z_min_mm"]) / axis["z_step_mm"]))
    return px, py


def _estimate_blob_size(
    img_db: Any, x_img: Any, z_img: Any, x_mm: float, z_mm: float
) -> dict[str, float]:
    """以候选点为中心，按 -6 dB 阈值估算缺陷连通区域的尺寸（mm）。"""
    try:
        import numpy as np
        from scipy import ndimage

        axis = _axis_info(x_img, z_img)
        px, py = _mm_to_px(x_mm, z_mm, axis)
        px = max(0, min(axis["n_x"] - 1, px))
        py = max(0, min(axis["n_z"] - 1, py))

        # 局部窗口（约 ±12 mm）内取峰值
        win_x = max(1, int(round(12.0 / axis["x_step_mm"])))
        win_z = max(1, int(round(12.0 / axis["z_step_mm"])))
        x0, x1 = max(0, px - win_x), min(axis["n_x"], px + win_x + 1)
        z0, z1 = max(0, py - win_z), min(axis["n_z"], py + win_z + 1)
        window = img_db[z0:z1, x0:x1]
        if window.size == 0:
            return {"width_mm": 0.0, "height_mm": 0.0}
        peak = float(np.nanmax(window))
        threshold = peak - 6.0

        mask = img_db >= threshold
        if not mask[py, px]:
            # 候选点本身低于阈值时，退化为局部最大值位置
            lx, lz = np.unravel_index(int(np.nanargmax(window)), window.shape)
            px, py = x0 + lx, z0 + lz
        label, num = ndimage.label(mask)
        if num == 0:
            return {"width_mm": 0.0, "height_mm": 0.0}
        component = label[py, px]
        if component == 0:
            return {"width_mm": 0.0, "height_mm": 0.0}
        ys, xs = np.where(label == component)
        width = (float(xs.max()) - float(xs.min()) + 1.0) * axis["x_step_mm"]
        height = (float(ys.max()) - float(ys.min()) + 1.0) * axis["z_step_mm"]
        return {"width_mm": round(width, 2), "height_mm": round(height, 2)}
    except Exception as exc:
        print(f"[Analysis] 尺寸估算失败：{exc}")
        return {"width_mm": 0.0, "height_mm": 0.0}


def _yolo_to_mm(detection: dict[str, Any], axis: dict[str, float]) -> dict[str, Any]:
    cx = (detection["x_min"] + detection["x_max"]) / 2.0
    cz = (detection["y_min"] + detection["y_max"]) / 2.0
    cx_mm = axis["x_min_mm"] + cx * axis["x_step_mm"]
    cz_mm = axis["z_min_mm"] + cz * axis["z_step_mm"]
    w_mm = (detection["x_max"] - detection["x_min"]) * axis["x_step_mm"]
    h_mm = (detection["y_max"] - detection["y_min"]) * axis["z_step_mm"]
    return {
        "class_name": detection.get("class_name", "缺陷"),
        "confidence": float(detection.get("confidence", 0.0)),
        "source": detection.get("source", "model"),
        "is_demo": detection.get("source", "") == "demo",
        "x_min_mm": round(cx_mm - w_mm / 2.0, 2),
        "z_min_mm": round(cz_mm - h_mm / 2.0, 2),
        "x_max_mm": round(cx_mm + w_mm / 2.0, 2),
        "z_max_mm": round(cz_mm + h_mm / 2.0, 2),
        "center_x_mm": round(cx_mm, 2),
        "center_z_mm": round(cz_mm, 2),
        "width_mm": round(w_mm, 2),
        "height_mm": round(h_mm, 2),
    }


def _fuse_defects(
    candidates: list[dict[str, Any]],
    yolo_mm: list[dict[str, Any]],
    img_db: Any,
    x_img: Any,
    z_img: Any,
    match_radius_mm: float = 8.0,
) -> list[dict[str, Any]]:
    defects: list[dict[str, Any]] = []
    used_ascan: set[int] = set()

    for yolo in yolo_mm:
        best_idx: int | None = None
        best_dist: float | None = None
        for j, cand in enumerate(candidates):
            if j in used_ascan:
                continue
            dist = math.hypot(cand["x_mm"] - yolo["center_x_mm"], cand["z_mm"] - yolo["center_z_mm"])
            if best_dist is None or dist < best_dist:
                best_dist, best_idx = dist, j
        matched = best_idx is not None and best_dist is not None and best_dist <= match_radius_mm
        if matched:
            used_ascan.add(best_idx)
            cand = candidates[best_idx]
            defects.append(
                {
                    "index": len(defects) + 1,
                    "source": "joint",
                    "class_name": yolo["class_name"],
                    "confidence": yolo["confidence"],
                    "is_demo": yolo["is_demo"],
                    "x_mm": round(yolo["center_x_mm"], 2),
                    "z_mm": round(yolo["center_z_mm"], 2),
                    "width_mm": yolo["width_mm"],
                    "height_mm": yolo["height_mm"],
                    "size_method": "yolo",
                    "ascan_score": cand["score"],
                    "ascan_support": cand["support"],
                    "ascan_avg_error_us": cand["avg_error_us"],
                    "match_distance_mm": round(best_dist, 2),
                }
            )
        else:
            defects.append(
                {
                    "index": len(defects) + 1,
                    "source": "yolo",
                    "class_name": yolo["class_name"],
                    "confidence": yolo["confidence"],
                    "is_demo": yolo["is_demo"],
                    "x_mm": round(yolo["center_x_mm"], 2),
                    "z_mm": round(yolo["center_z_mm"], 2),
                    "width_mm": yolo["width_mm"],
                    "height_mm": yolo["height_mm"],
                    "size_method": "yolo",
                    "ascan_score": None,
                    "ascan_support": None,
                    "ascan_avg_error_us": None,
                    "match_distance_mm": None,
                }
            )

    for j, cand in enumerate(candidates):
        if j in used_ascan:
            continue
        blob = _estimate_blob_size(img_db, x_img, z_img, cand["x_mm"], cand["z_mm"])
        defects.append(
            {
                "index": len(defects) + 1,
                "source": "ascan",
                "class_name": "待复核",
                "confidence": None,
                "is_demo": False,
                "x_mm": round(cand["x_mm"], 2),
                "z_mm": round(cand["z_mm"], 2),
                "width_mm": blob["width_mm"],
                "height_mm": blob["height_mm"],
                "size_method": "das_blob",
                "ascan_score": cand["score"],
                "ascan_support": cand["support"],
                "ascan_avg_error_us": cand["avg_error_us"],
                "match_distance_mm": None,
            }
        )

    return defects


def _load_default_font(size: int) -> Any:
    for name in ("arial.ttf", "DejaVuSans.ttf", "segoeui.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _render_annotated(
    rgb: Any,
    defects: list[dict[str, Any]],
    axis: dict[str, float],
    out_path: Path,
    peak: dict[str, float],
) -> Path:
    import numpy as np

    image = Image.fromarray(rgb).convert("RGB")
    draw = ImageDraw.Draw(image)
    font = _load_default_font(12)
    font_small = _load_default_font(10)

    n_x, n_z = axis["n_x"], axis["n_z"]
    scale_x = image.width / n_x
    scale_z = image.height / n_z

    # 峰值位置十字
    ppx = (peak["peak_x_mm"] - axis["x_min_mm"]) / axis["x_step_mm"] * scale_x
    ppy = (peak["peak_z_mm"] - axis["z_min_mm"]) / axis["z_step_mm"] * scale_z
    draw.line([ppx - 8, ppy, ppx + 8, ppy], fill=(0, 255, 0), width=1)
    draw.line([ppx, ppy - 8, ppx, ppy + 8], fill=(0, 255, 0), width=1)

    for defect in defects:
        color = (255, 255, 0) if defect["source"] == "joint" else (255, 0, 0)
        if defect["source"] == "yolo":
            color = (0, 200, 255)
        px = (defect["x_mm"] - axis["x_min_mm"]) / axis["x_step_mm"] * scale_x
        pz = (defect["z_mm"] - axis["z_min_mm"]) / axis["z_step_mm"] * scale_z
        w = max(4.0, defect["width_mm"] / axis["x_step_mm"] * scale_x)
        h = max(4.0, defect["height_mm"] / axis["z_step_mm"] * scale_z)
        draw.rectangle([px - w / 2, pz - h / 2, px + w / 2, pz + h / 2], outline=color, width=2)
        draw.line([px - 4, pz, px + 4, pz], fill=color, width=1)
        draw.line([px, pz - 4, px, pz + 4], fill=color, width=1)
        label = f"D{defect['index']} ({defect['x_mm']},{defect['z_mm']})mm"
        draw.text((px + 6, pz - 6), label, fill=color, font=font_small)

    # 顶部说明
    draw.rectangle([0, 0, image.width, 18], fill=(0, 0, 0))
    info = (
        f"DAS x[{axis['x_min_mm']:.1f},{axis['x_max_mm']:.1f}]mm "
        f"z[{axis['z_min_mm']:.1f},{axis['z_max_mm']:.1f}]mm  "
        f"峰值({peak['peak_x_mm']:.1f},{peak['peak_z_mm']:.1f})mm 缺陷数:{len(defects)}"
    )
    draw.text((4, 2), info, fill=(255, 255, 255), font=font)

    image.save(out_path)
    return out_path


def _to_url(path: Path) -> str:
    try:
        rel = path.resolve().relative_to(Path(settings.output_dir).resolve())
        return "/outputs/" + rel.as_posix()
    except ValueError:
        return str(path)


@dataclass
class AnalysisJob:
    analysis_id: str
    source_path: str
    params: dict[str, Any] = field(default_factory=dict)
    state: str = "排队中"
    message: str = ""
    error: str = ""
    created_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    progress: dict[str, int] = field(default_factory=lambda: {"processed": 0, "total": 0})
    result: dict[str, Any] | None = None


class DatasetAnalysisService:
    """数据集文件夹 → DAS + A 扫 → AI → 综合分析 → 标注 → 汇总。"""

    def __init__(self) -> None:
        self._jobs: dict[str, AnalysisJob] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # 公共入口
    # ------------------------------------------------------------------
    def start_analysis(self, request: dict[str, Any]) -> str:
        source_path = str(request.get("source_path", "")).strip().strip('"').strip("'")
        if not source_path:
            raise ValueError("必须提供数据集文件夹路径（source_path）。")

        analysis_id = uuid.uuid4().hex
        job = AnalysisJob(
            analysis_id=analysis_id,
            source_path=source_path,
            params=self._normalize_params(request),
        )
        with self._lock:
            self._jobs[analysis_id] = job

        thread = threading.Thread(target=self._run, args=(analysis_id,), daemon=True)
        thread.start()
        return analysis_id

    def get_job(self, analysis_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(analysis_id)
        if job is None:
            return None
        return self._job_to_dict(job)

    def list_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)[:limit]
        return [self._job_to_dict(job) for job in jobs]

    # ------------------------------------------------------------------
    # 参数与结果序列化
    # ------------------------------------------------------------------
    @staticmethod
    def _normalize_params(request: dict[str, Any]) -> dict[str, Any]:
        def num(key: str, default: float) -> float:
            try:
                value = request.get(key, default)
                return float(value) if value is not None else default
            except (TypeError, ValueError):
                return default

        return {
            "velocity_mps": num("velocity_mps", 5900.0),
            "plate_thickness_mm": num("plate_thickness_mm", 40.0),
            "x_min_mm": num("x_min_mm", 20.0),
            "x_max_mm": num("x_max_mm", 80.0),
            "z_min_mm": num("z_min_mm", 0.0),
            "z_max_mm": num("z_max_mm", 40.0),
            "pixel_step_mm": num("pixel_step_mm", 0.2),
            "dynamic_range_db": num("dynamic_range_db", 20.0),
            "gaussian_sigma_mm": num("gaussian_sigma_mm", 0.4),
            "yolo_confidence": num("yolo_confidence", 0.25),
        }

    @staticmethod
    def _job_to_dict(job: AnalysisJob) -> dict[str, Any]:
        return {
            "analysis_id": job.analysis_id,
            "source_path": job.source_path,
            "params": job.params,
            "state": job.state,
            "message": job.message,
            "error": job.error,
            "created_at": job.created_at,
            "completed_at": job.completed_at,
            "progress": job.progress,
            "result": job.result,
        }

    def _update(self, analysis_id: str, **kwargs: Any) -> None:
        with self._lock:
            job = self._jobs.get(analysis_id)
            if job is None:
                return
            for key, value in kwargs.items():
                setattr(job, key, value)

    # ------------------------------------------------------------------
    # 后台执行
    # ------------------------------------------------------------------
    def _run(self, analysis_id: str) -> None:
        with self._lock:
            job = self._jobs.get(analysis_id)
        if job is None:
            return
        try:
            # HTTP 数据集分析和桌面目录分析共用同一个执行器，避免两套流水线漂移。
            from .inspection_service import InspectionOptions, run_folder_inspection

            self._update(analysis_id, state="运行中", message="正在扫描数据组…")

            def on_progress(payload: dict[str, Any]) -> None:
                total = int(payload.get("total_groups") or job.progress.get("total") or 0)
                index = int(payload.get("group_index") or 0)
                processed = index + 1 if payload.get("stage") == "group_done" else index
                self._update(
                    analysis_id,
                    message=str(payload.get("message") or job.message),
                    progress={"processed": min(processed, total), "total": total},
                )

            inspection = run_folder_inspection(
                Path(job.source_path),
                options=InspectionOptions.from_dict(job.params),
                on_progress=on_progress,
            )
            raw = inspection.to_dict()
            groups: list[dict[str, Any]] = []
            joint_count = 0
            for group in raw.get("groups") or []:
                converted = dict(group)
                for source_key, url_key in (
                    ("das_image_path", "das_image_url"),
                    ("annotated_image_path", "das_annotated_url"),
                ):
                    value = converted.get(source_key)
                    converted[url_key] = _to_url(Path(value)) if value else None
                for defect in converted.get("defects") or []:
                    joint_count += int(defect.get("source") == "joint")
                groups.append(converted)

            combined = {
                "summary": raw.get("summary") or "数据集分析完成。",
                "defect_count": raw.get("defect_count", 0),
                "joint_count": joint_count,
                "agent": raw.get("agent"),
                "report_path": raw.get("report_path"),
                "report_csv_path": raw.get("report_csv_path"),
            }
            state = "失败" if inspection.status == "failed" else "完成"
            self._update(
                analysis_id,
                state=state,
                message="数据集分析完成" if state == "完成" else "数据集分析失败",
                error="；".join(str(item.get("error")) for item in raw.get("errors") or []) if state == "失败" else "",
                result={"groups": groups, "combined": combined, "inspection": raw},
                progress={"processed": raw.get("completed_groups", 0), "total": raw.get("total_groups", 0)},
                completed_at=time.time(),
            )
        except Exception as exc:
            self._update(
                analysis_id,
                state="失败",
                error=str(exc),
                completed_at=time.time(),
            )

    # ------------------------------------------------------------------
    # 单组分析
dataset_analysis_service = DatasetAnalysisService()
