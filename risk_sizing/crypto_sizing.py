"""加密口径的仓位换算。

外汇用「手」+ pip_value：lots = 风险金额 / (止损点数 × pip_value)。
加密没有 pip，单位是**张**（衍生品，1 张 = ctVal 个标的币）或**币**（现货），
因此改为按「美元风险 ÷ (止损价格距离 × 合约面值)」直接反推张数。

三个硬约束必须依次施加，否则会被交易所拒单：
  lotSz 取整 → minSz 下限 → maxSz/maxMktSz 上限
"""

from __future__ import annotations

import math
from typing import Any


def _decimals_of(step: float) -> int:
    if step <= 0:
        return 8
    s = f"{step:.12f}".rstrip("0")
    return len(s.split(".")[1]) if "." in s else 0


def clamp_size(raw: float, lot_sz: float, min_sz: float, max_sz: float) -> float:
    """对齐步进并施加上下限；任一步失败都返回 0（上层应视为不可开仓）。"""
    if raw <= 0 or lot_sz <= 0:
        return 0.0
    steps = math.floor(raw / lot_sz + 1e-9)
    size = round(steps * lot_sz, _decimals_of(lot_sz))
    if min_sz > 0 and size < min_sz:
        # 不足最小量时向上取整到 minSz（否则永远开不了仓），但仍受 max 约束
        size = round(min_sz, _decimals_of(lot_sz))
    if max_sz > 0 and size > max_sz:
        size = round(math.floor(max_sz / lot_sz) * lot_sz, _decimals_of(lot_sz))
    return max(size, 0.0)


def calculate_contracts(
    equity: float,
    risk_percent: float,
    entry_price: float,
    stop_price: float,
    ct_val: float = 1.0,
    lot_sz: float = 1.0,
    min_sz: float = 0.0,
    max_sz: float = 0.0,
) -> float:
    """按风险预算反推张数（U 本位正向合约）。

    risk_usd = equity × risk%
    每张亏损 = |entry − stop| × ctVal
    张数     = risk_usd / 每张亏损
    """
    stop_dist = abs(float(entry_price) - float(stop_price))
    if stop_dist <= 0 or equity <= 0 or ct_val <= 0:
        return 0.0
    risk_usd = float(equity) * float(risk_percent) / 100.0
    raw = risk_usd / (stop_dist * ct_val)
    return clamp_size(raw, lot_sz, min_sz, max_sz)


def contracts_for_notional(
    notional_usdt: float,
    price: float,
    ct_val: float = 1.0,
    lot_sz: float = 1.0,
    min_sz: float = 0.0,
    max_sz: float = 0.0,
) -> float:
    """按 USDT 名义价值反推张数（名义 = 张数 × ctVal × 价格）。"""
    per_contract = price * ct_val
    if per_contract <= 0:
        return 0.0
    raw = float(notional_usdt) / per_contract
    return clamp_size(raw, lot_sz, min_sz, max_sz)


def notional_of(contracts: float, price: float, ct_val: float = 1.0) -> float:
    return float(contracts) * float(ct_val) * float(price)


def contracts_for_base(
    base_amount: float,
    ct_val: float = 1.0,
    lot_sz: float = 1.0,
    min_sz: float = 0.0,
    max_sz: float = 0.0,
) -> float:
    """按标的币数量反推张数（1 张 = ctVal 个币；现货 ctVal=1，即原样返回）。"""
    if ct_val <= 0:
        return 0.0
    return clamp_size(float(base_amount) / ct_val, lot_sz, min_sz, max_sz)


def size_from_symbol_info(
    equity: float,
    risk_percent: float,
    entry_price: float,
    stop_price: float,
    symbol_info: dict[str, Any],
) -> dict[str, Any]:
    """从 get_symbol_info 的结构直接算，返回张数 + 名义价值 + 说明。"""
    ct_val = float(symbol_info.get("ct_val") or 1.0)
    lot_sz = float(symbol_info.get("lot_step") or 1.0)
    min_sz = float(symbol_info.get("min_lot") or 0.0)
    max_sz = float(symbol_info.get("max_lot") or 0.0)
    size = calculate_contracts(equity, risk_percent, entry_price, stop_price,
                               ct_val, lot_sz, min_sz, max_sz)
    notional = notional_of(size, entry_price, ct_val)
    return {
        "size": size,
        "notional_usdt": round(notional, 2),
        "ct_val": ct_val,
        "lot_sz": lot_sz,
        "min_sz": min_sz,
        "max_sz": max_sz,
        "risk_usd": round(equity * risk_percent / 100.0, 2),
        "message": (
            f"风险 {equity * risk_percent / 100.0:.2f} USDT，止损距离 "
            f"{abs(entry_price - stop_price):.4f}，每张 {ct_val} 币 → "
            f"{size} 张，名义 {notional:.2f} USDT"
            if size > 0 else "计算出的张数不足最小下单量，无法开仓"
        ),
    }
