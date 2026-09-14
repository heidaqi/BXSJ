from __future__ import annotations

import heapq
import math
from typing import Any

import numpy as np


FAMILY_LABELS = {
    "crack": "裂纹",
    "pore": "气孔",
    "inclusion": "夹渣/夹杂",
    "lack_of_fusion": "未熔合/未焊透",
    "unknown": "未分类异常",
}


def build_frame_analyses(
    frame_features: list[dict[str, Any]],
    *,
    mask_match_distance_mm: float = 5.0,
) -> dict[str, Any]:
    """Build one analysis record per frame and measurable response regions inside it.

    The FMC classifier is a frame-level prior. Masks and A-scan clusters describe where
    the response appears; their count is not treated as a physical defect count.
    """

    analyses: list[dict[str, Any]] = []
    flat_instances: list[dict[str, Any]] = []
    auxiliary: list[dict[str, Any]] = []
    for frame_order, frame in enumerate(frame_features, start=1):
        analysis = _analyze_frame(frame, frame_order, mask_match_distance_mm)
        analyses.append(analysis)
        flat_instances.extend(analysis["defect_instances"])
        auxiliary.extend(analysis["auxiliary_findings"])
    return {
        "frame_analyses": analyses,
        "defect_instances": flat_instances,
        # Compatibility aliases for risk, report and older clients during migration.
        "fusion_candidates": flat_instances,
        "primary_candidates": flat_instances,
        "auxiliary_image_findings": auxiliary,
    }


def _analyze_frame(frame: dict[str, Any], frame_order: int, match_distance_mm: float) -> dict[str, Any]:
    frame_id = str(frame.get("frame_id") or f"frame_{frame_order:04d}")
    ascan_points = [dict(item) for item in frame.get("ascan_candidates") or []]
    image_regions = [dict(item) for item in frame.get("image_candidates") or []]

    assignments: dict[int, list[dict[str, Any]]] = {index: [] for index in range(len(image_regions))}
    unassigned: list[dict[str, Any]] = []
    for point in ascan_points:
        matches = [
            (_point_region_distance(point, region), index)
            for index, region in enumerate(image_regions)
        ]
        matches = [(distance, index) for distance, index in matches if distance is not None]
        if matches:
            distance, index = min(matches, key=lambda item: item[0])
            if distance <= match_distance_mm:
                point["mask_distance_mm"] = round(float(distance), 3)
                assignments[index].append(point)
                continue
        unassigned.append(point)

    frame_family, frame_type, frame_confidence, frame_confidence_value, frame_type_basis = _frame_type(frame.get("frame_classifier") or {})
    provisional = [
        _instance_from_region(
            frame_id, frame, region, assignments[index],
            frame_family, frame_type, frame_confidence, frame_confidence_value, frame_type_basis,
        )
        for index, region in enumerate(image_regions)
    ]
    provisional.extend(
        _instance_from_ascan_cluster(
            frame_id, frame, cluster,
            frame_family, frame_type, frame_confidence, frame_confidence_value, frame_type_basis,
        )
        for cluster in _cluster_unassigned_ascan(unassigned)
    )
    instances = [
        _finalize_instance(frame_id, frame_order, index, instance)
        for index, instance in enumerate(sorted(provisional, key=_instance_sort_key), start=1)
    ]

    if instances:
        summary = f"本帧主要响应倾向{frame_type}，记录{len(instances)}处二维疑似缺陷。"
    else:
        summary = f"本帧主要响应倾向{frame_type}，未形成可量化的二维疑似缺陷；零检出不等于无缺陷。"
    return {
        "frame_id": frame_id,
        "frame_index": frame.get("frame_index"),
        "source_group": frame.get("source_group"),
        "analysis_unit": "single_frame",
        "frame_type": frame_type,
        "frame_family": frame_family,
        "frame_type_confidence": frame_confidence,
        "frame_type_confidence_value": frame_confidence_value,
        "frame_type_probabilities": (frame.get("frame_classifier") or {}).get("probabilities") or {},
        "frame_type_basis": frame_type_basis,
        "region_count": len(instances),
        "physical_defect_count": None,
        "defect_count": len(instances),
        "summary": summary,
        "response_regions": instances,
        "defect_instances": instances,
        "auxiliary_findings": [],
        "review_status": _frame_review_status(instances),
        "measurement_boundary": "仅表示当前DAS图x-z垂直截面的二维位置和尺寸，不推断三维尺寸或帧间连续性。",
    }


