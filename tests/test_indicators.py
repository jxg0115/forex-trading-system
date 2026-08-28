"""板块六：统一指标库测试。"""

import numpy as np
import pandas as pd

from indicators.technical import adx_series, atr_series, compute_features, macd, rsi, sma


def _frame(closes):
    n = len(closes)
    return pd.DataFrame(
        {
            "open": closes,
            "high": [c * 1.002 for c in closes],
            "low": [c * 0.998 for c in closes],
            "close": closes,
            "volume": [1000.0] * n,
        }
    )


def test_sma_and_macd_shapes():
    closes = list(np.linspace(1.0, 1.2, 60))
    df = _frame(closes)
    assert abs(sma(df["close"], 5).iloc[-1] - float(np.mean(closes[-5:]))) < 1e-9
    line, signal, hist = macd(df["close"])
    assert len(line) == 60
    assert len(signal) == 60
    assert len(hist) == 60


def test_rsi_bounds_after_warmup():
    rng = np.random.default_rng(7)
    closes = list(1.0 + np.cumsum(rng.normal(0, 0.001, 80)))
    values = rsi(pd.Series(closes), 14).dropna()
    assert ((values >= 0) & (values <= 100)).all()


def test_atr_and_adx_positive():
    rng = np.random.default_rng(11)
    closes = list(1.0 + np.cumsum(rng.normal(0, 0.001, 120)))
    df = _frame(closes)
    assert float(atr_series(df, 14).dropna().iloc[-1]) > 0
    assert float(adx_series(df, 14).dropna().iloc[-1]) >= 0


def test_compute_features_registry():
    closes = list(np.linspace(1.0, 1.1, 100))
    features = compute_features(_frame(closes))
    for column in ["sma_10", "ema_12", "rsi_14", "macd_line", "atr_14", "adx_14", "bollinger_upper"]:
        assert column in features.columns
    assert len(features) == 100

