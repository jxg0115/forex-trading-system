"""市场环境分类：ATR 分位、ADX 趋势强度、波动扩张。"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from indicators.technical import adx_series, atr_series


@dataclass
class MarketRegime:
    name: str
    trend_score: float
    atr_percentile: float
    volatility: str
    detail: str


def classify(df: pd.DataFrame) -> MarketRegime:
    """基于最近 120 根 K 线给出市场环境分类。"""

    window = df.tail(120)
    close = window["close"]
    atr = atr_series(window, 14).dropna()
    atr_now = float(atr.iloc[-1]) if len(atr) else 0.0
    atr_percentile = float((atr <= atr_now).mean()) * 100.0 if len(atr) else 50.0

    adx = adx_series(window, 14)
    adx_now = float(adx.iloc[-1]) if len(adx) else 0.0
    ema_fast = close.ewm(span=10, adjust=False).mean()
    ema_slow = close.ewm(span=50, adjust=False).mean()
    with np.errstate(invalid="ignore", divide="ignore"):
        slope = (ema_fast.iloc[-1] - ema_fast.iloc[-20]) / ema_fast.iloc[-20] * 1000.0 if len(ema_fast) >= 20 else 0.0

    if atr_percentile >= 75:
        volatility = "高波动"
    elif atr_percentile >= 40:
        volatility = "中等波动"
    else:
        volatility = "低波动"

    if adx_now >= 25 and abs(slope) > 0.3:
        name = "强趋势" + ("上行" if slope > 0 else "下行")
        detail = f"ADX {adx_now:.1f}，均线斜率 {slope:.2f}，适合趋势跟踪因子"
    elif adx_now >= 20:
        name = "弱趋势"
        detail = f"ADX {adx_now:.1f}，趋势尚不稳固，建议减少追价"
    else:
        name = "震荡"
        detail = f"ADX {adx_now:.1f}，区间特征明显，适合均值回归因子"

    return MarketRegime(
        name=name,
        trend_score=round(float(np.clip(adx_now / 40.0, 0.0, 1.0)), 3),
        atr_percentile=round(atr_percentile, 1),
        volatility=volatility,
        detail=detail,
    )