def _instance_from_region(
    frame_id: str,
    frame: dict[str, Any],
    region: dict[str, Any],
    points: list[dict[str, Any]],
    family: str,
    frame_type: str,
    type_confidence: str,
    type_confidence_value: float | None,
    type_basis: str,
) -> dict[str, Any]:
    geometry = _region_geometry(region)
    center = _weighted_center(points, region)
    evidence = "整帧A扫分类，区域边界由图像分割量化" if points else "整帧A扫分类，当前区域仅有图像分割支持"
    return {
        "frame_id": frame_id,
        "family": family,
        "frame_type": frame_type,
        "type_confidence": type_confidence,
        "type_confidence_value": type_confidence_value,
        "type_basis_short": type_basis,
        "center_mm": center,
        "geometry": _geometry_for_family(family, geometry),
        "ascan_points": points,
        "image_regions": [region],
        "frame_ascan_candidate_count": len(frame.get("ascan_candidates") or []),
        "frame_image_candidate_count": len(frame.get("image_candidates") or []),
        "brief_evidence": evidence,
        "review": _review_from_points(points, frame),
    }


def _instance_from_ascan_cluster(
    frame_id: str,
    frame: dict[str, Any],
    points: list[dict[str, Any]],
    family: str,
    frame_type: str,
    type_confidence: str,
    type_confidence_value: float | None,
    type_basis: str,
) -> dict[str, Any]:
    center = _weighted_center(points, None)
    coordinates = np.asarray([[item["x_mm"], item["z_mm"]] for item in points], dtype=float)
    major, minor, angle = _point_cloud_geometry(coordinates)
    geometry = {
        "shape": "A扫响应点组" if len(points) > 1 else "单点响应",
        "length_mm": major if len(points) > 1 else None,
        "width_mm": minor if len(points) > 2 else None,
        "orientation_deg": angle if len(points) > 1 else None,
        "diameter_mm": None,
        "major_axis_mm": major if len(points) > 1 else None,
        "minor_axis_mm": minor if len(points) > 2 else None,
        "area_mm2": None,
        "method": "A扫响应点范围",
        "quality": "低",
    }
    return {
        "frame_id": frame_id,
        "family": family,
        "frame_type": frame_type,
        "type_confidence": type_confidence,
        "type_confidence_value": type_confidence_value,
        "type_basis_short": type_basis,
        "center_mm": center,
        "geometry": geometry,
        "ascan_points": points,
        "image_regions": [],
        "frame_ascan_candidate_count": len(frame.get("ascan_candidates") or []),
        "frame_image_candidate_count": len(frame.get("image_candidates") or []),
        "brief_evidence": "仅有A扫/DAS响应，缺少分割边界",
        "review": _review_from_points(points, frame),
    }


def _region_geometry(region: dict[str, Any]) -> dict[str, Any]:
    polygon = _clean_polygon(region.get("mask_polygon_mm"))
    major = _number(region.get("projected_major_mm"))
    minor = _number(region.get("projected_minor_mm"))
    angle = _normalize_angle(_number(region.get("projected_orientation_deg")))
    area = abs(_polygon_area(polygon)) if polygon else None
    perimeter = _polygon_perimeter(polygon) if polygon else None
    if polygon:
        coordinates = np.asarray(polygon, dtype=float)
        pca_major, pca_minor, pca_angle = _point_cloud_geometry(coordinates)
        major = pca_major or major
        minor = pca_minor or minor
        angle = pca_angle if pca_angle is not None else angle
    equivalent_diameter = 2.0 * math.sqrt(area / math.pi) if area and area > 0 else None
    circularity = 4.0 * math.pi * area / (perimeter * perimeter) if area and perimeter and perimeter > 0 else None
    centerline = _skeleton_length(polygon) if polygon else None
    return {
        "polygon_mm": polygon,
        "area_mm2": _round(area),
        "perimeter_mm": _round(perimeter),
        "major_axis_mm": _round(major),
        "minor_axis_mm": _round(minor),
        "orientation_deg": _round(angle, 2),
        "equivalent_diameter_mm": _round(equivalent_diameter),
        "circularity": _round(circularity, 3),
        "centerline_length_mm": _round(centerline),
    }


