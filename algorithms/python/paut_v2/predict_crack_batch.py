"""Batch inference for MATLAB PAUT candidate patches."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import torch
from scipy.io import loadmat
from torch.nn import functional as F

try:
    from .ml_ndt_model import build_model
except ImportError:  # Direct script execution from MATLAB in development.
    from ml_ndt_model import build_model


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--patches", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def load_patches(path: Path) -> np.ndarray:
    data = loadmat(path)
    key = "patches_db" if "patches_db" in data else "patch_db" if "patch_db" in data else None
    if key is None:
        raise KeyError(f"MAT文件缺少patches_db或patch_db: {path}")
    patches = np.asarray(data[key], dtype=np.float32)
    if patches.ndim == 2:
        patches = patches[:, :, None]
    if patches.ndim != 3:
        raise ValueError(f"patches_db应为H×W×N，实际形状: {patches.shape}")
    patches = np.moveaxis(patches, 2, 0)
    tensors = []
    for patch in patches:
        patch = np.clip(patch, -35.0, 0.0)
        tensor = torch.from_numpy(patch.copy())[None, None]
        if tuple(patch.shape) != (256, 256):
            tensor = F.interpolate(tensor, size=(256, 256), mode="bilinear", align_corners=False)
        tensor = tensor[0, 0]
        tensor = (tensor - tensor.mean()) / (tensor.std(correction=0) + 1.0e-6)
        tensors.append(tensor.unsqueeze(0))
    return torch.stack(tensors, dim=0).numpy()


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    if not args.model.is_file():
        raise FileNotFoundError(f"裂纹模型不存在: {args.model}")
    patches = load_patches(args.patches)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    checkpoint = torch.load(args.model, map_location=device, weights_only=False)
    model = build_model().to(device)
    model.load_state_dict(checkpoint["model_state"])
    model.eval()
    with torch.inference_mode():
        logits = model(torch.from_numpy(patches).to(device))
        probabilities = torch.sigmoid(logits).cpu().numpy()
    threshold = float(checkpoint.get("decision_threshold", 0.5))
    payload = {
        "model": str(args.model),
        "device": str(device),
        "threshold": threshold,
        "probabilities": [float(value) for value in probabilities],
        "predictions": [int(value >= threshold) for value in probabilities],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
