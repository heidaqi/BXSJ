from __future__ import annotations

import argparse
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


NUMBER = r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?"
CRACK_RE = re.compile(
    rf"\(({NUMBER}),\s*({NUMBER})\)-\(({NUMBER}),\s*({NUMBER})\)"
)
PORE_RE = re.compile(rf"d({NUMBER})mm\(({NUMBER}),\s*({NUMBER})\)", re.IGNORECASE)
LOF_RE = re.compile(rf"^(bevel|root|interlayer)_({NUMBER})mm$", re.IGNORECASE)
INCLUSION_RE = re.compile(
    rf"FMC_(S\d+)_16x16_L=({NUMBER})mm_T=({NUMBER})mm_x=({NUMBER})mm_"
    rf"depth=({NUMBER})mm_angle=({NUMBER})deg",
    re.IGNORECASE,
)


def parse_case_name(name: str) -> dict[str, Any] | None:
    segments = []
    lengths = []
    orientations = []
    for match in CRACK_RE.finditer(name):
        x1, z1, x2, z2 = (float(value) * 1000.0 for value in match.groups())
        segments.append([[round(x1, 6), round(z1, 6)], [round(x2, 6), round(z2, 6)]])
        lengths.append(round(math.hypot(x2 - x1, z2 - z1), 6))
        orientations.append(round(math.degrees(math.atan2(z2 - z1, x2 - x1)), 6))
    centers = []
    diameters = []
    for match in PORE_RE.finditer(name):
        diameter, x, z = match.groups()
        diameters.append(round(float(diameter), 6))
        centers.append([round(float(x) * 1000.0, 6), round(float(z) * 1000.0, 6)])
    if not segments and not centers:
        return None
    family = "mixed" if segments and centers else "crack" if segments else "pore"
    return {
        "defect_family": family,
        "truth_geometry": {
            "segments_mm": segments,
            "lengths_mm": lengths,
            "orientations_deg": orientations,
            "centers_mm": centers,
            "diameters_mm": diameters,
        },
    }


