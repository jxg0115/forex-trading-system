"""因子有效性评估器：IC（秩相关）、方向命中率、滚动 ICIR、按行情状态分组的敏感性（行情标签）。

与 MarketAnalysisEngine 同口径的行情标签：trend_up / trend_down / range（按 ADX + 均线方向），
high_vol / low_vol（按 ATR 分位）。出场因子用\"出场后价格反向\"评估（IC 越负越好，报告负向 IC）。
"""

from __future__ import annotations

import math
import warnings
from statistics import NormalDist
from typing import Any

import numpy as np
import pandas as pd

from indicators.technical import adx_series, atr_series, ema  # ema 若存在；不存在则内联

try:  # indicators.technical 提供的均线函数名可能为 sma/ema
    from indicators.technical import ema
except ImportError:
    def ema(close: pd.Series, n: int) -> pd.Series:
        return close.ewm(span=n, adjust=False).mean()


def market_tags(df: pd.DataFrame) -> pd.Series:
    """为每根 K 线打行情标签（与行情过滤门同口径的简化版）。"""
    close = df["close"].astype(float)
    adx = adx_series(df, 14).fillna(0.0)
    atr = atr_series(df).fillna(0.0)
    vol_pct = atr.rank(pct=True)
    tags: list[str] = []
    sma50 = ema(close, 50)
    for i in range(len(df)):
        a = float(adx.iloc[i])
        vp = float(vol_pct.iloc[i])
        if vp >= 0.75:
            tags.append("high_vol")
        elif vp <= 0.25:
            tags.append("low_vol")
        elif a >= 25.0 and float(close.iloc[i]) >= float(sma50.iloc[i]):
            tags.append("trend_up")
        elif a >= 25.0:
            tags.append("trend_down")
        else:
            tags.append("range")
    return pd.Series(tags, index=df.index)


def _rank_ic(signal: pd.Series, future_ret: pd.Series) -> float:
    pair = pd.concat([signal.rank(), future_ret.rank()], axis=1).dropna()
    if len(pair) < 20:
        return 0.0
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=RuntimeWarning)  # 常量列导致 numpy stddev=0 的噪音
        return float(pair.iloc[:, 0].corr(pair.iloc[:, 1]))


def _hit_rate(signal: pd.Series, future_ret: pd.Series) -> float:
    pos = (signal > 0) & future_ret.notna()
    if pos.sum() == 0:
        return 0.0
    return float((future_ret[pos] > 0).mean())


def _rolling_ic(signal: pd.Series, future_ret: pd.Series, window: int = 60) -> pd.Series:
    ranks = pd.concat([signal.rank(), future_ret.rank()], axis=1).dropna()
    if len(ranks) < window:
        return pd.Series(dtype=float)
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=RuntimeWarning)  # 全 NaN 窗口的 numpy 噪音
        ic = ranks.iloc[:, 0].rolling(window).corr(ranks.iloc[:, 1])
    return ic


