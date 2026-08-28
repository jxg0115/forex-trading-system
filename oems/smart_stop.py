"""智能止损引擎：根据实时持仓、K 线与波动率动态调整止损。"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import pandas as pd

from indicators.technical import atr_series
from observability.notifier import Notifier
from oems.order_manager import OrderManager


@dataclass
class SmartStopConfig:
    enabled: bool = False
    timeframe: str = "M15"
    manage_manual: bool = True
    use_history_profile: bool = True
    use_pattern_match: bool = True
    pattern_match_min_similarity: float = 0.85
    pattern_refresh_seconds: int = 60
    soft_stop_enabled: bool = True
    soft_stop_atr: float = 0.5
    soft_stop_bars: int = 3
    soft_stop_action: str = "breakeven"  # breakeven | close
    breakeven_enabled: bool = True
    breakeven_atr: float = 0.5
    breakeven_buffer_atr: float = 0.1
    trailing_enabled: bool = True
    trailing_activation_atr: float = 0.8
    trailing_stop_atr: float = 1.0
    dynamic_level_enabled: bool = True
    swing_bars: int = 10
    level_buffer_atr: float = 0.3
    time_stop_enabled: bool = True
    max_hold_bars: int = 120
    time_stop_min_profit_atr: float = 0.5
    lock_profit_enabled: bool = True
    lock_activation_atr: float = 1.0
    lock_retrace_atr: float = 0.5
    hard_stop_atr: float = 1.5
    max_single_loss_usd: float = 1500.0
    partial_close_enabled: bool = False
    partial_close_tiers: list[dict[str, Any]] = field(
        default_factory=lambda: [
            {"profit_usd_per_lot": 300, "close_pct": 30},
            {"profit_usd_per_lot": 500, "close_pct": 30},
            {"profit_usd_per_lot": 800, "close_pct": 20},
        ]
    )
    ladder_take_enabled: bool = True
    ladder_tiers: list[dict[str, Any]] = field(
        default_factory=lambda: [
            {"profit_usd_per_lot": 300, "retrace_pct": 40},
            {"profit_usd_per_lot": 500, "retrace_pct": 30},
            {"profit_usd_per_lot": 800, "retrace_pct": 20},
            {"profit_usd_per_lot": 1200, "retrace_pct": 15},
        ]
    )
    cooldown_seconds: int = 60
    ai_enabled: bool = False
    ai_take_profit_enabled: bool = True
    ai_min_interval_seconds: int = 60
    ai_timeout_seconds: int = 8
    ai_min_confidence: float = 0.6
    ai_triggers: list[str] = field(
        default_factory=lambda: ["soft_stop", "time_stop", "near_hard", "profit_lock"]
    )
    ai_periodic_enabled: bool = False
    ai_periodic_interval_seconds: int = 60

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "SmartStopConfig":
        if not data:
            return cls()
        keys = set(cls.__dataclass_fields__.keys())
        return cls(**{k: v for k, v in data.items() if k in keys})


def _atr_of(gateway: Any, symbol: str, timeframe: str, default: float) -> float:
    try:
        bars = gateway.get_rates(symbol, timeframe, 300) or []
        if not bars:
            return default
        df = pd.DataFrame(bars)
        df["close"] = df["close"].astype(float)
        df["high"] = df["high"].astype(float)
        df["low"] = df["low"].astype(float)
        series = atr_series(df).dropna()
        return float(series.iloc[-1]) if len(series) else default
    except Exception:
        return default


def _contract_size(gateway: Any, symbol: str) -> float:
    try:
        info = gateway.get_symbol_info(symbol)
        if info:
            return float(info.get("contract_size") or 100.0)
    except Exception:
        pass
    return 100.0


def _bars_since_entry(bars: list[dict[str, Any]], entry_time: Any) -> int:
    if not bars or entry_time is None:
        return 0
    try:
        entry_dt = datetime.fromtimestamp(int(entry_time), tz=timezone.utc)
        count = 0
        for bar in bars:
            try:
                bar_dt = pd.to_datetime(bar["time"], utc=True).to_pydatetime()
            except Exception:
                continue
            if bar_dt >= entry_dt:
                count += 1
        return count
    except Exception:
        return 0


def _swing_levels(bars: list[dict[str, Any]], swing_bars: int) -> tuple[float, float]:
    if not bars:
        return 0.0, 0.0
    recent = bars[-swing_bars:]
    return min(float(b["low"]) for b in recent), max(float(b["high"]) for b in recent)


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    vals = sorted(values)
    n = len(vals)
    if n % 2:
        return float(vals[n // 2])
    return float((vals[n // 2 - 1] + vals[n // 2]) / 2)


def _format_ai_bars(bars: list[dict[str, Any]], max_rows: int = 30) -> str:
    head = "| 时间 | 开 | 高 | 低 | 收 | 量 |"
    lines = [head, "|---|---|---|---|---|---|"]
    step = max(1, len(bars) // max_rows) if bars else 1
    for row in bars[::step][-max_rows:]:
        lines.append(
            f"| {row['time']} | {float(row['open']):.5f} | {float(row['high']):.5f} "
            f"| {float(row['low']):.5f} | {float(row['close']):.5f} | {float(row['volume']):.0f} |"
        )
    return "\n".join(lines)


def _parse_ai_advice(text: str) -> dict[str, Any]:
    if not text:
        return {}
    match = re.search(r"```json\s*\n(.*?)```", text, flags=re.S)
    raw = match.group(1) if match else None
    if raw is None:
        match = re.search(r"\{.*\}", text, flags=re.S)
        raw = match.group(0) if match else None
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _build_ai_prompt(
    pos: dict[str, Any],
    bars: list[dict[str, Any]],
    atr: float,
    current_sl: float,
    hard_stop: float,
    max_loss_stop: float,
    bars_held: int,
    actions: list[str],
    config: SmartStopConfig,
) -> str:
    side = "多头" if pos["type"] == "BUY" else "空头"
    current_tp = float(pos.get("tp") or 0.0)
    return f"""你是外汇交易系统的智能止损辅助引擎。请根据以下持仓与最近K线，判断当前止损是否应该调整。

