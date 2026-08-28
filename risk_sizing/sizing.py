"""仓位计算：按风险金额倒推手数，并做杠杆上限约束。"""

from __future__ import annotations

import math

from models.factor import PositionSizingResult, RiskConfig


def calculate_position_size(
    equity: float,
    risk_percent: float,
    sl_pips: float,
    pip_value: float = 10.0,
    min_lot: float = 0.01,
    max_lot: float = 100.0,
    lot_step: float = 0.01,
) -> float:
    """按 MT5 品种参数反算开仓手数，并做最小/最大/步长约束。"""

    if sl_pips <= 0 or equity <= 0 or pip_value <= 0:
        return min_lot
    risk_amount = equity * (risk_percent / 100.0)
    raw_lot = risk_amount / (sl_pips * pip_value)
    steps = int(raw_lot / lot_step)
    calculated_lot = round(steps * lot_step, 2)
    return max(min_lot, min(max_lot, calculated_lot))


def compute_position(
    entry_price: float,
    atr: float,
    direction: str = "long",
    config: RiskConfig | None = None,
    stop_atr_mult: float = 2.0,
    take_atr_mult: float = 3.0,
) -> PositionSizingResult:
    cfg = config or RiskConfig()
    sign = 1.0 if direction == "long" else -1.0
    stop_dist = max(atr * stop_atr_mult, entry_price * 0.001)
    stop_price = entry_price - sign * stop_dist
    take_price = entry_price + sign * stop_dist * take_atr_mult
    stop_pips = stop_dist / cfg.pip_size

    risk_amount = cfg.account_equity * cfg.risk_per_trade_pct / 100.0
    pip_value = cfg.pip_value_per_lot
    lots = risk_amount / (stop_pips * pip_value) if stop_pips > 0 else 0.0

    max_notional = cfg.account_equity * cfg.leverage
    max_lots = max_notional / entry_price if entry_price > 0 else 0.0
    lots = min(lots, max_lots)
    lots = round(max(lots, 0.0), 2)
    margin_used = lots * entry_price / max(cfg.leverage, 1)

    message = (
        f"按 {cfg.risk_per_trade_pct:.2f}% 风险预算与 {stop_pips:.1f} 点止损距离，"
        f"建议仓位 {lots:.2f} 手，占用保证金约 {margin_used:.2f} 美元。"
    )
    return PositionSizingResult(
        lots=lots,
        risk_amount=round(risk_amount, 2),
        stop_price=round(stop_price, 5),
        take_price=round(take_price, 5),
        stop_distance_pips=round(stop_pips, 1),
        risk_pct=cfg.risk_per_trade_pct,
        margin_used=round(margin_used, 2),
        message=message,
    )
