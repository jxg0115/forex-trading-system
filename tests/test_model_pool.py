"""板块四：ML 模型池训练与预测测试。"""

import numpy as np
import pandas as pd

from ai_engine.model_pool import ModelPool


def _trend_frame(n=200, drift=0.0005, seed=3):
    rng = np.random.default_rng(seed)
    closes = 1.0 + np.cumsum(rng.normal(drift, 0.001, n))
    return pd.DataFrame(
        {
            "open": closes,
            "high": closes * 1.002,
            "low": closes * 0.998,
            "close": closes,
            "volume": [1000.0] * n,
        }
    )


def test_model_pool_list_and_train():
    pool = ModelPool()
    names = [m["name"] for m in pool.list_models()]
    assert "logistic_momentum" in names
    assert "threshold_momentum" in names

    result = pool.train("logistic_momentum", _trend_frame())
    assert result["metrics"]["samples"] > 30
    assert "logistic_momentum" in pool.trained


def test_model_pool_predict_signal():
    pool = ModelPool()
    df = _trend_frame()
    pool.train("logistic_momentum", df)
    prediction = pool.predict("logistic_momentum", df)
    assert prediction["model"] == "logistic_momentum"
    assert prediction["trained"] is True
    assert prediction["signal"] in ("long", "short", "none")
    assert 0.0 <= prediction["confidence"] <= 1.0


def test_threshold_model_train_predict():
    pool = ModelPool()
    df = _trend_frame()
    pool.train("threshold_momentum", df)
    prediction = pool.predict("threshold_momentum", df)
    assert "z_score" in prediction

