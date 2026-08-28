"""技术指标与特征注册表：MA、RSI、MACD、ATR、ADX、布林带。"""

from __future__ import annotations

from typing import Callable

import numpy as np
import pandas as pd


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100.0 - 100.0 / (1.0 + rs)


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal_period: int = 9) -> tuple[pd.Series, pd.Series, pd.Series]:
    line = ema(series, fast) - ema(series, slow)
    signal = ema(line, signal_period)
    histogram = line - signal
    return line, signal, histogram


def bollinger(series: pd.Series, period: int = 20, k: float = 2.0) -> tuple[pd.Series, pd.Series, pd.Series]:
    mid = sma(series, period)
    std = series.rolling(period).std()
    return mid + k * std, mid, mid - k * std


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    return pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)


def atr_series(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return true_range(df).ewm(alpha=1.0 / period, adjust=False).mean()


def adx_series(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["high"]
    low = df["low"]
    close = df["close"]
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index)
    tr_s = true_range(df).ewm(alpha=1.0 / period, adjust=False).mean()
    plus_s = plus_dm.ewm(alpha=1.0 / period, adjust=False).mean()
    minus_s = minus_dm.ewm(alpha=1.0 / period, adjust=False).mean()
    plus_di = 100.0 * plus_s / tr_s.replace(0, np.nan)
    minus_di = 100.0 * minus_s / tr_s.replace(0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1.0 / period, adjust=False).mean().fillna(0.0)


def compute_features(df: pd.DataFrame) -> pd.DataFrame:
    """计算统一特征矩阵，供图表、AI 决策与指标 API 使用。"""

    close = df["close"]
    macd_line, macd_signal, macd_hist = macd(close)
    bb_upper, bb_mid, bb_lower = bollinger(close)
    features = {
        "close": close,
        "sma_10": sma(close, 10),
        "sma_20": sma(close, 20),
        "ema_12": ema(close, 12),
        "ema_26": ema(close, 26),
        "rsi_14": rsi(close, 14),
        "macd_line": macd_line,
        "macd_signal": macd_signal,
        "macd_hist": macd_hist,
        "atr_14": atr_series(df, 14),
        "adx_14": adx_series(df, 14),
        "bollinger_upper": bb_upper,
        "bollinger_mid": bb_mid,
        "bollinger_lower": bb_lower,
    }
    return pd.DataFrame(features, index=df.index)


FEATURE_REGISTRY: dict[str, Callable[[pd.DataFrame], pd.Series]] = {
    "sma_10": lambda df: sma(df["close"], 10),
    "sma_20": lambda df: sma(df["close"], 20),
    "ema_12": lambda df: ema(df["close"], 12),
    "ema_26": lambda df: ema(df["close"], 26),
    "rsi_14": lambda df: rsi(df["close"], 14),
    "atr_14": lambda df: atr_series(df, 14),
    "adx_14": lambda df: adx_series(df, 14),
}

