from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np

from ..config import settings


CLASSIFIER_MODEL_PATH = settings.resource_dir / "models" / "defect_classifier" / "best_PHYS_ROBUST.joblib"
CLASSIFIER_FALLBACK_MODEL_PATH = settings.resource_dir / "models" / "defect_classifier" / "best_PHYS_SVM-RBF.joblib"
CLASSIFIER_CONFIDENCE_THRESHOLD = 0.70
CLASSIFIER_INPUT_BOUNDARY = (
    "A扫/FMC分类器基于COMSOL小样本物理特征模型，仅输出辅助类别概率；"
    "不替代检测人员复核或标准验收结论。"
)
CLASSIFIER_CLASS_CN = {"slag": "夹渣", "crack": "裂纹", "pore": "气孔", "lof": "未熔合", "uncertain": "不确定"}


def classify_frame(
    source_path: str | Path,
    defects: list[dict[str, Any]],
    *,
    velocity_mps: float | None,
    tx_positions_mm: list[float] | None = None,
) -> dict[str, Any]:
    """Run the case-level classifier exactly once for one FMC frame.

    The training sample is a complete FMC frame plus one main-defect location. Runtime
    therefore selects one dominant response anchor and returns a frame-level prior; it
    never copies the prediction onto individual A-scan candidates.
    """

    summary: dict[str, Any] = {
        "enabled": True,
        "status": "未运行",
        "model_path": str(CLASSIFIER_MODEL_PATH),
        "count": 0,
        "analysis_unit": "single_fmc_frame",
        "result": None,
        "warnings": [],
        "errors": [],
        "boundary": CLASSIFIER_INPUT_BOUNDARY,
    }
    try:
        from ..vendor.defect_classification.defect_classifier import predict_fmc_case
    except Exception as exc:  # noqa: BLE001
        summary["status"] = "不可用"
        summary["errors"].append(f"A扫分类器依赖未就绪: {exc}")
        return summary

    try:
        fmc, t_axis, x_el = load_fmc_from_source(source_path, tx_positions_mm=tx_positions_mm)
    except Exception as exc:  # noqa: BLE001
        summary["status"] = "数据不支持"
        summary["errors"].append(str(exc))
        return summary

    model_path = _available_model_path()
    summary["model_path"] = str(model_path)
    if not model_path:
        summary["status"] = "不可用"
        summary["errors"].append(f"模型文件不存在: {CLASSIFIER_MODEL_PATH}")
        return summary

    anchor_x, anchor_z, anchor_meta = _select_frame_anchor(defects)
    result = _predict_with_velocity_ensemble(
        predict_fmc_case,
        fmc=fmc,
        t=t_axis,
        x_el=x_el,
        candidate_x_mm=anchor_x,
        candidate_z_mm=anchor_z,
        velocity_mps=float(velocity_mps or 6200.0),
        model_path=str(model_path),
    )
    compact = _compact_frame_result(result, anchor_meta)
    summary["result"] = compact
    summary["frame_prediction"] = compact.get("prediction") or {}
    summary["anchor"] = compact.get("localization") or {}
    if result.get("ok"):
        summary["count"] = 1
        summary["status"] = "完成"
        label = (compact.get("prediction") or {}).get("label_cn") or "不确定"
        confidence = (compact.get("prediction") or {}).get("confidence")
        summary["summary"] = f"整帧FMC主要响应倾向{label}" + (f"，置信度{confidence:.2f}" if confidence is not None else "")
    else:
        summary["status"] = "失败"
        summary["errors"].extend(result.get("errors") or [])
    summary["warnings"].extend(result.get("warnings") or [])
    return summary


def classify_frame_candidates(
    source_path: str | Path,
    defects: list[dict[str, Any]],
    *,
    velocity_mps: float | None,
    tx_positions_mm: list[float] | None = None,
    max_candidates: int = 8,
) -> dict[str, Any]:
    """Compatibility wrapper. Classification is frame-level regardless of candidate count."""

    del max_candidates
    return classify_frame(
        source_path,
        defects,
        velocity_mps=velocity_mps,
        tx_positions_mm=tx_positions_mm,
    )


def _available_model_path() -> Path | None:
    if CLASSIFIER_MODEL_PATH.exists():
        return CLASSIFIER_MODEL_PATH
    if CLASSIFIER_FALLBACK_MODEL_PATH.exists():
        return CLASSIFIER_FALLBACK_MODEL_PATH
    return None