## 持仓
- 品种：{pos['symbol']} / {config.timeframe}
- 方向：{side}
- 开仓价：{float(pos['price_open']):.5f}，现价：{float(pos['price_current']):.5f}
- 手数：{float(pos['volume'])}，当前浮盈：{float(pos.get('profit') or 0):.2f} 美元
- 当前止损：{current_sl if current_sl > 0 else '未设置'}，已持仓：{bars_held} 根K线
- 当前止盈：{current_tp if current_tp > 0 else '未设置'}
- ATR：{atr:.5f}
- 硬止损参考价：{hard_stop:.5f}，单笔最大亏损止损参考价：{max_loss_stop:.5f}

## 规则引擎当前候选动作
{'；'.join(actions) or '无'}

## 最近K线
{_format_ai_bars(bars)}

## 输出要求
只输出 JSON，不要解释：
{{
  "action": "move|keep|breakeven|close",
  "stop_multiplier": 1.0,
  "take_action": "keep|trail|extend|close",
  "take_distance_atr": 1.0,
  "confidence": 0.7,
  "reason": "中文一句话理由"
}}
约束：
- move 的 stop_multiplier 必须在 0.5 到 {config.hard_stop_atr} 之间，且止损只能朝有利方向移动。
- breakeven 表示把止损移到保本附近。
- close 表示建议立即平仓，只有当前亏损未超过单笔最大亏损上限时才允许。
- take_action：
  - keep：保持当前止盈不动；
  - trail：把止盈跟随峰值移动，锁住大部分利润，take_distance_atr 为回撤距离；
  - extend：把止盈放宽到峰值更远处，继续持有博取更大利润；
  - close：当前盈利足够时立即止盈平仓。
