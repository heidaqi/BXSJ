# -*- coding: utf-8 -*-
"""PAUT/FMC 缺陷分类推理模块(自包含, 无绝对路径依赖, 可直接复制进外部项目)。

对外唯一入口::

    from defect_classifier import predict_fmc_case

    result = predict_fmc_case(
        fmc=fmc,                  # (16, 16, n_t) 全矩阵捕获波形 (tx, rx, 时间)
        t=t,                      # (n_t,) 时间轴, 单位 秒
        x_el=x_el,                # (16,) 阵元 x 坐标, 单位 mm
        candidate_x_mm=None,      # 可选: 已有候选缺陷 x (mm)
        candidate_z_mm=None,      # 可选: 已有候选缺陷深度 (mm, 自顶面)
        velocity_mps=6200.0,      # 声速, 实际用于 TOF/TFM 计算
        model_path=None,          # 默认同目录 models/best_PHYS_SVM-RBF.joblib
        confidence_threshold=0.60,# 低于该置信度判为 uncertain
    )

返回 JSON 可序列化 dict(所有值均为原生 Python 类型)::

    {
      "ok": true,
      "model_name": "PHYS_SVM_RBF", "model_version": "2026-08-23",
      "input_shape": [16, 16, 751],
      "localization": {"method": "provided_candidate|tfm_peak",
                       "x_mm": 34.5, "z_mm": 21.0,
                       "confidence": null, "warning": "..."},
      "prediction": {"label": "crack", "label_cn": "裂纹",
                     "confidence": 0.807,
                     "probabilities": {"slag": .., "crack": .., "pore": .., "lof": ..},
                     "recommended_review": false},
      "features": {"feature_names": [...], "feature_values": [...]},
      "explanation": {"top_features": [{"name","value","importance"}, ...],
                      "physics_basis": ["角域散射矩阵", ...]},
      "warnings": [...], "errors": [...]
    }

校验失败(shape/NaN/单位等)不会抛异常, 返回 ok=false + errors 列表。

本模块不 import 训练脚本(scripts/data.py 等), 训练脚本里的绝对路径与本模块无关。
依赖: numpy / scipy / scikit-learn / joblib(见 requirements.txt)。
"""
import math
from pathlib import Path

import joblib
import numpy as np
from scipy.signal import hilbert

# ---------------------------------------------------------------- 常量
N_EL = 16
N_T = 751                 # 训练统一时间轴长度 (0~15us @20ns)
DT_REF_S = 20e-9          # 训练采样间隔
NB = 28                   # 角域直方图 bin 数 (±70°, 5°/bin)
BIN_EDGES = np.linspace(-70.0, 70.0, NB + 1)
BIN_CENTERS = (BIN_EDGES[:-1] + BIN_EDGES[1:]) / 2.0
W_US = 0.8                # 缺陷回波时窗半宽 (us)

CLASSES = ["slag", "crack", "pore", "lof"]
CLASS_CN = {"slag": "夹渣", "crack": "裂纹", "pore": "气孔", "lof": "未熔合"}

# 17 个纯物理特征(不含深度/长度/倾角元数据), 顺序与模型 feature_names 一致
PHYS_FEATURE_NAMES = [
    "sm_entropy",        # 散射矩阵整体熵(各向同性度)
    "sc_entropy",        # 散射角边际分布熵
    "ridge_slope",       # 镜面脊线斜率(水平面理论 -1)
    "ridge_intercept",   # 脊线截距(≈2×倾角)
    "ridge_r2",          # 脊线拟合优度
    "ridge_sharpness",   # 脊线锐度(行峰值能量占比)
    "offridge_frac",     # 偏离脊线的能量占比
    "backscatter_frac",  # 背向散射能量占比 |θsc-θin|<10°
    "near_normal_frac",  # 近法向入射能量占比 |θin|<10°
    "spec_centroid_mhz", # 缺陷回波谱质心 (MHz)
    "spec_bw_mhz",       # 谱带宽 (MHz)
    "spec_lf_hf",        # 低频/高频能量比
    "echo_dur_us",       # 回波包络 FWHM 时长 (us)
    "echo_skew",         # 回波包络偏度
    "tfm_aspect",        # TFM 亮斑长宽比
    "tfm_orient_deg",    # 亮斑主轴相对水平的角度
    "tfm_sharpness",     # 亮斑峰值/背景对比度
]

