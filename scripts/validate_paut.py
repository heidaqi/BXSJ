from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.paut.validation import run_comsol_16x16_validation, run_validation


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="批量验证PAUT仿真A扫数据的Python DAS定位结果")
    parser.add_argument("--data-root", type=Path, required=True, help="包含各仿真数据集目录的根目录")
    parser.add_argument("--mode", choices=["comsol16", "manifest"], default="comsol16", help="默认只验证当前阶段的16发16收COMSOL数据")
    parser.add_argument("--manifest", type=Path, default=PROJECT_ROOT / "validation" / "simulation_manifest.json")
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "data" / "validation")
    args = parser.parse_args()
    if args.mode == "manifest":
        report = run_validation(args.manifest, args.data_root, args.output)
        json_name = "validation_report.json"
        csv_name = "validation_report.csv"
    else:
        report = run_comsol_16x16_validation(args.data_root, args.output)
        json_name = "comsol_16x16_validation.json"
        csv_name = "comsol_16x16_validation.csv"
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"JSON报告：{(args.output / json_name).resolve()}")
    print(f"CSV报告：{(args.output / csv_name).resolve()}")


if __name__ == "__main__":
    main()
