# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（onedir 优先）。

把只读资源（模型 / MATLAB 脚本 / 知识库）作为 datas 收集到打包目录内，
用户数据（输出 / 数据库 / 日志 / 配置）由 `backend.app.config` 在运行时定位到
%LOCALAPPDATA%\\YoloInspection，绝不写入解包临时目录。

入口脚本 `desktop/main.py` 以 `backend.app.*` 方式导入分析核心。
"""
from pathlib import Path
import importlib.util
import os

from PyInstaller.utils.hooks import collect_submodules

project_root = Path(SPECPATH).parent


def collect_dir(src: Path, dest: str) -> list:
    entries = []
    if not src.is_dir():
        return entries
    for file in src.rglob("*"):
        if file.is_file():
            rel = file.relative_to(src)
            entries.append((str(file), str(Path(dest) / rel.parent)))
    return entries


datas = []
offline_build = os.environ.get('PAUT_OFFLINE_BUILD') == '1'
if offline_build:
    worker = project_root / 'packaging' / 'runtime_build' / 'PautOfflineWorker.exe'
    if not worker.is_file():
        raise RuntimeError('Compile PautOfflineWorker before building offline package')
    datas.append((str(worker), 'runtime_worker'))
ascan_model_path = project_root / "models" / "ascan_candidate_random_forest.joblib"
if ascan_model_path.exists():
    datas.append((str(ascan_model_path), "models"))
datas += collect_dir(project_root / "models" / "defect_classifier", "models/defect_classifier")
datas += collect_dir(project_root / "models" / "paut_v2", "models/paut_v2")
datas += collect_dir(project_root / "algorithms" / "matlab", "algorithms/matlab")
datas += collect_dir(project_root / "algorithms" / "python" / "paut_v2", "algorithms/python/paut_v2")
datas += collect_dir(project_root / "knowledge", "knowledge")
datas += collect_dir(project_root / "frontend" / "dist", "frontend/dist")

hiddenimports = [
    "scipy.io",
    "scipy.ndimage",
    "scipy.sparse",
    "sklearn",
    "sklearn.ensemble",
    "sklearn.ensemble._forest",
    "sklearn.tree",
    "sklearn.tree._tree",
    "sklearn.utils",
    "algorithms.python.paut_v2.ml_ndt_model",
    "algorithms.python.paut_v2.predict_crack_batch",
    "torch",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtWebChannel",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
]

# A扫模型仅使用Pipeline、SimpleImputer和RandomForest；不要收集整个sklearn
# （那会把测试集和无关算法全部带入，显著拖慢构建并膨胀发布包）。
hiddenimports += [
    "joblib",
    "sklearn.pipeline",
    "sklearn.impute._base",
    "sklearn.ensemble._forest",
    "sklearn.tree._classes",
    "sklearn.tree._tree",
    "sklearn.tree._criterion",
    "sklearn.tree._splitter",
    "sklearn.tree._utils",
    "sklearn.utils._cython_blas",
    "sklearn.utils._typedefs",
    "sklearn.utils._weight_vector",
]

# 离线安装版始终使用内置编译 Worker，不携带构建机的 MATLAB Engine 或 DLL。
# 开发便携版仍保留 Engine 支持，方便源码调试。
if not offline_build:
    try:
        if importlib.util.find_spec("matlab") is None:
            raise ImportError
        hiddenimports += ["matlab", "matlab.engine"]
        hiddenimports += collect_submodules("matlab")
    except (ImportError, ModuleNotFoundError):
        pass

a = Analysis(
    [str(project_root / "desktop" / "main.py")],
    pathex=[str(project_root), str(project_root / "backend")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "ultralytics"],
    noarchive=False,
)


def _is_qt_icu_conflict(entry) -> bool:
    """排除Anaconda等环境注入的旧版/无版本ICU，避免Qt6Core加载错误。"""
    destination = str(entry[0]) if entry else ""
    source = str(entry[1]) if len(entry) > 1 else ""
    name = Path(destination).name.lower()
    source_name = Path(source).name.lower()
    candidate = name if name.startswith("icu") else source_name
    return candidate.startswith("icu") and candidate.endswith(".dll") and "74" not in candidate


# MATLAB Engine分析期间可能从系统PATH/Anaconda带入icudt73.dll、icuuc.dll。
# 它们位于应用DLL搜索根目录时会抢在Qt依赖之前加载，导致QtCore导入失败。
a.binaries = [entry for entry in a.binaries if not _is_qt_icu_conflict(entry)]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BXSJ",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="BXSJ",
)
