"""因子参数自动优化引擎测试。"""

from types import SimpleNamespace

import pandas as pd

from backtest_store.optimizer import FactorOptimizer, OptimizationConfig

CODE = """\
import pandas as pd

def calculate(df, params):
    lookback = int(params.get("lookback", 10))
    close = df["close"]
    high = df["high"].rolling(lookback).max().shift(1)
    low = df["low"].rolling(lookback).min().shift(1)
    entry = pd.Series(0.0, index=df.index)
    entry.loc[close > high] = 1.0
    entry.loc[close < low] = -1.0
    return {"entry": entry}
"""


class FakeMarket:
    def get_bars(self, symbol, timeframe, limit=300):
        bars = []
        price = 100.0
        for i in range(limit):
            price *= 1.005 if i % 3 == 0 else 0.998
            bars.append(
                {
                    "time": f"2026-01-01T00:{i % 60:02d}:00Z",
                    "open": round(price, 2),
                    "high": round(price * 1.002, 2),
                    "low": round(price * 0.998, 2),
                    "close": round(price, 2),
                    "volume": 1000.0,
                }
            )
        return bars


def test_optimizer_returns_best_params():
    optimizer = FactorOptimizer(FakeMarket())
    config = OptimizationConfig(
        symbol="EURUSD",
        timeframe="M15",
        bars=200,
        max_iterations=6,
        early_stop_rounds=2,
        param_ranges={"lookback": {"min": 5, "max": 20, "step": 5}},
    )
    result = optimizer.optimize(CODE, {"lookback": 10}, config)
    assert result["ok"] is True
    assert "best_params" in result
    assert len(result["trials"]) > 0
    assert result["best_params"]["lookback"] in (5, 10, 15, 20)


def test_optimizer_auto_ranges():
    optimizer = FactorOptimizer(FakeMarket())
    config = OptimizationConfig(bars=200, max_iterations=4, early_stop_rounds=2)
    result = optimizer.optimize(CODE, {"lookback": 10}, config)
    assert result["ok"] is True
    assert len(result["trials"]) >= 1


def test_optimizer_targets_not_reached():
    optimizer = FactorOptimizer(FakeMarket())
    config = OptimizationConfig(
        bars=200,
        max_iterations=4,
        early_stop_rounds=2,
        target_win_rate_pct=99,
        target_total_return_pct=9999,
    )
    result = optimizer.optimize(CODE, {"lookback": 10}, config)
    assert result["targets_reached"] is False
    assert "未找到" in result["message"]


def test_optimizer_targets_reached_without_constraints():
    optimizer = FactorOptimizer(FakeMarket())
    config = OptimizationConfig(bars=200, max_iterations=4, early_stop_rounds=2)
    result = optimizer.optimize(CODE, {"lookback": 10}, config)
    assert result["targets_reached"] is True


def test_optimizer_walk_forward_returns_test_metrics():
    optimizer = FactorOptimizer(FakeMarket())
    config = OptimizationConfig(bars=200, max_iterations=4, early_stop_rounds=2, walk_forward=True)
    result = optimizer.optimize(CODE, {"lookback": 10}, config)
    assert "test_metrics" in result
    assert result["test_metrics"] is not None
    assert "walk_forward_metrics" in result
    assert len(result["walk_forward_metrics"]) >= 1


def test_min_trades_marks_not_reached():
    optimizer = FactorOptimizer(FakeMarket())
    config = OptimizationConfig(bars=200, max_iterations=4, early_stop_rounds=2, min_trades=999)
    result = optimizer.optimize(CODE, {"lookback": 10}, config)
    assert result["targets_reached"] is False
    assert "低于最低要求" in result["message"]


def test_complexity_penalty_lowers_score():
    metrics = SimpleNamespace(
        win_rate_pct=50.0,
        profit_factor=1.0,
        total_return_pct=5.0,
        sharpe=0.5,
        max_drawdown_pct=10.0,
    )
    score_simple = FactorOptimizer._score(metrics, "composite", param_count=0, complexity_penalty=0.01)
    score_complex = FactorOptimizer._score(metrics, "composite", param_count=5, complexity_penalty=0.01)
    assert score_simple > score_complex


def test_local_beam_keeps_params_close_to_original():
    optimizer = FactorOptimizer(FakeMarket())
    config = OptimizationConfig(
        bars=120,
        max_iterations=20,
        early_stop_rounds=3,
        max_deviation_pct=10.0,
        fidelity_weight=0.5,
        beam_width=4,
        search_rounds=2,
        deviation_levels=(0.9, 1.0, 1.1),
    )
    result = optimizer.optimize(CODE, {"lookback": 10}, config)
    assert result["ok"] is True
    assert "fidelity" in result
    best = result["best_params"]["lookback"]
    assert 9 <= best <= 11


def test_fidelity_prefers_preserved_signals():
    index = pd.date_range("2026-01-01", periods=5, freq="min")
    original = pd.Series([0.0, 1.0, 0.0, 1.0, 0.0], index=index)
    same = original.copy()
    different = pd.Series([1.0, 0.0, 1.0, 0.0, 0.0], index=index)
    empty = pd.Series([0.0, 0.0, 0.0, 0.0, 0.0], index=index)
    assert FactorOptimizer._fidelity(original, same) == 1.0
    assert FactorOptimizer._fidelity(original, different) < 1.0
    assert FactorOptimizer._fidelity(original, empty) == 0.0


def test_combined_score_respects_fidelity_weight():
    high_fidelity = FactorOptimizer._combined_score(0.5, 1.0, 0.8)
    low_fidelity = FactorOptimizer._combined_score(0.5, 0.0, 0.8)
    assert high_fidelity > low_fidelity
    assert round(FactorOptimizer._combined_score(0.5, 0.5, 0.5), 6) == 0.5
