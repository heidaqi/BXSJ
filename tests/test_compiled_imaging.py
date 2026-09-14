import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from backend.app.services.compiled_imaging import run_compiled, runtime_directory


def _make_runtime(root):
    (root / 'runtime' / 'win64').mkdir(parents=True)
    (root / 'runtime' / 'win64' / 'mclmcrrt25_2.dll').write_bytes(b'test')
    (root / 'bin' / 'win64').mkdir(parents=True)


def test_runtime_directory_accepts_standard_layout(tmp_path, monkeypatch):
    monkeypatch.delenv('PAUT_RUNTIME_ROOT', raising=False)
    expected = tmp_path / 'runtime' / 'R2025b'
    _make_runtime(expected)
    assert runtime_directory(tmp_path) == expected


def test_runtime_directory_recovers_legacy_double_version_layout(tmp_path, monkeypatch):
    monkeypatch.delenv('PAUT_RUNTIME_ROOT', raising=False)
    expected = tmp_path / 'runtime' / 'R2025b' / 'R2025b'
    _make_runtime(expected)
    assert runtime_directory(tmp_path) == expected


def test_runtime_directory_uses_valid_marker(tmp_path, monkeypatch):
    monkeypatch.delenv('PAUT_RUNTIME_ROOT', raising=False)
    expected = tmp_path / 'custom runtime' / 'R2025b'
    _make_runtime(expected)
    marker = tmp_path / 'runtime' / 'runtime-root.txt'
    marker.parent.mkdir()
    marker.write_text(str(expected), encoding='utf-8-sig')
    assert runtime_directory(tmp_path) == expected.resolve()


def test_compiled_bridge_preserves_paths_and_uses_local_cache(tmp_path, monkeypatch):
    monkeypatch.delenv('PAUT_RUNTIME_ROOT', raising=False)
    dll = tmp_path / 'runtime/R2025b/runtime/win64/mclmcrrt25_2.dll'
    dll.parent.mkdir(parents=True)
    dll.touch()
    output = tmp_path / 'output'
    output.mkdir()
    def execute(args, **kwargs):
        request = json.loads(Path(args[1]).read_text(encoding='utf-8'))
        assert request['parameters'] == {'name': '中文参数'}
        assert kwargs['env']['MCR_CACHE_ROOT'].startswith(str(tmp_path))
        assert kwargs['env']['TEMP'].startswith(str(tmp_path))
        return SimpleNamespace(returncode=0, stdout=b'ok', stderr=b'')
    with patch('backend.app.services.compiled_imaging.subprocess.run', side_effect=execute):
        result = run_compiled(tmp_path / 'worker.exe', tmp_path, 'Paut.m',
                              tmp_path / 'input', output, {'name': '中文参数'}, 60)
    assert result['ok']
    assert not list(output.iterdir())


def test_compiled_bridge_requires_runtime(tmp_path, monkeypatch):
    monkeypatch.delenv('PAUT_RUNTIME_ROOT', raising=False)
    with pytest.raises(RuntimeError, match='运行环境'):
        run_compiled(tmp_path / 'worker.exe', tmp_path, 'Paut.m', tmp_path, tmp_path, {}, 60)
