"""Isolated MATLAB Runtime process; development MATLAB calls stay unchanged."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile


def _is_runtime_root(path: Path) -> bool:
    return (
        (path / 'runtime' / 'win64' / 'mclmcrrt25_2.dll').is_file()
        and (path / 'bin' / 'win64').is_dir()
    )


def runtime_directory(app_root: Path) -> Path:
    configured = os.environ.get('PAUT_RUNTIME_ROOT')
    if configured:
        configured_path = Path(configured).expanduser().resolve()
        if _is_runtime_root(configured_path):
            return configured_path

    runtime_base = app_root / 'runtime'
    marker = runtime_base / 'runtime-root.txt'
    if marker.is_file():
        try:
            marked_path = Path(marker.read_text(encoding='utf-8-sig').strip()).resolve()
            if _is_runtime_root(marked_path):
                return marked_path
        except (OSError, ValueError):
            pass

    candidates = (
        runtime_base / 'R2025b',
        runtime_base / 'R2025b' / 'R2025b',
        runtime_base,
    )
    for candidate in candidates:
        if _is_runtime_root(candidate):
            return candidate

    if runtime_base.is_dir():
        for dll in runtime_base.glob('**/runtime/win64/mclmcrrt25_2.dll'):
            candidate = dll.parents[2]
            if _is_runtime_root(candidate):
                return candidate
    return runtime_base / 'R2025b'


def run_compiled(worker: Path, app_root: Path, operation: str, input_dir: Path,
                 output_dir: Path, parameters: dict, timeout: int) -> dict:
    runtime = runtime_directory(app_root)
    runtime_bin = runtime / 'runtime' / 'win64'
    if not (runtime_bin / 'mclmcrrt25_2.dll').is_file():
        raise RuntimeError('成像运行环境未完整安装，请重新运行离线安装程序。')
    cache = app_root / 'data' / 'runtime_cache'
    cache.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env['PATH'] = str(runtime_bin) + os.pathsep + env.get('PATH', '')
    env['MCR_CACHE_ROOT'] = str(cache)
    env['TEMP'] = env['TMP'] = str(cache)
    request_path = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', suffix='.json',
                                         dir=output_dir, delete=False) as request:
            request_path = Path(request.name)
            json.dump({'operation': operation, 'input_dir': str(input_dir),
                       'output_dir': str(output_dir), 'parameters': parameters}, request,
                      ensure_ascii=False)
        result = subprocess.run([str(worker), str(request_path)], cwd=app_root,
                                env=env, capture_output=True, timeout=timeout,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        stdout = result.stdout.decode('utf-8', errors='replace')
        stderr = result.stderr.decode('utf-8', errors='replace')
        if result.returncode:
            raise RuntimeError(f'成像组件运行失败（{result.returncode}）：{stderr[-2000:]} {stdout[-2000:]}')
        return {'ok': True, 'script': operation, 'mode': 'compiled_runtime',
                'input_dir': str(input_dir), 'output_dir': str(output_dir),
                'stdout': stdout, 'stderr': stderr}
    finally:
        if request_path is not None:
            request_path.unlink(missing_ok=True)