def _geometry_for_family(family: str, geometry: dict[str, Any]) -> dict[str, Any]:
    major = geometry.get("major_axis_mm")
    minor = geometry.get("minor_axis_mm")
    area = geometry.get("area_mm2")
    if family == "pore":
        ratio = major / minor if major and minor else None
        shape = "紧凑形态" if ratio is not None and ratio <= 1.35 else "拉长或不规则形态"
        return {
            "shape": shape,
            "diameter_mm": geometry.get("equivalent_diameter_mm"),
            "length_mm": major,
            "width_mm": minor,
            "major_axis_mm": major,
            "minor_axis_mm": minor,
            "orientation_deg": geometry.get("orientation_deg") if ratio and ratio > 1.35 else None,
            "area_mm2": area,
            "method": "分割面积等效直径与区域主轴",
            "quality": "中" if area else "低",
        }
    if family in {"crack", "lack_of_fusion"}:
        length = geometry.get("centerline_length_mm") or major
        width = area / length if area and length and length > 0 else minor
        return {
            "shape": "线状/平面型截面",
            "diameter_mm": None,
            "length_mm": _round(length),
            "width_mm": _round(width),
            "major_axis_mm": major,
            "minor_axis_mm": minor,
            "orientation_deg": geometry.get("orientation_deg"),
            "area_mm2": area,
            "method": "分割中心线长度" if geometry.get("centerline_length_mm") else "分割主轴长度",
            "quality": "中" if length else "低",
        }
    return {
        "shape": "不规则区域" if family == "inclusion" else "未定型区域",
        "diameter_mm": None,
        "length_mm": major,
        "width_mm": minor,
        "major_axis_mm": major,
        "minor_axis_mm": minor,
        "orientation_deg": geometry.get("orientation_deg"),
        "area_mm2": area,
        "method": "分割区域主轴",
        "quality": "中" if major else "低",
    }


def _frame_type(classifier: dict[str, Any]) -> tuple[str, str, str, float | None, str]:
    label = classifier.get("label")
    label_cn = classifier.get("label_cn")
    confidence = _number(classifier.get("confidence"))
    family = _family_from_text(label_cn or label)
    if family == "unknown" or str(label or "").lower() == "uncertain":
        return "unknown", "未定型异常", "低", confidence, "整帧FMC分类置信度不足"
    confidence_label = "高" if (confidence or 0.0) >= 0.85 else "中"
    return family, FAMILY_LABELS[family], confidence_label, confidence, f"整帧FMC主要响应倾向{FAMILY_LABELS[family]}"


