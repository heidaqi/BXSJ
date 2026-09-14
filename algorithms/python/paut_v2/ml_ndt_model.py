"""PyTorch implementation of the CNN architecture used by ML-NDT."""

from __future__ import annotations

import torch
from torch import nn


class MLNDTClassifier(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.features = nn.Sequential(
            # The original model uses a 7x1 max pool to envelope the UT signal.
            nn.MaxPool2d(kernel_size=(7, 1), stride=(7, 1)),
            nn.Conv2d(1, 96, kernel_size=3, padding=1),
            nn.ReLU(inplace=True),
            nn.Conv2d(96, 64, kernel_size=3, padding=1),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(kernel_size=(2, 8), stride=(2, 8), ceil_mode=True),
            nn.Conv2d(64, 48, kernel_size=3, padding=1),
            nn.ReLU(inplace=True),
            nn.Conv2d(48, 32, kernel_size=3, padding=1),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(kernel_size=(3, 4), stride=(3, 4), ceil_mode=True),
        )
        self.classifier = nn.Sequential(
            nn.Flatten(),
            nn.Linear(32 * 6 * 8, 14),
            nn.ReLU(inplace=True),
            nn.Linear(14, 1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.classifier(self.features(x)).squeeze(1)


def build_model() -> MLNDTClassifier:
    return MLNDTClassifier()
