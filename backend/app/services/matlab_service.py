from __future__ import annotations

import shutil
import subprocess
import os
import threading
import json
import sys
import base64
from pathlib import Path
from typing import Any

from ..config import settings


class MatlabRuntimeError(RuntimeError):
    """MATLAB运行错误"""
    pass


class MatlabService:

    def __init__(
        self,
        project_root: Path | None = None,
        matlab_exe: str | None = None,
        use_engine: bool | None = None,
    ):
        if project_root is None:
            project_root = settings.resource_dir

        self.project_root = project_root.resolve()

        # MATLAB 脚本是只读资源，打包后位于资源目录；不再依赖 project_root。
        self.matlab_root = settings.resource_dir / "algorithms" / "matlab"
        self.matlab_exe = matlab_exe or settings.matlab_exe

        # 常驻 MATLAB Engine（懒加载：首次调用启动一次，之后复用，消除每组冷启动）
        self.use_engine = settings.matlab_use_engine if use_engine is None else use_engine
        self._eng = None                    # matlab.engine 实例
        self._engine_ready: bool | None = None  # None=未尝试, True/False=结果
        self._engine_error = ""
        self._engine_lock = threading.Lock()

    @staticmethod
    def _matlab_path(path: Path) -> str:
        return str(path.resolve()).replace("\\", "/").replace("'", "''")

    def _build_matlab_env(
        self,
        input_dir: Path,
        output_dir: Path,
        params: dict[str, Any] | None,
    ) -> dict[str, str]:
        """构建要注入 MATLAB 的环境变量（脚本用 getenv 读取）"""
        env: dict[str, str] = {
            "MATLAB_DATA_DIR": str(input_dir),
            "MATLAB_OUTPUT_DIR": str(output_dir),
        }
        if params:
            for key, value in params.items():
                env[f"MATLAB_{key.upper()}"] = str(value)
        return env

    def _try_get_engine(self):
        """懒启动常驻 MATLAB 引擎；不可用时返回 None（回退子进程）"""
        if self._engine_ready is not None:
            return self._eng if self._engine_ready else None

        with self._engine_lock:
            if self._engine_ready is not None:
                return self._eng if self._engine_ready else None
            try:
                import matlab.engine  # 延迟导入，避免硬依赖
                print("[Matlab] 首次启动常驻 MATLAB 引擎（约 20~40 秒，仅此一次）...")
                # EXE 中 MATLAB 必须保持后台模式：否则首次 figure/colorbar/saveas
                # 可能触发桌面/图形环境初始化，表现为卡住或弹出额外窗口。
                self._eng = matlab.engine.start_matlab("-nodesktop -nosplash -noFigureWindows")
                self._eng.addpath(str(self.matlab_root), nargout=0)
                self._engine_ready = True
                print("[Matlab] 常驻引擎已就绪，后续每组仅做函数调用，不再冷启动。")
            except Exception as exc:
                self._engine_ready = False
                self._engine_error = str(exc)
                print(f"[Matlab] 常驻引擎启动失败，回退到 matlab.exe -batch 子进程模式：{exc}")
        return self._eng if self._engine_ready else None

    def _invalidate_engine(self, *, graceful: bool = False) -> None:
        """Forget an unusable engine without blocking on a dead MATLAB process."""
        if graceful:
            try:
                if self._eng is not None:
                    self._eng.quit()
            except Exception:
                pass
        self._eng = None
        self._engine_ready = None

    def _run_via_engine(
        self,
        eng,
        script_name: str,
        script_path: Path,
        matlab_env: dict[str, str],
        input_dir: Path,
        output_dir: Path,
        params: dict[str, Any] | None,
        timeout: int,
    ) -> dict[str, Any]:
        """通过常驻引擎执行脚本（无冷启动，最快）"""
        import matlab.engine
        with self._engine_lock:
            try:
                if script_name in {"DAS.m", "Ascan.m", "Paut.m", "TOFD_complete_pipeline.m"}:
                    future = eng.feval(
                        script_path.stem,
                        str(input_dir),
                        str(output_dir),
                        json.dumps(params or {}, ensure_ascii=False),
                        nargout=0,
                        background=True,
                    )
                else:
                    for key, value in matlab_env.items():
                        eng.setenv(key, value, nargout=0)
                    future = eng.run(str(script_path), nargout=0, background=True)
                future.result(timeout=timeout)
            except TimeoutError as exc:
                try:
                    future.cancel()
                except Exception:
                    pass
                self._invalidate_engine(graceful=False)
                raise MatlabRuntimeError(
                    f"MATLAB常驻引擎运行超时（{timeout}秒）\n脚本：{script_path.name}"
                ) from exc
            except matlab.engine.MatlabExecutionError as exc:
                raise MatlabRuntimeError(
                    f"MATLAB运行失败（常驻引擎）\n脚本：{script_path.name}\n{exc}"
                ) from exc
            except matlab.engine.EngineError as exc:
                # 引擎本身崩溃或连接中断（含 OOM 拖死引擎的 "Unknown exception"）。
                # 会话已不可用：作废引擎，下次调用经 _try_get_engine 懒启动干净引擎，避免毒化后续数据组。
                self._invalidate_engine(graceful=False)
                raise MatlabRuntimeError(
                    f"MATLAB常驻引擎异常中断，引擎已重置、本组可重试\n脚本：{script_path.name}\n{exc}"
                ) from exc
        return {
            "ok": True,
            "script": script_path.name,
            "mode": "engine",
            "stdout": "",
            "stderr": "",
        }

    def _run_via_subprocess(
        self,
        script_name: str,
        script_path: Path,
        input_dir: Path,
        output_dir: Path,
        matlab_env: dict[str, str],
        params: dict[str, Any] | None,
        timeout: int,
    ) -> dict[str, Any]:
        """回退方案：启动独立的 matlab.exe -batch 进程"""
        env = os.environ.copy()
        env.update(matlab_env)

        matlab_root = self._matlab_path(self.matlab_root)
        script_path_matlab = self._matlab_path(script_path)
        command = (
            f"cd('{matlab_root}'); "
            f"run('{script_path_matlab}'); "
            f"exit;"
        )
        if script_name in {"DAS.m", "Ascan.m", "Paut.m", "TOFD_complete_pipeline.m"}:
            input_path = self._matlab_path(input_dir)
            output_path = self._matlab_path(output_dir)
            # Embedded JSON quotes are altered by Windows command-line parsing.
            # Base64 keeps the public MATLAB signature unchanged and is Unicode-safe.
            params_json = json.dumps(params or {}, ensure_ascii=False)
            params_base64 = base64.b64encode(params_json.encode("utf-8")).decode("ascii")
            command = (
                f"cd('{matlab_root}'); "
                f"params_json=native2unicode(matlab.net.base64decode('{params_base64}'),'UTF-8'); "
                f"feval('{script_path.stem}', '{input_path}', '{output_path}', params_json); "
                f"exit;"
            )

        try:
            # Keep MATLAB's batch process attached to the desktop task without
            # opening a separate console window on Windows.
            creationflags = 0
            startupinfo = None
            if os.name == "nt":
                creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE

            result = subprocess.run(
                [self.matlab_exe, "-batch", command, "-noFigureWindows", "-nosplash"],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(self.project_root),
                env=env,
                creationflags=creationflags,
                startupinfo=startupinfo,
            )

        except FileNotFoundError as exc:
            raise MatlabRuntimeError(
                "没有找到 matlab.exe。\n"
                "请确认MATLAB已经安装，并将matlab.exe加入PATH，"
                "或者在配置中填写MATLAB完整路径。"
            ) from exc

        except subprocess.TimeoutExpired as exc:
            raise MatlabRuntimeError(f"MATLAB运行超时：{script_name}") from exc

        stdout = result.stdout or ""
        stderr = result.stderr or ""

        print("\n========== MATLAB STDOUT ==========")
        print(stdout)

        if stderr:
            print("\n========== MATLAB STDERR ==========")
            print(stderr)

        if result.returncode != 0:
            raise MatlabRuntimeError(
                f"MATLAB运行失败\n"
                f"脚本：{script_name}\n"
                f"返回码：{result.returncode}\n\n"
                f"STDOUT：\n{stdout}\n\n"
                f"STDERR：\n{stderr}"
            )

        return {
            "ok": True,
            "script": script_name,
            "mode": "subprocess",
            "input_dir": str(input_dir),
            "output_dir": str(output_dir),
            "stdout": stdout,
            "stderr": stderr,
        }

    def run_script(
        self,
        script_name: str,
        input_dir: Path,
        output_dir: Path,
        timeout: int = 300,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:

        input_dir = Path(input_dir).resolve()
        output_dir = Path(output_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        worker = self.project_root / 'runtime_worker' / 'PautOfflineWorker.exe'
        if getattr(sys, 'frozen', False) and worker.is_file():
            from .compiled_imaging import run_compiled, runtime_directory

            app_root = Path(sys.executable).resolve().parent
            runtime = runtime_directory(app_root)
            runtime_dll = runtime / 'runtime' / 'win64' / 'mclmcrrt25_2.dll'
            # 离线安装版带有完整的应用内 Runtime，直接使用编译 Worker。
            # 普通 dist 只有 Worker 而没有 Runtime 时，继续尝试本机 MATLAB。
            if runtime_dll.is_file():
                return run_compiled(worker, app_root, script_name, input_dir,
                                    output_dir, params or {}, timeout)

        script_path = self.matlab_root / script_name

        if not script_path.exists():
            raise MatlabRuntimeError(f"MATLAB脚本不存在：\n{script_path}")

        if not input_dir.exists():
            raise MatlabRuntimeError(f"MATLAB输入数据目录不存在：\n{input_dir}")

        matlab_env = self._build_matlab_env(input_dir, output_dir, params)

        print("\n======================================")
        print("开始调用 MATLAB")
        print("======================================")
        print(f"MATLAB目录：{self.matlab_root}")
        print(f"MATLAB脚本：{script_path}")
        print(f"输入目录：{input_dir}")
        print(f"输出目录：{output_dir}")

        # 优先使用常驻引擎（消除每组一次冷启动）
        if self.use_engine:
            eng = self._try_get_engine()
            if eng is not None:
                result = self._run_via_engine(
                    eng, script_name, script_path, matlab_env,
                    input_dir, output_dir, params, timeout,
                )
                result["input_dir"] = str(input_dir)
                result["output_dir"] = str(output_dir)
                print("\nMATLAB运行完成（常驻引擎）。")
                return result

        result = self._run_via_subprocess(
            script_name, script_path, input_dir, output_dir, matlab_env, params, timeout
        )
        print("\nMATLAB运行完成。")
        return result

    def check_environment(self) -> dict[str, Any]:
        """启动前检查 MATLAB 能力，返回可读的检查结果。"""
        worker = self.project_root / 'runtime_worker' / 'PautOfflineWorker.exe'
        if getattr(sys, 'frozen', False) and worker.is_file():
            from .compiled_imaging import runtime_directory

            runtime = runtime_directory(Path(sys.executable).resolve().parent)
            available = (runtime / 'runtime' / 'win64' / 'mclmcrrt25_2.dll').is_file()
            if available:
                return {'ok': True, 'mode': 'compiled_runtime', 'engine_ok': False,
                        'matlab_exe_ok': False, 'scripts_ok': True, 'messages': []}
        scripts_ok = True
        missing_scripts: list[str] = []
        for name in ("DAS.m", "Ascan.m", "Paut.m", "TOFD_complete_pipeline.m"):
            if not (self.matlab_root / name).exists():
                scripts_ok = False
                missing_scripts.append(name)
        required_resources = [
            self.matlab_root / "paut_v2" / "PAUT_complete_pipeline.m",
            self.matlab_root / "paut_v2" / "paut_canonical_imaging_config.m",
            self.matlab_root / "paut_v2" / "paut_reconstruct_das_shared.m",
            self.matlab_root / "paut_v2" / "predict_slag_large_v84.m",
            self.matlab_root / "paut_v2" / "predict_lof_slag_v1.m",
            settings.resource_dir / "algorithms" / "python" / "paut_v2" / "predict_crack_batch.py",
            settings.resource_dir / "models" / "paut_v2" / "crack_classifier.pt",
            settings.resource_dir / "models" / "paut_v2" / "crack_length_model.mat",
            settings.resource_dir / "models" / "paut_v2" / "refined_crack_model.mat",
            settings.resource_dir / "models" / "paut_v2" / "lof_length_model.mat",
            settings.resource_dir / "models" / "paut_v2" / "pore_v32_model.mat",
            settings.resource_dir / "models" / "paut_v2" / "slag_v84_model.mat",
            settings.resource_dir / "models" / "paut_v2" / "lof_slag_advisor_v1.mat",
        ]
        missing_resources = [str(path) for path in required_resources if not path.is_file()]
        if missing_resources:
            scripts_ok = False
        optional_resources = {
            "裂纹局部精细特征函数": self.matlab_root / "paut_v2" / "extract_crack_multithreshold_features.m",
        }
        degraded_resources = [label for label, path in optional_resources.items() if not path.is_file()]

        engine_ok = False
        engine_error = ""
        try:
            import matlab.engine  # noqa: F401
            engine_ok = True
        except Exception as exc:  # noqa: BLE001
            engine_error = str(exc)

        matlab_exe_ok = shutil.which(self.matlab_exe) is not None

        messages: list[str] = []
        if missing_scripts:
            messages.append(
                f"缺少 MATLAB 脚本：{', '.join(missing_scripts)}（目录：{self.matlab_root}）"
            )
        if missing_resources:
            messages.append(f"缺少 PAUT V2 运行资源：{len(missing_resources)} 个")
        if degraded_resources:
            messages.append("可降级能力缺失：" + "、".join(degraded_resources) + "；相关定量将采用团队回退模型")
        if not engine_ok and not matlab_exe_ok:
            messages.append(
                "未找到可用的 MATLAB：既未安装 Python 引擎（pip install matlabengine，"
                "版本需精确匹配本机 MATLAB），且 matlab.exe 不在 PATH 中。"
            )
        elif not engine_ok:
            messages.append(
                "未安装 matlab.engine，将回退到 matlab.exe -batch 子进程模式（每组会较慢）。"
            )

        return {
            "ok": scripts_ok and (engine_ok or matlab_exe_ok),
            "scripts_ok": scripts_ok,
            "missing_scripts": missing_scripts,
            "missing_resources": missing_resources,
            "degraded_resources": degraded_resources,
            "engine_ok": engine_ok,
            "engine_error": engine_error,
            "matlab_exe_ok": matlab_exe_ok,
            "matlab_exe": self.matlab_exe,
            "matlab_root": str(self.matlab_root),
            "messages": messages,
        }

    def shutdown(self) -> None:
        """关闭常驻引擎（服务停止时调用，释放 MATLAB license）"""
        with self._engine_lock:
            if self._eng is not None:
                try:
                    self._eng.quit()
                except Exception:
                    pass
                self._eng = None
                self._engine_ready = None

    def run_das(
        self,
        input_dir: Path,
        output_dir: Path,
        timeout: int = 300,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.run_script(
            script_name="DAS.m",
            input_dir=input_dir,
            output_dir=output_dir,
            timeout=timeout,
            params=params,
        )

    def run_ascan(
        self,
        input_dir: Path,
        output_dir: Path,
        timeout: int = 300,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.run_script(
            script_name="Ascan.m",
            input_dir=input_dir,
            output_dir=output_dir,
            timeout=timeout,
            params=params,
        )

    def run_paut(
        self,
        input_dir: Path,
        output_dir: Path,
        timeout: int = 600,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """一次 MATLAB 调用完成 V2 PAUT 成像、分类与定量分析。"""
        paut_params = dict(params or {})
        resource_root = settings.resource_dir
        model_root = resource_root / "models" / "paut_v2"
        inference_script = resource_root / "algorithms" / "python" / "paut_v2" / "predict_crack_batch.py"
        paut_params.update({
            "python_executable": str(Path(sys.executable).resolve()),
            "inference_mode": "frozen" if getattr(sys, "frozen", False) else "script",
            "inference_script": str(inference_script.resolve()),
            "crack_cnn_model": str((model_root / "crack_classifier.pt").resolve()),
            "crack_length_model": str((model_root / "crack_length_model.mat").resolve()),
            "refined_crack_model": str((model_root / "refined_crack_model.mat").resolve()),
            "lof_length_model": str((model_root / "lof_length_model.mat").resolve()),
            "pore_area_model": str((model_root / "pore_v32_model.mat").resolve()),
            "slag_area_model": str((model_root / "slag_v84_model.mat").resolve()),
            "lof_slag_classifier_model": str((model_root / "lof_slag_advisor_v1.mat").resolve()),
            "enable_advisory_slag_quantification": True,
        })
        return self.run_script(
            script_name="Paut.m",
            input_dir=input_dir,
            output_dir=output_dir,
            timeout=timeout,
            params=paut_params,
        )

    def run_tofd(
        self,
        input_dir: Path,
        output_dir: Path,
        state_dir: Path,
        timeout: int = 300,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run the migrated binary TOFD pipeline; keep state_dir for API compatibility."""
        tofd_params = dict(params or {})
        return self.run_script(
            script_name="TOFD_complete_pipeline.m",
            input_dir=input_dir,
            output_dir=output_dir,
            timeout=timeout,
            params=tofd_params,
        )

    def run_realtime(
        self,
        input_dir: Path,
        output_dir: Path,
        state_dir: Path | None = None,
        timeout: int = 600,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Compatibility entry that dispatches one explicitly selected data type."""
        rt_params = dict(params or {})
        data_type = str(rt_params.get("data_type") or "paut").lower()
        if data_type == "tofd":
            return self.run_tofd(
                input_dir=input_dir,
                output_dir=output_dir,
                state_dir=state_dir or output_dir,
                timeout=timeout,
                params=rt_params,
            )
        return self.run_paut(input_dir, output_dir, timeout=timeout, params=rt_params)


_matlab_service: MatlabService | None = None
_matlab_service_lock = threading.Lock()


def get_matlab_service() -> MatlabService:
    """获取当前 Python 进程唯一的 MatlabService 实例。"""
    global _matlab_service
    if _matlab_service is not None:
        return _matlab_service
    with _matlab_service_lock:
        if _matlab_service is None:
            _matlab_service = MatlabService()
        return _matlab_service


def close_matlab_service() -> None:
    """应用退出时关闭进程级 MATLAB Engine。"""
    global _matlab_service
    with _matlab_service_lock:
        service = _matlab_service
        _matlab_service = None
    if service is not None:
        service.shutdown()
