from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MemorySnapshot:
    total_physical: int
    available_physical: int
    total_commit: int
    available_commit: int

    def as_dict(self) -> dict[str, Any]:
        gib = 1024**3
        return {
            "total_physical_gb": round(self.total_physical / gib, 2),
            "available_physical_gb": round(self.available_physical / gib, 2),
            "total_commit_gb": round(self.total_commit / gib, 2),
            "available_commit_gb": round(self.available_commit / gib, 2),
        }


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def memory_snapshot() -> MemorySnapshot | None:
    if os.name != "nt":
        return None
    status = _MemoryStatusEx()
    status.dwLength = ctypes.sizeof(status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return None
    return MemorySnapshot(
        total_physical=int(status.ullTotalPhys),
        available_physical=int(status.ullAvailPhys),
        total_commit=int(status.ullTotalPageFile),
        available_commit=int(status.ullAvailPageFile),
    )


def require_processing_headroom(
    *,
    minimum_physical_gb: float = 1.0,
    minimum_commit_gb: float = 6.0,
) -> dict[str, Any]:
    """Reject heavy processing before Windows reaches an unstable memory state.

    主要看 commit（虚拟内存）：此前的整机 OOM 是“虚拟内存耗尽”，commit 一旦逼近
    上限才会真正拖垮系统。物理可用量 ullAvailPhys 不含可回收的 standby 缓存，
    日常可能偏低，因此只作为接近枯竭时的最后底线，不作为主要门槛。
    """
    snapshot = memory_snapshot()
    if snapshot is None:
        return {"checked": False}
    gib = 1024**3
    details = {
        "checked": True,
        **snapshot.as_dict(),
        "minimum_physical_gb": minimum_physical_gb,
        "minimum_commit_gb": minimum_commit_gb,
    }
    if (
        snapshot.available_physical < minimum_physical_gb * gib
        or snapshot.available_commit < minimum_commit_gb * gib
    ):
        raise RuntimeError(
            "可用内存不足，已阻止启动以避免系统卡死。"
            f"当前可用物理内存 {details['available_physical_gb']} GB，"
            f"可用虚拟内存 {details['available_commit_gb']} GB；"
            "请先关闭其他占用内存较多的程序，释放内存后重试。"
        )
    return details
