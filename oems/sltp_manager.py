"""多维融合实时止损止盈引擎：五维输入 + 六单元机制矩阵 + 融合决策（R/ATR 单位）。

对应 docs/止损止盈重构设计方案.md（已定稿）：
- 五维输入：时间 / 行情（MarketAnalysisEngine）/ 指标（RSI·MACD·BOLL·ADX）/ 结构（极值·swing）/ 风控；
- 六单元矩阵：保本 / 移动止损止盈 / 阶梯 / 分批 / 高水位 / 时间 —— 并行评估、Gate 决定激活、非流水线；
- 指标强制事件（阶段 3）：RSI 超买超卖 / MACD 柱转向 / 布林触碰 / 超时未进阶 → 立即锁利上档；
- 时段维度：亚洲时段噪音高 → 倾向早锁利；
- 融合：底线类取最有利、TP 类取最谨慎、离场类保守优先（时间 > 高水位 > 分批）；SL 只朝有利方向；
- 周期执行：update() 由 _system_loop 调用，扫描间隔按波动自适应（5/10/20s）；
- 持仓监控：diagnose() 输出每笔持仓的五维快照 + 本轮激活单元 + 预判动作（仅监控、不执行）。

辅助函数复用 oems/smart_stop.py 的既有实现（_atr_of/_bars_since_entry/_swing_levels/
_format_ai_bars/_parse_ai_advice），旧引擎文件保留不拆。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

import pandas as pd

from observability.notifier import Notifier
from oems.order_manager import OrderManager
from oems.smart_stop import _atr_of, _bars_since_entry, _format_ai_bars, _parse_ai_advice, _swing_levels
from oems.sltp_policy import SltpPolicyConfig


def _better_stop(new_stop: float, current_stop: float, side: str, current: float) -> bool:
    """止损只朝有利方向：BUY 抬高、SELL 压低（沿用 smart_stop 的约定）。"""
    if current_stop <= 0:
        return True
    if side == "BUY":
        return new_stop > current_stop and new_stop < current
    return new_stop < current_stop and new_stop > current


def _better_take(new_take: float, current_take: float, side: str) -> bool:
    if current_take <= 0:
        return True
    if side == "BUY":
        return new_take > current_take
    return new_take < current_take


def _session_of(bars: list[dict[str, Any]]) -> str:
    """按 K 线最后时间（服务器时区口径）划分交易时段。"""
    if not bars:
        return "过渡"
    try:
        h = pd.to_datetime(bars[-1]["time"], utc=True).hour
    except Exception:
        return "过渡"
    if 0 <= h < 6:
        return "亚洲"
    if 6 <= h < 12:
        return "欧洲早盘"
    if 12 <= h < 21:
        return "美洲"
    return "过渡"


class SltpPolicyManager:
    """周期执行的统一止损止盈管理器：五维输入 → 六单元矩阵 → 融合 → 实时调整持仓。"""

    def __init__(
        self,
        gateway: Any,
        orders: OrderManager,
        notifier: Optional[Notifier] = None,
        repository: Any = None,
        ai_manager: Any = None,
        market_analysis: Any = None,
    ) -> None:
        self.gateway = gateway
        self.orders = orders
        self.notifier = notifier
        self.repository = repository
        self.ai_manager = ai_manager
        self.market_analysis = market_analysis
        self._state: dict[int, dict[str, Any]] = {}
        self.last_updates: list[dict[str, Any]] = []
        self.last_scan_at: float = 0.0

    # ---------- 扫描周期（波动自适应） ----------

    @staticmethod
    def scan_interval(policy: SltpPolicyConfig, market: dict[str, Any] | None = None) -> float:
        """高波动 5s / 中 10s / 低 20s；关闭自适应时用基础心跳。"""
        if not policy.scan_adaptive:
            return max(float(policy.scan_interval_seconds), 1.0)
        volatility = str((market or {}).get("volatility") or "中等波动")
        if "高波动" in volatility:
            return 5.0
        if "低波动" in volatility:
            return 20.0
        return 10.0

    # ---------- 主入口 ----------

    async def update(self, policy: SltpPolicyConfig | None = None) -> list[dict[str, Any]]:
        """扫描 MT5 持仓并按五维输入 + 六单元矩阵实时调整止损止盈，返回本次动作记录。"""
        if not policy or not policy.enabled or not self.gateway:
            self._state.clear()
            self.last_updates = []
            return []

        positions = self.gateway.get_positions() or []
        current_tickets: set[int] = set()
        updates: list[dict[str, Any]] = []
        now_ts = time.time()

        for pos in positions:
            ticket = int(pos["ticket"])
            current_tickets.add(ticket)
            if pos["type"] not in ("BUY", "SELL"):
                continue
            plan = await self._assess(policy, pos, now_ts)
            update = self._execute(policy, plan, now_ts)
            if update:
                updates.append(update)
                self.last_updates.insert(0, update)
                self.last_updates = self.last_updates[:50]

        for ticket in list(self._state):
            if ticket not in current_tickets:
                self._state.pop(ticket, None)
        return updates

    # ---------- 持仓监控快照（仅评估、不执行） ----------

    async def diagnose(self, policy: SltpPolicyConfig | None = None) -> list[dict[str, Any]]:
        """输出当前每笔持仓的五维快照 + 本轮激活单元 + 预判动作（监控用途，不改单）。"""
        if not policy or not self.gateway:
            return []
        positions = self.gateway.get_positions() or []
        out: list[dict[str, Any]] = []
        now_ts = time.time()
        for pos in positions:
            ticket = int(pos["ticket"])
            if pos["type"] not in ("BUY", "SELL"):
                continue
            plan = await self._assess(policy, pos, now_ts, with_ai=False)
            recent = next((u for u in self.last_updates if int(u.get("ticket") or 0) == ticket), None)
            dims = plan.get("dims") or {}
            out.append(
                {
                    "ticket": ticket,
                    "symbol": pos["symbol"],
                    "side": pos["type"],
                    "entry": float(pos["price_open"]),
                    "current": float(pos["price_current"]),
                    "sl": float(pos.get("sl") or 0.0),
                    "tp": float(pos.get("tp") or 0.0),
                    "target_sl": plan.get("new_sl") or 0.0,
                    "target_tp": plan.get("new_tp") or 0.0,
                    "volume": float(pos.get("volume") or 0.0),
                    "dims": dims,
                    "active_units": plan.get("fired") or [],
                    "plan_action": plan.get("plan_action") or "保持",
                    "last_update": recent,
                }
            )
        return out

    # ---------- 单持仓评估（六单元矩阵，纯计算） ----------

    async def _assess(
        self,
        policy: SltpPolicyConfig,
        pos: dict[str, Any],
        now_ts: float,
        with_ai: bool = True,
    ) -> dict[str, Any]:
        ticket = int(pos["ticket"])
        side = pos["type"]
        direction = 1.0 if side == "BUY" else -1.0
        symbol = pos["symbol"]
        entry = float(pos["price_open"])
        current = float(pos["price_current"])
        atr = _atr_of(self.gateway, symbol, policy.timeframe, float(pos["price_open"]) * 0.005)
        r_unit = max(policy.risk_mult * atr, atr * 0.1)
        current_sl = float(pos.get("sl") or 0.0)
        current_tp = float(pos.get("tp") or 0.0)

        state = self._state.setdefault(
            ticket,
            {
                "peak_price": current,
                "peak_profit": float(pos.get("profit") or 0.0),
                "partial_closed": 0.0,
                "partial_at": set(),
                "last_modify_at": 0.0,
            },
        )
        if side == "BUY":
            state["peak_price"] = max(float(state.get("peak_price") or current), current)
        else:
            state["peak_price"] = min(float(state.get("peak_price") or current), current)

        # —— 五维输入 ——
        bars = self.gateway.get_rates(symbol, policy.timeframe, 300) or []
        indicators = self._indicators(bars)
        bars_held = _bars_since_entry(bars, pos.get("time") or pos.get("open_time")) if bars else 0
        market: dict[str, Any] = {}
        if self.market_analysis:
            try:
                market = self.market_analysis.analyze(symbol, policy.timeframe)
            except Exception:
                market = {}
        gates = self._gates(policy, market, indicators, bars)

        # 盈利按价格距离折算 R（R 体系统一，不与手数/美元挂钩）
        profit_r = (current - entry) * direction / r_unit
        peak_r = (float(state["peak_price"]) - entry) * direction / r_unit

        stop_candidates: list[float] = []
        take_candidates: list[float] = []
        fired: list[str] = []
        close_action: str | None = None  # "time" | "high_watermark" | "ai_close"

        # —— ① 保本单元 ——
        if policy.breakeven_enabled and profit_r >= policy.breakeven_trigger_r:
            stop_candidates.append(entry + direction * policy.breakeven_buffer_atr * atr)
            fired.append("保本")

        # —— ② 移动止损止盈单元（结构维度基线：极值追踪 + 触 TP 锁利上移） ——
        if policy.trailing_enabled:
            if peak_r >= policy.trailing_activation_r:
                stop_candidates.append(current - direction * policy.trailing_stop_atr * atr)
                fired.append("移动止损")
            if self._take_hit(side, current, current_tp):
                new_tp = float(state["peak_price"]) - direction * policy.trailing_take_atr * atr
                if _better_take(new_tp, current_tp, side):
                    take_candidates.append(new_tp)
                    fired.append("移动止盈")

        # —— ③ 阶梯止盈单元（档位底线，只进不退 + 指标/超时强制事件） ——
        if policy.ladder_enabled:
            adv = policy.ladder_advance or {}
            reached = 0
            for tier in policy.ladder_tiers:
                trigger_r = float(tier.get("trigger_r") or 0.0)
                if peak_r >= trigger_r and self._gate_ok(tier.get("gate") or {}, gates):
                    reached += 1
            force_lvl = reached
            if adv.get("time_force_bars") and bars_held >= int(adv["time_force_bars"]):
                force_lvl = max(force_lvl, min(reached + 1, len(policy.ladder_tiers)))
                fired.append("超时强制上档")
            if (
                adv.get("rsi_extreme_force") and gates.get("rsi_extreme")
            ) or (
                adv.get("macd_reverse_force") and gates.get("macd_reverse")
            ) or (
                adv.get("boll_touch_force") and gates.get("boll_touch")
            ):
                force_lvl = max(force_lvl, 1)
                fired.append("指标强制锁利")
            if gates.get("session") == "亚洲":
                force_lvl = max(force_lvl, 1)
                fired.append("亚洲时段早锁利")
            for idx, tier in enumerate(policy.ladder_tiers):
                if idx < force_lvl:
                    raise_sl_r = float(tier.get("raise_to_r") or 0.0)
                    stop_candidates.append(entry + direction * raise_sl_r * r_unit)
                    if idx < reached:
                        fired.append(f"阶梯{float(tier.get('trigger_r') or 0.0):g}R")

        # —— ④ 分批落袋单元（分档兑现，每档一次） ——
        partial_lots: float | None = None
        partial_idx: int | None = None
        partial_tier: str | None = None
        if policy.partial_close_enabled:
            for idx, tier in enumerate(policy.partial_close_tiers):
                if idx in state["partial_at"]:
                    continue
                trigger_r = float(tier.get("trigger_r") or 0.0)
                if peak_r >= trigger_r and self._gate_ok(tier.get("gate") or {}, gates):
                    close_pct = float(tier.get("close_pct") or 10.0)
                    volume = float(pos.get("volume") or 0.0)
                    remaining = max(volume - float(state["partial_closed"]), 0.0)
                    lots = round(remaining * close_pct / 100.0, 2)
                    if lots > 0:
                        partial_lots = lots
                        partial_idx = idx
                        partial_tier = f"{trigger_r:g}R"
                        fired.append(f"分批{trigger_r:g}R")
                        break  # 本轮只触发一个未兑现档（最接近触发点）

        # —— ⑤ 高水位离场单元（峰值回落超容忍 → 全平） ——
        if (
            policy.high_watermark_enabled
            and peak_r >= policy.hw_activation_r
            and not close_action
        ):
            retrace_atr = (float(state["peak_price"]) - current) * direction / atr
            if retrace_atr > policy.hw_retrace_atr:
                close_action = "high_watermark"
                fired.append("高水位回落")

        # —— ⑥ 时间止损单元（超时未达标 → 离场或收紧） ——
        if (
            policy.time_stop_enabled
            and bars_held >= policy.time_stop_bars
            and profit_r < policy.time_stop_min_profit_r
            and not close_action
        ):
            if policy.time_stop_action == "close":
                close_action = "time"
                fired.append("时间止损")
            else:
                stop_candidates.append(entry + direction * policy.breakeven_buffer_atr * atr)
                fired.append("时间收紧")

        # —— AI 辅助层 L6（可选，默认关闭） ——
        if with_ai and policy.ai_enabled and self.ai_manager and self._ai_trigger_hit(policy, close_action, fired):
            advice = await self._ask_ai(policy, pos, bars, atr, current_sl, current_tp, bars_held, fired)
            if advice:
                action = advice.get("action")
                if action == "close":
                    close_action = "ai_close"
                elif action == "breakeven":
                    stop_candidates.append(entry + direction * policy.breakeven_buffer_atr * atr)
                    fired.append("AI保本")
                elif action == "trail":
                    stop_candidates.append(current - direction * policy.trailing_stop_atr * atr)
                    fired.append("AI追踪")
                elif action == "move" and advice.get("price"):
                    stop_candidates.append(float(advice["price"]))
                    fired.append("AI移损")

        # —— 融合决策 ——
        new_sl = current_sl
        if stop_candidates:
            cand = max(stop_candidates) if side == "BUY" else min(stop_candidates)
            if _better_stop(cand, current_sl, side, current):
                new_sl = cand
        new_tp = current_tp
        if take_candidates:
            cand = min(take_candidates) if side == "BUY" else max(take_candidates)
            if _better_take(cand, current_tp, side):
                new_tp = cand

        plan_action = "保持"
        if close_action:
            plan_action = "全平"
        elif partial_lots and partial_idx is not None:
            plan_action = f"分批平仓 {partial_lots} 手"
        elif new_sl != current_sl or new_tp != current_tp:
            plan_action = "调整 SL/TP"

        dims = {
            "bars_held": bars_held,
            "session": gates.get("session") or "过渡",
            "regime": gates.get("regime") or "weak",
            "rsi": indicators.get("rsi"),
            "adx": gates.get("adx"),
            "volatility": market.get("volatility") or "中等波动",
            "trend_direction": market.get("trend_direction") or "flat",
            "atr": atr,
            "profit_r": round(profit_r, 2),
            "peak_r": round(peak_r, 2),
        }

        return {
            "ticket": ticket,
            "symbol": symbol,
            "side": side,
            "direction": direction,
            "entry": entry,
            "current": current,
            "atr": atr,
            "r_unit": r_unit,
            "sl_before": current_sl,
            "tp_before": current_tp,
            "new_sl": new_sl,
            "new_tp": new_tp,
            "close_action": close_action,
            "partial_lots": partial_lots,
            "partial_idx": partial_idx,
            "partial_tier": partial_tier,
            "fired": fired,
            "dims": dims,
            "plan_action": plan_action,
        }

    # ---------- 执行（动作 + 冷却 + 状态） ----------

    def _execute(self, policy: SltpPolicyConfig, plan: dict[str, Any], now_ts: float) -> dict[str, Any] | None:
        ticket = int(plan["ticket"])
        symbol = plan["symbol"]
        side = plan["side"]
        fired = plan["fired"]
        state = self._state.get(ticket)
        if state is None:
            return None
        close_action = plan["close_action"]
        partial_lots = plan["partial_lots"]
        partial_idx = plan["partial_idx"]
        cooled = now_ts - float(state.get("last_modify_at") or 0.0) >= policy.modify_cooldown_seconds

        if close_action:
            self.gateway.close_position(ticket)
            state["last_modify_at"] = now_ts
            return self._record(ticket, symbol, side, "全平", fired, f"离场原因：{close_action}", True)
        if partial_lots and partial_idx is not None:
            self.gateway.close_position(ticket, volume=partial_lots)
            state["partial_at"].add(partial_idx)
            state["partial_closed"] = float(state.get("partial_closed") or 0.0) + partial_lots
            state["last_modify_at"] = now_ts
            return self._record(ticket, symbol, side, f"分批平仓 {partial_lots} 手", fired, f"落袋档位：{plan['partial_tier']}", True)
        if (plan["new_sl"] != plan["sl_before"] or plan["new_tp"] != plan["tp_before"]) and cooled:
            self.gateway.modify_position(ticket, sl=plan["new_sl"] if plan["new_sl"] != 0.0 else None, tp=plan["new_tp"] if plan["new_tp"] != 0.0 else None)
            state["last_modify_at"] = now_ts
            return self._record(ticket, symbol, side, "调整 SL/TP", fired, f"SL→{plan['new_sl']:.5f}；TP→{plan['new_tp']:.5f}", True)
        if fired:
            return self._record(ticket, symbol, side, "保持", fired, "冷却或无需调整", False)
        return None

    # ---------- 维度计算与 Gate ----------

    @staticmethod
    def _indicators(bars: list[dict[str, Any]]) -> dict[str, Any]:
        if not bars:
            return {}
        df = pd.DataFrame(bars)
        if df.empty or "close" not in df:
            return {}
        from indicators.technical import adx_series, bollinger, macd, rsi

        close = df["close"].astype(float)
        out: dict[str, Any] = {"close": float(close.iloc[-1])}
        if len(close) > 15:
            rsi_vals = rsi(close, 14).dropna()
            if len(rsi_vals):
                out["rsi"] = float(rsi_vals.iloc[-1])
        if len(df) > 30:
            adx_vals = adx_series(df, 14).dropna()
            if len(adx_vals):
                out["adx"] = float(adx_vals.iloc[-1])
        if len(close) > 30:
            _, _, hist = macd(close)
            hist_vals = hist.dropna()
            if len(hist_vals) >= 2:
                out["macd_hist"] = float(hist_vals.iloc[-1])
                out["macd_hist_prev"] = float(hist_vals.iloc[-2])
        if len(close) > 20:
            up, _, lo = bollinger(close)
            if not up.isna().all():
                out["boll_upper"] = float(up.iloc[-1])
                out["boll_lower"] = float(lo.iloc[-1])
        return out

    @staticmethod
    def _gates(
        policy: SltpPolicyConfig,
        market: dict[str, Any],
        ind: dict[str, Any],
        bars: list[dict[str, Any]],
    ) -> dict[str, Any]:
        trend = str(market.get("trend_direction") or "flat")
        adx = float(market.get("adx") or ind.get("adx") or 0.0)
        regime = "weak"
        if adx >= policy.adx_strong and trend in ("up", "down"):
            regime = f"strong_{trend}"
        elif "震荡" in str(market.get("trend") or ""):
            regime = "range"
        rsi_extreme = False
        if ind.get("rsi") is not None:
            rsi_extreme = bool(ind["rsi"] >= policy.rsi_ob or ind["rsi"] <= policy.rsi_os)
        macd_reverse = False
        if ind.get("macd_hist") is not None and ind.get("macd_hist_prev") is not None:
            macd_reverse = bool((ind["macd_hist"] > 0) != (ind["macd_hist_prev"] > 0))
        boll_touch = False
        if ind.get("close") is not None and ind.get("boll_upper") is not None:
            boll_touch = bool(ind["close"] >= ind["boll_upper"] or ind["close"] <= ind["boll_lower"])
        return {
            "regime": regime,
            "rsi_extreme": rsi_extreme,
            "macd_reverse": macd_reverse,
            "boll_touch": boll_touch,
            "adx": round(adx, 1),
            "session": _session_of(bars),
        }

    @staticmethod
    def _gate_ok(gate: dict[str, Any], g: dict[str, Any]) -> bool:
        require = gate.get("require_regime") or []
        deny = gate.get("deny_regime") or []
        if isinstance(require, str):
            require = [require]
        if isinstance(deny, str):
            deny = [deny]
        if require and g["regime"] not in require:
            return False
        if deny and g["regime"] in deny:
            return False
        if gate.get("require_rsi_extreme") and not g["rsi_extreme"]:
            return False
        if gate.get("deny_rsi_extreme") and g["rsi_extreme"]:
            return False
        return True

    @staticmethod
    def _take_hit(side: str, current: float, last_tp: float) -> bool:
        if last_tp <= 0:
            return False
        return bool(current >= last_tp if side == "BUY" else current <= last_tp)

    # ---------- AI 辅助层 L6 ----------

    @staticmethod
    def _ai_trigger_hit(policy: SltpPolicyConfig, close_action: str | None, fired: list[str]) -> bool:
        triggers = policy.ai_triggers or []
        if "time_stop" in triggers and "时间止损" in fired:
            return True
        if any(t in ("high_watermark", "profit_lock") for t in triggers) and any(
            "高水位" in f or "阶梯" in f for f in fired
        ):
            return True
        return False

    async def _ask_ai(
        self,
        policy: SltpPolicyConfig,
        pos: dict[str, Any],
        bars: list[dict[str, Any]],
        atr: float,
        current_sl: float,
        current_tp: float,
        bars_held: int,
        fired: list[str],
    ) -> dict[str, Any] | None:
        if not self.ai_manager:
            return None
        bars_text = _format_ai_bars(bars, 30)
        prompt = (
            f"当前持仓：{pos['symbol']} {pos['type']} 开仓价 {float(pos['price_open']):.5f} "
            f"现价 {float(pos['price_current']):.5f} ATR {atr:.6f} 已持 {bars_held} 根\n"
            f"SL {current_sl:.5f} TP {current_tp:.5f} 已激活单元：{'、'.join(fired) or '无'}\n"
            f"K线（最近30根，含high/low/close）：\n{bars_text}\n"
            f"请只输出 JSON：{{\"action\": \"move|keep|trail|breakeven|close\", \"price\": 止损价(可选), \"reason\": \"中文理由\"}}"
        )
        try:
            raw = await asyncio.wait_for(
                self.ai_manager.generate(
                    "default",
                    prompt,
                    system_prompt="你是一名严谨的外汇风控助理，所有输出必须是 JSON。",
                ),
                timeout=8.0,
            )
        except Exception:
            return None
        advice = _parse_ai_advice(raw)
        if advice.get("action") not in ("move", "keep", "trail", "breakeven", "close"):
            return None
        return advice

    # ---------- 记录 ----------

    @staticmethod
    def _record(ticket: int, symbol: str, side: str, action: str, fired: list[str], reason: str, ok: bool) -> dict[str, Any]:
        return {
            "ticket": ticket,
            "symbol": symbol,
            "side": side,
            "action": action,
            "units": fired,
            "reason": reason,
            "ok": ok,
            "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        }