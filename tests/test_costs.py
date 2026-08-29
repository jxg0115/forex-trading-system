"""阶段三成本口径中心单测：品种默认、merge 兜底（显式优先）、滑点计算。"""

from __future__ import annotations

import pytest

from backtest_store.costs import DEFAULT_COSTS, calc_slippage, cost_defaults, merge_costs


def test_cost_defaults_gold():
    d = cost_defaults("GOLD")
    assert d["spread_points"] == 10.0
    assert d["slippage_price"] == 0.02
    assert d["commission_pct"] == 0.0001


def test_cost_defaults_fallback():
    d = cost_defaults("EURUSD")
    assert d["spread_points"] == 0.0
    assert d["commission_pct"] == 0.0001
    # 未知品种走 default 档
    assert cost_defaults("UNKNOWN_SYM") == DEFAULT_COSTS["default"]


def test_merge_costs_defaults_only_missing():
    merged = merge_costs({"symbol": "GOLD"}, "GOLD")
    assert merged["spread_points"] == 10.0
    assert merged["slippage_price"] == 0.02


def test_merge_costs_explicit_wins():
    merged = merge_costs({"symbol": "GOLD", "spread_points": 0.0}, "GOLD")
    assert merged["spread_points"] == 0.0  # 显式传值优先


def test_calc_slippage_fixed():
    assert calc_slippage(0.02, 0.0, 1.5) == 0.02  # atr_mult 关闭 = 固定值


def test_calc_slippage_atr_takes_max():
    assert calc_slippage(0.02, 0.1, 1.5) == pytest.approx(0.15)  # max(0.02, 0.15)
    assert calc_slippage(0.05, 0.1, 0.1) == 0.05  # 固定值更大时取固定