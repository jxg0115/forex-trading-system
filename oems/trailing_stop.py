"""移动止损服务：盈利达到阈值后，将止损随价格向有利方向逐步移动。"""

from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from indicators.technical import atr_series
from observability.notifier import Notifier
from oems.order_manager import OrderManager
from signal_matcher.executor import ExecutorRuntimeConfig


class TrailingStopService:
    """移动止损：盈利达标后先锁保本/关键位，再随实时价格按 ATR 距离移动。"""

    def __init__(
        self,
        gateway: Any,
        orders: OrderManager,
        notifier: Optional[Notifier] = None,
        repository: Any = None,
    ) -> None:
        self.gateway = gateway
        self.orders = orders
        self.notifier = notifier
        self.repository = repository
        self._state: dict[int, dict[str, Any]] = {}
        self.last_updates: list[dict[str, Any]] = []

    async def update(self, config: ExecutorRuntimeConfig | None = None) -> list[dict[str, Any]]:
        """扫描 MT5 持仓并执行移动止损，返回本次更新记录。"""

        if not config or not config.trailing_enabled or not self.gateway:
            self._state.clear()
            self.last_updates = []
            return []

        positions = self.gateway.get_positions()
        current_tickets: set[int] = set()
        atr_cache: dict[str, float] = {}
        updates: list[dict[str, Any]] = []
        ticket_factor: dict[str, str] = {}
        if self.repository:
            for log in self.repository.list_order_logs(limit=500):
                if log.mt5_ticket:
                    ticket_factor[str(log.mt5_ticket)] = log.factor_id or ""

        for pos in positions:
            ticket = int(pos["ticket"])
            current_tickets.add(ticket)
            if pos["type"] not in ("BUY", "SELL"):
                continue
            state = self._state.setdefault(
                ticket,
                {
                    "extreme": float(pos["price_current"]),
                    "last_stop": float(pos.get("sl") or 0.0),
                    "last_take": float(pos.get("tp") or 0.0),
                    "take_hit": float("inf") if pos["type"] == "SELL" else 0.0,
                    "peak_profit": float(pos.get("profit") or 0.0),
                    "pc_closed_volume": 0.0,
                },
            )
            current = float(pos["price_current"])
            if pos["type"] == "BUY":
                state["extreme"] = max(state["extreme"], current)
            else:
                state["extreme"] = min(state["extreme"], current)

            atr = atr_cache.get(pos["symbol"])
            if atr is None:
                bars = self.gateway.get_rates(pos["symbol"], config.timeframe, 300)
                atr = self._last_atr(bars) or float(pos["price_open"]) * 0.005
                atr_cache[pos["symbol"]] = atr

            current_profit = float(pos.get("profit") or 0.0)
            state["peak_profit"] = max(float(state.get("peak_profit") or 0.0), current_profit)
            hw_action = await self._apply_high_watermark(pos, state, config, current_profit)
            if hw_action:
                item = {
                    "ticket": ticket,
                    "symbol": pos["symbol"],
                    "side": pos["type"],
                    "action": "最高浮盈回落平仓",
                    "reason": f"峰值盈利 {hw_action['peak']:.2f}，回落至 {current_profit:.2f} 触发",
                    "ok": True,
                }
                updates.append(item)
                self.last_updates.insert(0, item)
                continue
            pc_action = await self._apply_partial_close(pos, state, config, current_profit)
            if pc_action:
                item = {
                    "ticket": ticket,
                    "symbol": pos["symbol"],
                    "side": pos["type"],
                    "action": f"分批落袋第 {pc_action['tier']} 档",
                    "close_volume": pc_action["volume"],
                    "ok": True,
                }
                updates.append(item)
                self.last_updates.insert(0, item)

            support_levels, resistance_levels = self._levels_for_factor(ticket_factor.get(str(ticket), ""))
            current_sl = float(pos.get("sl") or 0.0)
            current_tp = float(pos.get("tp") or 0.0)
            new_stop, new_take = self._trailing_targets(
                pos,
                state,
                config,
                atr,
                support_levels,
                resistance_levels,
            )
            sl_changed = (
                new_stop is not None
                and self._better_stop(new_stop, current_sl, pos["type"], current)
                and abs(new_stop - state["last_stop"]) > 1e-12
            )
            tp_changed = (
                new_take is not None
                and self._better_take(new_take, current_tp, pos["type"], current)
                and abs(new_take - state["last_take"]) > 1e-12
            )
            if not sl_changed and not tp_changed:
                continue
            try:
                await self.orders.modify_position(
                    str(ticket),
                    sl=new_stop if sl_changed else None,
                    tp=new_take if tp_changed else None,
                )
            except Exception as exc:
                updates.append({"ticket": ticket, "ok": False, "error": str(exc)})
                continue
            if sl_changed:
                state["last_stop"] = new_stop
            if tp_changed:
                state["last_take"] = new_take
            item = {
                "ticket": ticket,
                "symbol": pos["symbol"],
                "side": pos["type"],
                "stop": round(new_stop, 5) if new_stop is not None else None,
                "take": round(new_take, 5) if new_take is not None else None,
                "ok": True,
            }
            updates.append(item)
            self.last_updates.insert(0, item)

        self.last_updates = self.last_updates[:50]
        for ticket in list(self._state):
            if ticket not in current_tickets:
                del self._state[ticket]

        if updates and self.notifier:
            await self.notifier.send_alert(
                "info",
                "移动止损",
                f"已更新 {len(updates)} 笔持仓止损：{'；'.join(str(u['ticket']) for u in updates)}",
            )
        return updates

    async def _apply_high_watermark(
        self,
        pos: dict[str, Any],
        state: dict[str, Any],
        config: ExecutorRuntimeConfig,
        current_profit: float,
    ) -> dict[str, Any] | None:
        strategies = list(getattr(config, "sl_tp_strategies", None) or [])
        if "high_watermark" not in strategies:
            return None
        peak = float(state.get("peak_profit") or 0.0)
        original_volume = float(pos["volume"]) + float(state.get("pc_closed_volume") or 0.0)
        if original_volume <= 0:
            return None
        activation = max(float(config.hw_activation_profit), 0.0) * original_volume
        if peak < activation:
            return None
        retrace = self._hw_retrace_pct(peak / original_volume, config)
        if retrace is None:
            return None
        target = peak * (1.0 - retrace / 100.0)
        if current_profit > target:
            return None
        await self.orders.close_position(str(pos["ticket"]), float(pos["price_current"]))
        return {"peak": peak, "target": target}

    async def _apply_partial_close(
        self,
        pos: dict[str, Any],
        state: dict[str, Any],
        config: ExecutorRuntimeConfig,
        current_profit: float,
    ) -> dict[str, Any] | None:
        strategies = list(getattr(config, "sl_tp_strategies", None) or [])
        if "partial_close" not in strategies:
            return None
        remaining = float(pos["volume"])
        if remaining <= 0.01:
            return None
        original_volume = remaining + float(state.get("pc_closed_volume") or 0.0)
        pc_closed = float(state.get("pc_closed_volume") or 0.0)
        tier1_target = original_volume * max(float(config.pc_tier1_close_pct), 0.0) / 100.0
        tier2_target = original_volume * max(float(config.pc_tier2_close_pct), 0.0) / 100.0
        tier1_profit = max(float(config.pc_tier1_profit), 0.0) * original_volume
        tier2_profit = max(float(config.pc_tier2_profit), 0.0) * original_volume
        tier = 0
        close_volume = 0.0
        if current_profit >= tier1_profit and pc_closed < tier1_target:
            tier = 1
            close_volume = round(min(tier1_target - pc_closed, remaining), 2)
        elif current_profit >= tier2_profit and pc_closed < tier1_target + tier2_target:
            tier = 2
            close_volume = round(min(tier1_target + tier2_target - pc_closed, remaining), 2)
        if tier == 0 or close_volume <= 0:
            return None
        await self.orders.close_position(str(pos["ticket"]), float(pos["price_current"]), volume=close_volume)
        state["pc_closed_volume"] = pc_closed + close_volume
        entry = float(pos["price_open"])
        breakeven = (
            entry + float(config.pc_breakeven_buffer)
            if pos["type"] == "BUY"
            else entry - float(config.pc_breakeven_buffer)
        )
        try:
            await self.orders.modify_position(str(pos["ticket"]), sl=breakeven)
            state["last_stop"] = breakeven
        except Exception:
            pass
        state["peak_profit"] = current_profit
        return {"tier": tier, "volume": close_volume}

    @staticmethod
    def _hw_retrace_pct(peak_profit: float, config: ExecutorRuntimeConfig) -> float | None:
        if peak_profit < 200:
            return None
        if peak_profit < 500:
            return 35.0
        if peak_profit < 1000:
            return 25.0
        return max(float(config.hw_max_retrace_pct), 0.0) or 20.0

    def _trailing_targets(
        self,
        pos: dict[str, Any],
        state: dict[str, Any],
        config: ExecutorRuntimeConfig,
        atr: float,
        support_levels: list[float] | None = None,
        resistance_levels: list[float] | None = None,
    ) -> tuple[float | None, float | None]:
        """三阶段移动止损/止盈：盈利达标跟随、接近止盈锁定、突破后移动止盈。"""

        entry = float(pos["price_open"])
        extreme = state["extreme"]
        current = float(pos["price_current"])
        if entry <= 0:
            return None, None
        side = pos["type"]
        new_stop: float | None = None
        new_take: float | None = None
        profit_pct = (
            (extreme - entry) / entry * 100.0
            if side == "BUY"
            else (entry - extreme) / entry * 100.0
        )
        atr_value = max(float(atr), 0.0)
        use_atr = str(config.trailing_unit).lower() == "atr"
        if use_atr:
            activation_dist = max(float(config.trailing_activation_atr), 0.0) * atr_value
            stop_dist = max(float(config.trailing_stop_atr), 0.0) * atr_value
            take_dist = max(float(config.trailing_take_atr), 0.0) * atr_value
            take_buffer_dist = max(float(config.trailing_take_buffer_atr), 0.0) * atr_value
        else:
            activation_dist = entry * max(float(config.trailing_activation_pct), 0.0) / 100.0
            stop_dist = extreme * max(float(config.trailing_retrace_pct), 0.0) / 100.0
            take_dist = extreme * max(float(config.trailing_take_retrace_pct), 0.0) / 100.0
            take_buffer_dist = 0.0

        profit_distance = extreme - entry if side == "BUY" else entry - extreme
        activated = (
            profit_distance >= activation_dist
            if use_atr
            else profit_pct >= max(float(config.trailing_activation_pct), 0.0)
        )
        # 阶段一：盈利达标后，止损先保本/锁关键位，再随极值追踪。
        if activated:
            anchor = entry
            if side == "BUY":
                supports = [p for p in (support_levels or []) if entry < p < extreme]
                if supports:
                    anchor = max(supports)
                candidate = round(extreme - stop_dist, 5)
                new_stop = max(candidate, anchor)
            else:
                resistances = [p for p in (resistance_levels or []) if extreme < p < entry]
                if resistances:
                    anchor = min(resistances)
                candidate = round(extreme + stop_dist, 5)
                new_stop = min(candidate, anchor)

        take_line = float(pos.get("tp") or 0.0)
        if take_line > 0:
            # 阶段二：首次触达/突破止盈价，止损先锁到该止盈价，止盈随极值顺势调整。
            if side == "BUY" and current >= take_line:
                state["take_hit"] = max(float(state.get("take_hit") or 0.0), take_line)
            elif side == "SELL" and current <= take_line:
                state["take_hit"] = min(float(state.get("take_hit") or float("inf")), take_line)

            if side == "BUY" and current >= take_line:
                lock_stop = float(state["take_hit"])
                if new_stop is None or lock_stop > new_stop:
                    new_stop = lock_stop
                candidate_take = (
                    round(extreme + take_dist, 5)
                    if use_atr
                    else round(extreme * (1.0 + max(float(config.trailing_take_retrace_pct), 0.0) / 100.0), 5)
                )
                if candidate_take > take_line:
                    new_take = candidate_take
                    follow_stop = (
                        round(new_take - take_buffer_dist, 5)
                        if use_atr
                        else round(new_take * (1.0 - max(float(config.trailing_take_buffer_pct), 0.0) / 100.0), 5)
                    )
                    if follow_stop < current and follow_stop > new_stop:
                        new_stop = follow_stop
            elif side == "SELL" and current <= take_line:
                lock_stop = float(state["take_hit"])
                if new_stop is None or lock_stop < new_stop:
                    new_stop = lock_stop
                candidate_take = (
                    round(extreme - take_dist, 5)
                    if use_atr
                    else round(extreme * (1.0 - max(float(config.trailing_take_retrace_pct), 0.0) / 100.0), 5)
                )
                if candidate_take < take_line:
                    new_take = candidate_take
                    follow_stop = (
                        round(new_take + take_buffer_dist, 5)
                        if use_atr
                        else round(new_take * (1.0 + max(float(config.trailing_take_buffer_pct), 0.0) / 100.0), 5)
                    )
                    if follow_stop > current and follow_stop < new_stop:
                        new_stop = follow_stop
        # 已触达过的止盈价作为锁定底线，防止止盈上移后首档锁仓漏掉
        hit = state.get("take_hit")
        if hit is not None:
            if side == "BUY" and float(hit) > 0 and (new_stop is None or float(hit) > new_stop):
                new_stop = float(hit)
            elif side == "SELL" and float(hit) != float("inf") and (new_stop is None or float(hit) < new_stop):
                new_stop = float(hit)
        if side == "BUY" and new_stop is not None and new_stop >= current:
            candidates = [entry]
            if hit is not None and float(hit) > 0:
                candidates.append(float(hit))
            valid = [c for c in candidates if c < current]
            new_stop = max(valid) if valid else None
        elif side == "SELL" and new_stop is not None and new_stop <= current:
            candidates = [entry]
            if hit is not None and float(hit) != float("inf"):
                candidates.append(float(hit))
            valid = [c for c in candidates if c > current]
            new_stop = min(valid) if valid else None
        return new_stop, new_take

    def _levels_for_factor(self, factor_id: str) -> tuple[list[float], list[float]]:
        """从触发因子的形态快照中提取支撑位/压力位。"""

        if not factor_id or not self.repository or factor_id.startswith("model:"):
            return [], []
        factor = self.repository.get_factor(factor_id)
        if not factor:
            return [], []
        snapshot = getattr(factor, "prompt_snapshot", None) or {}
        supports = [
            float(level["price"])
            for level in (snapshot.get("support_levels") or [])
            if level.get("price")
        ]
        resistances = [
            float(level["price"])
            for level in (snapshot.get("resistance_levels") or [])
            if level.get("price")
        ]
        return supports, resistances

    @staticmethod
    def _better_stop(new_stop: float, current_sl: float, side: str, current_price: float) -> bool:
        if side == "BUY":
            return new_stop > current_sl and new_stop < current_price
        return new_stop < current_sl and new_stop > current_price

    @staticmethod
    def _better_take(new_take: float, current_tp: float, side: str, current_price: float) -> bool:
        if side == "BUY":
            return new_take > current_tp and new_take > current_price
        return new_take < current_tp and new_take < current_price

    @staticmethod
    def _last_atr(bars: list[dict[str, Any]] | None) -> float:
        try:
            df = pd.DataFrame(bars or [])
            if df.empty:
                return 0.0
            if "time" in df.columns:
                df["time"] = pd.to_datetime(df["time"], utc=True)
                df = df.set_index("time")
            atr = atr_series(df, 14).dropna()
            return float(atr.iloc[-1]) if len(atr) else 0.0
        except Exception:
            return 0.0
