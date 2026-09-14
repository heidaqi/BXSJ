from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from backend.app.services.matlab_service import MatlabService, MatlabRuntimeError


class _EngineError(Exception):
    """模拟 matlab.engine.EngineError（含 OOM 拖死引擎的 Unknown exception）。"""


class _MatlabExecutionError(_EngineError):
    """模拟 matlab.engine.MatlabExecutionError，是 EngineError 的子类。"""


class _Future:
    def __init__(self, error: Exception | None):
        self._error = error

    def cancel(self) -> bool:
        return True

    def result(self, timeout=None):
        if self._error is not None:
            raise self._error
        return None


class _StubMatlabEngineModule:
    """matlab.engine 桩：start_matlab 每次返回一个干净的伪引擎。"""

    EngineError = _EngineError
    MatlabExecutionError = _MatlabExecutionError

    def __init__(self) -> None:
        self.start_matlab_calls = 0

    def start_matlab(self, *args, **kwargs):
        self.start_matlab_calls += 1
        return SimpleNamespace(
            addpath=lambda *args, **kwargs: None,
            quit=lambda: None,
            setenv=lambda *args, **kwargs: None,
            feval=lambda *args, **kwargs: _Future(None),
        )


def _install_matlab_stub(monkeypatch: pytest.MonkeyPatch) -> _StubMatlabEngineModule:
    stub = _StubMatlabEngineModule()
    monkeypatch.setitem(sys.modules, "matlab", SimpleNamespace(engine=stub))
    monkeypatch.setitem(sys.modules, "matlab.engine", stub)
    return stub


def test_engine_error_invalidates_engine_and_raises_retryable(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    """EngineError（引擎被 OOM 拖死等）→ 作废引擎并抛出可重试的 MatlabRuntimeError。"""
    _install_matlab_stub(monkeypatch)

    def raising_feval(*args, **kwargs):
        return _Future(_EngineError("Unknown exception"))

    fake_eng = SimpleNamespace(feval=raising_feval, quit=lambda: None)
    service = MatlabService(project_root=tmp_path, matlab_exe="matlab", use_engine=True)
    service._eng = fake_eng
    service._engine_ready = True

    with pytest.raises(MatlabRuntimeError, match="引擎已重置"):
        service._run_via_engine(
            fake_eng, "Paut.m", tmp_path / "Paut.m",
            {}, tmp_path, tmp_path, {}, 60,
        )

    assert service._eng is None
    assert service._engine_ready is None  # 下次调用将懒启动干净引擎


def test_engine_recovery_lazy_restarts_clean_engine(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    """作废后再次 run_script 会自动懒启动一个干净引擎，而不复用死引擎。"""
    stub = _install_matlab_stub(monkeypatch)

    service = MatlabService(project_root=tmp_path, matlab_exe="matlab", use_engine=True)
    service._eng = None
    service._engine_ready = None

    matlab_root = tmp_path / "ml"
    matlab_root.mkdir()
    (matlab_root / "Paut.m").write_text("", encoding="utf-8")
    service.matlab_root = matlab_root
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    result = service.run_script("Paut.m", input_dir, output_dir, timeout=60, params={})

    assert result["mode"] == "engine"
    assert result["ok"] is True
    assert service._engine_ready is True
    assert service._eng is not None
    assert stub.start_matlab_calls == 1
