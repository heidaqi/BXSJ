"""桌面应用配置与能力检测 API。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .config import ensure_user_config, reload_settings, settings
from .services.imaging_backend import imaging_capabilities


router = APIRouter(prefix="/api/app", tags=["app"])
CONFIG_FIELDS = {
    "AGENT_ENABLED", "OPENAI_BASE_URL", "OPENAI_MODEL", "MATLAB_EXE",
    "MATLAB_USE_ENGINE", "MATLAB_RUNTIME_MODULE", "OUTPUT_DIR",
}


class AppSettingsUpdate(BaseModel):
    agent_enabled: bool = False
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"
    openai_api_key: str = Field(default="", max_length=4096)
    matlab_exe: str = "matlab.exe"
    matlab_runtime_module: str = ""
    output_dir: str = ""


def _config_path() -> Path:
    return ensure_user_config(settings.user_data_dir)


def _read_config() -> dict[str, str]:
    try:
        data = json.loads(_config_path().read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _local_env_path() -> Path:
    return settings.user_data_dir / "config" / "secrets.env"


def _read_local_env() -> dict[str, str]:
    path = _local_env_path()
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _write_local_env(values: dict[str, str]) -> None:
    path = _local_env_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 本文件由应用设置页自动生成，只保存在当前项目/便携程序目录。",
        "# 不要提交到 GitHub，也不要随打包文件一起分发。",
    ]
    for key in sorted(values):
        value = values[key]
        if value:
            lines.append(f'{key}="{value}"')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _store_api_key(value: str) -> None:
    value = value.strip()
    if "\n" in value or "\r" in value:
        raise HTTPException(status_code=400, detail="API Key 不能包含换行符")
    values = _read_local_env()
    if value:
        values["OPENAI_API_KEY"] = value
    else:
        values.pop("OPENAI_API_KEY", None)
    _write_local_env(values)


@router.get("/capabilities")
def capabilities() -> dict[str, Any]:
    return {
        "ok": True,
        "desktop": True,
        "imaging": imaging_capabilities(),
        "agent": {
            "available": settings.agent_enabled and bool(settings.openai_api_key),
            "enabled": settings.agent_enabled,
            "configured": bool(settings.openai_api_key),
        },
        "paths": {
            "user_data": str(settings.user_data_dir),
            "output": str(settings.output_dir),
        },
    }


@router.get("/settings")
def read_settings() -> dict[str, Any]:
    return {
        "agent_enabled": settings.agent_enabled,
        "openai_base_url": settings.openai_base_url,
        "openai_model": settings.openai_model,
        "openai_api_key_set": bool(settings.openai_api_key),
        "matlab_exe": settings.matlab_exe,
        "matlab_runtime_module": settings.matlab_runtime_module,
        "output_dir": str(settings.output_dir),
    }


@router.put("/settings")
def save_settings(payload: AppSettingsUpdate) -> dict[str, Any]:
    current = _read_config()
    values = {
        "AGENT_ENABLED": str(payload.agent_enabled).lower(),
        "OPENAI_BASE_URL": payload.openai_base_url.strip().rstrip("/"),
        "OPENAI_MODEL": payload.openai_model.strip(),
        "MATLAB_EXE": payload.matlab_exe.strip() or "matlab.exe",
        "MATLAB_RUNTIME_MODULE": payload.matlab_runtime_module.strip(),
        "OUTPUT_DIR": payload.output_dir.strip(),
    }
    current.update({key: value for key, value in values.items() if key in CONFIG_FIELDS})
    current.pop("OPENAI_API_KEY", None)
    _config_path().write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    if payload.openai_api_key:
        _store_api_key(payload.openai_api_key)
    reload_settings()
    return {
        "ok": True,
        "restart_required": False,
        "message": "设置已保存，已在当前程序中生效。",
        "secrets_path": str(_local_env_path()),
    }


@router.post("/settings/test-agent")
def test_agent(payload: AppSettingsUpdate) -> dict[str, Any]:
    api_key = payload.openai_api_key or settings.openai_api_key
    if not api_key:
        raise HTTPException(status_code=400, detail="请先填写 API Key")
    try:
        response = httpx.post(
            f"{payload.openai_base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": payload.openai_model, "messages": [{"role": "user", "content": "仅回复 OK"}], "max_tokens": 8},
            timeout=15.0,
        )
        response.raise_for_status()
        return {"ok": True, "message": "Agent 接口连接成功"}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Agent 接口连接失败：{exc}") from exc


@router.post("/settings/test-imaging")
def test_imaging() -> dict[str, Any]:
    result = imaging_capabilities()
    return {"ok": result["available"], **result}
