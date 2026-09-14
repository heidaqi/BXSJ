from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path
from typing import Any

import requests
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ..config import settings


_YOLO_CONFIG_DIR = settings.app_data_dir / "ultralytics"
_YOLO_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("YOLO_CONFIG_DIR", str(_YOLO_CONFIG_DIR))


Detection = dict[str, Any]


class DetectionRuntimeError(RuntimeError):
    pass


def get_local_runtime_status() -> dict[str, Any]:
    return {
        "ultralytics": _module_available("ultralytics"),
        "torch": _module_available("torch"),
        "cv2": _module_available("cv2"),
        "numpy": _module_available("numpy"),
    }


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _load_yolo_model():
    if settings.yolo_backend != "local" or not settings.yolo_model_path.exists():
        return None
    try:
        from ultralytics import YOLO
    except Exception as exc:
        raise DetectionRuntimeError("未安装 ultralytics/torch，无法启用本地 YOLO 推理。请先运行 scripts\\setup_backend.ps1。") from exc
    try:
        return YOLO(str(settings.yolo_model_path))
    except Exception as exc:
        raise DetectionRuntimeError(f"YOLO 模型加载失败：{exc}") from exc


_MODEL = None


def detect_image(image_path: Path, confidence: float) -> list[Detection]:
    global _MODEL
    detections: list[Detection] = []
    if settings.yolo_backend == "api" and settings.yolo_api_url:
        detections = _detect_with_api(image_path, confidence)
        if detections:
            return _filter_source_near_top(image_path, detections)

    if settings.yolo_backend == "local":
        if not settings.yolo_model_path.exists():
            if settings.demo_detection:
                return _filter_source_near_top(image_path, _demo_detections(image_path, confidence))
            raise DetectionRuntimeError(f"YOLO 模型文件不存在：{settings.yolo_model_path}")
        if _MODEL is None:
            _MODEL = _load_yolo_model()
        if _MODEL is not None:
            return _filter_source_near_top(image_path, _detect_with_local_model(_MODEL, image_path, confidence))

    if settings.demo_detection:
        return _filter_source_near_top(image_path, _demo_detections(image_path, confidence))
    return []


def _filter_source_near_top(image_path: Path, detections: list[Detection]) -> list[Detection]:
    ratio = settings.yolo_ignore_top_ratio
    if ratio <= 0 or not detections:
        return detections
    try:
        with Image.open(image_path) as image:
            _, height = image.size
    except OSError:
        return detections
    boundary_y = height * ratio
    kept: list[Detection] = []
    removed = 0
    for detection in detections:
        center = detection.get("mask_centroid_px") or {}
        try:
            center_y = float(center.get("y")) if isinstance(center, dict) and center.get("y") is not None else (
                float(detection["y_min"]) + float(detection["y_max"])
            ) / 2
        except (KeyError, TypeError, ValueError):
            kept.append(detection)
            continue
        if center_y <= boundary_y:
            removed += 1
            continue
        detection["top_source_filter"] = {
            "enabled": True,
            "ignore_top_ratio": ratio,
            "boundary_y_px": round(boundary_y, 2),
        }
        kept.append(detection)
    if removed:
        for detection in kept:
            detection["filtered_top_source_count"] = removed
    return kept


def _detect_with_local_model(model: Any, image_path: Path, confidence: float) -> list[Detection]:
    device = None if settings.yolo_device == "auto" else settings.yolo_device
    try:
        result = model.predict(source=str(image_path), conf=confidence, device=device, verbose=False)[0]
    except Exception as exc:
        raise DetectionRuntimeError(f"YOLO 推理失败：{exc}") from exc
    names = result.names or {}
    detections: list[Detection] = []
    boxes = result.boxes
    if boxes is None:
        return detections
    mask_polygons = result.masks.xy if getattr(result, "masks", None) is not None else []
    for index, box in enumerate(boxes):
        xyxy = box.xyxy[0].tolist()
        class_id = int(box.cls[0].item())
        x_min, y_min, x_max, y_max = [float(value) for value in xyxy]
        detection: Detection = {
                "class_name": str(names.get(class_id, class_id)),
                "confidence": float(box.conf[0].item()),
                "x_min": max(0.0, x_min),
                "y_min": max(0.0, y_min),
                "x_max": max(0.0, x_max),
                "y_max": max(0.0, y_max),
                "source": "model",
            }
        if index < len(mask_polygons):
            polygon = np.asarray(mask_polygons[index], dtype=float)
            if polygon.ndim == 2 and polygon.shape[0] >= 3:
                step = max(1, int(np.ceil(polygon.shape[0] / 120)))
                sampled = polygon[::step]
                centered = polygon - polygon.mean(axis=0)
                _, _, vectors = np.linalg.svd(centered, full_matrices=False)
                major_axis = vectors[0]
                minor_axis = vectors[1]
                major_projection = centered @ major_axis
                minor_projection = centered @ minor_axis
                detection.update({
                    "mask_polygon": [[round(float(x), 2), round(float(y), 2)] for x, y in sampled],
                    "mask_centroid_px": {
                        "x": round(float(polygon[:, 0].mean()), 2),
                        "y": round(float(polygon[:, 1].mean()), 2),
                    },
                    "mask_major_span_px": round(float(np.ptp(major_projection)), 2),
                    "mask_minor_span_px": round(float(np.ptp(minor_projection)), 2),
                    "orientation_deg_image": round(float(np.degrees(np.arctan2(major_axis[1], major_axis[0]))), 2),
                    "geometry_source": "segmentation_mask",
                })
        detections.append(detection)
    return detections


