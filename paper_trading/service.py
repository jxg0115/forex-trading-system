"""前向模拟盘：逐根新 K 线驱动因子信号，模拟经纪商成交并管理持仓。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from backtest_store.repository import FactorRepository, TradeRecord
from market_data.service import MarketDataService
from models.factor import OrderModel, RiskConfig
from oems.order_manager import OrderManager
from risk_sizing.sizing import compute_position
from sandbox.runner import execute_factor
from ai_replay.replay import generate_replay_report


@dataclass
class JobState:
    id: str
    factor_id: str
    factor_name: str
    symbol: str
    timeframe: str
    status: str = "running"
    position: OrderModel | None = None
    last_bar_time: Any = None
    extreme_price: float | None = None
    take_hit: float | None = None
    equity: float = 10_000.0
    trades: list[dict[str, Any]] = field(default_factory=list)
    message: str = ""


class PaperTradingService:
    def __init__(self, market: MarketDataService, repository: FactorRepository, orders: OrderManager) -> None:
        self.market = market
        self.repository = repository
        self.orders = orders
        self.jobs: dict[str, JobState] = {}
        self.trailing_config = None

    def start(self, factor_id: str, symbol: str, timeframe: str, equity: float = 10_000.0) -> JobState:
        factor = self.repository.get_factor(factor_id)
        if not factor:
            raise ValueError("因子不存在")
        job = JobState(
            id=str(uuid.uuid4()),
            factor_id=factor_id,
            factor_name=factor.name,
            symbol=symbol,
            timeframe=timeframe,
            equity=equity,
        )
        self.jobs[job.id] = job
        return job

    def stop(self, job_id: str | None = None) -> list[JobState]:
        if job_id:
            job = self.jobs.get(job_id)
            if job:
                job.status = "stopped"
            return [job] if job else []
        for job in self.jobs.values():
            job.status = "stopped"
        return list(self.jobs.values())

    def state(self) -> list[dict[str, Any]]:
        return [job.__dict__ | {"position": job.position.model_dump() if job.position else None} for job in self.jobs.values()]

    def tick(self) -> None:
        """每个行情节拍调用：为新 K 线计算信号并处理订单。"""

        for job in list(self.jobs.values()):
            if job.status != "running":
                continue
            df = self.market.dataframe(job.symbol, job.timeframe)
            bars = self.market.get_bars(job.symbol, job.timeframe)
            latest_time = bars[-1]["time"]
            if job.last_bar_time == latest_time:
                continue
            job.last_bar_time = latest_time

            factor = self.repository.get_factor(job.factor_id)
            if not factor:
                job.status = "stopped"
                continue
            execution = execute_factor(factor.code, bars, factor.params)
            if not execution.ok or not execution.entry_values:
                job.message = f"信号计算失败：{execution.message}"
                continue

            latest_bar = bars[-1]
            signal = execution.entry_values[-2] if len(execution.entry_values) >= 2 else 0
            if job.position is None and signal not in (None, 0):
                direction = "long" if signal > 0 else "short"
                atr = self._atr(bars)
                sizing = compute_position(
                    entry_price=float(latest_bar["open"]),
                    atr=atr,
                    direction=direction,
                    config=RiskConfig(account_equity=job.equity),
                )
                job.position = OrderModel(
                    id=str(uuid.uuid4()),
                    symbol=job.symbol,
                    side="buy" if direction == "long" else "sell",
                    lots=sizing.lots,
                    entry_price=float(latest_bar["open"]),
                    stop_price=sizing.stop_price,
                    take_price=sizing.take_price,
                    status="filled",
                    factor_id=job.factor_id,
                    factor_name=job.factor_name,
                    reason=f"{factor.description[:40]}",
                )
                job.message = f"模拟开仓：{direction} {sizing.lots} 手 @ {latest_bar['open']}"
            elif job.position is not None:
                self._check_exit(job, latest_bar)
                self._apply_trailing(job, latest_bar)

    def _apply_trailing(self, job: JobState, bar: dict[str, Any]) -> None:
        """模拟盘三阶段移动止损/止盈：跟随、接近止盈锁定、突破后移动止盈。"""

        cfg = self.trailing_config
        if not cfg or not cfg.trailing_enabled:
            return
        pos = job.position
        if pos is None:
            return
        entry = pos.entry_price or 0.0
        if entry <= 0:
            return
        current = float(bar["close"])
        if job.take_hit is None:
            job.take_hit = 0.0 if pos.side == "buy" else float("inf")
        use_atr = str(cfg.trailing_unit).lower() == "atr"
        bars = self.market.get_bars(job.symbol, job.timeframe)
        atr_value = self._atr(bars) or entry * 0.005
        if use_atr:
            activation_dist = max(cfg.trailing_activation_atr, 0.0) * atr_value
            take_buffer_dist = max(cfg.trailing_take_buffer_atr, 0.0) * atr_value
        else:
            activation_dist = entry * max(cfg.trailing_activation_pct, 0.0) / 100.0
            take_buffer_dist = 0.0
        if pos.side == "buy":
            high = float(bar["high"])
            extreme = max(job.extreme_price or high, high)
            job.extreme_price = extreme
            stop_dist = (
                max(cfg.trailing_stop_atr, 0.0) * atr_value
                if use_atr
                else extreme * max(cfg.trailing_retrace_pct, 0.0) / 100.0
            )
            take_dist = (
                max(cfg.trailing_take_atr, 0.0) * atr_value
                if use_atr
                else extreme * max(cfg.trailing_take_retrace_pct, 0.0) / 100.0
            )
            new_stop: float | None = None
            profit_distance = extreme - entry
            activated = (
                profit_distance >= activation_dist
                if use_atr
                else (extreme - entry) / entry * 100.0 >= max(cfg.trailing_activation_pct, 0.0)
            )
            if activated:
                new_stop = max(round(extreme - stop_dist, 5), entry)
            take_line = pos.take_price or 0.0
            if take_line > 0 and current >= take_line:
                job.take_hit = max(job.take_hit, take_line)
                lock_stop = job.take_hit
                if new_stop is None or lock_stop > new_stop:
                    new_stop = lock_stop
                candidate_take = round(extreme + take_dist, 5)
                if candidate_take > take_line:
                    pos.take_price = round(candidate_take, 5)
                    follow_stop = (
                        round(candidate_take - take_buffer_dist, 5)
                        if use_atr
                        else round(candidate_take * (1.0 - max(cfg.trailing_take_buffer_pct, 0.0) / 100.0), 5)
                    )
                    if follow_stop < current and follow_stop > new_stop:
                        new_stop = follow_stop
            if job.take_hit > 0:
                new_stop = job.take_hit if new_stop is None else max(new_stop, job.take_hit)
            if new_stop is not None and new_stop >= current:
                candidates = [entry]
                if job.take_hit > 0:
                    candidates.append(job.take_hit)
                valid = [c for c in candidates if c < current]
                new_stop = max(valid) if valid else None
            if new_stop and new_stop > (pos.stop_price or 0.0) and new_stop < current:
                pos.stop_price = round(new_stop, 5)
        else:
            low = float(bar["low"])
            extreme = min(job.extreme_price or low, low)
            job.extreme_price = extreme
            stop_dist = (
                max(cfg.trailing_stop_atr, 0.0) * atr_value
                if use_atr
                else extreme * max(cfg.trailing_retrace_pct, 0.0) / 100.0
            )
            take_dist = (
                max(cfg.trailing_take_atr, 0.0) * atr_value
                if use_atr
                else extreme * max(cfg.trailing_take_retrace_pct, 0.0) / 100.0
            )
            new_stop = None
            profit_distance = entry - extreme
            activated = (
                profit_distance >= activation_dist
                if use_atr
                else (entry - extreme) / entry * 100.0 >= max(cfg.trailing_activation_pct, 0.0)
            )
            if activated:
                new_stop = min(round(extreme + stop_dist, 5), entry)
            take_line = pos.take_price or 0.0
            if take_line > 0 and current <= take_line:
                job.take_hit = min(job.take_hit, take_line)
                lock_stop = job.take_hit
                if new_stop is None or lock_stop < new_stop:
                    new_stop = lock_stop
                candidate_take = round(extreme - take_dist, 5)
                if candidate_take < take_line:
                    pos.take_price = round(candidate_take, 5)
                    follow_stop = (
                        round(candidate_take + take_buffer_dist, 5)
                        if use_atr
                        else round(candidate_take * (1.0 + max(cfg.trailing_take_buffer_pct, 0.0) / 100.0), 5)
                    )
                    if follow_stop > current and follow_stop < new_stop:
                        new_stop = follow_stop
            if job.take_hit != float("inf"):
                new_stop = job.take_hit if new_stop is None else min(new_stop, job.take_hit)
            if new_stop is not None and new_stop <= current:
                candidates = [entry]
                if job.take_hit != float("inf"):
                    candidates.append(job.take_hit)
                valid = [c for c in candidates if c > current]
                new_stop = min(valid) if valid else None
            if new_stop and new_stop < (pos.stop_price or float("inf")) and new_stop > current:
                pos.stop_price = round(new_stop, 5)

    def _check_exit(self, job: JobState, bar: dict[str, Any]) -> None:
        pos = job.position
        assert pos is not None
        side_sign = 1.0 if pos.side == "buy" else -1.0
        exit_price = None
        reason = ""
        stop = pos.stop_price or 0.0
        take = pos.take_price or 0.0
        if pos.side == "buy":
            if bar["low"] <= stop:
                exit_price, reason = stop, "止损"
            elif bar["high"] >= take:
                exit_price, reason = take, "止盈"
        else:
            if bar["high"] >= stop:
                exit_price, reason = stop, "止损"
            elif bar["low"] <= take:
                exit_price, reason = take, "止盈"
        if exit_price is None:
            return

        entry = pos.entry_price or 0.0
        pnl_pct = side_sign * (exit_price - entry) / entry if entry else 0.0
        risk_pct = RiskConfig(account_equity=job.equity).risk_per_trade_pct / 100.0
        stop_pct = abs(entry - stop) / entry if entry else 0.01
        trade_pnl = job.equity * (risk_pct / stop_pct) * pnl_pct
        job.equity += trade_pnl
        job.trades.append(
            {
                "id": pos.id,
                "side": pos.side,
                "entry_price": entry,
                "exit_price": exit_price,
                "exit_reason": reason,
                "pnl": round(trade_pnl, 2),
                "entry_time": pos.created_at.isoformat(),
                "exit_time": bar["time"],
            }
        )
        report = generate_replay_report(
            {
                "id": pos.id,
                "side": pos.side,
                "entry_time": pos.created_at.isoformat(),
                "exit_time": bar["time"],
                "entry_price": entry,
                "exit_price": exit_price,
                "pnl": trade_pnl,
                "exit_reason": reason,
            },
            factor_name=job.factor_name,
            symbol=job.symbol,
        )
        record = TradeRecord(
            id=str(uuid.uuid4()),
            factor_id=job.factor_id,
            factor_name=job.factor_name,
            symbol=job.symbol,
            side=pos.side,
            entry_time=pos.created_at,
            entry_price=entry,
            exit_time=datetime.now(timezone.utc),
            exit_price=exit_price,
            lots=pos.lots,
            pnl=round(trade_pnl, 2),
            exit_reason=reason,
            report_markdown=report.markdown,
        )
        self.repository.save_trade(record)
        case = self.repository.get_pattern_case_by_factor(job.factor_id)
        if case:
            stats = dict(case.statistics or {})
            stats["samples"] = int(stats.get("samples", 0)) + 1
            if trade_pnl > 0:
                stats["wins"] = int(stats.get("wins", 0)) + 1
            else:
                stats["losses"] = int(stats.get("losses", 0)) + 1
            prev_total = float(stats.get("avg_pnl", 0.0)) * (stats["samples"] - 1)
            stats["avg_pnl"] = round((prev_total + trade_pnl) / stats["samples"], 2)
            self.repository.update_pattern_statistics(case.id, stats)
        job.position = None
        job.message = f"模拟平仓：{reason} @ {exit_price}，盈亏 {trade_pnl:+.2f} 美元"

    @staticmethod
    def _atr(bars: list[dict[str, Any]]) -> float:
        window = bars[-20:]
        diffs = [abs(b["high"] - b["low"]) for b in window]
        return sum(diffs) / len(diffs) if diffs else 0.0
