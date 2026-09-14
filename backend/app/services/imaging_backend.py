"""成像调用边界；不包含也不修改任何成像算法。"""
from __future__ import annotations

import importlib
import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from ..config import settings


class ImagingBackend(ABC):
    name = "unknown"

    @abstractmethod
    def is_available(self) -> bool: ...

    @abstractmethod
    def get_diagnostics(self) -> dict[str, Any]: ...

    @abstractmethod
    def run_imaging(
        self, dataset: Path, output_dir: Path, parameters: dict[str, Any] | None = None
    ) -> dict[str, Any]: ...

    @abstractmethod
    def run_ascan(
        self, dataset: Path, output_dir: Path, parameters: dict[str, Any] | None = None
    ) -> dict[str, Any]: ...

    def run_paut(
        self, dataset: Path, output_dir: Path, parameters: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Compatibility default for runtime components that still expose two stages."""
        imaging = self.run_imaging(dataset, output_dir, parameters)
        ascan = self.run_ascan(dataset, output_dir, parameters)
        return {"ok": True, "imaging": imaging, "ascan": ascan}

    @abstractmethod
    def close(self) -> None: ...


class MatlabEngineBackend(ImagingBackend):
    name = "matlab_engine"

    def __init__(self) -> None:
        from .matlab_service import get_matlab_service

        self.service = get_matlab_service()

    def is_available(self) -> bool:
        return bool(self.get_diagnostics().get("available"))

    def get_diagnostics(self) -> dict[str, Any]:
        env = self.service.check_environment()
        return {
            "name": self.name,
            "available": bool(env.get("ok")),
            "message": "；".join(env.get("messages") or []) or "MATLAB Engine 或 matlab.exe 可用",
            "details": env,
        }

    def run_imaging(
        self, dataset: Path, output_dir: Path, parameters: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return self.service.run_das(dataset, output_dir, params=parameters)

    def run_ascan(
        self, dataset: Path, output_dir: Path, parameters: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return self.service.run_ascan(dataset, output_dir, params=parameters)

    def run_paut(
        self, dataset: Path, output_dir: Path, parameters: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return self.service.run_paut(dataset, output_dir, params=parameters)

    def close(self) -> None:
        self.service.shutdown()


class MatlabRuntimeBackend(ImagingBackend):
    """MATLAB Compiler 组件适配器。

    团队组件需暴露 initialize()，返回对象需提供 run_imaging(input, output, params)。
    """

    name = "matlab_runtime"

    def __init__(self, module_name: str = "") -> None:
        self.module_name = module_name.strip()
        self._instance: Any = None

    def _module(self):
        if not self.module_name:
            return None
        try:
            return importlib.import_module(self.module_name)
        except Exception:  # noqa: BLE001
            return None

    def is_available(self) -> bool:
        return self._module() is not None

    def get_diagnostics(self) -> dict[str, Any]:
        available = self.is_available()
        return {
            "name": self.name,
            "available": available,
            "module": self.module_name,
            "message": "MATLAB Runtime 组件可用" if available else "尚未配置团队编译的 MATLAB Runtime 组件",
        }

    def run_imaging(
        self, dataset: Path, output_dir: Path, parameters: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        module = self._module()
        if module is None:
            raise RuntimeError("MATLAB Runtime 组件不可用")
        if self._instance is None:
            self._instance = module.initialize()
        output_dir.mkdir(parents=True, exist_ok=True)
        result = self._instance.run_imaging(str(dataset), str(output_dir), parameters or {})
        return {"ok": True, "mode": self.name, "result": result}

    def run_ascan(
        self, dataset: Path, output_dir: Path, parameters: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        module = self._module()
        if module is None:
            raise RuntimeError("MATLAB Runtime 组件不可用")
        if self._instance is None:
            self._instance = module.initialize()
        method = getattr(self._instance, "run_ascan", None)
        if not callable(method):
            raise RuntimeError("MATLAB Runtime 组件未提供 run_ascan 接口")
        result = method(str(dataset), str(output_dir), parameters or {})
        return {"ok": True, "mode": self.name, "result": result}

    def close(self) -> None:
        if self._instance is not None:
            terminate = getattr(self._instance, "terminate", None)
            if callable(terminate):
                terminate()
            self._instance = None


_backend: ImagingBackend | None = None


def get_imaging_backend() -> ImagingBackend:
    global _backend
    if _backend is not None:
        return _backend
    runtime = MatlabRuntimeBackend(settings.matlab_runtime_module)
    _backend = runtime if runtime.is_available() else MatlabEngineBackend()
    return _backend


def imaging_capabilities() -> dict[str, Any]:
    runtime = MatlabRuntimeBackend(settings.matlab_runtime_module)
    engine = MatlabEngineBackend()
    selected = runtime if runtime.is_available() else engine
    return {
        "available": selected.is_available(),
        "selected": selected.name if selected.is_available() else None,
        "backends": [runtime.get_diagnostics(), engine.get_diagnostics()],
        "matlab_executable_found": bool(shutil.which(settings.matlab_exe) or Path(settings.matlab_exe).exists()),
    }


def close_imaging_backend() -> None:
    global _backend
    if _backend is not None:
        _backend.close()
        _backend = None
