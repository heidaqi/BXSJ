from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..config import settings


REVIEW_THRESHOLD = 0.13


def _number(value: Any) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else math.nan
    except (TypeError, ValueError):
        return math.nan


@lru_cache(maxsize=1)
def _load_artifact() -> dict[str, Any] | None:
    model_path = Path(settings.resource_dir) / "models" / "ascan_candidate_random_forest.joblib"
    if not model_path.exists():
        print(f"[AscanML] 模型不存在，保留物理候选：{model_path}")
        return None
    try:
        import joblib

        return joblib.load(model_path)
    except Exception as exc:
        print(f"[AscanML] 模型加载失败，保留物理候选：{exc}")
        return None


def enrich_ascan_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """为Ascan CSV候选追加概率和分级；失败时原样返回。"""
    artifact = _load_artifact()
    if artifact is None or not rows:
        return rows
    try:
        import numpy as np

        features = list(artifact["features"])
        matrix = np.asarray([
            [
                1.0 if feature == "CandidateTierSecondary" and row.get("CandidateTier") == "secondary"
                else _number(row.get(feature))
                for feature in features
            ]
            for row in rows
        ], dtype=float)
        probabilities = artifact["model"].predict_proba(matrix)[:, 1]
        high_threshold = float(artifact["threshold"])
        for row, probability in zip(rows, probabilities):
            value = float(probability)
            row["MLProbability"] = round(value, 8)
            row["MLHighThreshold"] = high_threshold
            row["MLReviewThreshold"] = REVIEW_THRESHOLD
            row["MLHighConfidence"] = int(value >= high_threshold)
            row["MLAccepted"] = int(value >= REVIEW_THRESHOLD)
            row["MLLevel"] = (
                "高可信" if value >= high_threshold
                else "待复核" if value >= REVIEW_THRESHOLD
                else "低可信"
            )
        return rows
    except Exception as exc:
        print(f"[AscanML] 候选复核失败，保留物理候选：{exc}")
        return rows


def accepted_ascan_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    enriched = enrich_ascan_candidates(rows)
    # 没有MLAccepted表示模型未加载或推理失败，此时必须保留全部物理候选。
    if not enriched or "MLAccepted" not in enriched[0]:
        return enriched
    return [row for row in enriched if int(row.get("MLAccepted", 0)) == 1]