def _predict_with_velocity_ensemble(
    predict_fmc_case: Any,
    *,
    fmc: np.ndarray,
    t: np.ndarray,
    x_el: np.ndarray,
    candidate_x_mm: float | None,
    candidate_z_mm: float | None,
    velocity_mps: float,
    model_path: str,
) -> dict[str, Any]:
    """对声速轻微不确定做内部集成，避免项目默认声速差异造成硬误判。"""

    velocities: list[float] = []
    for value in (velocity_mps, 6200.0):
        if not math.isfinite(value):
            continue
        if all(abs(value - existing) > 1e-6 for existing in velocities):
            velocities.append(value)
    if not velocities:
        velocities = [6200.0]

    results = [
        predict_fmc_case(
            fmc=fmc,
            t=t,
            x_el=x_el,
            candidate_x_mm=candidate_x_mm,
            candidate_z_mm=candidate_z_mm,
            velocity_mps=velocity,
            model_path=model_path,
            confidence_threshold=0.0,
        )
        for velocity in velocities
    ]
    ok_results = [item for item in results if item.get("ok")]
    if not ok_results:
        return results[0] if results else {"ok": False, "errors": ["A扫分类器未返回结果"]}

    best_base = max(
        ok_results,
        key=lambda item: float((item.get("prediction") or {}).get("confidence") or 0.0),
    )
    prob_keys: list[str] = []
    for item in ok_results:
        for key in ((item.get("prediction") or {}).get("probabilities") or {}).keys():
            if key not in prob_keys:
                prob_keys.append(key)
    averaged = {
        key: float(np.mean([
            float(((item.get("prediction") or {}).get("probabilities") or {}).get(key, 0.0))
            for item in ok_results
        ]))
        for key in prob_keys
    }
    if not averaged:
        return best_base

    best_label = max(averaged, key=averaged.get)
    confidence = float(averaged[best_label])
    if confidence < CLASSIFIER_CONFIDENCE_THRESHOLD:
        label = "uncertain"
        label_cn = CLASSIFIER_CLASS_CN[label]
        recommended_review = True
    else:
        label = best_label
        label_cn = CLASSIFIER_CLASS_CN.get(label, label)
        recommended_review = False

    prediction = dict(best_base.get("prediction") or {})
    prediction.update(
        {
            "label": label,
            "label_cn": label_cn,
            "confidence": round(confidence, 4),
            "probabilities": {key: round(value, 4) for key, value in averaged.items()},
            "recommended_review": recommended_review,
        }
    )
    merged = dict(best_base)
    merged["prediction"] = prediction
    merged["model_name"] = f"{best_base.get('model_name') or 'ascan_classifier'}+velocity_ensemble"
    warnings = list(best_base.get("warnings") or [])
    if len(velocities) > 1:
        warnings.append(f"已按 {', '.join(f'{item:.0f}' for item in velocities)} m/s 做内部声速集成")
    if recommended_review:
        warnings.append(
            f"最高类别概率 {confidence:.2f} 低于阈值 {CLASSIFIER_CONFIDENCE_THRESHOLD:.2f}，输出不确定"
        )
    merged["warnings"] = warnings
    return merged