def _finalize_instance(frame_id: str, frame_order: int, index: int, item: dict[str, Any]) -> dict[str, Any]:
    defect_id = f"{frame_id}_region_{index:02d}"
    points = item["ascan_points"]
    regions = item["image_regions"]
    scores = [_number(point.get("score")) for point in points]
    supports = [_number(point.get("support")) for point in points]
    geometry = item["geometry"]
    type_label = item["frame_type"]
    quality = "强" if points and regions else ("中" if points else "弱")
    center = item["center_mm"]
    return {
        "defect_id": defect_id,
        "region_id": defect_id,
        "candidate_id": defect_id,
        "fusion_id": defect_id,
        "frame_id": frame_id,
        "frame_ids": [frame_id],
        "frame_index": frame_order,
        "analysis_unit": "frame_response_region",
        "type": type_label,
        "family": item["family"],
        "type_scope": "frame_primary_response",
        "physical_instance_confirmed": False,
        "type_confidence": item["type_confidence"],
        "type_confidence_value": item.get("type_confidence_value"),
        "type_basis_short": item["type_basis_short"],
        "center_mm": center,
        "centroid_mm": center,
        "x_mm": center["x"],
        "z_mm": center["z"],
        "geometry": geometry,
        "diameter_mm": geometry.get("diameter_mm"),
        "length_mm": geometry.get("length_mm"),
        "width_mm": geometry.get("width_mm"),
        "orientation_deg": geometry.get("orientation_deg"),
        "section_length_estimate_mm": geometry.get("length_mm"),
        "section_width_estimate_mm": geometry.get("width_mm"),
        "section_orientation_deg": geometry.get("orientation_deg"),
        "ascan_response_span_mm": _ascan_span(points),
        "point_count": len(points),
        "frame_count": 1,
        "frame_relation": "single_frame_only",
        "image_support_count": len(regions),
        "ascan_score_median": _median(scores),
        "ascan_support_median": _median(supports),
        "ascan_classifier_votes": {type_label: 1} if item["family"] != "unknown" else {},
        "ascan_classifier_confidence_median": item.get("type_confidence_value"),
        "image_type_votes": {},
        "type_votes": {type_label: 1},
        "source_counts": {"ascan": len(points), "yolo": len(regions)},
        "primary_evidence": "ascan_das" if points else "yolo_seg",
        "evidence_quality": quality,
        "evidence_status": item["brief_evidence"],
        "brief_evidence": item["brief_evidence"],
        "image_match_diagnostics": {
            "status": "matched" if points and regions else ("image_only" if regions else "no_segmentation"),
            "match_strengths": {"strong": len(regions)} if points and regions else {},
            "note": item["brief_evidence"],
        },
        "frame_context": {
            "frame_ascan_candidate_count": int(item.get("frame_ascan_candidate_count") or 0),
            "frame_image_candidate_count": int(item.get("frame_image_candidate_count") or 0),
            "merged_candidate_count": int(item.get("merged_region_count") or max(len(regions), 1)),
        },
        "classification": f"{type_label}帧内二维疑似缺陷",
        "review": item["review"],
        "measurement_boundary": "位置由A扫/DAS主导；尺寸为当前DAS图x-z截面的二维估计，不代表三维真实尺寸。",
    }


def _cluster_unassigned_ascan(points: list[dict[str, Any]], distance_mm: float = 3.0) -> list[list[dict[str, Any]]]:
    if not points:
        return []
    parent = list(range(len(points)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for left in range(len(points)):
        for right in range(left + 1, len(points)):
            distance = math.hypot(points[left]["x_mm"] - points[right]["x_mm"], points[left]["z_mm"] - points[right]["z_mm"])
            if distance <= distance_mm:
                a, b = find(left), find(right)
                if a != b:
                    parent[b] = a
    grouped: dict[int, list[dict[str, Any]]] = {}
    for index, point in enumerate(points):
        grouped.setdefault(find(index), []).append(point)
    return list(grouped.values())


def _skeleton_length(polygon: list[list[float]]) -> float | None:
    """Estimate the longest centerline path of a physical mask polygon."""
    try:
        import cv2
    except Exception:
        return None
    points = np.asarray(polygon, dtype=float)
    if len(points) < 3:
        return None
    x_min, z_min = points.min(axis=0)
    x_max, z_max = points.max(axis=0)
    span = max(x_max - x_min, z_max - z_min)
    if span <= 0:
        return None
    resolution = max(span / 320.0, 0.04)
    pixels = np.rint((points - [x_min, z_min]) / resolution).astype(np.int32) + 3
    width = int(pixels[:, 0].max()) + 4
    height = int(pixels[:, 1].max()) + 4
    mask = np.zeros((height, width), dtype=np.uint8)
    cv2.fillPoly(mask, [pixels.reshape((-1, 1, 2))], 255)
    element = cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3))
    skeleton = np.zeros_like(mask)
    working = mask.copy()
    for _ in range(max(width, height) + 8):
        opened = cv2.morphologyEx(working, cv2.MORPH_OPEN, element)
        skeleton = cv2.bitwise_or(skeleton, cv2.subtract(working, opened))
        working = cv2.erode(working, element)
        if cv2.countNonZero(working) == 0:
            break
    coordinates = np.argwhere(skeleton > 0)
    if len(coordinates) < 2:
        return None
    graph = {tuple(point): index for index, point in enumerate(coordinates)}

    def farthest(start: int) -> tuple[int, float]:
        distances = [math.inf] * len(coordinates)
        distances[start] = 0.0
        queue = [(0.0, start)]
        far_index, far_distance = start, 0.0
        while queue:
            distance, current = heapq.heappop(queue)
            if distance != distances[current]:
                continue
            if distance > far_distance:
                far_index, far_distance = current, distance
            row, column = coordinates[current]
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    if dr == 0 and dc == 0:
                        continue
                    neighbor = graph.get((int(row + dr), int(column + dc)))
                    if neighbor is None:
                        continue
                    step = math.sqrt(2.0) if dr and dc else 1.0
                    next_distance = distance + step
                    if next_distance < distances[neighbor]:
                        distances[neighbor] = next_distance
                        heapq.heappush(queue, (next_distance, neighbor))
        return far_index, far_distance

    first, _ = farthest(0)
    _, pixel_length = farthest(first)
    return float(pixel_length * resolution)


