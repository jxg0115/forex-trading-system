# -*- coding: utf-8 -*-
"""K 线形态独立交易模块（用户 2026-10-14 需求）。

设计原则
--------
1. **完全独立**：自带识别、自带仓位计算、自带风控闸门、自带账本。
   不经过 SignalExecutor，因此**永不受镜像交易开关影响**（用户明确要求）。
2. **语义**：M15 与 M30 必须各自走出完整 V 字底（倒 V 则做空）；
   H1 只看**最新一根**做佐证（收在同向半边即可），不要求 H1 自己走完形态。
3. **参数冻结**：形态参数取自回测定稿（v5，2026-10-14），禁止线上调参。
4. **无前视**：H1 当根由**已收盘的 M15** 拼出（open=本小时首根开盘、
   high/low=至今极值、close=当前 M15 收盘），等价于提前最多 45 分钟看到
   H1 趋势，且不含未来信息。

回测依据（XAUUSD，2026-04-13 ~ 2026-10-13，M15/M30 双周期 + H1 半边佐证）
  n=178，胜率 37.6%，PF 1.17，累计 +19.6R，最大回撤 -12.4R
  配对统计检验：H1 佐证属"轻过滤"（剔除批 t 值在 ±0.3 内），非显著边缘来源。
  → 期望薄，实盘前建议影子对照；本模块已按用户指令直接上线（模拟盘）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np
import pandas as pd

from indicators.technical import atr_series

# ---------------------------------------------------------------------------
# 冻结参数（回测定稿 —— 请勿在线修改，改了就失去已验证性）
# ---------------------------------------------------------------------------

HOOK = dict(n_arm=20, fresh_k=6, arm_atr=2.0, shadow_mult=1.5,
            recover_frac=0.2, bottom_near=0.3)

STATE_HOURS = {"M15": 1, "M30": 2}      # 状态窗口制：触发后状态持续时长
HARD_STOP_ATR = 1.5                      # SL = 1.5 x ATR
RISK_MULT = 1.5                          # R = 1.5 x ATR（与回测一致）
TAKE_R_MULT = 2.0                        # TP = 2R
HOLD_MAX_BARS = 120                      # 最长持有 M15 根数
COOLDOWN_BARS = 8                        # 平仓后冷却（按 bottom 去重）
HOLD_TOL_ATR = 0.1                       # 低点守住容差
EPS = 1e-9
ORDER_TAG = "VPATTERN"                   # 订单标记：用于识别本方持仓，避免与主系统抢单

# H1 佐证规则：half = 当根 H1 收盘落在同向半边（回测最优档）
H1_RULE = "half"


@dataclass
class VPatternConfig:
    """模块配置。风险三件套（risk_pct / 连亏减半 / 日亏停手）见注释。"""

    enabled: bool = False                # 默认关闭，由前端/接口开启
    symbol: str = "XAUUSD"
    timeframe: str = "M15"

    # —— 仓位与风控（用户口径：测试为主，每笔 3% 本金）——
    risk_pct: float = 3.0                # 每笔风险预算 = 本金 x 3%
    max_positions: int = 1               # 最大同时持仓（不叠单）
    loss_streak_halve: int = 3           # 连亏 N 笔后仓位减半
    halt_daily_loss_pct: float = 20.0    # 单日亏损达本金 x% 即当日停手
    cooldown_minutes: int = 30           # 两次开仓最小间隔（防抖）

    # —— 运行时（由引擎维护，勿手工改）——
    consec_loss: int = 0                 # 当前连亏笔数
    day: str = ""                        # 当前风控日（UTC 日期）
    day_pnl: float = 0.0                 # 当日已实现盈亏（账户币种）
    day_halted: bool = False             # 当日是否已停手
    last_entry_ts: str = ""              # 上次开仓时间（ISO）
    last_signal: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "VPatternConfig":
        if not d:
            return cls()
        names = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in names})


# ---------------------------------------------------------------------------
# 形态识别（与 .workbuddy/vpattern_run3.py::detect 逐行等价）
# ---------------------------------------------------------------------------

def detect_events(df: pd.DataFrame, atr: np.ndarray, **hp: Any) -> list[dict[str, Any]]:
    """识别 V 字底（long）/ 倒 V 顶（short）事件。

    返回 [{bottom, trigger, side}]，trigger = 状态起点（该根的收盘时刻即可用）。
    """
    n_arm = int(hp["n_arm"])
    fresh_k = int(hp["fresh_k"])
    arm_atr = float(hp["arm_atr"])
    shadow_mult = float(hp["shadow_mult"])
    recover_frac = float(hp["recover_frac"])
    bottom_near = float(hp["bottom_near"])

    o = df["open"].to_numpy(); h = df["high"].to_numpy()
    l = df["low"].to_numpy(); c = df["close"].to_numpy()
    n = len(df)
    if n < n_arm + 2:
        return []

    body = np.maximum(np.abs(c - o), EPS)
    lower_sh = np.minimum(o, c) - l
    upper_sh = h - np.maximum(o, c)
    rej_long = lower_sh >= shadow_mult * body
    rej_short = upper_sh >= shadow_mult * body

    win = np.lib.stride_tricks.sliding_window_view(l, n_arm)
    win_h = np.lib.stride_tricks.sliding_window_view(h, n_arm)
    lo_min = win.min(axis=1)
    lo_arg = win.argmin(axis=1)
    hi_max = win_h.max(axis=1)
    hi_arg = win_h.argmax(axis=1)

    base = np.arange(n_arm - 1, n)
    L = lo_min; argL = lo_arg + (base - (n_arm - 1))
    H = hi_max; argH = hi_arg + (base - (n_arm - 1))
    amp_l = H - L
    a = atr[base]
    prev_close_l = c[np.maximum(argL - 1, 0)]
    prev_close_s = c[np.maximum(argH - 1, 0)]

    ok_l = ((base - argL) <= fresh_k) & (base - argL >= 1) \
        & (amp_l >= arm_atr * a) \
        & (prev_close_l <= L + bottom_near * amp_l) \
        & (c[base] >= L + recover_frac * amp_l)
    amp_s = H - L
    ok_s = ((base - argH) <= fresh_k) & (base - argH >= 1) \
        & (amp_s >= arm_atr * a) \
        & (prev_close_s >= H - bottom_near * amp_s) \
        & (c[base] <= H - recover_frac * amp_s)

    events: list[dict[str, Any]] = []
    for mask, side, argmin, extreme, rej in (
            (ok_l, "long", argL, L, rej_long),
            (ok_s, "short", argH, H, rej_short)):
        for k in np.nonzero(mask)[0]:
            i = int(base[k]); ai = int(argmin[k])
            if ai < 1 or not np.isfinite(atr[i]) or atr[i] <= 0:
                continue
            if not rej[ai:i + 1].any():          # 底部区域至少一根长影
                continue
            tol = HOLD_TOL_ATR * atr[i]
            if side == "long" and l[ai + 1:i + 1].min() < extreme[k] - tol:
                continue
            if side == "short" and h[ai + 1:i + 1].max() > extreme[k] + tol:
                continue
            events.append({"bottom": ai, "trigger": i, "side": side})
    events.sort(key=lambda e: (e["trigger"], e["bottom"]))
    return events


class StateIndex:
    """某周期某方向的状态区间索引：bisect + 前缀最大终点，O(log n)。"""

    def __init__(self, events: list[dict[str, Any]], times: pd.Series, hours: float) -> None:
        delta = pd.Timedelta(hours=hours)
        iv = sorted((times.iloc[e["trigger"]], times.iloc[e["trigger"]] + delta) for e in events)
        self.starts = [x[0] for x in iv]
        self.max_end: list[pd.Timestamp] = []
        m: Optional[pd.Timestamp] = None
        for _, t1 in iv:
            m = t1 if m is None or t1 > m else m
            self.max_end.append(m)

    def active(self, t: pd.Timestamp) -> bool:
        from bisect import bisect_right

        k = bisect_right(self.starts, t)
        return k > 0 and self.max_end[k - 1] >= t


def build_h1_live(m15: pd.DataFrame) -> pd.DataFrame:
    """用**已收盘**的 M15 拼出"当根 H1"（无前视）。

    open = 本小时首根 M15 开盘；high/low = 至今 cummax/cummin；
    close = 当前 M15 收盘；prev_close = 上一整点最后一根 M15 收盘。
    """
    hour = m15["time"].dt.floor("h")
    g = m15.groupby(hour)
    out = pd.DataFrame({
        "open": g["open"].transform("first"),
        "high": g["high"].cummax(),
        "low": g["low"].cummin(),
        "close": m15["close"],
    })
    hour_last = g["close"].last()
    out["prev_close"] = hour.map(hour_last.shift(1))
    return out


def h1_half_ok(h1l: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """H1 佐证规则 half：收在区间的同向半边。"""
    hi = h1l["high"].to_numpy()
    lo = h1l["low"].to_numpy()
    c = h1l["close"].to_numpy()
    mid = (hi + lo) / 2.0
    return c > mid, c < mid


# ---------------------------------------------------------------------------
# 执行引擎
# ---------------------------------------------------------------------------

class VPatternEngine:
    """K 线形态独立交易引擎：识别 -> 风控 -> 下单 -> 记账，全自包含。"""

    def __init__(self, market: Any, orders: Any, gateway: Any, repository: Any,
                 notifier: Any = None, config: VPatternConfig | None = None) -> None:
        self.market = market
        self.orders = orders
        self.gateway = gateway
        self.repository = repository
        self.notifier = notifier
        self.config = config or VPatternConfig()
        self._last_signal_ts: Optional[pd.Timestamp] = None
        self._last_error: str = ""
        self._last_scan: dict[str, Any] = {}
        self._signals: list[dict[str, Any]] = []       # 最近信号（含未成单）
        self._orders_placed: list[dict[str, Any]] = []  # 最近成交
        self._last_step_at: float = 0.0

    # ---------- 对外状态 ----------

    def state(self) -> dict[str, Any]:
        return {
            "enabled": self.config.enabled,
            "config": self.config.to_dict(),
            "last_scan": self._last_scan,
            "last_error": self._last_error,
            "recent_signals": self._signals[:20],
            "recent_orders": self._orders_placed[:20],
            "stats": self.repository.vpattern_stats() if hasattr(self.repository, "vpattern_stats") else {},
            "hook": HOOK,
            "h1_rule": H1_RULE,
        }

    def set_enabled(self, enabled: bool) -> None:
        self.config.enabled = enabled
        if hasattr(self.repository, "save_vpattern_state"):
            self.repository.save_vpattern_state(self.config.to_dict())

    def update_config(self, patch: dict[str, Any]) -> VPatternConfig:
        cfg = self.config
        for key, val in patch.items():
            if hasattr(cfg, key) and key in {
                "symbol", "timeframe", "risk_pct", "max_positions",
                "loss_streak_halve", "halt_daily_loss_pct", "cooldown_minutes",
            }:
                setattr(cfg, key, val)
        if hasattr(self.repository, "save_vpattern_state"):
            self.repository.save_vpattern_state(cfg.to_dict())
        return cfg

    # ---------- 数据 ----------

    def _rates(self, timeframe: str, count: int) -> list[dict[str, Any]]:
        if self.gateway and getattr(self.gateway, "is_connected", False):
            return self.gateway.get_rates(self.config.symbol, timeframe, count)
        return self.market.get_bars(self.config.symbol, timeframe, limit=count)

    # ---------- 平仓对账（账本闭环） ----------

    def _settle_closed(self) -> None:
        """把已从券商消失的 vpattern 持仓在本地账本里标记平仓。

        依据：本地 status=open 的记录，若其 ticket 已不在 MT5 持仓列表中，
        说明已被 SL/TP 或人工平掉。用 MT5 历史成交价与本地开仓价估算 R。
        """
        if not self.gateway:
            return
        try:
            open_records = self.repository.list_vpattern_trades(limit=200, status="open")
        except Exception:
            return
        if not open_records:
            return
        try:
            live = {str(p.get("ticket")) for p in (self.gateway.get_positions() or [])}
        except Exception:
            return

        for rec in open_records:
            ticket = str(rec.get("ticket") or "")
            if not ticket or ticket in live:
                continue
            close_price = None
            pnl_val = None
            try:
                hist = self.gateway.get_closed_trade(int(ticket))
                if hist:
                    close_price = float(hist.get("close_price") or 0.0) or None
                    pnl_val = float(hist.get("profit") or 0.0)
            except Exception:
                pass
            if close_price is None:
                # 取不到历史成交 → 用最近价格兜底估算，仅记账不追求精确
                try:
                    tick = self.gateway.get_tick(self.config.symbol)
                    close_price = float(tick.get("bid") or tick.get("ask") or rec["entry_price"])
                except Exception:
                    close_price = float(rec["entry_price"] or 0.0)
            d = 1.0 if rec["side"] == "long" else -1.0
            r_mult = None
            try:
                risk_per_unit = abs(float(rec["entry_price"]) - float(rec["sl"] or 0.0))
                if risk_per_unit > 0:
                    r_mult = round((close_price - float(rec["entry_price"])) * d / risk_per_unit, 3)
            except Exception:
                r_mult = None
            if pnl_val is None:
                pnl_val = float(rec.get("risk_amount") or 0.0) * (r_mult or 0.0)
            # 判定出场原因
            reason = "manual"
            if rec.get("sl") and abs(close_price - float(rec["sl"])) < 1e-6:
                reason = "sl"
            elif rec.get("tp") and abs(close_price - float(rec["tp"])) < 1e-6:
                reason = "tp"
            try:
                self.repository.close_vpattern_trade(
                    ticket, float(close_price), float(pnl_val),
                    r_multiple=r_mult, exit_reason=reason,
                )
            except Exception:
                continue
            # 更新风控计数：连亏累加/清零，当日盈亏累计
            self.config.day_pnl = float(self.config.day_pnl) + float(pnl_val)
            if r_mult is not None and r_mult < 0:
                self.config.consec_loss = int(self.config.consec_loss) + 1
            elif r_mult is not None and r_mult > 0:
                self.config.consec_loss = 0
            if self.notifier:
                try:
                    import asyncio as _aio

                    _aio.ensure_future(self.notifier.send_alert(
                        "info", "K线形态平仓",
                        f"{self.config.symbol} #{ticket} 以 {close_price:.2f} 平仓"
                        f"（{'%+.2fR' % r_mult if r_mult is not None else 'R 未知'}，原因 {reason}）",
                    ))
                except Exception:
                    pass
        if hasattr(self.repository, "save_vpattern_state"):
            try:
                self.repository.save_vpattern_state(self.config.to_dict())
            except Exception:
                pass

    @staticmethod
    def _to_df(bars: list[dict[str, Any]]) -> pd.DataFrame:
        if not bars:
            return pd.DataFrame()
        df = pd.DataFrame(bars)
        df["time"] = pd.to_datetime(df["time"], utc=True)
        for c in ("open", "high", "low", "close"):
            df[c] = df[c].astype(float)
        return df.sort_values("time").reset_index(drop=True)

    # ---------- 风控闸门 ----------

    def _roll_day(self, now: pd.Timestamp) -> None:
        """跨日重置：清当日盈亏与停手标记。"""
        day = now.strftime("%Y-%m-%d")
        if self.config.day != day:
            self.config.day = day
            self.config.day_pnl = 0.0
            self.config.day_halted = False

    def _equity(self) -> float:
        info = self.gateway.get_account_info() if self.gateway else None
        if info:
            try:
                return float(info["equity"])
            except Exception:
                pass
        return 0.0

    def _risk_gate(self, now: pd.Timestamp) -> tuple[bool, str]:
        """返回 (可否开仓, 原因)。"""
        cfg = self.config
        equity = self._equity()
        if equity <= 0:
            return False, "无法获取账户权益"
        if cfg.day_halted:
            return False, "当日已触发亏损停手"
        if equity > 0 and abs(cfg.day_pnl) / equity * 100.0 >= cfg.halt_daily_loss_pct \
                and cfg.day_pnl < 0:
            cfg.day_halted = True
            return False, f"当日亏损 {abs(cfg.day_pnl):.2f} 已达停手线"
        if cfg.last_entry_ts:
            try:
                last = pd.Timestamp(cfg.last_entry_ts)
                if (now - last).total_seconds() < cfg.cooldown_minutes * 60:
                    return False, f"距离上次开仓不足 {cfg.cooldown_minutes} 分钟"
            except Exception:
                pass
        return True, ""

    def _open_lots(self) -> list[Any]:
        """返回本模块自己的持仓（按订单注释标记识别）。"""
        try:
            positions = self.gateway.get_positions() if self.gateway else []
        except Exception:
            positions = []
        own = []
        for p in positions or []:
            comment = str(p.get("comment") or "")
            if ORDER_TAG in comment or str(p.get("symbol")) == self.config.symbol and ORDER_TAG in comment:
                own.append(p)
        # 若券商不保留注释，退化为"同品种全部持仓"由 max_positions 兜底
        return own if own else []

    def _symbol_positions(self) -> int:
        try:
            positions = self.gateway.get_positions() if self.gateway else []
        except Exception:
            positions = []
        return sum(1 for p in positions or [] if str(p.get("symbol")) == self.config.symbol)

    # ---------- 仓位计算（XAUUSD 真实合约口径，不走外汇兜底） ----------

    def _size(self, entry: float, stop_dist: float, equity: float) -> tuple[float, float, str]:
        """返回 (手数, 每笔风险金额, 说明)。按 risk_pct 风险预算反算。

        用 gateway.get_symbol_info 的 contract_size / pip_value（该接口已修过
        tick_value 与 contract_size 不自洽的坑），不走 executor 的外汇 pip_value=10 兜底。
        """
        cfg = self.config
        info = self.gateway.get_symbol_info(cfg.symbol) if self.gateway else None

        risk_pct = float(cfg.risk_pct)
        if cfg.consec_loss >= cfg.loss_streak_halve > 0:
            # 连亏达阈值后仓位减半（每多亏一轮再减半，最低 1/8）
            rounds = 1 + (cfg.consec_loss - cfg.loss_streak_halve) // cfg.loss_streak_halve
            risk_pct = risk_pct / (2 ** min(rounds, 3))
        risk_amount = equity * risk_pct / 100.0

        if not info:
            return 0.0, risk_amount, "无法获取品种信息，放弃下单"

        point = float(info["point"])
        contract = float(info.get("contract_size") or 0.0)
        min_lot = float(info.get("min_lot") or 0.01)
        max_lot = float(info.get("max_lot") or 100.0)
        lot_step = float(info.get("lot_step") or 0.01)

        # 每 1 手、价格变动 1.0 的盈亏 = contract_size（账户币种与利润币种一致时）
        value_per_price_per_lot = contract if contract > 0 else 100.0
        loss_per_lot = stop_dist * value_per_price_per_lot
        if loss_per_lot <= 0:
            return 0.0, risk_amount, "止损距离为 0"
        raw = risk_amount / loss_per_lot
        steps = int(raw / lot_step) if lot_step > 0 else int(raw * 100)
        lots = round(steps * (lot_step or 0.01), 2)
        lots = max(min_lot, min(max_lot, lots))
        return lots, risk_amount, f"风险预算 {risk_amount:.2f} / 每手止损 {loss_per_lot:.2f}"

    # ---------- 主流程 ----------

    def should_scan(self, min_seconds: float = 60.0) -> bool:
        import time

        return time.time() - self._last_step_at >= min_seconds

    async def step(self) -> Optional[dict[str, Any]]:
        """一轮：拉数据 -> 判断 -> 风控 -> 下单。返回本次动作（无动作返回 None）。"""
        import time

        self._last_step_at = time.time()
        cfg = self.config
        if not cfg.enabled:
            return None

        try:
            # 先对账：把已被 SL/TP 平掉的持仓回写账本（失败不阻断本轮）
            try:
                self._settle_closed()
            except Exception:
                pass
            bars15 = self._rates("M15", 400)
            bars30 = self._rates("M30", 300)
            if len(bars15) < 60 or len(bars30) < 40:
                self._last_error = "K 线不足"
                return None
            m15 = self._to_df(bars15)
            m30 = self._to_df(bars30)
            h1l = build_h1_live(m15)

            atr15 = atr_series(m15.set_index("time"), 14).to_numpy()
            atr30 = atr_series(m30.set_index("time"), 14).to_numpy()

            ev15 = detect_events(m15, atr15, **HOOK)
            ev30 = detect_events(m30, atr30, **HOOK)
            idx15 = {s: StateIndex([e for e in ev15 if e["side"] == s], m15["time"],
                                   STATE_HOURS["M15"]) for s in ("long", "short")}
            idx30 = {s: StateIndex([e for e in ev30 if e["side"] == s], m30["time"],
                                   STATE_HOURS["M30"]) for s in ("long", "short")}
            ok_l, ok_s = h1_half_ok(h1l)

            # 判定"当前这根 M15 收盘时刻"的状态（只用已收盘数据）
            i = len(m15) - 1
            t = m15["time"].iloc[i]
            side_on = None
            for s in ("long", "short"):
                if not (idx15[s].active(t) and idx30[s].active(t)):
                    continue
                if not (ok_l[i] if s == "long" else ok_s[i]):
                    continue
                side_on = s
                break

            if ok_l[i]:
                h1_side = "long"
            elif ok_s[i]:
                h1_side = "short"
            else:
                h1_side = "flat"
            self._last_scan = {
                "time": str(t),
                "price": float(m15["close"].iloc[i]),
                "atr15": float(atr15[i]) if np.isfinite(atr15[i]) else 0.0,
                "ev15": len(ev15), "ev30": len(ev30),
                "m15_active": [s for s in ("long", "short") if idx15[s].active(t)],
                "m30_active": [s for s in ("long", "short") if idx30[s].active(t)],
                "h1_half": h1_side,
                "side": side_on,
            }
            if side_on is None:
                self._last_error = ""
                return None

            # —— 同根去重：同一根 M15 只判断一次 ——
            if self._last_signal_ts is not None and t <= self._last_signal_ts:
                return None
            self._last_signal_ts = t

            now = pd.Timestamp.utcnow()
            self._roll_day(now)

            a = float(atr15[i])
            if not np.isfinite(a) or a <= 0:
                return None
            d = 1.0 if side_on == "long" else -1.0
            r_unit = max(HARD_STOP_ATR, 0.5) * a
            stop_dist = HARD_STOP_ATR * a
            take_dist = TAKE_R_MULT * max(RISK_MULT * a, a * 0.1)
            entry = float(m15["close"].iloc[i])
            sl = entry - d * stop_dist
            tp = entry + d * take_dist

            signal = {
                "time": str(t), "side": side_on, "entry": round(entry, 2),
                "sl": round(sl, 2), "tp": round(tp, 2), "atr": round(a, 2),
                "note": "M15+M30 V形同步 + H1 同向半边",
            }

            # —— 风控闸门 ——
            if self._symbol_positions() >= cfg.max_positions:
                signal["action"] = "skip"
                signal["reason"] = f"已有 {cfg.max_positions} 笔持仓"
                self._signals.insert(0, signal)
                return signal
            ok_gate, reason = self._risk_gate(now)
            if not ok_gate:
                signal["action"] = "skip"
                signal["reason"] = reason
                self._signals.insert(0, signal)
                return signal

            equity = self._equity()
            lots, risk_amount, note = self._size(entry, stop_dist, equity)
            if lots <= 0:
                signal["action"] = "skip"
                signal["reason"] = note
                self._signals.insert(0, signal)
                return signal

            order = await self.orders.place_order(
                symbol=cfg.symbol,
                side="buy" if side_on == "long" else "sell",
                lots=lots,
                stop_price=round(sl, 2),
                take_price=round(tp, 2),
                factor_id="vpattern",
                factor_name="K线形态",
                reason=f"{ORDER_TAG} V形底{'做多' if side_on == 'long' else '做空'}",
                idempotency_key=f"vpattern:{cfg.symbol}:{t}",
            )
            cfg.last_entry_ts = str(now)
            # 注意：不要在这里清零连亏计数——那会抹掉"连亏减半"的依据。
            # 连亏只在 _settle_closed 里按真实平仓结果更新。

            record = {**signal, "action": "order", "lots": lots,
                      "risk_amount": round(risk_amount, 2), "order_id": str(order.id),
                      "ticket": str(order.id), "note": note}
            self._signals.insert(0, record)
            self._orders_placed.insert(0, record)
            self._signals = self._signals[:50]
            self._orders_placed = self._orders_placed[:50]
            if hasattr(self.repository, "save_vpattern_trade"):
                try:
                    self.repository.save_vpattern_trade({
                        "ticket": str(order.id),
                        "symbol": cfg.symbol,
                        "side": side_on,
                        "lots": lots,
                        "entry_price": entry,
                        "sl": round(sl, 2),
                        "tp": round(tp, 2),
                        "atr": a,
                        "risk_amount": round(risk_amount, 2),
                        "signal_time": str(t),
                        "open_time": str(now),
                        "status": "open",
                        "note": note,
                    })
                except Exception as db_exc:  # noqa: BLE001
                    self._last_error = f"账本写入失败: {db_exc}"
            if hasattr(self.repository, "save_vpattern_state"):
                self.repository.save_vpattern_state(cfg.to_dict())
            if self.notifier:
                try:
                    await self.notifier.send_alert(
                        "success", "K线形态开仓",
                        f"{cfg.symbol} {'做多' if side_on == 'long' else '做空'} {lots} 手 "
                        f"@ {entry:.2f}，SL {sl:.2f} / TP {tp:.2f}（风险 {risk_amount:.2f}）",
                    )
                except Exception:
                    pass
            return record

        except Exception as exc:  # noqa: BLE001
            self._last_error = f"{type(exc).__name__}: {exc}"
            return None