def load_fmc_from_source(
    source_path: str | Path,
    *,
    tx_positions_mm: list[float] | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """读取当前项目支持的16x16 PAUT文本数据为 (fmc, t, x_el)。"""

    path = Path(source_path)
    if path.is_file():
        return _load_single_fmc_file(path, tx_positions_mm)
    if not path.is_dir():
        raise ValueError(f"数据源不存在: {path}")
    if _is_tx_folder_group(path):
        return _load_tx_folder_group(path, tx_positions_mm)
    fmc_files = _fmc_files(path)
    if len(fmc_files) == 1:
        return _load_single_fmc_file(fmc_files[0], tx_positions_mm)
    if len(fmc_files) > 1:
        raise ValueError("目录中包含多个单文件FMC，请传入具体文件路径")
    numeric_files = _numeric_txt_files(path)
    if len(numeric_files) >= 16:
        return _load_legacy_tx_files(numeric_files[:16], tx_positions_mm)
    raise ValueError("未识别为支持的16x16 FMC文本数据格式")


def _load_single_fmc_file(
    path: Path,
    tx_positions_mm: list[float] | None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    arr = _read_numeric(path)
    if arr.ndim != 2 or arr.shape[1] < 1 + 16 * 16:
        raise ValueError(f"单文件FMC列数不足: {path}")
    t_axis = arr[:, 0].astype(float)
    fmc = arr[:, 1:1 + 16 * 16].reshape(arr.shape[0], 16, 16).transpose(1, 2, 0)
    return fmc.astype(float), t_axis, _default_x_el(tx_positions_mm)


def _load_legacy_tx_files(
    files: list[Path],
    tx_positions_mm: list[float] | None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rows: list[tuple[float, np.ndarray, np.ndarray]] = []
    for file_path in files:
        arr = _read_numeric(file_path)
        if arr.ndim != 2 or arr.shape[1] < 18:
            continue
        tx_raw = float(arr[0, 0])
        tx_mm = tx_raw * 1000.0 if abs(tx_raw) < 1.0 else tx_raw
        rows.append((tx_mm, arr[:, 1].astype(float), arr[:, 2:18].astype(float)))
    if len(rows) < 16:
        raise ValueError("legacy Tx文件不足16个有效发射通道")
    rows.sort(key=lambda item: item[0])
    n_t = max(item[1].shape[0] for item in rows)
    fmc = np.zeros((16, 16, n_t), dtype=float)
    t_axis = rows[0][1]
    for tx_index, (_, t_values, rx_matrix) in enumerate(rows[:16]):
        if t_values.shape[0] > t_axis.shape[0]:
            t_axis = t_values
        fmc[tx_index, :, :rx_matrix.shape[0]] = rx_matrix.T
    x_el = np.asarray(tx_positions_mm, dtype=float) if tx_positions_mm else np.asarray([item[0] for item in rows[:16]])
    return fmc, t_axis.astype(float), x_el


def _load_tx_folder_group(
    path: Path,
    tx_positions_mm: list[float] | None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    tx_dirs = sorted(
        [item for item in path.iterdir() if item.is_dir() and item.name.lower().startswith("tx_")],
        key=lambda item: int(item.name.split("_", 1)[1]) if item.name.split("_", 1)[1].isdigit() else item.name,
    )
    mats = []
    t_axis = None
    for tx_dir in tx_dirs[:16]:
        rx_rows = []
        for rx_index in range(1, 17):
            candidates = [tx_dir / f"{rx_index:03d}.txt", tx_dir / f"{rx_index}.txt"]
            file_path = next((item for item in candidates if item.exists()), None)
            if file_path is None:
                raise ValueError(f"缺少接收通道文件: {tx_dir}/{rx_index:03d}.txt")
            arr = _read_numeric(file_path, skip_comments=False)
            if arr.ndim != 2 or arr.shape[1] < 2:
                raise ValueError(f"接收通道文件格式错误: {file_path}")
            t_axis = arr[:, 0].astype(float)
            rx_rows.append(arr[:, 1].astype(float))
        mats.append(np.stack(rx_rows))
    return np.stack(mats).astype(float), np.asarray(t_axis, dtype=float), _default_x_el(tx_positions_mm)


def _read_numeric(path: Path, *, skip_comments: bool = True) -> np.ndarray:
    rows = []
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            if skip_comments and line.startswith("%"):
                continue
            if not skip_comments and line.startswith("%"):
                continue
            try:
                rows.append([float(value) for value in line.split()])
            except ValueError:
                continue
    if not rows:
        raise ValueError(f"没有读到数值数据: {path}")
    return np.asarray(rows, dtype=float)


def _select_frame_anchor(defects: list[dict[str, Any]]) -> tuple[float | None, float | None, dict[str, Any]]:
    points: list[dict[str, float]] = []
    for defect in defects:
        x_mm = _number(defect.get("X_mm", defect.get("x_mm")))
        z_mm = _number(defect.get("Z_mm", defect.get("z_mm")))
        if x_mm is None or z_mm is None:
            continue
        score = max(_number(defect.get("Score", defect.get("score"))) or 0.0, 0.05)
        support = max(_number(defect.get("Support", defect.get("support"))) or 0.0, 0.0)
        weight = score * (1.0 + math.log1p(support))
        points.append({"x": x_mm, "z": z_mm, "score": score, "support": support, "weight": weight})
    if not points:
        return None, None, {
            "selection_method": "tfm_peak_fallback",
            "candidate_count": 0,
            "cluster_count": 0,
            "note": "没有稳定A扫候选，由分类器使用整帧TFM峰值定位。",
        }

    parent = list(range(len(points)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for left in range(len(points)):
        for right in range(left + 1, len(points)):
            distance = math.hypot(points[left]["x"] - points[right]["x"], points[left]["z"] - points[right]["z"])
            if distance <= 4.0:
                a, b = find(left), find(right)
                if a != b:
                    parent[b] = a
    groups: dict[int, list[dict[str, float]]] = {}
    for index, point in enumerate(points):
        groups.setdefault(find(index), []).append(point)
    dominant = max(
        groups.values(),
        key=lambda group: (sum(item["weight"] for item in group), max(item["score"] for item in group)),
    )
    weights = np.asarray([item["weight"] for item in dominant], dtype=float)
    coordinates = np.asarray([[item["x"], item["z"]] for item in dominant], dtype=float)
    anchor = np.average(coordinates, axis=0, weights=weights)
    return float(anchor[0]), float(anchor[1]), {
        "selection_method": "dominant_ascan_cluster",
        "candidate_count": len(points),
        "cluster_count": len(groups),
        "anchor_support_count": len(dominant),
        "note": "按A扫响应强度和通道支撑选择主能量候选簇。",
    }


def _compact_frame_result(result: dict[str, Any], anchor_meta: dict[str, Any]) -> dict[str, Any]:
    prediction = result.get("prediction") or {}
    localization = result.get("localization") or {}
    features = result.get("features") or {}
    explanation = result.get("explanation") or {}
    return {
        "ok": bool(result.get("ok")),
        "analysis_unit": "single_fmc_frame",
        "model_name": result.get("model_name"),
        "model_version": result.get("model_version"),
        "prediction": {
            "label": prediction.get("label"),
            "label_cn": prediction.get("label_cn"),
            "confidence": prediction.get("confidence"),
            "probabilities": prediction.get("probabilities") or {},
            "recommended_review": bool(prediction.get("recommended_review")),
        },
        "localization": {
            "method": localization.get("method"),
            "x_mm": localization.get("x_mm"),
            "z_mm": localization.get("z_mm"),
            "warning": localization.get("warning") or "",
            **anchor_meta,
        },
        "feature_names": features.get("feature_names") or [],
        "feature_values": features.get("feature_values") or [],
        "top_features": explanation.get("top_features") or [],
        "physics_basis": explanation.get("physics_basis") or [],
        "warnings": result.get("warnings") or [],
        "errors": result.get("errors") or [],
        "boundary": CLASSIFIER_INPUT_BOUNDARY,
    }


def _default_x_el(tx_positions_mm: list[float] | None) -> np.ndarray:
    if tx_positions_mm and len(tx_positions_mm) == 16:
        return np.asarray(tx_positions_mm, dtype=float)
    return np.arange(30.0, 62.0, 2.0, dtype=float)


def _numeric_file_stem(path: Path) -> float | None:
    try:
        value = float(path.stem)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _numeric_txt_files(path: Path) -> list[Path]:
    return sorted(
        [item for item in path.glob("*.txt") if _numeric_file_stem(item) is not None],
        key=lambda item: _numeric_file_stem(item) or 0.0,
    )


def _is_fmc_file(path: Path) -> bool:
    if not path.is_file() or path.suffix.lower() != ".txt" or _numeric_file_stem(path) is None:
        return False
    try:
        row = np.genfromtxt(path, comments="%", max_rows=1)
        return np.asarray(row).size >= 1 + 16 * 16
    except (OSError, ValueError):
        return False


def _fmc_files(path: Path) -> list[Path]:
    return sorted(
        [item for item in path.glob("*.txt") if _is_fmc_file(item)],
        key=lambda item: _numeric_file_stem(item) or 0.0,
    )


def _is_tx_folder_group(path: Path) -> bool:
    if not path.is_dir():
        return False
    tx_dirs = [item for item in path.iterdir() if item.is_dir() and item.name.lower().startswith("tx_")]
    return len(tx_dirs) >= 16 and all(any(item.glob("*.txt")) for item in tx_dirs)


def _number(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None