def inspect_txt_group(case_dir: Path) -> dict[str, Any]:
    tx_dirs = sorted(
        (item for item in case_dir.glob("tx_*") if item.is_dir()),
        key=lambda item: item.name,
    )
    files = sorted(case_dir.glob("*.txt"), key=lambda item: item.name)
    if tx_dirs:
        channel_files = [file for tx_dir in tx_dirs for file in tx_dir.glob("*.txt")]
        files = sorted(channel_files, key=lambda item: str(item))
    metadata: dict[str, Any] = {
        "tx_file_count": len(tx_dirs) if tx_dirs else len(files),
        "channel_file_count": len(files),
    }
    if not files:
        return metadata
    header: list[str] = []
    rows: list[list[float]] = []
    try:
        with files[0].open("r", encoding="utf-8-sig", errors="replace") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                if stripped.startswith("%"):
                    if len(header) < 20:
                        header.append(stripped.lstrip("% "))
                    continue
                values = [float(value) for value in re.split(r"[\s,;]+", stripped) if value]
                if values:
                    rows.append(values)
    except (OSError, ValueError):
        return metadata
    metadata["sample_count"] = len(rows)
    if rows:
        metadata["receiver_count"] = (
            max((len(files) // len(tx_dirs)), 0) if tx_dirs else max(len(rows[0]) - 2, 0)
        )
    time_column = 0 if tx_dirs else 1
    if len(rows) >= 2 and len(rows[0]) > time_column and len(rows[1]) > time_column:
        metadata["sample_interval_s"] = rows[1][time_column] - rows[0][time_column]
        if metadata["sample_interval_s"] > 0:
            metadata["sample_rate_hz"] = 1.0 / metadata["sample_interval_s"]
    metadata["format"] = (
        "COMSOL分通道文本：时间、单通道幅值" if tx_dirs
        else "COMSOL文本导出：位置、时间、接收通道幅值"
    )
    metadata["header_excerpt"] = [line[:300] for line in header[:5]]
    return metadata


def parse_lof_case(name: str) -> dict[str, Any] | None:
    match = LOF_RE.match(name)
    if not match:
        return None
    subtype, length = match.groups()
    subtype = subtype.lower()
    definitions = {
        "bevel": ("坡口未熔合", [50.0, 20.0], 5.0),
        "root": ("根部未熔合", [50.0, 32.0], 0.0),
        "interlayer": ("层间未熔合", [50.0, 15.0], 0.0),
    }
    label, center, orientation = definitions[subtype]
    return {
        "defect_family": "lack_of_fusion",
        "defect_subtype": subtype,
        "truth_geometry": {
            "label": label,
            "centers_mm": [center],
            "lengths_mm": [float(length)],
            "widths_mm": [0.8],
            "orientations_deg": [orientation],
            "model_geometry": "厚0.8 mm的长圆形空腔，圆角端部，声硬边界",
        },
    }


def inspect_full_fmc_file(path: Path) -> tuple[dict[str, Any], dict[str, Any] | None]:
    header: list[str] = []
    rows: list[list[float]] = []
    with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("%"):
                if len(header) < 8:
                    header.append(stripped.lstrip("% "))
                continue
            values = [float(value) for value in re.split(r"[\s,;]+", stripped) if value]
            if values:
                rows.append(values)
    table = next((line for line in header if line.startswith("Table:")), "")
    match = INCLUSION_RE.search(table)
    parsed = None
    if match:
        case_label, length, thickness, x, depth, angle = match.groups()
        parsed = {
            "defect_family": "inclusion",
            "defect_subtype": "slag_inclusion",
            "truth_geometry": {
                "label": "夹渣仿真体",
                "case_label": case_label.upper(),
                "centers_mm": [[float(x), float(depth)]],
                "lengths_mm": [float(length)],
                "widths_mm": [float(thickness)],
                "orientations_deg": [float(angle)],
            },
        }
    channel_count = max(len(rows[0]) - 1, 0) if rows else 0
    side = int(round(math.sqrt(channel_count))) if channel_count else 0
    dt = rows[1][0] - rows[0][0] if len(rows) >= 2 else None
    acquisition = {
        "tx_file_count": side,
        "receiver_count": side,
        "channel_count": channel_count,
        "sample_count": len(rows),
        "sample_interval_s": dt,
        "sample_rate_hz": 1.0 / dt if dt and dt > 0 else None,
        "fmc_shape": [side, side, len(rows)],
        "format": "COMSOL单文件完整FMC：时间、256个Tx-Rx通道幅值",
        "source_kind": "COMSOL仿真A扫/FMC文本",
        "header_excerpt": [line[:300] for line in header[:4]],
    }
    return acquisition, parsed


def load_validation(path: Path | None) -> dict[str, dict[str, Any]]:
    if not path or not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    return {
        str(item.get("dataset")): item
        for item in payload.get("results", [])
        if isinstance(item, dict) and item.get("dataset")
    }


def compact_validation(item: dict[str, Any] | None) -> dict[str, Any]:
    if not item:
        return {"status": "not_run", "boundary": "尚未进入当前DAS真值验证清单。"}
    return {
        "status": item.get("status"),
        "shape": item.get("shape"),
        "sample_rate_hz": item.get("sample_rate_hz"),
        "target_metrics": item.get("target_metrics"),
        "target_neighborhood_response": item.get("target_neighborhood_response"),
        "matlab_comparison": item.get("matlab_comparison"),
        "warnings": item.get("warnings") or [],
        "boundary": "验证结果只说明当前算法与COMSOL文件名真值的接近程度，不代表真实工件定性或验收。",
    }


def select_typical_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """选取用于Agent类比解释的少量原型；全量数据仍由验证流程管理。"""

    selected: list[dict[str, Any]] = []
    by_family = {
        family: [case for case in cases if case.get("defect_family") == family]
        for family in ("crack", "pore", "lack_of_fusion", "inclusion")
    }

    cracks = by_family["crack"]
    single = [case for case in cracks if len((case.get("truth_geometry") or {}).get("segments_mm") or []) == 1]
    multiple = [case for case in cracks if len((case.get("truth_geometry") or {}).get("segments_mm") or []) > 1]
    single.sort(key=_case_primary_size)
    multiple.sort(key=_case_primary_size)
    selected.extend(_unique_cases([
        single[0] if single else None,
        single[-1] if single else None,
        multiple[len(multiple) // 2] if multiple else None,
    ]))

    pores = sorted(by_family["pore"], key=_case_average_diameter)
    selected.extend(_spread_three(pores))

    lof = by_family["lack_of_fusion"]
    for subtype in ("bevel", "root", "interlayer"):
        subtype_cases = sorted(
            (case for case in lof if case.get("defect_subtype") == subtype),
            key=_case_primary_size,
        )
        if subtype_cases:
            selected.append(min(subtype_cases, key=lambda case: abs(_case_primary_size(case) - 10.0)))
    bevel = [case for case in lof if case.get("defect_subtype") == "bevel"]
    if bevel:
        selected.append(max(bevel, key=_case_primary_size))

    inclusions = by_family["inclusion"]
    depths = sorted({_case_depth(case) for case in inclusions})
    for depth_index, depth in enumerate(depths):
        depth_cases = sorted(
            (case for case in inclusions if _case_depth(case) == depth),
            key=_case_primary_size,
        )
        if not depth_cases:
            continue
        position = 0 if depth_index == 0 else len(depth_cases) - 1 if depth_index == len(depths) - 1 else len(depth_cases) // 2
        selected.append(depth_cases[position])

    return _unique_cases(selected)


def _case_primary_size(case: dict[str, Any]) -> float:
    geometry = case.get("truth_geometry") or {}
    values = geometry.get("lengths_mm") or geometry.get("diameters_mm") or [0]
    return max((float(value) for value in values), default=0.0)


def _case_average_diameter(case: dict[str, Any]) -> float:
    values = [float(value) for value in (case.get("truth_geometry") or {}).get("diameters_mm") or []]
    return sum(values) / len(values) if values else 0.0


def _case_depth(case: dict[str, Any]) -> float:
    centers = (case.get("truth_geometry") or {}).get("centers_mm") or []
    return float(centers[0][1]) if centers and len(centers[0]) >= 2 else 0.0


def _spread_three(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(cases) <= 3:
        return cases
    return _unique_cases([cases[0], cases[len(cases) // 2], cases[-1]])


def _unique_cases(cases: list[dict[str, Any] | None]) -> list[dict[str, Any]]:
    output = []
    seen = set()
    for case in cases:
        if not case or case.get("case_id") in seen:
            continue
        seen.add(case.get("case_id"))
        output.append(case)
    return output


def build_library(data_root: Path, validation_path: Path | None = None) -> dict[str, Any]:
    validations = load_validation(validation_path)
    cases: list[dict[str, Any]] = []

    crack_root = data_root / "裂纹和气孔" if (data_root / "裂纹和气孔").is_dir() else data_root
    crack_dirs = sorted((item for item in crack_root.iterdir() if item.is_dir()), key=lambda item: item.name)
    for case_dir in crack_dirs:
        parsed = parse_case_name(case_dir.name)
        if not parsed:
            continue
        acquisition = inspect_txt_group(case_dir)
        acquisition.update({
            "fmc_shape": [acquisition.get("tx_file_count"), acquisition.get("receiver_count"), acquisition.get("sample_count")],
            "source_kind": "COMSOL仿真A扫/FMC文本",
        })
        cases.append({
            "case_id": "",
            "dataset_name": case_dir.name,
            **parsed,
            "acquisition": acquisition,
            "validation": compact_validation(validations.get(case_dir.name)),
            "evidence_level": "仿真真值案例",
            "use_boundary": "仅用于算法验证和相似性解释，不作为真实工件的缺陷类型、风险或验收阈值。",
        })

    lof_root = data_root / "未熔合仿真" / "dataset"
    if lof_root.is_dir():
        for case_dir in sorted((item for item in lof_root.iterdir() if item.is_dir()), key=lambda item: item.name):
            parsed = parse_lof_case(case_dir.name)
            if not parsed:
                continue
            acquisition = inspect_txt_group(case_dir / "data")
            acquisition.update({
                "fmc_shape": [acquisition.get("tx_file_count"), acquisition.get("receiver_count"), acquisition.get("sample_count")],
                "source_kind": "COMSOL仿真A扫/FMC文本",
                "velocity_mps": 6200.0,
                "plate_thickness_mm": 40.0,
                "excitation": "3 MHz，3周期Hanning正弦脉冲",
            })
            cases.append({
                "case_id": "",
                "dataset_name": f"未熔合仿真/{case_dir.name}",
                **parsed,
                "acquisition": acquisition,
                "validation": {
                    "status": "dataset_report_available",
                    "boundary": "团队数据集报告给出DAS定位、尺寸和SNR验证；仍不代表真实工件验收结论。",
                },
                "evidence_level": "仿真真值及数据集验证案例",
                "use_boundary": "未熔合类别、位置和尺寸来自仿真说明，只用于算法验证与相似性解释。",
            })

    inclusion_root = data_root / "夹渣" / "DAS_result" / "comsol数据"
    if inclusion_root.is_dir():
        for path in sorted(
            inclusion_root.glob("*.txt"),
            key=lambda item: (0, int(item.stem)) if item.stem.isdigit() else (1, item.stem),
        ):
            try:
                acquisition, parsed = inspect_full_fmc_file(path)
            except (OSError, ValueError):
                continue
            if not parsed:
                continue
            cases.append({
                "case_id": "",
                "dataset_name": f"夹渣/{path.stem}",
                **parsed,
                "acquisition": acquisition,
                "validation": {"status": "not_run", "boundary": "尚未进入当前统一DAS真值验证。"},
                "evidence_level": "仿真文件头真值案例",
                "use_boundary": "夹渣几何来自COMSOL表头，只用于算法验证与相似性解释。",
            })

    family_prefix = {"crack": "crack", "pore": "pore", "lack_of_fusion": "lof", "inclusion": "slag", "mixed": "mixed"}
    family_counts: dict[str, int] = {}
    for case in cases:
        family = str(case.get("defect_family") or "case")
        family_counts[family] = family_counts.get(family, 0) + 1
        case["case_id"] = f"comsol_{family_prefix.get(family, 'case')}_{family_counts[family]:03d}"
    discovered_counts = {
        family: sum(1 for case in cases if case.get("defect_family") == family)
        for family in ("crack", "pore", "lack_of_fusion", "inclusion")
    }
    typical_cases = select_typical_cases(cases)
    selected_counts = {
        family: sum(1 for case in typical_cases if case.get("defect_family") == family)
        for family in discovered_counts
    }
    return {
        "schema_version": "1.1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "COMSOL仿真16发16收A扫/FMC典型缺陷真值案例",
        "selection": {
            "purpose": "只向Agent提供少量典型原型用于相似性解释，全量数据由独立验证流程管理。",
            "discovered_case_counts": discovered_counts,
            "selected_case_counts": selected_counts,
            "policy": [
                "裂纹：短单裂纹、长单裂纹和多裂纹各一例。",
                "气孔：按平均孔径选取小、中、大孔群各一例。",
                "未熔合：坡口、根部、层间各一例，并增加较长坡口未熔合作尺寸变化参考。",
                "夹渣：按深度选取浅层小尺寸、中层中等尺寸和深层大尺寸各一例。",
            ],
        },
        "sources": [
            "团队提供的COMSOL仿真文本数据",
            "目录名编码的裂纹线段端点或气孔中心与直径",
            "未熔合数据集README与case命名中的类型、中心、长度和角度",
            "夹渣COMSOL文件头中的长度、厚度、位置、深度和角度",
            "项目已有Python-DAS真值验证结果（如存在）",
        ],
        "limitations": [
            "文件名真值只适用于仿真验证，不适用于未知真实设备数据。",
            "A扫单帧不能独立确定缺陷真实类型、长度、宽度或空间走向。",
            "案例相似度不是验收等级，也不能替代校准、标准和工程完整性评定。",
        ],
        "cases": typical_cases,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="从COMSOL仿真目录构建结构化PAUT案例库")
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--validation-json", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    payload = build_library(args.data_root, args.validation_json)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已生成 {len(payload['cases'])} 个仿真案例：{args.output}")


if __name__ == "__main__":
    main()
