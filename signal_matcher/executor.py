"""板块四 -> 板块八 闭环：AI 激活信号自动执行桥（支持运行时完整参数组）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import pandas as pd

from config import Settings, settings as app_settings
from indicators.technical import atr_series
from market_data.market_analysis import MarketAnalysisEngine
from market_data.service import MarketDataService
from observability.notifier import Notifier
from oems.mt5_gateway import MT5Gateway
from oems.order_manager import OrderManager
from risk_sizing.sizing import calculate_position_size
from signal_matcher.market_filter import matches_market_filter


@dataclass
class ExecutorRuntimeConfig:
    symbol: str = "EURUSD"
    timeframe: str = "M15"
    min_confidence: float = 0.55
    direction: str = "both"
    lot_mode: str = "risk"
    fixed_lots: float = 0.01
    risk_percent: float = 1.0
    max_positions: int = 5
    initial_sltp_source: str = "pattern"
    stop_method: str = "atr"
    take_method: str = "atr"
    stop_atr_mult: float = 2.0
    take_atr_mult: float = 3.0
    stop_points: float = 0.0
    take_points: float = 0.0
    level_buffer_pct: float = 0.1
    take_level_buffer_pct: float = 0.1
    trailing_enabled: bool = False
    trailing_unit: str = "atr"
    trailing_activation_pct: float = 0.3
    trailing_retrace_pct: float = 0.3
    trailing_take_retrace_pct: float = 0.3
    trailing_take_buffer_pct: float = 0.2
    trailing_activation_atr: float = 0.5
    trailing_stop_atr: float = 1.0
    trailing_take_atr: float = 0.5
    trailing_take_buffer_atr: float = 0.5
    sl_tp_strategies: list[str] = field(default_factory=lambda: ["high_watermark"])
    hw_activation_profit: float = 200.0
    hw_max_retrace_pct: float = 20.0
    pc_tier1_profit: float = 400.0
    pc_tier1_close_pct: float = 50.0
    pc_tier2_profit: float = 1000.0
    pc_tier2_close_pct: float = 30.0
    pc_breakeven_buffer: float = 0.5
    delay_ms: int = 0
    auto_close_enabled: bool = True
    alert_enabled: bool = True
    max_daily_loss_pct: float = 3.0
    max_drawdown_pct: float = 20.0
    market_filter: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in self.__dict__.items()
        }


class SignalExecutor:
    """扫描激活信号并按风控仓位自动下单，带同品种去重、信号冷却与执行延迟。"""

    def __init__(
        self,
        market: MarketDataService,
        repository,
        orders: OrderManager,
        notifier: Optional[Notifier] = None,
        gateway: Optional[MT5Gateway] = None,
        settings: Settings | None = None,
        market_analysis: Optional[MarketAnalysisEngine] = None,
    ) -> None:
        cfg = settings or app_settings
        self.market = market
        self.repository = repository
        self.orders = orders
        self.notifier = notifier
        self.gateway = gateway
        self.enabled = cfg.auto_trade_enabled
        self.config = ExecutorRuntimeConfig(
            symbol=cfg.auto_trade_symbol,
            timeframe=cfg.auto_trade_timeframe,
            min_confidence=cfg.auto_trade_min_confidence,
            max_positions=cfg.auto_trade_max_positions,
            risk_percent=cfg.auto_trade_risk_percent,
            max_daily_loss_pct=cfg.max_daily_loss_pct,
            max_drawdown_pct=cfg.max_drawdown_pct,
        )
        self.cooldown_seconds = 60
        self._last_signal: dict[str, tuple[datetime, str]] = {}
        self._next_execute: dict[str, datetime] = {}
        self.last_executions: list[dict[str, Any]] = []
        self.last_failures: list[dict[str, Any]] = []
        self.market_analysis = market_analysis or MarketAnalysisEngine(market)
        self.last_market_skip: dict[str, Any] | None = None

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled

    def apply_runtime_config(self, config: ExecutorRuntimeConfig) -> None:
        self.config = config

    async def execute_scan(self, scan_result: dict[str, Any]) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        # 防呆：下单品种/周期必须跟随扫描结果（扫描什么就下单什么），
        # 避免 executor.config 与用户选择的匹配品种错位（历史错位曾导致 XAUUSD 扫描、EURUSD 下单）。
        scan_sym = str(scan_result.get("symbol") or "").upper()
        scan_tf = str(scan_result.get("timeframe") or "").upper()
        if scan_sym and scan_sym != self.config.symbol:
            print(f"[signal_executor] 同步下单品种 {self.config.symbol} -> {scan_sym}（跟随扫描）")
            self.config.symbol = scan_sym
        if scan_tf and scan_tf != self.config.timeframe:
            print(f"[signal_executor] 同步下单周期 {self.config.timeframe} -> {scan_tf}（跟随扫描）")
            self.config.timeframe = scan_tf
        config = self.config
        direction_allowed = config.direction
        candidates = [
            c
            for c in scan_result.get("candidates", [])
            if c.get("signal") in ("long", "short")
            and (direction_allowed == "both" or c.get("signal") == direction_allowed)
            and c.get("confidence", 0.0) >= config.min_confidence
        ]
        if not candidates:
            print(f"[executor] 无候选通过门槛：direction={direction_allowed} min_conf={config.min_confidence}")
            return []

        positions = await self.orders.positions()
        open_symbols = {p.symbol for p in positions}
        if len(open_symbols) >= config.max_positions:
            print(f"[executor] 持仓已达上限：open={len(open_symbols)} max={config.max_positions}")
            return []

        now = datetime.now(timezone.utc)
        bars = self._bars(config.symbol)
        if not bars:
            print(f"[executor] bars 为空：{config.symbol}/{config.timeframe}（gateway_connected={bool(self.gateway and self.gateway.is_connected)}）")
            return []
        market_filter = config.market_filter or {}
        print(f"[executor] 过候选/持仓/bars 检查，进行情过滤：enabled={market_filter.get('enabled')}")
        if market_filter.get("enabled"):
            market = self._market_environment(config.symbol, config.timeframe)
            if not market:
                # 行情环境数据不可用（分析失败/数据源异常）——防御性拦截，不冒险开仓
                self.last_market_skip = {
                    "time": now.isoformat(),
                    "symbol": config.symbol,
                    "timeframe": config.timeframe,
                    "label": "",
                    "trend_direction": None,
                    "volatility": None,
                    "volume_state": None,
                    "environment_score": None,
                    "reason": "行情环境数据不可用（分析失败），防御性拦截，不冒险开仓",
                }
                return []
            import json as _json

            _mtf_layers = {
                str(l.get("key") or "").lower(): str(l.get("direction") or "flat")
                for l in ((market.get("multi_timeframe") or {}).get("layers") or [])
            }
            print(
                f"[executor] 匹配输入: tf={config.timeframe} "
                f"mtf配置={_json.dumps(market_filter.get('mtf_directions'), ensure_ascii=False)} "
                f"层={_json.dumps(_mtf_layers, ensure_ascii=False)} "
                f"非空={bool(market)}"
            )
            if not matches_market_filter(market_filter, market):
                self.last_market_skip = {
                    "time": now.isoformat(),
                    "symbol": config.symbol,
                    "timeframe": config.timeframe,
                    "label": market.get("label") or "",
                    "trend_direction": market.get("trend_direction"),
                    "volatility": market.get("volatility"),
                    "volume_state": market.get("volume_state"),
                    "environment_score": market.get("environment_score"),
                    "reason": f"当前行情不符合开仓条件：{market.get('label') or '无行情数据'}",
                }
                return []
        self.last_market_skip = None
        executed: list[dict[str, Any]] = []
        for candidate in candidates:
            factor_id = candidate["factor_id"]
            last = self._last_signal.get(factor_id)
            if last and (now - last[0]).total_seconds() < self.cooldown_seconds:
                continue
            if self._next_execute.get(factor_id, now) > now:
                continue
            if config.symbol in open_symbols or len(open_symbols) >= config.max_positions:
                continue

            if factor_id.startswith("model:"):
                factor_name = candidate["factor_name"]
            else:
                factor = self.repository.get_factor(factor_id)
                if not factor:
                    continue
                factor_name = factor.name
            entry_price = float(bars[-1]["close"])
            atr = self._last_atr(bars) or entry_price * 0.001
            sizing = self._position(
                config.symbol,
                entry_price,
                atr,
                candidate["signal"],
                support_levels=candidate.get("support_levels") or [],
                resistance_levels=candidate.get("resistance_levels") or [],
                suggested_stop_pct=candidate.get("suggested_stop_pct"),
                suggested_take_pct=candidate.get("suggested_take_pct"),
            )
            side = "buy" if candidate["signal"] == "long" else "sell"
            try:
                idempotency_key = (
                    f"exec:{factor_id}:{config.symbol}:{config.timeframe}:"
                    f"{bars[-1].get('time') if bars else ''}"
                )
                order = await asyncio.wait_for(
                    self.orders.place_order(
                        symbol=config.symbol,
                        side=side,
                        lots=sizing["lots"],
                        stop_price=sizing["stop_price"],
                        take_price=sizing["take_price"],
                        factor_id=factor_id,
                        factor_name=factor_name,
                        reason="AI 自动执行",
                        idempotency_key=idempotency_key,
                    ),
                    timeout=15.0,
                )
            except Exception as exc:  # noqa: BLE001
                if not isinstance(exc, RuntimeError):
                    import traceback

                    traceback.print_exc()
                failure = {
                    "time": now.isoformat(),
                    "factor_id": factor_id,
                    "factor_name": factor_name,
                    "symbol": config.symbol,
                    "side": side,
                    "error": str(exc),
                }
                self.last_failures.insert(0, failure)
                self.last_failures = self.last_failures[:20]
                if config.alert_enabled and self.notifier:
                    await self.notifier.send_alert(
                        "error",
                        "AI 自动下单失败",
                        f"{factor_name} {config.symbol} {side} 下单被拒：{exc}",
                    )
                continue
            open_symbols.add(config.symbol)
            self._last_signal[factor_id] = (now, candidate["signal"])
            if config.delay_ms > 0:
                self._next_execute[factor_id] = now + timedelta(milliseconds=config.delay_ms)
            record = {
                "time": now.isoformat(),
                "factor_id": factor_id,
                "factor_name": factor_name,
                "symbol": config.symbol,
                "side": side,
                "lots": sizing["lots"],
                "stop": sizing["stop_price"],
                "take": sizing["take_price"],
                "confidence": candidate["confidence"],
                "order_id": order.id,
            }
            self.last_executions.insert(0, record)
            self.last_executions = self.last_executions[:20]
            executed.append(record)

        if executed and config.alert_enabled and self.notifier:
            await self.notifier.send_alert(
                "success",
                "AI 自动下单",
                f"共执行 {len(executed)} 笔信号：{'; '.join(r['factor_name'] for r in executed)}",
            )
        return executed

    def _position(
        self,
        symbol: str,
        entry_price: float,
        atr: float,
        direction: str,
        support_levels: list[float] | None = None,
        resistance_levels: list[float] | None = None,
        suggested_stop_pct: float | None = None,
        suggested_take_pct: float | None = None,
    ) -> dict[str, Any]:
        config = self.config
        sign = 1.0 if direction == "long" else -1.0
        point = 0.00001
        pip_value = 10.0
        min_lot = 0.01
        max_lot = 100.0
        lot_step = 0.01
        equity = app_settings.account_equity
        info = None
        if self.gateway:
            info = self.gateway.get_symbol_info(symbol)
            if info:
                point = float(info["point"])
                pip_value = float(info["pip_value"])
                min_lot = float(info["min_lot"])
                max_lot = float(info["max_lot"])
                lot_step = float(info["lot_step"])
            account = self.gateway.get_account_info()
            if account:
                equity = float(account["equity"])

        stop_dist = self._stop_distance(entry_price, atr, direction, point, support_levels or [], resistance_levels or [], suggested_stop_pct)
        take_dist = self._take_distance(entry_price, atr, direction, point, support_levels or [], resistance_levels or [], suggested_take_pct)
        if stop_dist <= 0:
            stop_dist = entry_price * 0.005
        if take_dist <= 0:
            take_dist = stop_dist * 1.5

        if config.lot_mode == "fixed":
            lots = max(min_lot, min(max_lot, config.fixed_lots))
        else:
            pip_size = point
            if info and int(info.get("digits", 5)) in (3, 5):
                pip_size = point * 10
            stop_pips = stop_dist / pip_size
            lots = calculate_position_size(
                equity=equity,
                risk_percent=config.risk_percent,
                sl_pips=stop_pips,
                pip_value=pip_value,
                min_lot=min_lot,
                max_lot=max_lot,
                lot_step=lot_step,
            )
        stop_price = entry_price - sign * stop_dist
        take_price = entry_price + sign * take_dist
        return {"lots": round(lots, 2), "stop_price": round(stop_price, 5), "take_price": round(take_price, 5)}

    def _stop_distance(
        self,
        entry_price: float,
        atr: float,
        direction: str,
        point: float,
        support_levels: list[float],
        resistance_levels: list[float],
        suggested_stop_pct: float | None = None,
    ) -> float:
        """独立计算止损距离：ATR / 固定点数 / 支撑压力位。"""
        config = self.config
        if config.initial_sltp_source == "pattern" and suggested_stop_pct and float(suggested_stop_pct) > 0:
            return max(entry_price * float(suggested_stop_pct), atr * 0.5)
        if config.stop_method == "points":
            return max(config.stop_points, 0.0) * point
        if config.stop_method == "levels":
            dist = self._level_distance(entry_price, direction, support_levels, resistance_levels, take=False)
            return dist if dist > 0 else atr * max(float(config.stop_atr_mult), 0.0)
        return atr * max(float(config.stop_atr_mult), 0.0)

    def _take_distance(
        self,
        entry_price: float,
        atr: float,
        direction: str,
        point: float,
        support_levels: list[float],
        resistance_levels: list[float],
        suggested_take_pct: float | None = None,
    ) -> float:
        """独立计算止盈距离：ATR / 固定点数 / 支撑压力位。"""
        config = self.config
        if config.initial_sltp_source == "pattern" and suggested_take_pct and float(suggested_take_pct) > 0:
            return max(entry_price * float(suggested_take_pct), atr * 0.5)
        if config.take_method == "points":
            return max(config.take_points, 0.0) * point
        if config.take_method == "levels":
            dist = self._level_distance(entry_price, direction, support_levels, resistance_levels, take=True)
            return dist if dist > 0 else atr * max(float(config.take_atr_mult), 0.0)
        return atr * max(float(config.take_atr_mult), 0.0)

    def _level_distance(
        self,
        entry_price: float,
        direction: str,
        support_levels: list[float],
        resistance_levels: list[float],
        take: bool,
    ) -> float:
        """按最近支撑/压力位计算单边止损或止盈距离。"""
        buffer_pct = max(float(self.config.take_level_buffer_pct if take else self.config.level_buffer_pct), 0.0) / 100.0
        supports = sorted({float(p) for p in support_levels if float(p) > 0}, reverse=True)
        resistances = sorted({float(p) for p in resistance_levels if float(p) > 0})
        below_supports = [p for p in supports if p < entry_price]
        above_resistances = [p for p in resistances if p > entry_price]
        nearest_support = below_supports[0] if below_supports else (supports[0] if supports else 0.0)
        nearest_resistance = above_resistances[0] if above_resistances else (resistances[0] if resistances else 0.0)

        if direction == "long":
            if take:
                return max(nearest_resistance * (1 - buffer_pct) - entry_price, 0.0) if nearest_resistance else 0.0
            return max(entry_price - nearest_support * (1 - buffer_pct), 0.0) if nearest_support else 0.0
        if take:
            return max(entry_price - nearest_support * (1 + buffer_pct), 0.0) if nearest_support else 0.0
        return max(nearest_resistance * (1 + buffer_pct) - entry_price, 0.0) if nearest_resistance else 0.0

    def _bars(self, symbol: str) -> list[dict[str, Any]]:
        if self.gateway and self.gateway.is_connected:
            return self.gateway.get_rates(symbol, self.config.timeframe, 300)
        return self.market.get_bars(symbol, self.config.timeframe, limit=300)

    def _market_environment(self, symbol: str, timeframe: str) -> dict[str, Any]:
        """取当前行情分析快照（与页面 /api/market/analysis 同源、同缓存），异常时放行。"""
        try:
            return self.market_analysis.analyze(symbol, timeframe)
        except Exception:
            return {}

    @staticmethod
    def _last_atr(bars: list[dict[str, Any]]) -> float:
        try:
            df = pd.DataFrame(bars)
            if df.empty:
                return 0.0
            if "time" in df.columns:
                df["time"] = pd.to_datetime(df["time"], utc=True)
                df = df.set_index("time")
            atr = atr_series(df, 14).dropna()
            return float(atr.iloc[-1]) if len(atr) else 0.0
        except Exception:
            return 0.0