- confidence 低于 {config.ai_min_confidence} 时，引擎会忽略 AI 建议。"""


class SmartStopEngine:
    """规则驱动的实时止损引擎，AI 后续可挂接为异步辅助。"""

    def __init__(
        self,
        gateway: Any,
        orders: OrderManager,
        notifier: Optional[Notifier] = None,
        repository: Any = None,
        ai_manager: Any = None,
        matcher: Any = None,
    ) -> None:
        self.gateway = gateway
        self.orders = orders
        self.notifier = notifier
        self.repository = repository
        self.ai_manager = ai_manager
        self.matcher = matcher
        self._state: dict[int, dict[str, Any]] = {}
        self._pattern_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._history_cache: dict[str, dict[str, float]] = {}
        self.last_updates: list[dict[str, Any]] = []

    async def update(self, config: SmartStopConfig | None = None) -> list[dict[str, Any]]:
        config = config or SmartStopConfig()
        if not config.enabled or not self.gateway:
            self._state.clear()
            self.last_updates = []
            return []

        positions = self.gateway.get_positions()
        current_tickets: set[int] = set()
        system_tickets: set[str] | None = None
        if not config.manage_manual and self.repository:
            system_tickets = {
                str(log.mt5_ticket)
                for log in self.repository.list_order_logs(limit=5000)
                if log.mt5_ticket
            }
        atr_cache: dict[str, float] = {}
        updates: list[dict[str, Any]] = []

        for pos in positions:
            ticket = int(pos["ticket"])
            current_tickets.add(ticket)
            if pos["type"] not in ("BUY", "SELL"):
                continue
            if system_tickets is not None and str(ticket) not in system_tickets:
                continue
            state = self._state.setdefault(
                ticket,
                {
                    "peak_price": float(pos["price_current"]),
                    "last_stop": float(pos.get("sl") or 0.0),
                    "last_take": float(pos.get("tp") or 0.0),
                    "last_action_at": 0.0,
                    "soft_stop_done": False,
                    "last_ai_at": 0.0,
                    "last_ai_periodic_at": 0.0,
                    "ai_future": None,
                    "ai_advice": None,
                    "partial_closed": [],
                },
            )
            current = float(pos["price_current"])
            entry = float(pos["price_open"])
            direction = 1.0 if pos["type"] == "BUY" else -1.0
            if pos["type"] == "BUY":
                state["peak_price"] = max(float(state["peak_price"]), current)
            else:
                state["peak_price"] = min(float(state["peak_price"]), current)

            atr = atr_cache.get(pos["symbol"])
            if atr is None:
                atr = _atr_of(self.gateway, pos["symbol"], config.timeframe, max(entry * 0.005, 1e-9))
                atr_cache[pos["symbol"]] = atr
            contract = _contract_size(self.gateway, pos["symbol"])
            volume = float(pos["volume"])
            bars = self.gateway.get_rates(pos["symbol"], config.timeframe, 300) or []
            bars_held = _bars_since_entry(bars, pos.get("time"))
            current_sl = float(pos.get("sl") or 0.0)
            current_take = float(pos.get("tp") or 0.0)
            adverse = (current - entry) * direction
            peak_profit = (state["peak_price"] - entry) * direction * volume * contract
            current_profit = float(pos.get("profit") or 0.0)
            hard_stop = entry - direction * max(float(config.hard_stop_atr), 0.5) * atr
            max_loss_dist = float(config.max_single_loss_usd) / max(volume * contract, 1e-9)
            max_loss_stop = entry - direction * max_loss_dist if float(config.max_single_loss_usd) > 0 else hard_stop
            pattern_advice: dict[str, Any] = {}
            history_profile: dict[str, float] = {}
            if config.use_pattern_match and (self.matcher or self.repository):
                pattern_advice = self._get_pattern_advice(config, pos["symbol"], pos["type"])
            if config.use_history_profile and self.repository:
                history_profile = self._history_profile(pos["symbol"], pos["type"])
            pattern_take: float | None = None

            candidates: list[float] = []
            actions: list[str] = []
            now = time.time()
            in_cooldown = now - float(state.get("last_action_at") or 0.0) < max(int(config.cooldown_seconds), 0)

            # 硬止损兜底：无止损或止损过宽时先收紧到 ATR 距离
            if current_sl <= 0 or self._better_stop(hard_stop, current_sl, pos["type"], current):
                candidates.append(hard_stop)
                actions.append("硬止损兜底")

            # 单笔最大亏损封顶
            if float(config.max_single_loss_usd) > 0:
                if self._better_stop(max_loss_stop, current_sl, pos["type"], current):
                    candidates.append(max_loss_stop)
                    actions.append("单笔亏损封顶")

            if pattern_advice:
                stop_pct = float(pattern_advice.get("stop_pct") or 0.0)
                take_pct = float(pattern_advice.get("take_pct") or 0.0)
                if stop_pct > 0:
                    candidates.append(entry - direction * stop_pct * entry)
                    actions.append("形态匹配止损")
                if take_pct > 0:
                    pattern_take = entry + direction * take_pct * entry
                    actions.append("形态匹配止盈")
            if history_profile and history_profile.get("med_loss", 0.0) > 0:
                history_loss = float(history_profile["med_loss"])
                history_stop = entry - direction * history_loss / max(volume * contract, 1e-9)
                candidates.append(history_stop)
                actions.append(f"历史统计止损（中位亏损 {history_loss:.0f} 美元/手）")

            soft_stop_hit = (
                config.soft_stop_enabled
                and not state.get("soft_stop_done")
                and 0 < bars_held <= max(int(config.soft_stop_bars), 1)
                and adverse <= -max(float(config.soft_stop_atr), 0.1) * atr
            )
            time_stop_hit = (
                config.time_stop_enabled
                and bars_held >= max(int(config.max_hold_bars), 1)
                and current_profit < max(float(config.time_stop_min_profit_atr), 0.0) * atr * volume * contract
            )
            near_hard_hit = adverse <= -max(float(config.hard_stop_atr), 0.5) * atr * 0.8
            profit_lock_hit = peak_profit >= max(float(config.lock_activation_atr), 0.1) * atr * volume * contract

            ai_advice: dict[str, Any] | None = None
            triggers = set(config.ai_triggers or ["soft_stop", "time_stop", "near_hard", "profit_lock"])
            critical_triggered = (
                ("soft_stop" in triggers and soft_stop_hit)
                or ("time_stop" in triggers and time_stop_hit)
                or ("near_hard" in triggers and near_hard_hit)
                or ("profit_lock" in triggers and profit_lock_hit)
            )
            interval_ok = now - float(state.get("last_ai_at") or 0.0) >= max(int(config.ai_min_interval_seconds), 5)
            periodic_ok = (
                bool(config.ai_periodic_enabled)
                and now - float(state.get("last_ai_periodic_at") or 0.0)
                >= max(int(config.ai_periodic_interval_seconds), 10)
            )
            should_ask_ai = bool(config.ai_enabled) and bool(self.ai_manager) and (interval_ok and critical_triggered or periodic_ok)
            if should_ask_ai:
                state["last_ai_at"] = now
                state["last_ai_periodic_at"] = now
                if not state.get("ai_future"):
                    state["ai_future"] = asyncio.create_task(
                        self._ask_ai(
                            config,
                            pos,
                            bars,
                            atr,
                            current_sl,
                            hard_stop,
                            max_loss_stop,
                            bars_held,
                            actions,
                        )
                    )
            pending = state.get("ai_future")
            if pending and pending.done():
                try:
                    ai_advice = pending.result()
                except Exception:
                    ai_advice = None
                state["ai_future"] = None
                state["ai_advice"] = ai_advice
            elif state.get("ai_advice"):
                ai_advice = state["ai_advice"]
                state["ai_advice"] = None
            if ai_advice:
                if float(ai_advice.get("confidence") or 0.0) < max(float(config.ai_min_confidence), 0.0):
                    ai_advice = None
            if ai_advice:
                actions.append(f"AI 建议：{ai_advice.get('action', 'unknown')}")

            # 软止损：入场后短时间反向过多
            if soft_stop_hit:
                state["soft_stop_done"] = True
                if ai_advice and ai_advice.get("action") == "close" and not in_cooldown:
                    await self.orders.close_position(str(ticket), current)
                    updates.append(
                        {
                            "ticket": ticket,
                            "symbol": pos["symbol"],
                            "action": "AI 软止损平仓",
                            "reason": ai_advice.get("reason") or "AI 建议入场失败离场",
                            "ai_advice": ai_advice,
                            "ok": True,
                        }
                    )
                    continue
                if config.soft_stop_action == "close" and not in_cooldown:
                    await self.orders.close_position(str(ticket), current)
                    updates.append(
                        {
                            "ticket": ticket,
                            "symbol": pos["symbol"],
                            "action": "软止损平仓",
                            "reason": f"入场后 {bars_held} 根反向超过 {config.soft_stop_atr} ATR",
                            "ok": True,
                        }
                    )
                    continue
                breakeven = entry + direction * max(float(config.breakeven_buffer_atr), 0.0) * atr
                candidates.append(breakeven)
                actions.append("软止损保本")

            # 浮盈达标后移到保本
            if config.breakeven_enabled and current_profit >= max(float(config.breakeven_atr), 0.1) * atr * volume * contract:
                breakeven = entry + direction * max(float(config.breakeven_buffer_atr), 0.0) * atr
                candidates.append(breakeven)
                actions.append("浮盈保本")

            # ATR 移动止损
            if config.trailing_enabled:
                if peak_profit >= max(float(config.trailing_activation_atr), 0.1) * atr * volume * contract:
                    trailing_stop = state["peak_price"] - direction * max(float(config.trailing_stop_atr), 0.2) * atr
                    candidates.append(trailing_stop)
                    actions.append("ATR 移动止损")

            # 阶梯锁利：达到一定盈利后锁定回撤
            if config.lock_profit_enabled:
                if peak_profit >= max(float(config.lock_activation_atr), 0.1) * atr * volume * contract:
                    lock_stop = state["peak_price"] - direction * max(float(config.lock_retrace_atr), 0.1) * atr
                    candidates.append(lock_stop)
                    actions.append("阶梯锁利")

            # 分批落袋：达到每手盈利档位后平掉固定比例
            if config.partial_close_enabled and volume > 0.01:
                usd_per_lot = peak_profit / volume
                partial_tiers = pattern_advice.get("partial_close_tiers") or config.partial_close_tiers
                for tier in sorted(
                    partial_tiers,
                    key=lambda x: float(x.get("profit_usd_per_lot", 0)),
                ):
                    tier_profit = float(tier.get("profit_usd_per_lot") or 0.0)
                    if tier_profit <= 0 or tier_profit in (state.get("partial_closed") or []):
                        continue
                    if usd_per_lot >= tier_profit:
                        close_pct = min(max(float(tier.get("close_pct") or 0.0), 1.0), 100.0)
                        close_volume = max(round(volume * close_pct / 100.0, 2), 0.01)
                        if close_volume < volume:
                            await self.orders.close_position(str(ticket), current, volume=close_volume)
                            state.setdefault("partial_closed", []).append(tier_profit)
                            updates.append(
                                {
                                    "ticket": ticket,
                                    "symbol": pos["symbol"],
                                    "action": "分批落袋",
                                    "volume": close_volume,
                                    "reason": f"每手浮盈 {usd_per_lot:.0f} 美元，平 {close_pct:.0f}%",
                                    "ok": True,
                                }
                            )
                        break

            # 阶梯止盈：按每手浮盈档位和回落比例锁定剩余仓位
            if config.ladder_take_enabled and volume > 0:
                usd_per_lot = peak_profit / volume
                ladder_tiers = pattern_advice.get("ladder_tiers") or config.ladder_tiers
                active_tier: dict[str, Any] | None = None
                for tier in sorted(
                    ladder_tiers,
                    key=lambda x: float(x.get("profit_usd_per_lot", 0)),
                ):
                    if usd_per_lot >= float(tier.get("profit_usd_per_lot") or 0.0):
                        active_tier = tier
                if active_tier:
                    retrace_pct = min(max(float(active_tier.get("retrace_pct") or 0.0), 1.0), 90.0) / 100.0
                    retrace_dist = (usd_per_lot * retrace_pct) / max(contract, 1e-9)
                    ladder_stop = state["peak_price"] - direction * retrace_dist
                    candidates.append(ladder_stop)
                    actions.append(
                        f"阶梯止盈（{active_tier.get('profit_usd_per_lot')} 美元/手，回落 {active_tier.get('retrace_pct')}%）"
                    )

            # 关键位跟随
            if config.dynamic_level_enabled and bars:
                recent_low, recent_high = _swing_levels(bars, max(int(config.swing_bars), 3))
                if pos["type"] == "BUY" and recent_low > 0:
                    level_stop = recent_low - max(float(config.level_buffer_atr), 0.0) * atr
                    candidates.append(level_stop)
                    actions.append("支撑位跟随")
                elif pos["type"] == "SELL" and recent_high > 0:
                    level_stop = recent_high + max(float(config.level_buffer_atr), 0.0) * atr
                    candidates.append(level_stop)
                    actions.append("压力位跟随")

            # AI 平仓建议
            if ai_advice and ai_advice.get("action") == "close" and not in_cooldown:
                await self.orders.close_position(str(ticket), current)
                updates.append(
                    {
                        "ticket": ticket,
                        "symbol": pos["symbol"],
                        "action": "AI 建议平仓",
                        "reason": ai_advice.get("reason") or "AI 建议离场",
                        "ai_advice": ai_advice,
                        "ok": True,
                    }
                )
                continue

            # 时间止损
            if time_stop_hit and not in_cooldown:
                await self.orders.close_position(str(ticket), current)
                updates.append(
                    {
                        "ticket": ticket,
                        "symbol": pos["symbol"],
                        "action": "时间止损平仓",
                        "reason": f"持仓 {bars_held} 根未达到 {config.time_stop_min_profit_atr} ATR 盈利",
                        "ok": True,
                    }
                )
                continue

            # AI 移动/保本建议
            if ai_advice and not in_cooldown:
                ai_action = ai_advice.get("action")
                confidence = float(ai_advice.get("confidence") or 0.0)
                if ai_action in ("move", "breakeven") and confidence >= max(float(config.ai_min_confidence), 0.0):
                    if ai_action == "breakeven":
                        ai_stop = entry + direction * max(float(config.breakeven_buffer_atr), 0.0) * atr
                    else:
                        mult = min(max(float(ai_advice.get("stop_multiplier") or 1.0), 0.5), max(float(config.hard_stop_atr), 0.5))
                        ai_stop = entry - direction * mult * atr
                    if self._better_stop(ai_stop, current_sl, pos["type"], current):
                        candidates.append(ai_stop)
                        actions.append("AI 调整止损")

            # AI 止盈建议：跟随峰值移动止盈 / 放宽止盈 / 止盈平仓
            ai_take: float | None = None
            if ai_advice and bool(config.ai_take_profit_enabled) and not in_cooldown:
                take_action = ai_advice.get("take_action")
                confidence = float(ai_advice.get("confidence") or 0.0)
                if take_action == "close" and current_profit > 0 and confidence >= max(float(config.ai_min_confidence), 0.0):
                    await self.orders.close_position(str(ticket), current)
                    updates.append(
                        {
                            "ticket": ticket,
                            "symbol": pos["symbol"],
                            "action": "AI 建议止盈平仓",
                            "reason": ai_advice.get("reason") or "AI 建议锁住利润离场",
                            "ai_advice": ai_advice,
                            "ok": True,
                        }
                    )
                    continue
                if take_action in ("trail", "extend") and confidence >= max(float(config.ai_min_confidence), 0.0):
                    distance = max(float(ai_advice.get("take_distance_atr") or 1.0), 0.1)
                    if take_action == "trail":
                        new_take = state["peak_price"] - direction * distance * atr
                    else:
                        new_take = state["peak_price"] + direction * distance * atr
                    if self._better_take(new_take, current_take, pos["type"]):
                        ai_take = new_take
                        actions.append("AI 调整止盈")

            tp_candidate = ai_take if ai_take is not None else pattern_take
            best_stop = None
            if candidates:
                best_stop = max(candidates) if pos["type"] == "BUY" else min(candidates)
            sl_changed = (
                best_stop is not None
                and self._better_stop(best_stop, current_sl, pos["type"], current)
                and abs(best_stop - float(state.get("last_stop") or 0.0)) > 1e-12
                and not in_cooldown
            )
            tp_changed = (
                tp_candidate is not None
                and self._better_take(tp_candidate, current_take, pos["type"])
                and abs(tp_candidate - float(state.get("last_take") or 0.0)) > 1e-12
                and not in_cooldown
            )
            if not sl_changed and not tp_changed:
                continue
            try:
                await self.orders.modify_position(
                    str(ticket),
                    sl=best_stop if sl_changed else None,
                    tp=tp_candidate if tp_changed else None,
                )
            except Exception as exc:
                updates.append({"ticket": ticket, "symbol": pos["symbol"], "action": "修改止损失败", "ok": False, "error": str(exc)})
                continue
            if sl_changed:
                state["last_stop"] = best_stop
            if tp_changed:
                state["last_take"] = tp_candidate
            state["last_action_at"] = now
            updates.append(
                {
                    "ticket": ticket,
                    "symbol": pos["symbol"],
                    "side": pos["type"],
                    "action": "智能止损更新",
                    "stop": round(best_stop, 5),
                    "take": round(tp_candidate, 5) if tp_candidate is not None else None,
                    "reasons": actions,
                    "ok": True,
                }
            )

        for ticket in list(self._state):
            if ticket not in current_tickets:
                del self._state[ticket]
        self.last_updates = (updates + self.last_updates)[:50]
        if updates and self.notifier:
            await self.notifier.send_alert(
                "info",
                "智能止损引擎",
                f"本次调整 {len(updates)} 笔持仓止损：{'；'.join(str(u['ticket']) for u in updates)}",
            )
        return updates

    def _get_pattern_advice(self, config: SmartStopConfig, symbol: str, side: str) -> dict[str, Any]:
        key = f"{symbol}:{config.timeframe}"
        now = time.time()
        cached = self._pattern_cache.get(key)
        if cached and now - cached[0] < max(int(config.pattern_refresh_seconds), 10):
            return cached[1]
        advice: dict[str, Any] = {}
        if self.repository:
            try:
                side_db = "long" if side == "BUY" else "short"
                for row in self.repository.list_sltp_cases(
                    symbol=symbol,
                    side=side_db,
                    enabled=True,
                    limit=20,
                ):
                    if float(row.confidence or 0.0) < max(float(config.pattern_match_min_similarity), 0.5):
                        continue
                    if int(row.sample_count or 0) < 3:
                        continue
                    strat = row.strategy or {}
                    stop_pct = float(strat.get("suggested_stop_pct") or 0.0) / 100.0
                    take_pct = float(strat.get("suggested_take_pct") or 0.0) / 100.0
                    if stop_pct > 0 and take_pct > 0:
                        advice = {"stop_pct": stop_pct, "take_pct": take_pct}
                        if row.partial_close_tiers:
                            advice["partial_close_tiers"] = row.partial_close_tiers
                        if row.ladder_tiers:
                            advice["ladder_tiers"] = row.ladder_tiers
                        break
            except Exception:
                pass
        if advice:
            self._pattern_cache[key] = (now, advice)
            return advice
        if self.matcher:
            try:
                scan = self.matcher.scan(symbol, config.timeframe, 300)
                for item in scan.get("pattern_matches") or []:
                    if float(item.get("similarity") or 0.0) < max(float(config.pattern_match_min_similarity), 0.5):
                        continue
                    learned = item.get("learned") or {}
                    stop_pct = float(learned.get("suggested_stop_pct") or 0.0)
                    take_pct = float(learned.get("suggested_take_pct") or 0.0)
                    if stop_pct > 0 and take_pct > 0:
                        advice = {"stop_pct": stop_pct / 100.0, "take_pct": take_pct / 100.0}
                        break
                if not advice:
                    for cand in scan.get("candidates") or []:
                        stop_pct = float(cand.get("suggested_stop_pct") or 0.0)
                        take_pct = float(cand.get("suggested_take_pct") or 0.0)
                        if stop_pct > 0 and take_pct > 0:
                            advice = {"stop_pct": stop_pct / 100.0, "take_pct": take_pct / 100.0}
                            break
            except Exception:
                pass
        self._pattern_cache[key] = (now, advice)
        return advice

    def _history_profile(self, symbol: str, side: str) -> dict[str, float]:
        key = f"{symbol}:{side}"
        cached = self._history_cache.get(key)
        if cached:
            return cached
        peak_vals: list[float] = []
        loss_vals: list[float] = []
        if self.repository:
            try:
                trades = self.repository.list_trades(limit=1000)
            except Exception:
                trades = []
            for t in trades:
                if t.symbol != symbol:
                    continue
                lots = float(t.lots or 0.0)
                if lots <= 0:
                    continue
                t_side = "BUY" if t.side in ("long", "buy") else "SELL"
                if t_side != side:
                    continue
                if t.peak_pnl is not None:
                    peak_vals.append(float(t.peak_pnl) / lots)
                if t.pnl is not None and float(t.pnl) < 0:
                    loss_vals.append(abs(float(t.pnl)) / lots)
        profile = {"med_peak": _median(peak_vals), "med_loss": _median(loss_vals)}
        self._history_cache[key] = profile
        return profile

    @staticmethod
    def _better_stop(new_stop: float, current_stop: float, side: str, current: float) -> bool:
        if current_stop <= 0:
            return True
        if side == "BUY":
            return new_stop > current_stop and new_stop < current
        return new_stop < current_stop and new_stop > current

    @staticmethod
    def _better_take(new_take: float, current_take: float, side: str) -> bool:
        if current_take <= 0:
            return True
        if side == "BUY":
            return new_take > current_take
        return new_take < current_take

    async def _ask_ai(
        self,
        config: SmartStopConfig,
        pos: dict[str, Any],
        bars: list[dict[str, Any]],
        atr: float,
        current_sl: float,
        hard_stop: float,
        max_loss_stop: float,
        bars_held: int,
        actions: list[str],
    ) -> dict[str, Any] | None:
        if not self.ai_manager:
            return None
        ai_config = self.ai_manager.active_for("stop_optimizer")
        if not ai_config:
            return None
        prompt = _build_ai_prompt(
            pos,
            bars or [],
            atr,
            current_sl,
            hard_stop,
            max_loss_stop,
            bars_held,
            actions,
            config,
        )
        try:
            raw = await asyncio.wait_for(
                self.ai_manager.generate(
                    ai_config["id"],
                    prompt,
                    system_prompt="你是一名严谨的外汇风控助理，所有输出必须是 JSON。",
                ),
                timeout=max(float(config.ai_timeout_seconds), 1.0),
            )
            advice = _parse_ai_advice(raw)
            if advice.get("action") not in ("move", "keep", "breakeven", "close"):
                return None
            return advice
        except Exception:
            return None
