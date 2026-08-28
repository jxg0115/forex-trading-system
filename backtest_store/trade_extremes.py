"""按历史 K 线计算每笔交易的最高浮盈 / 最低浮亏，用于复盘止损止盈策略。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


def _to_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _to_local_naive(value: Any) -> Any:
    dt = _to_dt(value)
    if dt is None:
        return value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().replace(tzinfo=None)


def _as_utc(value: Any) -> datetime | None:
    dt = _to_dt(value)
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _timeframe_minutes(timeframe: str) -> int:
    return {
        "M1": 1,
        "M5": 5,
        "M15": 15,
        "M30": 30,
        "H1": 60,
        "H4": 240,
        "D1": 1440,
    }.get(timeframe.upper(), 15)


def compute_trade_extremes(
    gateway: Any,
    symbol: str,
    timeframe: str,
    entry_time: Any,
    exit_time: Any,
    side: str,
    lots: float,
    entry_price: float,
    final_pnl: float | None = None,
) -> dict[str, Any]:
    """根据入场到出场之间的 OHLCV，估算浮盈最高点与最低点。"""

    bars: list[dict[str, Any]] = []
    if gateway is not None:
        try:
            local_from = _to_local_naive(entry_time)
            local_to = _to_local_naive(exit_time)
            if isinstance(local_from, datetime) and isinstance(local_to, datetime):
                local_from = local_from - timedelta(minutes=_timeframe_minutes(timeframe))
            bars = gateway.get_rates_range(symbol, timeframe, local_from, local_to) or []
        except Exception:
            bars = []

    contract = 100.0
    if gateway is not None:
        try:
            info = gateway.get_symbol_info(symbol)
            if info:
                contract = float(info.get("contract_size") or 100.0)
        except Exception:
            pass

    fallback_pnl = float(final_pnl or 0.0)
    peak = {"pnl": fallback_pnl, "price": float(entry_price), "time": entry_time}
    trough = {"pnl": fallback_pnl, "price": float(entry_price), "time": entry_time}
    entry_dt = _as_utc(entry_time)
    exit_dt = _as_utc(exit_time)
    for bar in bars:
        high = float(bar["high"])
        low = float(bar["low"])
        bar_time = _as_utc(bar.get("time")) or entry_dt
        if entry_dt is not None and exit_dt is not None:
            if bar_time is None or bar_time < entry_dt or bar_time > exit_dt:
                continue
        if side == "long":
            high_pnl = (high - float(entry_price)) * float(lots) * contract
            low_pnl = (low - float(entry_price)) * float(lots) * contract
        else:
            high_pnl = (float(entry_price) - low) * float(lots) * contract
            low_pnl = (float(entry_price) - high) * float(lots) * contract
        if high_pnl > peak["pnl"]:
            peak = {"pnl": high_pnl, "price": high, "time": bar_time}
        if low_pnl < trough["pnl"]:
            trough = {"pnl": low_pnl, "price": low, "time": bar_time}

    return {
        "peak_pnl": round(peak["pnl"], 2),
        "trough_pnl": round(trough["pnl"], 2),
        "peak_price": round(float(peak["price"]), 5),
        "trough_price": round(float(trough["price"]), 5),
        "peak_time": _to_dt(peak["time"]),
        "trough_time": _to_dt(trough["time"]),
    }
