from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, fields
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打包后的环境。"""
    return bool(getattr(sys, "frozen", False))


def bundle_dir() -> Path:
    """只读资源根目录：模型、MATLAB 脚本、知识库、默认配置。"""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return PROJECT_ROOT


def default_user_data_dir() -> Path:
    """可写运行目录：开发版为项目根目录，打包版为 EXE 所在便携目录。"""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    if os.getenv("YOLO_DESKTOP_MODE") == "1":
        return PROJECT_ROOT
    return PROJECT_ROOT


def _user_env_path(user_data_dir: Path) -> Path:
    return user_data_dir / "config" / "secrets.env"


def _read_env_file(env_path: Path) -> dict[str, str]:
    if not env_path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _project_env_values() -> dict[str, str]:
    # 打包版只读便携目录配置，避免交付运行时误读开发目录 .env。
    if is_frozen() or os.getenv("YOLO_DESKTOP_MODE") == "1":
        return {}
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        env_path = PROJECT_ROOT / ".env.example"
    return _read_env_file(env_path)


def _load_user_config(user_data_dir: Path) -> dict[str, str]:
    """读取用户配置 JSON（首次运行由桌面入口写入默认值）。"""
    path = user_data_dir / "config" / "config.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(key): str(value) for key, value in payload.items() if value is not None}


DEFAULT_USER_CONFIG = {
    "YOLO_MODEL_PATH": "models/best.pt",
    "YOLO_CONFIDENCE": "0.25",
    "YOLO_DEVICE": "auto",
    "YOLO_IGNORE_TOP_RATIO": "0.08",
    "YOLO_BACKEND": "local",
    "DEMO_DETECTION": "false",
    "MATLAB_EXE": "matlab.exe",
    "MATLAB_USE_ENGINE": "true",
    "MATLAB_RUNTIME_MODULE": "",
    "OUTPUT_DIR": "",
    "AGENT_ENABLED": "false",
    "REALTIME_CACHE_RETENTION_DAYS": "7",
    "REALTIME_CACHE_CLEANUP_HOURS": "6",
    "OPENAI_BASE_URL": "https://api.openai.com/v1",
    "OPENAI_MODEL": "gpt-4o-mini",
}


def ensure_user_config(user_data_dir: Path) -> Path:
    """确保用户配置目录与默认配置存在，返回配置路径。"""
    path = user_data_dir / "config" / "config.json"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(DEFAULT_USER_CONFIG, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return path


def ensure_user_directories(user_data_dir: Path) -> None:
    """确保可写目录存在：输出、上传、数据、日志。"""
    for sub in ("outputs", "uploads", "data", "logs"):
        (user_data_dir / sub).mkdir(parents=True, exist_ok=True)


@dataclass
class Settings:
    app_env: str
    backend_host: str
    backend_port: int
    frontend_url: str
    resource_dir: Path
    user_data_dir: Path
    app_data_dir: Path
    upload_dir: Path
    output_dir: Path
    database_url: str
    database_path: Path
    yolo_backend: str
    yolo_model_path: Path
    yolo_confidence: float
    yolo_device: str
    yolo_ignore_top_ratio: float
    demo_detection: bool
    yolo_api_url: str
    matlab_exe: str
    matlab_use_engine: bool
    matlab_runtime_module: str
    agent_enabled: bool
    openai_api_key: str
    openai_base_url: str
    openai_model: str
    agent_language: str
    agent_send_project_context: bool
    web_search_enabled: bool
    web_search_send_project_context: bool
    web_search_provider: str
    web_search_max_results: int
    web_search_api_key: str
    web_search_api_url: str
    report_org_name: str
    report_prepared_by: str
    report_reviewed_by: str
    report_approved_by: str
    realtime_cache_retention_days: int
    realtime_cache_cleanup_hours: int


def get_settings() -> Settings:
    resource_dir = bundle_dir()
    user_data_dir = Path(os.getenv("YOLO_USER_DATA_DIR") or default_user_data_dir()).resolve()
    user_cfg = _load_user_config(user_data_dir)
    project_env = _project_env_values()
    user_env = _read_env_file(_user_env_path(user_data_dir))

    def setting(name: str, default: str) -> str:
        # 优先级：进程环境变量 > 用户本机 .env > 开发 .env > 用户配置 JSON > 默认值
        return os.getenv(name) or user_env.get(name) or project_env.get(name) or user_cfg.get(name, default)

    def resolve_resource(value: str) -> Path:
        path = Path(value)
        if not path.is_absolute():
            path = resource_dir / path
        return path.resolve()

    def resolve_user(value: str) -> Path:
        path = Path(value)
        if not path.is_absolute():
            path = user_data_dir / path
        return path.resolve()

    def resolve_output(value: str) -> Path:
        if value.strip():
            path = Path(value)
            if not path.is_absolute():
                path = (Path(sys.executable).resolve().parent if is_frozen() else PROJECT_ROOT) / path
            return path.resolve()
        base = Path(sys.executable).resolve().parent if is_frozen() else PROJECT_ROOT
        return (base / "outputs").resolve()

    database_url = setting("DATABASE_URL", "sqlite:///./data/app.db")
    if database_url.startswith("sqlite:///"):
        database_path = resolve_user(database_url.replace("sqlite:///", "", 1))
    else:
        database_path = resolve_user("./data/app.db")

    return Settings(
        app_env=setting("APP_ENV", "development"),
        backend_host=setting("BACKEND_HOST", "127.0.0.1"),
        backend_port=int(setting("BACKEND_PORT", "8000")),
        frontend_url=setting("FRONTEND_URL", "http://localhost:5173"),
        resource_dir=resource_dir,
        user_data_dir=user_data_dir,
        app_data_dir=resolve_user(setting("APP_DATA_DIR", "./data")),
        upload_dir=resolve_user(setting("UPLOAD_DIR", "./data/uploads")),
        output_dir=resolve_output(setting("OUTPUT_DIR", "")),
        database_url=database_url,
        database_path=database_path,
        yolo_backend=setting("YOLO_BACKEND", "local"),
        yolo_model_path=resolve_resource(setting("YOLO_MODEL_PATH", "./models/best.pt")),
        yolo_confidence=float(setting("YOLO_CONFIDENCE", "0.25")),
        yolo_device=setting("YOLO_DEVICE", "auto"),
        yolo_ignore_top_ratio=max(0.0, min(0.5, float(setting("YOLO_IGNORE_TOP_RATIO", "0.08")))),
        demo_detection=setting("DEMO_DETECTION", "true").lower() in {"1", "true", "yes", "on"},
        yolo_api_url=setting("YOLO_API_URL", ""),
        matlab_exe=setting("MATLAB_EXE", "matlab.exe"),
        matlab_use_engine=setting("MATLAB_USE_ENGINE", "true").lower() in {"1", "true", "yes", "on"},
        matlab_runtime_module=setting("MATLAB_RUNTIME_MODULE", ""),
        agent_enabled=setting("AGENT_ENABLED", "false").lower() in {"1", "true", "yes", "on"},
        openai_api_key=setting("OPENAI_API_KEY", ""),
        openai_base_url=setting("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
        openai_model=setting("OPENAI_MODEL", "gpt-4o-mini"),
        agent_language=setting("AGENT_LANGUAGE", "zh-CN"),
        agent_send_project_context=setting("AGENT_SEND_PROJECT_CONTEXT", "true").lower() in {"1", "true", "yes", "on"},
        web_search_enabled=setting("WEB_SEARCH_ENABLED", "true").lower() in {"1", "true", "yes", "on"},
        web_search_send_project_context=setting("WEB_SEARCH_SEND_PROJECT_CONTEXT", "false").lower() in {"1", "true", "yes", "on"},
        web_search_provider=setting("WEB_SEARCH_PROVIDER", "duckduckgo"),
        web_search_max_results=int(setting("WEB_SEARCH_MAX_RESULTS", "5")),
        web_search_api_key=setting("WEB_SEARCH_API_KEY", ""),
        web_search_api_url=setting("WEB_SEARCH_API_URL", ""),
        report_org_name=setting("REPORT_ORG_NAME", ""),
        report_prepared_by=setting("REPORT_PREPARED_BY", ""),
        report_reviewed_by=setting("REPORT_REVIEWED_BY", ""),
        report_approved_by=setting("REPORT_APPROVED_BY", ""),
        realtime_cache_retention_days=max(1, int(setting("REALTIME_CACHE_RETENTION_DAYS", "7"))),
        realtime_cache_cleanup_hours=max(1, int(setting("REALTIME_CACHE_CLEANUP_HOURS", "6"))),
    )


settings = get_settings()


def reload_settings() -> Settings:
    """重新加载配置，并原地刷新全局 settings，避免保存后必须重启。"""
    latest = get_settings()
    for field in fields(Settings):
        setattr(settings, field.name, getattr(latest, field.name))
    return settings
