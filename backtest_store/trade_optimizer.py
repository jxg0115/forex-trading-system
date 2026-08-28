"""单笔交易模拟优化：固定入场点，搜索更优的止损止盈与持仓参数。"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any

from backtest_store.trade_extremes import _as_utc, _to_local_naive, _timeframe_minutes


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _contract_size(gateway: Any, symbol: str) -> float:
    try:
        info = gateway.get_symbol_info(symbol)
        if info:
            return float(info.get("contract_size") or 100.0)
    except Exception:
        pass
    return 100.0


def _fetch_bars(
    gateway: Any,
    symbol: str,
    timeframe: str,
    entry_time: datetime,
    max_hold_bars: int,
) -> list[dict[str, Any]]:
    if gateway is None:
        return []
    minutes = _timeframe_minutes(timeframe)
    local_from = _to_local_naive(entry_time) - timedelta(minutes=minutes)
    local_to = _to_local_naive(entry_time) + timedelta(minutes=minutes * (max_hold_bars + 6))
    try:
        bars = gateway.get_rates_range(symbol, timeframe, local_from, local_to) or []
    except Exception:
        return []
    entry_dt = _as_utc(entry_time)
    if entry_dt is None:
        return bars
    filtered = []
    for bar in bars:
        bar_dt = _as_utc(bar.get("time"))
        if bar_dt is not None and bar_dt >= entry_dt:
            filtered.append(bar)
    return filtered


def _simulate(
    bars: list[dict[str, Any]],
    entry_price: float,
    side: str,
    lots: float,
    contract: float,
    stop_dist: float,
    take_dist: float,
    max_hold: int,
) -> dict[str, Any]:
    direction = 1.0 if side in ("long", "buy") else -1.0
    stop = entry_price - direction * stop_dist
    take = entry_price + direction * take_dist
    exit_price = entry_price
    exit_reason = "timeout"
    mae = 0.0
    for i, bar in enumerate(bars[:max_hold]):
        high = _num(bar["high"])
        low = _num(bar["low"])
        close = _num(bar["close"])
        adverse = (low - entry_price) * direction if direction > 0 else (entry_price - high) * direction
        mae = min(mae, adverse) if i == 0 else min(mae, adverse)
        if direction > 0 and low <= stop:
            exit_price, exit_reason = stop, "stop"
            break
        if direction < 0 and high >= stop:
            exit_price, exit_reason = stop, "stop"
            break
        if direction > 0 and high >= take:
            exit_price, exit_reason = take, "take"
            break
        if direction < 0 and low <= take:
            exit_price, exit_reason = take, "take"
            break
        exit_price = close
    pnl = (exit_price - entry_price) * direction * lots * contract
    return {
        "exit_price": round(exit_price, 5),
        "pnl": round(pnl, 2),
        "mae": round(mae * lots * contract, 2),
        "exit_reason": exit_reason,
        "bars_held": min(len(bars), max_hold),
    }


def optimize_trade(
    gateway: Any,
    symbol: str,
    timeframe: str,
    entry_time: Any,
    exit_time: Any,
    side: str,
    lots: float,
    entry_price: float,
    current_pnl: float | None = None,
) -> dict[str, Any]:
    """在入场点固定的前提下，搜索更优止损/止盈/持仓参数。"""

    entry_dt = _as_utc(entry_time)
    exit_dt = _as_utc(exit_time)
    if entry_dt is None:
        return {"ok": False, "message": "入场时间无效"}
    contract = _contract_size(gateway, symbol)
    max_hold_candidates = [1, 2, 3, 5, 8, 15, 30, 60, 120]
    bars = _fetch_bars(gateway, symbol, timeframe, entry_dt, max(max_hold_candidates))
    if not bars:
        return {"ok": False, "message": "无法获取该订单区间 K 线，无法模拟优化"}

    # ATR 估算：使用入场后前 14 根 K 线的平均真实波幅
    prev_close = _num(bars[0]["close"])
    trs: list[float] = []
    for bar in bars[:14]:
        high = _num(bar["high"])
        low = _num(bar["low"])
        close = _num(bar["close"])
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
        prev_close = close
    atr = sum(trs) / len(trs) if trs else max(_num(bars[0]["close"]) * 0.005, 1e-9)

    best: dict[str, Any] | None = None
    trials = 0
    for stop_mult in (1.0, 1.5, 2.0, 3.0, 4.0):
        for take_mult in (1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 6.0):
            for max_hold in max_hold_candidates:
                trials += 1
                sim = _simulate(
                    bars,
                    entry_price,
                    side,
                    lots,
                    contract,
                    atr * stop_mult,
                    atr * take_mult,
                    max_hold,
                )
                score = sim["pnl"] - 0.1 * max(0.0, sim["mae"])
                candidate = {
                    **sim,
                    "stop_atr_mult": stop_mult,
                    "take_atr_mult": take_mult,
                    "max_hold_bars": max_hold,
                    "score": round(score, 2),
                }
                if best is None or score > best["score"] or (
                    score == best["score"] and candidate["mae"] < best["mae"]
                ):
                    best = candidate

    if best is None:
        return {"ok": False, "message": "未找到有效优化方案"}
    current = _num(current_pnl)
    return {
        "ok": True,
        "symbol": symbol,
        "timeframe": timeframe,
        "entry_price": round(entry_price, 5),
        "side": side,
        "current_pnl": round(current, 2),
        "optimized_pnl": best["pnl"],
        "improvement": round(best["pnl"] - current, 2),
        "best_params": {
            "stop_atr_mult": best["stop_atr_mult"],
            "take_atr_mult": best["take_atr_mult"],
            "max_hold_bars": best["max_hold_bars"],
        },
        "exit_reason": best["exit_reason"],
        "mae": best["mae"],
        "bars_held": best["bars_held"],
        "trials": trials,
    }