def walk_forward(
    signal: pd.Series,
    df: pd.DataFrame,
    forward_bars: int = 5,
    kind: str = "entry",
    segments: int = 4,
) -> dict[str, Any]:
    """walk-forward 滚动分段一致性：时间等分 segments 段、逐段评估。

    按 score 符号统计「有利段数」：稳定（≥75% 段同向）/ 一般（≥50%）/ 不稳（<50%）。
    供候选标注多段一致性，防单段偶然成立的假阳性。
    """
    n = len(df)
    seg_cnt = segments if n >= segments * 30 else max(1, n // 30)
    step = max(n // seg_cnt, 1)
    seg_ic: list[float] = []
    seg_hit: list[float] = []
    seg_sig: list[int] = []
    seg_score: list[float] = []
    for i in range(seg_cnt):
        end = (i + 1) * step if i < seg_cnt - 1 else n
        seg_df = df.iloc[i * step : end]
        if len(seg_df) < 20:
            continue
        seg_eval = evaluate_signal(signal.reindex(seg_df.index), seg_df, forward_bars=forward_bars, kind=kind)
        seg_ic.append(seg_eval["ic"])
        seg_hit.append(seg_eval["hit_rate"])
        seg_sig.append(seg_eval["signal_bars"])
        seg_score.append(seg_eval["score"])
    if not seg_score:
        return {"segments": seg_cnt, "ic": [], "hit_rate": [], "signal_bars": [], "positive": 0, "level": "不稳"}
    positive = sum(1 for s in seg_score if s > 0)
    half = math.ceil(len(seg_score) * 0.5)
    strong = math.ceil(len(seg_score) * 0.75)
    level = "稳定" if positive >= strong else ("一般" if positive >= half else "不稳")
    return {
        "segments": seg_cnt,
        "ic": [round(x, 4) for x in seg_ic],
        "hit_rate": [round(x, 4) for x in seg_hit],
        "signal_bars": seg_sig,
        "positive": positive,
        "level": level,
    }


def evaluate_signal(signal: pd.Series, df: pd.DataFrame, forward_bars: int = 5, kind: str = "entry") -> dict[str, Any]:
    """评估信号有效性：IC/命中率/滚动 ICIR/行情分组敏感性。

    入场（entry）：正向 IC（信号越好、未来收益越高）；
    出场（exit）：负向 IC 为好（出场后价格反向），报告为 exit_ic（原始负值）与 score=-ic。
    """
    close = df["close"].astype(float)
    future_ret = close.shift(-forward_bars) / close - 1.0
    sig = signal.reindex(df.index).fillna(0.0)

    ic = _rank_ic(sig, future_ret)
    hit = _hit_rate(sig, future_ret)
    rank_pair = pd.concat([sig.rank(), future_ret.rank()], axis=1).dropna()
    n_pairs = len(rank_pair)
    ic_tstat = 0.0
    if n_pairs > 2 and abs(ic) < 1.0:
        ic_tstat = ic * math.sqrt(max(n_pairs - 2, 0)) / math.sqrt(max(1.0 - ic * ic, 1e-9))
    ic_series = _rolling_ic(sig, future_ret, 60)
    icir = float(ic_series.mean() / ic_series.std()) if len(ic_series.dropna()) > 20 else 0.0

    tags = market_tags(df)
    groups: dict[str, dict[str, float]] = {}
    for tag in sorted(set(tags)):
        mask = tags == tag
        if mask.sum() < 20:
            continue
        g_ic = _rank_ic(sig[mask], future_ret[mask])
        g_hit = _hit_rate(sig[mask], future_ret[mask])
        groups[tag] = {"ic": round(g_ic, 4), "hit_rate": round(g_hit, 4), "bars": int(mask.sum())}

    if kind == "exit":
        score = -ic
    else:
        score = ic
    return {
        "kind": kind,
        "forward_bars": forward_bars,
        "ic": round(ic, 4),
        "exit_ic": round(ic, 4) if kind == "exit" else None,
        "icir": round(icir, 3),
        "ic_tstat": round(ic_tstat, 3),   # 整体 IC 的显著性 t 统计（|t|≥2 才谈得上有预测力）
        "n_pairs": n_pairs,                # 参与秩相关的有效样本对数
        "hit_rate": round(hit, 4),
        "score": round(score, 4),
        "signal_bars": int((sig != 0).sum()),
        "groups": groups,
    }


_EULER_GAMMA = 0.5772156649015329


def deflated_sharpe(pnl_pct: list[float], n_trials: int) -> float:
    """Bailey-López de Prado Deflated Sharpe Ratio（DSR）：考虑试验次数的夏普显著性概率（0~1）。

    把每笔收益当作样本序列估计夏普与三/四阶矩；基准用"N 次独立试验下的期望最大夏普"。
    DSR 高（如 ≥0.95）说明该因子不是多重检验下的随机幸存者。
    """
    if not pnl_pct or len(pnl_pct) < 3:
        return 0.5
    arr = pd.Series([float(v) for v in pnl_pct]).replace([None], 0.0)
    if arr.std(ddof=1) <= 0:
        return 0.5
    sr = float(arr.mean() / arr.std(ddof=1))
    n = len(arr)
    g3 = float(arr.skew())
    g4 = float(arr.kurt())  # pandas kurt 是超额峰度
    denom = max(1.0 - g3 * sr + (g4) / 4.0 * sr * sr, 1e-9)
    n_tr = max(int(n_trials), 1)
    v_sr = 1.0 / n
    z1 = NormalDist().inv_cdf(1.0 - 1.0 / n_tr)
    z2 = NormalDist().inv_cdf(1.0 - 1.0 / (n_tr * math.e))
    sr0 = math.sqrt(v_sr) * ((1.0 - _EULER_GAMMA) * z1 + _EULER_GAMMA * z2)
    z = (sr - sr0) * math.sqrt(n - 1.0) / math.sqrt(denom)
    return float(NormalDist().cdf(z))


def harvey_t_required(n_trials: int, alpha: float = 0.05) -> float:
    """Harvey-Liu-Zhu（2016）多重检验修正的常规 t 门槛（Bonferroni 单边）。

    一次挖掘 N 个候选时，单个因子的 |t| 需超过该值才不算随机幸存者。
    """
    n_tr = max(int(n_trials), 1)
    return float(NormalDist().inv_cdf(1.0 - alpha / n_tr))


def t_ratio(ic_tstat: float, n_trials: int, alpha: float = 0.05) -> float:
    """候选 t 与修正门槛的比值（>1 表示通过多重检验修正）。"""
    crit = harvey_t_required(n_trials, alpha)
    return float(ic_tstat) / crit if crit else 0.0