def _point_region_distance(point: dict[str, Any], region: dict[str, Any]) -> float | None:
    x = _number(point.get("x_mm"))
    z = _number(point.get("z_mm"))
    if x is None or z is None:
        return None
    polygon = _clean_polygon(region.get("mask_polygon_mm"))
    if polygon:
        if _point_in_polygon((x, z), polygon):
            return 0.0
        return min(
            _point_segment_distance((x, z), tuple(polygon[index]), tuple(polygon[(index + 1) % len(polygon)]))
            for index in range(len(polygon))
        )
    center_x = _number(region.get("x_mm"))
    center_z = _number(region.get("z_mm"))
    if center_x is None or center_z is None:
        return None
    return math.hypot(x - center_x, z - center_z)


def _weighted_center(points: list[dict[str, Any]], region: dict[str, Any] | None) -> dict[str, float]:
    if points:
        coordinates = np.asarray([[point["x_mm"], point["z_mm"]] for point in points], dtype=float)
        weights = np.asarray([max(_number(point.get("score")) or 0.2, 0.05) for point in points], dtype=float)
        center = np.average(coordinates, axis=0, weights=weights)
        return {"x": _round(float(center[0])), "z": _round(float(center[1]))}
    polygon = _clean_polygon((region or {}).get("mask_polygon_mm"))
    if polygon:
        center = np.asarray(polygon, dtype=float).mean(axis=0)
        return {"x": _round(float(center[0])), "z": _round(float(center[1]))}
    return {
        "x": _round(_number((region or {}).get("x_mm")) or 0.0),
        "z": _round(_number((region or {}).get("z_mm")) or 0.0),
    }


def _point_cloud_geometry(coordinates: np.ndarray) -> tuple[float | None, float | None, float | None]:
    if len(coordinates) < 2:
        return None, None, None
    centered = coordinates - coordinates.mean(axis=0)
    _, _, vectors = np.linalg.svd(centered, full_matrices=False)
    major_axis = vectors[0]
    minor_axis = vectors[1] if len(vectors) > 1 else np.asarray([-major_axis[1], major_axis[0]])
    major = float(np.ptp(centered @ major_axis))
    minor = float(np.ptp(centered @ minor_axis))
    angle = _normalize_angle(float(math.degrees(math.atan2(major_axis[1], major_axis[0]))))
    return _round(major), _round(minor), _round(angle, 2)


def _family_from_text(value: Any) -> str:
    text = str(value or "").lower()
    if "未熔合" in text or "未焊透" in text or text in {"lof", "lack_of_fusion"}:
        return "lack_of_fusion"
    if "裂纹" in text or "crack" in text:
        return "crack"
    if "气孔" in text or "孔洞" in text or text in {"pore", "porosity"}:
        return "pore"
    if "夹渣" in text or "夹杂" in text or "slag" in text or "inclusion" in text:
        return "inclusion"
    return "unknown"


