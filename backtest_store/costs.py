"""回测成本口径中心（阶段三：统一引擎/验证/挖掘的成本默认）。

- DEFAULT_COSTS：按品种的成本默认（GOLD 点差 10 点、滑点 2 点；其余默认不计点差/滑点）；
- merge_costs：把品种默认补进 params（调用方显式传的值优先）；
- calc_slippage：固定滑点 + 可选 ATR 比例滑点（slippage_atr_mult，默认 0=关）。
"""

from __future__ import annotations

from typing import Any

# 品种成本默认（spread_points=点差（价格点）；slippage_price=单边滑点（价格）；commission_pct=单边佣金比例）
DEFAULT_COSTS: dict[str, dict[str, float]] = {
    "GOLD": {"spread_points": 10.0, "slippage_price": 0.02, "commission_pct": 0.0001},
    "default": {"spread_points": 0.0, "slippage_price": 0.0, "commission_pct": 0.0001},
}


def cost_defaults(symbol: str) -> dict[str, float]:
    """按品种取成本默认。"""
    return DEFAULT_COSTS.get(str(symbol).upper(), DEFAULT_COSTS["default"])


def merge_costs(params: dict[str, Any], symbol: str) -> dict[str, Any]:
    """把品种成本默认补进 params（显式传值优先；缺失才用默认）。"""
    out = {**params}
    defaults = cost_defaults(symbol)
    for key, val in defaults.items():
        if key not in out:
            out[key] = val
    return out


def calc_slippage(fixed_price: float, atr_mult: float, atr: float) -> float:
    """滑点：固定价与 ATR 比例两者取大（atr_mult<=0 时仅固定价）。"""
    fixed = max(float(fixed_price), 0.0)
    if atr_mult and atr_mult > 0:
        return max(fixed, float(atr_mult) * float(atr))
    return fixed