PHYSICS_BASIS = ["角域散射矩阵", "回波谱特征", "回波包络形态", "TFM亮斑形态"]

_MODEL_DIR = Path(__file__).resolve().parent / "models"
DEFAULT_MODEL_PATH = _MODEL_DIR / "best_PHYS_SVM-RBF.joblib"
DEFAULT_CONFIDENCE_THRESHOLD = 0.60


# ---------------------------------------------------------------- 工具
def _dt_us(t):
    """时间轴采样间隔 (us), 由 t 实际计算; 退化时回退 20ns。"""
    if t.size < 2:
        return DT_REF_S * 1e6
    dt = float(np.median(np.diff(t))) * 1e6
    return dt if dt > 0 else DT_REF_S * 1e6


def _tof_us(x_el, xd, zd, c_mm_us):
    """每个收发对的缺陷回波到达时间 (us)。"""
    d_tx = np.hypot(x_el - xd, zd)                        # (16,)
    return (d_tx[:, None] + d_tx[None, :]) / c_mm_us      # (16,16)


# ---------------------------------------------------------------- TFM 成像
def tfm_image(fmc, t, x_el, xg, zg, velocity_mps):
    """非相干 TFM: 各收发对包络在 TOF 处求和(含 2D 几何扩散补偿)。"""
    c_mm_us = velocity_mps / 1000.0
    dt_us = _dt_us(t)
    t_us = t * 1e6
    t0 = t_us[0]
    xg2, zg2 = np.meshgrid(xg, zg)
    xg_f, zg_f = xg2.ravel(), zg2.ravel()                       # (nx*nz,)
    d_tx = np.hypot(x_el[:, None] - xg_f[None, :], zg_f[None, :])  # (16, N)
    tof = (d_tx[:, None, :] + d_tx[None, :, :]) / c_mm_us       # (16,16,N)
    img = np.zeros(xg.size * zg.size)
    for i in range(N_EL):
        env = np.abs(hilbert(fmc[i], axis=1))                   # (16, n_t)
        for j in range(N_EL):
            pos = tof[i, j]                                     # us
            ok = pos < t_us[-1] - 0.1                           # 丢弃超时窗
            idx = np.clip(pos, t0, t_us[-1] - dt_us)
            idx = (idx - t0) / dt_us
            lo = np.floor(idx).astype(int)
            hi = np.minimum(lo + 1, fmc.shape[2] - 1)
            w = idx - lo
            v = env[j, lo] * (1 - w) + env[j, hi] * w
            img += np.where(ok, v * np.sqrt(d_tx[i] * d_tx[j]), 0.0)
    return img.reshape(len(zg), len(xg))


def localize_tfm(fmc, t, x_el, velocity_mps):
    """全板 TFM 峰值定位, 返回 (x_mm, z_mm, 峰值对比度)。"""
    xg = np.arange(0.0, 90.5, 0.5)
    zg = np.arange(1.5, 40.0, 0.5)
    img = tfm_image(fmc, t, x_el, xg, zg, velocity_mps)
    p = np.unravel_index(np.argmax(img), img.shape)
    x_mm = float(xg[p[1]])
    z_mm = float(zg[p[0]])
    peak = float(img[p])
    contrast = peak / (float(np.median(img)) + 1e-30)
    return x_mm, z_mm, contrast