def _review_from_points(points: list[dict[str, Any]], frame: dict[str, Any]) -> dict[str, Any]:
    reviews = [point.get("review") or {} for point in points]
    if not reviews:
        reviews = [frame.get("review") or {}]
    return _merge_reviews(reviews)


def _merge_reviews(reviews: list[dict[str, Any]]) -> dict[str, Any]:
    statuses = [str(review.get("status") or "待复核") for review in reviews]
    status = "已确认" if "已确认" in statuses else ("误检" if statuses and all(item == "误检" for item in statuses) else "待复核")
    notes = [str(review.get("note")) for review in reviews if review.get("note")]
    return {"status": status, "note": "；".join(dict.fromkeys(notes)), "source": "frame_record"}


def _frame_review_status(instances: list[dict[str, Any]]) -> str:
    statuses = [str((item.get("review") or {}).get("status") or "待复核") for item in instances]
    if statuses and all(status == "已确认" for status in statuses):
        return "已确认"
    if statuses and all(status == "误检" for status in statuses):
        return "误检"
    return "待复核"


def _instance_sort_key(item: dict[str, Any]) -> tuple[float, float]:
    return float(item["center_mm"]["x"]), float(item["center_mm"]["z"])


def _ascan_span(points: list[dict[str, Any]]) -> float | None:
    if len(points) < 2:
        return None
    coordinates = np.asarray([[point["x_mm"], point["z_mm"]] for point in points], dtype=float)
    major, _, _ = _point_cloud_geometry(coordinates)
    return major


def _clean_polygon(value: Any) -> list[list[float]]:
    if not isinstance(value, list):
        return []
    result = []
    for point in value:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            continue
        x, z = _number(point[0]), _number(point[1])
        if x is not None and z is not None:
            result.append([x, z])
    return result if len(result) >= 3 else []


def _polygon_area(polygon: list[list[float]]) -> float:
    return 0.5 * sum(
        polygon[index][0] * polygon[(index + 1) % len(polygon)][1]
        - polygon[(index + 1) % len(polygon)][0] * polygon[index][1]
        for index in range(len(polygon))
    )


def _polygon_perimeter(polygon: list[list[float]]) -> float:
    return sum(
        math.hypot(
            polygon[index][0] - polygon[(index + 1) % len(polygon)][0],
            polygon[index][1] - polygon[(index + 1) % len(polygon)][1],
        )
        for index in range(len(polygon))
    )


def _point_in_polygon(point: tuple[float, float], polygon: list[list[float]]) -> bool:
    x, z = point
    inside = False
    previous = polygon[-1]
    for current in polygon:
        x1, z1 = previous
        x2, z2 = current
        if (z1 > z) != (z2 > z):
            crossing = (x2 - x1) * (z - z1) / ((z2 - z1) or 1e-12) + x1
            if x < crossing:
                inside = not inside
        previous = current
    return inside


def _point_segment_distance(point: tuple[float, float], start: tuple[float, float], end: tuple[float, float]) -> float:
    px, pz = point
    sx, sz = start
    ex, ez = end
    dx, dz = ex - sx, ez - sz
    denominator = dx * dx + dz * dz
    if denominator <= 1e-12:
        return math.hypot(px - sx, pz - sz)
    ratio = max(0.0, min(1.0, ((px - sx) * dx + (pz - sz) * dz) / denominator))
    return math.hypot(px - (sx + ratio * dx), pz - (sz + ratio * dz))


def _normalize_angle(value: float | None) -> float | None:
    if value is None:
        return None
    angle = value % 180.0
    return angle - 180.0 if angle > 90.0 else angle


def _median(values: list[float | None]) -> float | None:
    clean = [value for value in values if value is not None]
    return _round(float(np.median(clean))) if clean else None


def _round(value: float | None, digits: int = 3) -> float | None:
    return round(float(value), digits) if value is not None and math.isfinite(float(value)) else None


def _number(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None
