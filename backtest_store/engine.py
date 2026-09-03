"""向量化历史回测引擎：ATR 止损止盈 + 固定分数风险模型。"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

import sys
from pathlib import Path
from types import SimpleNamespace

from backtest_store.costs import calc_slippage, merge_costs
from models.factor import BacktestMetrics, BacktestResult, TradeDetail

# —— 供给层（与实盘 SltpPolicyManager 同款）：收益K线估计器 + 七档动态阈值合成 ——
_EXIT_MANAGER_DIR = str(Path(__file__).resolve().parent.parent / "exit_manager")
if _EXIT_MANAGER_DIR not in sys.path:
    sys.path.insert(0, _EXIT_MANAGER_DIR)
_SUPPLY_OK = False
try:
    from framework.profit_kline import BarConfig, ProfitKlineGenerator, Snapshot
    from framework.estimators import EstimatorBundle
    from framework.indicators import IndicatorSnapshot
    from strategy.threshold_scheduler import ThresholdScheduler
    _SUPPLY_OK = True
except Exception:  # 供给层缺失 -> 动态档自动降级（诚实）
    pass

DEFAULT_PARAMS = {
    "atr_period": 14,
    "stop_atr_mult": 2.0,
    "take_atr_mult": 3.0,
    "max_hold_bars": 120,
    "commission_pct": 0.0001,
    "initial_equity": 10_000.0,
    "risk_per_trade_pct": 1.0,
    "execution_delay_bars": 0,
    "leverage": 30,
    "slippage_price": 0.0,
    "spread_points": 0.0,   # 点差成本（价格点，如 EURUSD 5 位报价 10 点 = 1 pip）；0 = 不计
    "point_size": 0.0,      # 1 点的价格单位（EURUSD 0.0001 / JPY 0.01 / 黄金 0.1 / 指数&加密 1.0）
    "bars_per_year": 35_040,
    # —— 方案A：系统决策层模拟（默认关闭，行为不变）——
    "system_filter": None,   # dict: direction / trend_ma / atr_ratio_max / atr_ratio_win / no_entry_hour / daily_loss_limit
    "dynamic_sltp": False,   # True 时用供给层动态档（与实盘同款合成器）
}


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period).mean()


class Backtester:
    """对入场/出场信号做逐根 K 线模拟，杜绝未来数据泄漏。"""

    def __init__(self) -> None:
        self.symbol = "EURUSD"
        self.timeframe = "M15"

    def run(
        self,
        df: pd.DataFrame,
        entry: pd.Series,
        exit_signal: pd.Series | None = None,
        params: dict[str, Any] | None = None,
    ) -> BacktestResult:
        p = {**DEFAULT_PARAMS, **(params or {})}
        p = merge_costs(p, p.get("symbol") or self.symbol)  # 成本口径中心：品种默认兜底（显式传值优先）
        df = df.copy()
        entry = entry.reindex(df.index).fillna(0.0)
        if exit_signal is not None:
            exit_signal = exit_signal.reindex(df.index).fillna(0.0)

        atr = _atr(df, int(p["atr_period"])).fillna(df["close"].diff().abs().rolling(5).mean()).fillna(0.0)
        equity = float(p["initial_equity"])
        initial = equity
        risk_pct = float(p["risk_per_trade_pct"]) / 100.0
        sltp = p.get("sltp") or None
        if sltp:
            # R/ATR 体系：与实盘 SltpPolicyConfig 同口径（risk_mult × ATR = R，止盈 = R × take_r_mult）
            sl_mult = float(sltp.get("risk_mult") or 1.5)
            tp_mult = float(sltp.get("take_r_mult") or 2.0)
            max_hold = int(sltp.get("time_stop_bars") or 120)
            ladder_enabled = bool(sltp.get("ladder_enabled"))
            ladder_tiers = sltp.get("ladder_tiers") or []
            hw_enabled = bool(sltp.get("high_watermark_enabled"))
            hw_activation_r = float(sltp.get("hw_activation_r") or 1.0)
            hw_retrace_atr = float(sltp.get("hw_retrace_atr") or 1.5)
        else:
            sl_mult = float(p["stop_atr_mult"])
            tp_mult = float(p["take_atr_mult"])
            max_hold = int(p["max_hold_bars"])
            ladder_enabled = False
            ladder_tiers = []
            hw_enabled = False
            hw_activation_r = 0.0
            hw_retrace_atr = 0.0
        commission = float(p["commission_pct"])
        delay_bars = max(int(p["execution_delay_bars"]), 0)

        # —— 方案A 系统过滤参数 ——
        system_filter = p.get("system_filter") or {}
        filter_direction = str(system_filter.get("direction") or "both")
        trend_ma = max(int(system_filter.get("trend_ma") or 0), 0)
        atr_ratio_max = max(float(system_filter.get("atr_ratio_max") or 0.0), 0.0)
        atr_ratio_win = max(int(system_filter.get("atr_ratio_win") or 40), 1)
        no_entry_hour: set[int] = set(int(h) for h in (system_filter.get("no_entry_hour") or []))
        daily_loss_limit = max(float(system_filter.get("daily_loss_limit") or 0.0), 0.0)
        price_window = max(int(system_filter.get("price_window") or 0), 0)
        price_percentile_max = max(float(system_filter.get("price_percentile_max") or 0.0), 0.0)  # 高位禁多（0=关）
        price_percentile_min = max(float(system_filter.get("price_percentile_min") or 0.0), 0.0)  # 低位禁空（0=关）
        use_dynamic_sltp = bool(p.get("dynamic_sltp")) and _SUPPLY_OK
        trend_line = df["close"].rolling(trend_ma).mean() if trend_ma > 0 else None
        atr_base = atr.rolling(atr_ratio_win).mean() if atr_ratio_max > 0 else None
        price_range_hi = df["high"].rolling(price_window).max() if price_window > 0 else None
        price_range_lo = df["low"].rolling(price_window).min() if price_window > 0 else None
        leverage = max(float(p["leverage"]), 1.0)
        slippage = max(float(p["slippage_price"]), 0.0)
        spread_points = max(float(p.get("spread_points") or 0.0), 0.0)
        point_size = max(float(p.get("point_size") or 0.0), 0.0)
        bars_per_year = float(p.get("bars_per_year", 35_040))

        trades: list[TradeDetail] = []
        equity_curve: list[dict[str, Any]] = []
        position: dict[str, Any] | None = None
        pending_entry: dict[str, Any] | None = None
        cur_day = ""
        day_realized = 0.0

        n = len(df)

        atr_mult = float(p.get("slippage_atr_mult") or 0.0)  # 可选：滑点按 ATR 比例（与固定值取大）

        def _open_position(i: int, side: str, direction: float) -> dict[str, Any]:
            bar_open = float(df["open"].iloc[i])
            stop_dist = atr.iloc[i] * sl_mult
            if not math.isfinite(stop_dist) or stop_dist <= 0:
                stop_dist = bar_open * 0.01
            entry_slip = calc_slippage(slippage, atr_mult, float(atr.iloc[i]))
            entry_price = bar_open + direction * entry_slip
            stop_price = entry_price - direction * stop_dist
            take_price = entry_price + direction * stop_dist * tp_mult
            # 点差成本（按半差价折算：买高卖低各承担一半，更贴近真实成交）
            spread_pct = (spread_points * point_size / 2.0) / entry_price if entry_price and spread_points else 0.0
            pos_dict = {
                "side": side,
                "direction": direction,
                "entry_time": df.index[i],
                "entry_price": entry_price,
                "stop": stop_price,
                "take": take_price,
                "bars_held": 0,
                "peak": entry_price,
                "stop_pct": abs(stop_dist) / entry_price if entry_price else 0.01,
                "spread_pct": spread_pct,
                "slippage": entry_slip,  # 出场成交滑点（与入场同口径，持仓期内固定）
            }
            if use_dynamic_sltp:
                # 动态档逐单状态（与实盘 SltpPolicyManager 同款构造）
                pos_dict["_gen"] = ProfitKlineGenerator(BarConfig(threshold_r=0.25, max_seconds=900.0))
                pos_dict["_est"] = EstimatorBundle()
                pos_dict["_sch"] = ThresholdScheduler()
                pos_dict["_est_n"] = 0
            return pos_dict

        for i in range(n):
            o = float(df["open"].iloc[i])
            h = float(df["high"].iloc[i])
            l = float(df["low"].iloc[i])
            c = float(df["close"].iloc[i])

            # 日熔断：按 bar 日期切换
            d_key = ""
            if hasattr(df.index[i], "date"):
                d_key = str(df.index[i].date())
            if d_key and d_key != cur_day:
                cur_day = d_key
                day_realized = 0.0

            if position is None and i > 0:
                sig = float(entry.iloc[i - 1])
                if sig != 0.0:
                    side = "long" if sig > 0 else "short"
                    direction = 1.0 if side == "long" else -1.0
                    # —— 方案A 系统决策层模拟：方向 / 趋势 / 波动 / 时段 / 日熔断（全部用上一根数据，无未来泄漏） ——
                    if filter_direction not in ("both", side):
                        sig = 0.0
                    if sig != 0.0 and trend_line is not None:
                        ma_prev = float(trend_line.iloc[i - 1])
                        if math.isnan(ma_prev):
                            sig = 0.0
                        else:
                            trend_up = float(df["close"].iloc[i - 1]) > ma_prev
                            if (side == "long" and not trend_up) or (side == "short" and trend_up):
                                sig = 0.0
                    if sig != 0.0 and atr_base is not None:
                        base = 0.0 if math.isnan(float(atr_base.iloc[i - 1])) else float(atr_base.iloc[i - 1])
                        if base > 0 and float(atr.iloc[i - 1]) / base > atr_ratio_max:
                            sig = 0.0
                    if sig != 0.0 and no_entry_hour:
                        last_ts = df.index[i - 1]
                        if isinstance(last_ts, pd.Timestamp) and last_ts.hour in no_entry_hour:
                            sig = 0.0
                    if sig != 0.0 and price_range_hi is not None:
                        hi = float(price_range_hi.iloc[i - 1])
                        lo = float(price_range_lo.iloc[i - 1])
                        if not math.isnan(hi) and not math.isnan(lo) and hi > lo:
                            pos_pct = (float(df["close"].iloc[i - 1]) - lo) / (hi - lo)
                            if side == "long" and price_percentile_max > 0 and pos_pct >= price_percentile_max:
                                sig = 0.0  # 高位禁多（趋势末端保护）
                            if side == "short" and price_percentile_min > 0 and pos_pct <= price_percentile_min:
                                sig = 0.0  # 低位禁空
                    if sig != 0.0 and daily_loss_limit > 0 and day_realized <= -daily_loss_limit:
                        sig = 0.0
                    if sig != 0.0:
                        if delay_bars > 0:
                            pending_entry = {"side": side, "direction": direction, "execute_at": i + delay_bars}
                        else:
                            position = _open_position(i, side, direction)

            if position is None and pending_entry is not None and i >= pending_entry["execute_at"]:
                position = _open_position(i, pending_entry["side"], pending_entry["direction"])
                pending_entry = None

            if position is not None:
                position["bars_held"] += 1
                side = position["side"]
                direction = position["direction"]
                exit_price: float | None = None
                reason = ""

                entry_price = position["entry_price"]
                # —— 离场保护模拟（与实盘 SltpPolicyManager 同口径）：峰值跟踪 + 阶梯抬底 ——
                if ladder_enabled or hw_enabled:
                    atr_i = float(atr.iloc[i]) if math.isfinite(float(atr.iloc[i])) else (abs(c - o) or 0.0001)
                    r_unit = max(sl_mult * atr_i, atr_i * 0.1)
                    if direction > 0:
                        position["peak"] = max(position["peak"], h)
                    else:
                        position["peak"] = min(position["peak"], l)
                    peak_profit_r = (position["peak"] - entry_price) * direction / r_unit
                    dyn_tiers = ladder_tiers
                    dyn_max_hold = max_hold
                    dyn_hw_r = hw_activation_r
                    if use_dynamic_sltp and position.get("_est") is not None:
                        try:
                            # 收益K线采样 -> 估计器 -> 七档动态合成（与实盘供给层同款，逐 bar 近似事件驱动）
                            snap = Snapshot(ts=int(df.index[i].timestamp()), price=c, floating_r=peak_profit_r,
                                            remaining_frac=1.0, direction=int(direction))
                            committed = position["_gen"].on_snapshot(snap)
                            if committed is not None:
                                position["_est"].on_bar(committed)
                                position["_est_n"] = int(position.get("_est_n") or 0) + 1
                            ind_snap = IndicatorSnapshot(atr=atr_i, spread_pip=0.0, spread_pip_avg=0.0)
                            est_out = position["_est"].snapshot(int(df.index[i].timestamp()), ind_snap)
                            order_proxy = SimpleNamespace(
                                dd_avg=0.3,  # V1：收益K线回撤近似常量（与实盘同款口径）
                                hold_frac=min(position["bars_held"] / max(max_hold, 1), 1.0),
                            )
                            thr = position["_sch"].compute(est_out, order_proxy, ts=int(df.index[i].timestamp()))
                            dyn_tiers = [
                                {"trigger_r": round(float(thr.be_r), 3), "raise_to_r": 0.0},
                                {"trigger_r": round(float(thr.partial1_r), 3), "raise_to_r": round(float(thr.be_r), 3)},
                                {"trigger_r": round(float(thr.partial2_r), 3), "raise_to_r": round(float(thr.partial1_r), 3)},
                            ]
                            dyn_max_hold = int(thr.max_hold_bars)
                            dyn_hw_r = round(float(thr.trail_start_r), 3)
                        except Exception:
                            pass  # 合成异常 -> 退回固定档（诚实降级）
                    if ladder_enabled and dyn_tiers:
                        for tier in dyn_tiers:
                            if peak_profit_r >= float(tier.get("trigger_r") or 0.0):
                                ladder_stop = entry_price + direction * float(tier.get("raise_to_r") or 0.0) * r_unit
                                if direction > 0:
                                    position["stop"] = max(position["stop"], ladder_stop)
                                else:
                                    position["stop"] = min(position["stop"], ladder_stop)

                if side == "long":
                    if l <= position["stop"]:
                        exit_price, reason = position["stop"] - position["slippage"], "止损"
                    elif h >= position["take"]:
                        exit_price, reason = position["take"] - position["slippage"], "止盈"
                else:
                    if h >= position["stop"]:
                        exit_price, reason = position["stop"] + position["slippage"], "止损"
                    elif l <= position["take"]:
                        exit_price, reason = position["take"] + position["slippage"], "止盈"

                if exit_price is None and exit_signal is not None and float(exit_signal.iloc[i]) != 0.0:
                    exit_price, reason = c - direction * position["slippage"], "出场信号"
                if exit_price is None and hw_enabled:
                    atr_i = float(atr.iloc[i]) if math.isfinite(float(atr.iloc[i])) else (abs(c - o) or 0.0001)
                    r_unit = max(sl_mult * atr_i, atr_i * 0.1)
                    peak_profit_r = (position["peak"] - entry_price) * direction / r_unit
                    if peak_profit_r >= dyn_hw_r:
                        drawdown = (
                            (position["peak"] - l) / atr_i
                            if direction > 0
                            else (h - position["peak"]) / atr_i
                        )
                        if drawdown > hw_retrace_atr:
                            exit_price, reason = c - direction * position["slippage"], "高水位回落"
                if exit_price is None and position["bars_held"] >= dyn_max_hold:
                    exit_price, reason = c - direction * position["slippage"], "超时平仓"

                if exit_price is not None:
                    entry_price = position["entry_price"]
                    side_sign = position["direction"]
                    gross_pnl_pct = side_sign * (exit_price - entry_price) / entry_price
                    net_pnl_pct = gross_pnl_pct - commission - position["spread_pct"]
                    size_frac = min(risk_pct / position["stop_pct"], leverage)
                    trade_pnl_pct = net_pnl_pct * size_frac
                    equity_before_trade = equity
                    equity *= 1.0 + trade_pnl_pct
                    day_realized += equity - equity_before_trade

                    trades.append(
                        TradeDetail(
                            entry_time=position["entry_time"],
                            entry_price=round(entry_price, 5),
                            side=side,  # type: ignore[arg-type]
                            exit_time=df.index[i],
                            exit_price=round(exit_price, 5),
                            pnl=round(equity - equity_before_trade, 2),
                            pnl_pct=round(trade_pnl_pct * 100.0, 4),
                            bars_held=position["bars_held"],
                            exit_reason=reason,
                        )
                    )
                    position = None

            equity_curve.append(
                {
                    "time": df.index[i].isoformat(),
                    "equity": round(equity, 2),
                    "position": bool(position),
                }
            )

        if position is not None:
            last_index = n - 1
            close_price = float(df["close"].iloc[last_index]) - position["direction"] * position["slippage"]
            entry_price = position["entry_price"]
            side_sign = position["direction"]
            gross_pnl_pct = side_sign * (close_price - entry_price) / entry_price
            net_pnl_pct = gross_pnl_pct - commission - position["spread_pct"]
            size_frac = min(risk_pct / position["stop_pct"], leverage)
            trade_pnl_pct = net_pnl_pct * size_frac
            equity_before_trade = equity
            equity *= 1.0 + trade_pnl_pct
            trades.append(
                TradeDetail(
                    entry_time=position["entry_time"],
                    entry_price=round(entry_price, 5),
                    side=position["side"],  # type: ignore[arg-type]
                    exit_time=df.index[last_index],
                    exit_price=round(close_price, 5),
                    pnl=round(equity - equity_before_trade, 2),
                    pnl_pct=round(trade_pnl_pct * 100.0, 4),
                    bars_held=position["bars_held"],
                    exit_reason="期末平仓",
                )
            )
            equity_curve[-1] = {
                "time": df.index[last_index].isoformat(),
                "equity": round(equity, 2),
                "position": False,
            }

        metrics = self._metrics(trades, equity_curve, initial, n, bars_per_year)
        return BacktestResult(
            ok=True,
            message=f"回测完成：共 {len(trades)} 笔交易",
            metrics=metrics,
            trades=trades,
            equity_curve=equity_curve,
        )

    @staticmethod
    def _metrics(
        trades: list[TradeDetail],
        equity_curve: list[dict[str, Any]],
        initial: float,
        n_bars: int,
        bars_per_year: float,
    ) -> BacktestMetrics:
        final = float(equity_curve[-1]["equity"]) if equity_curve else initial
        total_return = (final / initial - 1.0) * 100.0 if initial else 0.0
        annual_return = 0.0
        if n_bars > 0 and total_return > -100.0:
            annual_return = ((final / initial) ** (bars_per_year / n_bars) - 1.0) * 100.0
            annual_return = max(min(annual_return, 99_999.0), -99.99)

        equity_arr = np.array([p["equity"] for p in equity_curve], dtype=float)
        returns = np.diff(equity_arr) / equity_arr[:-1] if len(equity_arr) > 1 else np.array([0.0])
        sharpe = 0.0
        if len(returns) > 1 and float(np.std(returns)) > 0:
            sharpe = float(np.mean(returns) / np.std(returns)) * math.sqrt(bars_per_year)
        max_dd = 0.0
        if len(equity_arr) > 1:
            peak = np.maximum.accumulate(equity_arr)
            max_dd = float(np.max((peak - equity_arr) / peak) * 100.0)

        pnl_list = [t.pnl_pct or 0.0 for t in trades]
        wins = [v for v in pnl_list if v > 0]
        losses = [v for v in pnl_list if v < 0]
        win_rate = len(wins) / len(pnl_list) * 100.0 if pnl_list else 0.0
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)
        avg_trade = sum(pnl_list) / len(pnl_list) if pnl_list else 0.0

        max_loss_streak = 0
        streak = 0
        for v in pnl_list:
            if v < 0:
                streak += 1
                max_loss_streak = max(max_loss_streak, streak)
            else:
                streak = 0

        return BacktestMetrics(
            total_return_pct=round(total_return, 4),
            annual_return_pct=round(annual_return, 4),
            sharpe=round(sharpe, 3),
            max_drawdown_pct=round(max_dd, 4),
            win_rate_pct=round(win_rate, 2),
            profit_factor=round(profit_factor, 3),
            num_trades=len(trades),
            avg_trade_pct=round(avg_trade, 4),
            max_consecutive_losses=max_loss_streak,
        )