# ---------------------------------------------------------------- 特征提取
def defect_scattering(fmc, t, x_el, xd, zd, velocity_mps):
    """实测散射矩阵 S(θin, θsc): 256 对回波能量按角域二维直方图累积。"""
    c_mm_us = velocity_mps / 1000.0
    dt_us = _dt_us(t)
    tof = _tof_us(x_el, xd, zd, c_mm_us)
    t_us = t * 1e6
    env = np.abs(hilbert(fmc, axis=2))                     # (16,16,n_t)
    n_t = fmc.shape[2]
    E = np.zeros((N_EL, N_EL))
    t0 = t_us[0]
    for i in range(N_EL):
        for j in range(N_EL):
            lo = int(np.clip((tof[i, j] - W_US - t0) / dt_us, 0, n_t - 1))
            hi = int(np.clip((tof[i, j] + W_US - t0) / dt_us, 0, n_t))
            if hi > lo:
                E[i, j] = np.sum(env[i, j, lo:hi] ** 2)
    # 每个阵元相对缺陷的射线角(自竖直向下法线计)。由互易性, 同一阵元
    # "发"与"收"的射线角相等; 对 (tx=i, rx=j): θin 取 i, θsc 取 j。
    th = np.degrees(np.arctan((x_el - xd) / max(zd, 0.5)))
    th_in = np.broadcast_to(th[:, None], (N_EL, N_EL)).ravel()   # (i,j)->θ(i)
    th_sc = np.broadcast_to(th[None, :], (N_EL, N_EL)).ravel()   # (i,j)->θ(j)
    H, _, _ = np.histogram2d(th_in, th_sc, bins=NB,
                             range=[[-70, 70], [-70, 70]],
                             weights=E.ravel())
    S = H / max(H.sum(), 1e-30)
    row_sum = S.sum(axis=1, keepdims=True)
    P = np.where(row_sum > 0, S / np.maximum(row_sum, 1e-30), 0.0)
    return S, P, row_sum.ravel(), E


def ridge_features(S, P, w):
    """对每个入射角取主导散射角, 加权拟合镜面脊线 θsc = s·θin + o。"""
    xs, ys, ws = [], [], []
    for a in range(NB):
        if w[a] <= 1e-12:
            continue
        b = int(np.argmax(P[a]))
        xs.append(BIN_CENTERS[a])
        ys.append(BIN_CENTERS[b])
        ws.append(w[a])
    xs, ys, ws = map(np.asarray, (xs, ys, ws))
    if len(xs) < 3:
        return 0.0, 0.0, 0.0, 0.0, 1.0
    A = np.vstack([xs, np.ones_like(xs)]).T
    coef, *_ = np.linalg.lstsq(A * ws[:, None], ys * ws, rcond=None)
    s, o = coef
    resid = ys - (s * xs + o)
    ss_tot = np.sum(ws * (ys - np.average(ys, weights=ws)) ** 2)
    r2 = 1 - np.sum(ws * resid ** 2) / max(ss_tot, 1e-30)
    sharp = 0.0
    for a in range(NB):
        if w[a] > 1e-12:
            sharp += w[a] * P[a, int(np.argmax(P[a]))]
    sharp /= max(np.sum(w), 1e-30)
    off = 0.0
    for a in range(NB):
        for b in range(NB):
            if abs(BIN_CENTERS[b] - (s * BIN_CENTERS[a] + o)) > 10:
                off += S[a, b]
    return float(s), float(o), float(r2), float(sharp), float(off)


def entropy(p):
    p = np.asarray(p)
    p = p[p > 0]
    if p.size == 0:
        return 0.0
    return float(-np.sum(p * np.log(p)) / math.log(NB * NB))