def _detect_with_api(image_path: Path, confidence: float) -> list[Detection]:
    try:
        with image_path.open("rb") as stream:
            response = requests.post(
                settings.yolo_api_url,
                files={"file": (image_path.name, stream, "application/octet-stream")},
                data={"confidence": str(confidence)},
                timeout=60,
            )
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return []
    return _normalize_api_detections(payload)


def _normalize_api_detections(payload: Any) -> list[Detection]:
    if isinstance(payload, dict):
        items = payload.get("detections") or payload.get("results") or payload.get("boxes") or []
    elif isinstance(payload, list):
        items = payload
    else:
        items = []

    detections: list[Detection] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        bbox = _extract_bbox(item)
        if bbox is None:
            continue
        class_name = item.get("class_name") or item.get("label") or item.get("name") or item.get("class") or "缺陷"
        confidence = item.get("confidence", item.get("score", item.get("conf", 1.0)))
        try:
            detections.append(
                {
                    "class_name": str(class_name),
                    "confidence": float(confidence),
                    "x_min": float(bbox[0]),
                    "y_min": float(bbox[1]),
                    "x_max": float(bbox[2]),
                    "y_max": float(bbox[3]),
                    "source": "model",
                }
            )
        except (TypeError, ValueError):
            continue
    return detections


def _extract_bbox(item: dict[str, Any]) -> list[float] | None:
    for key in ["xyxy", "bbox", "box"]:
        value = item.get(key)
        if isinstance(value, (list, tuple)) and len(value) >= 4:
            return [float(value[0]), float(value[1]), float(value[2]), float(value[3])]
        if isinstance(value, dict):
            nested = _extract_bbox(value)
            if nested:
                return nested
    keys = ["x_min", "y_min", "x_max", "y_max"]
    if all(key in item for key in keys):
        return [float(item[key]) for key in keys]
    keys = ["xmin", "ymin", "xmax", "ymax"]
    if all(key in item for key in keys):
        return [float(item[key]) for key in keys]
    return None


def _demo_detections(image_path: Path, confidence: float) -> list[Detection]:
    with Image.open(image_path) as image:
        width, height = image.size
    digest = hashlib.sha256(image_path.name.encode("utf-8")).digest()
    score = max(confidence, 0.58 + digest[0] / 255 * 0.32)
    box_w = max(32, width * (0.16 + digest[1] / 255 * 0.12))
    box_h = max(24, height * (0.12 + digest[2] / 255 * 0.10))
    cx = width * (0.35 + digest[3] / 255 * 0.30)
    cy = height * (0.35 + digest[4] / 255 * 0.30)
    return [
        {
            "class_name": "疑似缺陷",
            "confidence": round(min(score, 0.96), 4),
            "x_min": max(0, cx - box_w / 2),
            "y_min": max(0, cy - box_h / 2),
            "x_max": min(width, cx + box_w / 2),
            "y_max": min(height, cy + box_h / 2),
            "source": "demo",
        }
    ]


def draw_annotated_image(image_path: Path, detections: list[Detection], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(image_path).convert("RGB") as image:
        draw = ImageDraw.Draw(image)
        font = ImageFont.load_default()
        for item in detections:
            x_min, y_min, x_max, y_max = [float(item[k]) for k in ["x_min", "y_min", "x_max", "y_max"]]
            label = f"{item['class_name']} {float(item['confidence']):.2f}"
            draw.rectangle((x_min, y_min, x_max, y_max), outline=(220, 38, 38), width=3)
            polygon = item.get("mask_polygon") or []
            if len(polygon) >= 3:
                draw.line([tuple(point) for point in polygon] + [tuple(polygon[0])], fill=(255, 196, 0), width=3)
            text_box = draw.textbbox((x_min, max(0, y_min - 16)), label, font=font)
            draw.rectangle(text_box, fill=(220, 38, 38))
            draw.text((x_min, max(0, y_min - 16)), label, fill=(255, 255, 255), font=font)
        image.save(output_path, quality=95)