def spectral_features(fmc, t, tof, dt_us):
    """各收发对缺陷回波(TOF 对齐后)的平均功率谱特征。"""
    t_us = t * 1e6
    n_t = fmc.shape[2]
    nw = int(2 * W_US / dt_us) + 1
    spec = np.zeros(nw // 2 + 1)
    cnt = 0
    for i in range(N_EL):
        for j in range(N_EL):
            lo = int(np.clip((tof[i, j] - W_US - t_us[0]) / dt_us,
                             0, n_t - 1))
            hi = lo + nw
            if hi > n_t:
                continue
            sig = fmc[i, j, lo:hi]
            if np.abs(sig).max() < 1e-12:
                continue
            fft = np.abs(np.fft.rfft(sig)) ** 2
            spec[:len(fft)] += fft
            cnt += 1
    if cnt == 0 or spec.sum() < 1e-30:
        return 0.0, 0.0, 1.0
    spec /= cnt
    f = np.fft.rfftfreq(nw, d=dt_us * 1e-6) / 1e6           # MHz
    spec = spec[:len(f)]
    c = float(np.sum(f * spec) / np.sum(spec))
    bw = float(np.sqrt(np.sum((f - c) ** 2 * spec) / np.sum(spec)))
    lf = float(np.sum(spec[f < 1.5]))
    hf = float(np.sum(spec[(f >= 1.5) & (f <= 4.5)]))
    return c, bw, lf / max(hf, 1e-30)


def echo_shape(fmc, t, tof, dt_us):
    """各对对齐回波包络的均值 -> FWHM 时长 (us) 与偏度。"""
    t_us = t * 1e6
    n_t = fmc.shape[2]
    nw = int(2.4 / dt_us) + 1
    stack = np.zeros(nw)
    cnt = 0
    for i in range(N_EL):
        env = np.abs(hilbert(fmc[i], axis=1))
        for j in range(N_EL):
            lo = int(np.clip((tof[i, j] - 1.2 - t_us[0]) / dt_us,
                             0, n_t - 1))
            if lo + nw > n_t:
                continue
            stack += env[j, lo:lo + nw]
            cnt += 1
    if cnt == 0 or stack.max() < 1e-30:
        return 0.0, 0.0
    stack /= cnt
    half = stack.max() / 2
    above = np.nonzero(stack > half)[0]
    dur = (above[-1] - above[0] + 1) * dt_us if len(above) else 0.0
    m = stack.mean()
    sd = stack.std() + 1e-30
    skew = float(np.mean(((stack - m) / sd) ** 3))
    return float(dur), skew


def compute_features(fmc, t, x_el, xd, zd, velocity_mps):
    """提取与训练一致的 17 个物理特征, 返回 {特征名: 值}。"""
    c_mm_us = velocity_mps / 1000.0
    dt_us = _dt_us(t)
    tof = _tof_us(x_el, xd, zd, c_mm_us)

    S, P, w, _ = defect_scattering(fmc, t, x_el, xd, zd, velocity_mps)
    s, o, r2, sharp, off = ridge_features(S, P, w)
    fc_, bw, lfhf = spectral_features(fmc, t, tof, dt_us)
    dur, skew = echo_shape(fmc, t, tof, dt_us)

    # TFM 亮斑形态(局部网格, 与训练相同的网格/阈值)
    xg = np.arange(max(0.0, xd - 20.0), min(90.0, xd + 20.0) + 0.5, 0.5)
    zg = np.arange(1.5, min(39.5, zd + 15.0), 0.5)
    img = tfm_image(fmc, t, x_el, xg, zg, velocity_mps)
    pk = np.unravel_index(np.argmax(img), img.shape)
    peak = float(img[pk])
    aspect, orient, tsharp = 1.0, 0.0, 0.0
    if peak > 1e-30:
        z0, x0 = pk
        dz = int(10 / (zg[1] - zg[0]))
        dx = int(10 / (xg[1] - xg[0]))
        zl, zh = max(0, z0 - dz), min(len(zg), z0 + dz + 1)
        xl, xh = max(0, x0 - dx), min(len(xg), x0 + dx + 1)
        sub = img[zl:zh, xl:xh]
        ys, xs = np.nonzero(sub > 0.5 * peak)
        if len(ys) >= 4:
            ev, evec = np.linalg.eigh(np.cov(xs, ys))
            s1, s2 = np.sqrt(np.maximum(ev, 1e-6))
            aspect = max(s1, s2) / min(s1, s2)
            major = evec[:, np.argmax(ev)]
            orient = abs(((np.degrees(np.arctan2(major[1], major[0])) + 90)
                          % 180) - 90)
            tsharp = peak / (np.median(sub) + 1e-30)

    sc_marg = P.sum(axis=0)
    sc_marg = sc_marg / max(sc_marg.sum(), 1e-30)
    back = float(S[np.abs(BIN_CENTERS[:, None] - BIN_CENTERS[None, :])
                   < 10].sum())
    near = float(S[np.abs(BIN_CENTERS) < 10, :].sum())

    vals = [entropy(S), entropy(sc_marg), s, o, r2, sharp, off,
            back, near, fc_, bw, lfhf, dur, skew,
            float(aspect), float(orient), float(tsharp)]
    return dict(zip(PHYS_FEATURE_NAMES, vals))


# ---------------------------------------------------------------- 统一入口
def _empty_result():
    return {
        "ok": False,
        "model_name": None,
        "model_version": None,
        "input_shape": None,
        "localization": {"method": None, "x_mm": None, "z_mm": None,
                         "confidence": None, "warning": ""},
        "prediction": {"label": None, "label_cn": None, "confidence": None,
                       "probabilities": {}, "recommended_review": False},
        "features": {"feature_names": [], "feature_values": []},
        "explanation": {"top_features": [], "physics_basis": PHYSICS_BASIS},
        "warnings": [],
        "errors": [],
    }


def predict_fmc_case(fmc, t, x_el, candidate_x_mm=None, candidate_z_mm=None,
                     velocity_mps=6200.0, model_path=None,
                     confidence_threshold=DEFAULT_CONFIDENCE_THRESHOLD):
    """统一推理入口(见模块 docstring 的返回结构)。"""
    result = _empty_result()
    errors, warnings = result["errors"], result["warnings"]

    # ---- 1. 输入校验 ----
    try:
        fmc_arr = np.asarray(fmc, dtype=np.float64)
    except Exception as e:
        errors.append(f"fmc 无法转为数值数组: {e}")
        return result

    if fmc_arr.ndim != 3:
        errors.append(f"fmc 必须是三维 (16,16,n_t), 实际 ndim={fmc_arr.ndim}")
    else:
        result["input_shape"] = [int(fmc_arr.shape[0]), int(fmc_arr.shape[1]),
                                 int(fmc_arr.shape[2])]
        if fmc_arr.shape[0] != N_EL or fmc_arr.shape[1] != N_EL:
            errors.append(f"fmc 前两维必须是 {N_EL}x{N_EL}, "
                          f"实际 {fmc_arr.shape[0]}x{fmc_arr.shape[1]}")
        if fmc_arr.shape[2] < 2:
            errors.append(f"fmc 时间采样点过少: {fmc_arr.shape[2]}")

    t_arr = np.asarray(t, dtype=np.float64)
    if t_arr.ndim != 1:
        errors.append(f"t 必须是一维, 实际 ndim={t_arr.ndim}")
    elif fmc_arr.ndim == 3 and t_arr.shape[0] != fmc_arr.shape[2]:
        errors.append(f"t 长度 {t_arr.shape[0]} 与 fmc 时间维 "
                      f"{fmc_arr.shape[2]} 不匹配")

    x_el_arr = np.asarray(x_el, dtype=np.float64)
    if x_el_arr.ndim != 1 or x_el_arr.shape[0] != N_EL:
        errors.append(f"x_el 必须是 {N_EL} 个元素的数组, "
                      f"实际 shape={x_el_arr.shape}")

    for name, a in (("fmc", fmc_arr), ("t", t_arr), ("x_el", x_el_arr)):
        if a.size and not np.isfinite(a).all():
            errors.append(f"{name} 含 NaN/Inf")

    try:
        velocity_mps = float(velocity_mps)
    except Exception:
        velocity_mps = np.nan
    if not np.isfinite(velocity_mps) or velocity_mps <= 0:
        errors.append(f"velocity_mps 必须为正有限数, 实际 {velocity_mps}")

    # 候选坐标成对出现 + 数值化 + 范围
    if (candidate_x_mm is None) != (candidate_z_mm is None):
        errors.append("candidate_x_mm 与 candidate_z_mm 必须同时提供或同时省略")
    elif candidate_x_mm is not None:
        try:
            cx, cz = float(candidate_x_mm), float(candidate_z_mm)
        except Exception:
            errors.append("候选坐标必须是数值")
        else:
            if not (0.0 <= cx <= 100.0):
                warnings.append(f"候选 x={cx} mm 超出 [0,100] 范围")
            if not (0.0 <= cz <= 40.0):
                warnings.append(f"候选 z={cz} mm 超出板厚 [0,40] 范围")

    if errors:
        return result

    # ---- 2. 单位/采样率检查(非致命警告) ----
    if t_arr.size >= 2:
        t_span = float(t_arr[-1] - t_arr[0])
        if not (1e-6 < t_span < 1e-3):
            warnings.append(f"时间轴跨度 {t_span:.3g} s 疑似不是秒"
                            f"(751点@20ns 应约为 1.5e-5 s), 请确认单位")
        dt_s = _dt_us(t_arr) * 1e-6
        if abs(dt_s - DT_REF_S) > 5e-9:
            warnings.append(f"采样间隔 {dt_s * 1e9:.1f} ns 非训练值 20 ns, "
                            f"结果可能偏差")

    # ---- 3. 加载模型 ----
    mp = Path(model_path) if model_path else DEFAULT_MODEL_PATH
    if not mp.exists():
        errors.append(f"模型文件不存在: {mp}")
        return result
    try:
        model = joblib.load(str(mp))
    except Exception as e:
        errors.append(f"模型加载失败: {e}")
        return result
    if not isinstance(model, dict) or "pipeline" not in model:
        errors.append("模型文件格式不支持: 需 dict 且含 'pipeline' 键")
        return result

    result["model_name"] = model.get("model_name", mp.stem)
    result["model_version"] = model.get("model_version", "unknown")
    model_classes = model.get("classes", CLASSES)
    model_cn = model.get("class_names_cn", CLASS_CN)
    model_feat_names = list(model.get("feature_names", PHYS_FEATURE_NAMES))

    # ---- 4. 定位 ----
    if candidate_x_mm is not None:
        xd, zd = cx, cz
        result["localization"] = {
            "method": "provided_candidate", "x_mm": xd, "z_mm": zd,
            "confidence": None, "warning": "",
        }
    else:
        try:
            xd, zd, _ = localize_tfm(fmc_arr, t_arr, x_el_arr, velocity_mps)
        except Exception as e:
            errors.append(f"自动 TFM 定位失败: {e}")
            return result
        result["localization"] = {
            "method": "tfm_peak", "x_mm": xd, "z_mm": zd,
            "confidence": None,
            "warning": "自动定位结果仅作为候选点,建议人工复核",
        }

    # ---- 5. 特征提取 + 模型推理 ----
    try:
        feat = compute_features(fmc_arr, t_arr, x_el_arr, xd, zd,
                                velocity_mps)
    except Exception as e:
        errors.append(f"特征提取失败: {e}")
        return result

    try:
        vec = np.array([[feat[n] for n in model_feat_names]])
        pipe = model["pipeline"]
        pred = int(pipe.predict(vec)[0])
        if hasattr(pipe, "predict_proba"):
            probs = np.asarray(pipe.predict_proba(vec)[0], dtype=float)
        else:
            d = np.asarray(pipe.decision_function(vec))[0]
            e = np.exp(d - d.max())
            probs = e / e.sum()
    except Exception as e:
        errors.append(f"模型推理失败: {e}")
        return result

    if len(probs) != len(model_classes):
        errors.append(f"模型输出 {len(probs)} 类与元数据 {len(model_classes)} "
                      f"类不一致")
        return result

    prob_dict = {cls: float(round(p, 4))
                 for cls, p in zip(model_classes, probs)}
    confidence = float(probs[pred])

    # ---- 6. 低置信度处理 ----
    try:
        threshold = float(confidence_threshold)
    except Exception:
        threshold = DEFAULT_CONFIDENCE_THRESHOLD
    if confidence < threshold:
        label, label_cn, review = "uncertain", "不确定", True
        warnings.append(f"最高置信度 {confidence:.2f} 低于阈值 {threshold:.2f}, "
                        f"不强行定型, 建议人工复核")
    else:
        label = model_classes[pred]
        label_cn = model_cn.get(label, label)
        review = False

    result["prediction"] = {
        "label": label,
        "label_cn": label_cn,
        "confidence": confidence,
        "probabilities": prob_dict,
        "recommended_review": review,
    }
    result["features"] = {
        "feature_names": list(model_feat_names),
        "feature_values": [float(feat[n]) for n in model_feat_names],
    }

    # ---- 7. 解释(按训练期特征重要性取 top) ----
    fi = model.get("feature_importance", [])
    top = []
    if fi:
        order = sorted(fi, key=lambda d: -d.get("score", 0.0))
        for d in order[:5]:
            name = d.get("name")
            if name in feat:
                top.append({"name": name, "value": float(feat[name]),
                            "importance": float(d.get("score", 0.0))})
    result["explanation"]["top_features"] = top

    result["ok"] = True
    result["warnings"] = warnings
    result["errors"] = errors
    return result
