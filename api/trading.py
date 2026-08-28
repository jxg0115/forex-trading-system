"""回测、模拟盘、信号匹配、风控与订单路由。"""

from __future__ import annotations

import math
import asyncio
import os
import subprocess
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import pandas as pd

from api.deps import get_app_state, repo_dependency
from api.schemas import BacktestOptimizeRequest, BacktestRunRequest, PortfolioBacktestRequest, PaperStartRequest, PaperStopRequest, ReplayExportRequest
from backtest_store.engine import Backtester
from backtest_store.repository import SltpCaseRecord, TradeRecord
from backtest_store.optimizer import FactorOptimizer, OptimizationConfig
from backtest_store.trade_extremes import compute_trade_extremes
from backtest_store.vectorbt_engine import try_vectorbt
from indicators.technical import atr_series
from models.factor import ReplayReport, RiskConfig
from sandbox.ast_check import check_source
from sandbox.runner import execute_factor
from signal_matcher.executor import ExecutorRuntimeConfig
from oems.smart_stop import SmartStopConfig
from oems.sltp_manager import SltpPolicyManager
from oems.sltp_policy import SltpPolicyConfig
from oems.sltp_recommend import recommend_sltp

router = APIRouter(prefix="/api", tags=["交易"])


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    vals = sorted(values)
    n = len(vals)
    if n % 2:
        return float(vals[n // 2])
    return float((vals[n // 2 - 1] + vals[n // 2]) / 2)


class MatcherStartRequest(BaseModel):
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
    sl_tp_strategies: list[str] = []
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
    market_filter: dict[str, Any] = {}
    pattern_min_similarity: float = 0.85
    pattern_min_samples: int = 0
    pattern_time_decay_days: int = 365


class RuntimeConfigUpdateRequest(BaseModel):
    trailing_enabled: bool = False
    trailing_unit: str = "atr"
    initial_sltp_source: str = "pattern"
    trailing_activation_pct: float = 0.3
    trailing_retrace_pct: float = 0.3
    trailing_take_retrace_pct: float = 0.3
    trailing_take_buffer_pct: float = 0.2
    trailing_activation_atr: float = 0.5
    trailing_stop_atr: float = 1.0
    trailing_take_atr: float = 0.5
    trailing_take_buffer_atr: float = 0.5
    sl_tp_strategies: list[str] = []
    hw_activation_profit: float = 200.0
    hw_max_retrace_pct: float = 20.0
    pc_tier1_profit: float = 400.0
    pc_tier1_close_pct: float = 50.0
    pc_tier2_profit: float = 1000.0
    pc_tier2_close_pct: float = 30.0
    pc_breakeven_buffer: float = 0.5


class TradeQualityRequest(BaseModel):
    trade_id: str
    quality: str = "general"


class TradeStatsRequest(BaseModel):
    symbol: str | None = None
    account_id: str | None = None
    side: str | None = None
    quality: str | None = None
    min_lots: float | None = None
    max_lots: float | None = None
    fixed_lots: float | None = None
    normalize_to_one_lot: bool = False
    start_time: Any | None = None
    end_time: Any | None = None


class DeleteTradesRequest(BaseModel):
    trade_ids: list[str]


class SmartStopConfigRequest(BaseModel):
    config: dict[str, Any] = {}


class SltpPolicyRequest(BaseModel):
    config: dict[str, Any] = {}


class SyncMt5TradesRequest(BaseModel):
    days: int = 30


class FactorStatsRequest(BaseModel):
    factor_id: str | None = None
    symbol: str | None = None
    timeframe: str | None = None
    status: str | None = None


class SltpLearnRequest(BaseModel):
    trade_ids: list[str]


_repo = repo_dependency


@router.get("/trading/config")
async def get_runtime_config(state=Depends(get_app_state)):
    return {
        "config": state.signal_executor.config.to_dict(),
        "matcher_running": state.matcher_running,
        "matcher_symbol": state.matcher_symbol,
        "matcher_timeframe": state.matcher_timeframe,
    }


@router.post("/trading/config")
async def update_runtime_config(req: RuntimeConfigUpdateRequest, state=Depends(get_app_state)):
    current = state.signal_executor.config
    updated = replace(
        current,
        trailing_enabled=req.trailing_enabled,
        trailing_unit=req.trailing_unit,
        initial_sltp_source=req.initial_sltp_source,
        trailing_activation_pct=req.trailing_activation_pct,
        trailing_retrace_pct=req.trailing_retrace_pct,
        trailing_take_retrace_pct=req.trailing_take_retrace_pct,
        trailing_take_buffer_pct=req.trailing_take_buffer_pct,
        trailing_activation_atr=req.trailing_activation_atr,
        trailing_stop_atr=req.trailing_stop_atr,
        trailing_take_atr=req.trailing_take_atr,
        trailing_take_buffer_atr=req.trailing_take_buffer_atr,
        sl_tp_strategies=req.sl_tp_strategies,
        hw_activation_profit=req.hw_activation_profit,
        hw_max_retrace_pct=req.hw_max_retrace_pct,
        pc_tier1_profit=req.pc_tier1_profit,
        pc_tier1_close_pct=req.pc_tier1_close_pct,
        pc_tier2_profit=req.pc_tier2_profit,
        pc_tier2_close_pct=req.pc_tier2_close_pct,
        pc_breakeven_buffer=req.pc_breakeven_buffer,
    )
    state.signal_executor.apply_runtime_config(updated)
    state.repository.save_system_state(
        state.matcher_running,
        state.matcher_symbol,
        state.matcher_timeframe,
        {
            **updated.to_dict(),
            "pattern_min_similarity": state.matcher_pattern_min_similarity,
            "pattern_min_samples": state.matcher_pattern_min_samples,
            "pattern_time_decay_days": state.matcher_pattern_time_decay_days,
        },
    )
    return {
        "message": "持仓保护设置已保存，移动止损将独立于实盘匹配运行。",
        "config": updated.to_dict(),
    }


@router.post("/backtest/run")
async def run_backtest(req: BacktestRunRequest, state=Depends(get_app_state)):
    code = req.code
    params = dict(req.params)
    if req.factor_id:
        factor = state.repository.get_factor(req.factor_id)
        if not factor:
            raise HTTPException(status_code=404, detail="因子不存在")
        code = factor.code
        params = {**factor.params, **params}
    if not code:
        raise HTTPException(status_code=400, detail="缺少因子代码")

    sandbox = check_source(code)
    if not sandbox.ok:
        raise HTTPException(status_code=400, detail=f"沙盒检查未通过：{'；'.join(sandbox.errors)}")

    if req.start_time or req.end_time:
        if not state.mt5_gateway:
            raise HTTPException(status_code=503, detail="日期范围回测需要 MT5 数据源")
        bars = state.mt5_gateway.get_rates_range(req.symbol, req.timeframe, req.start_time, req.end_time)
    else:
        bars = state.market.get_bars(req.symbol, req.timeframe, limit=req.bars)
    if not bars:
        raise HTTPException(status_code=503, detail=f"MT5 未连接或无法获取 {req.symbol} 回测数据")
    execution = execute_factor(code, bars, params)
    if not execution.ok:
        raise HTTPException(status_code=400, detail=f"因子执行失败：{execution.message}")

    df = pd.DataFrame(bars)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df = df.set_index("time")
    entry = pd.Series(execution.entry_values, index=df.index)
    exit_sig = pd.Series(execution.exit_values, index=df.index) if execution.exit_values else None

    point = 0.00001
    if state.mt5_gateway:
        symbol_info = state.mt5_gateway.get_symbol_info(req.symbol)
        if symbol_info:
            point = float(symbol_info["point"])
    bar_seconds = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400}.get(
        req.timeframe.upper(), 900
    )
    delay_bars = math.ceil(req.delay_ms / 1000 / bar_seconds) if req.delay_ms > 0 else 0
    bt_params = {
        "initial_equity": req.initial_equity,
        "risk_per_trade_pct": req.risk_per_trade_pct,
        "leverage": req.leverage,
        "execution_delay_bars": delay_bars,
        "slippage_price": req.slippage_points * point,
        "sltp": state.sltp_policy.to_dict() if state.sltp_policy.enabled else None,
        **params,
    }
    if state.settings.backtest_engine == "vectorbt":
        vectorbt_result = try_vectorbt(df, entry, exit_sig, bt_params)
        if vectorbt_result is not None:
            return vectorbt_result.model_dump()
    result = Backtester().run(df, entry, exit_sig, bt_params)
    return result.model_dump()


@router.post("/backtest/optimize")

@router.post("/backtest/portfolio")
async def run_portfolio_backtest(req: PortfolioBacktestRequest, state=Depends(get_app_state)):
    """组合历史回测（只读离线）：启用因子 + 多周期方向过滤 + 实盘 sltp 策略整体验证。"""
    from backtest_store.portfolio_backtest import PortfolioBacktestJob
    gateway = getattr(state, "mt5_gateway", None) or getattr(state, "gateway", None) or getattr(state, "market", None)
    job = PortfolioBacktestJob(
        symbol=req.symbol,
        timeframe=req.timeframe,
        repository=state.repository,
        gateway=gateway,
        date_from=req.date_from,
        date_to=req.date_to,
        market_filter=req.market_filter or {},
        sltp_policy=state.sltp_policy.to_dict() if state.sltp_policy else {},
        max_factors=req.max_factors,
    )
    setattr(state, "portfolio_job", job)
    asyncio.create_task(job.run())
    return {"ok": True, "message": f"组合历史回测已启动：{req.symbol} {req.timeframe}（启用的因子 + 多周期硬过滤 + 实盘 sltp 策略）"}


@router.get("/backtest/portfolio/status")
async def portfolio_backtest_status(state=Depends(get_app_state)):
    job = getattr(state, "portfolio_job", None)
    if not job:
        return {"ok": True, "running": False, "message": "尚未运行组合回测", "result": None}
    return {"ok": True, **job.status}

async def optimize_backtest(req: BacktestOptimizeRequest, state=Depends(get_app_state)):
    """因子参数自动优化：多轮回测、评分排序、提前停止。"""

    code = req.code
    params = dict(req.params)
    if req.factor_id:
        factor = state.repository.get_factor(req.factor_id)
        if not factor:
            raise HTTPException(status_code=404, detail="因子不存在")
        code = factor.code
        params = {**factor.params, **params}
    if not code:
        raise HTTPException(status_code=400, detail="缺少因子代码")
    sandbox = check_source(code)
    if not sandbox.ok:
        raise HTTPException(status_code=400, detail=f"沙盒检查未通过：{'；'.join(sandbox.errors)}")

    def build_config(timeframe: str, bars: int) -> OptimizationConfig:
        return OptimizationConfig(
            symbol=req.symbol,
            timeframe=timeframe,
            bars=bars,
            start_time=req.start_time,
            end_time=req.end_time,
            initial_equity=req.initial_equity,
            leverage=req.leverage,
            delay_ms=req.delay_ms,
            slippage_points=req.slippage_points,
            objective=req.objective,
            target_win_rate_pct=req.target_win_rate_pct,
            target_total_return_pct=req.target_total_return_pct,
            target_profit_factor=req.target_profit_factor,
            target_max_drawdown_pct=req.target_max_drawdown_pct,
            max_iterations=req.max_iterations,
            early_stop_rounds=req.early_stop_rounds,
            walk_forward=req.walk_forward,
            train_ratio=req.train_ratio,
            walk_forward_folds=req.walk_forward_folds,
            min_trades=req.min_trades,
            complexity_penalty=req.complexity_penalty,
            max_deviation_pct=req.max_deviation_pct,
            fidelity_weight=req.fidelity_weight,
            beam_width=req.beam_width,
            search_rounds=req.search_rounds,
            param_ranges=req.param_ranges,
        )

    config = build_config(req.timeframe, req.bars or 500)

    def run() -> dict:
        optimizer = FactorOptimizer(state.market, state.mt5_gateway)
        return optimizer.optimize(code, params, config)

    result = await asyncio.to_thread(run)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("message", "参数优化失败"))
    if req.auto_expand and result.get("best_metrics"):
        target = max(int(req.min_trades or 0), 1)
        if req.min_trades <= 0:
            target = 10
        tf_order = ["M1", "M5", "M15", "M30", "H1", "H4", "D1"]
        bar_steps = [500, 1000, 2000, 5000, 10000, 20000]
        current_tf = req.timeframe
        current_bars = req.bars or 500
        expanded: list[dict[str, Any]] = []
        attempts = 0
        while (
            attempts < 5
            and result.get("best_metrics", {}).get("num_trades", 0) < target
        ):
            upper = str(current_tf).upper()
            if upper in tf_order and tf_order.index(upper) < len(tf_order) - 1:
                current_tf = tf_order[tf_order.index(upper) + 1]
            for step in bar_steps:
                if step > current_bars:
                    current_bars = step
                    break
            else:
                current_bars = int(current_bars * 2)
            expanded.append({"timeframe": current_tf, "bars": current_bars})
            config = build_config(current_tf, current_bars)
            result = await asyncio.to_thread(run)
            if not result.get("ok"):
                break
            attempts += 1
        result["auto_expanded"] = expanded
        result["auto_expand_message"] = (
            f"已自动扩样 {len(expanded)} 次："
            + "；".join(f"{e['timeframe']} {e['bars']} 根" for e in expanded)
            if expanded
            else "数据已满足最低交易笔数，无需扩样"
        )
    return result


@router.post("/matcher/scan")
async def scan_matcher(symbol: str = "EURUSD", timeframe: str = "M15", state=Depends(get_app_state)):
    return state.matcher.scan(symbol, timeframe)


@router.get("/executor/state")
async def executor_state(state=Depends(get_app_state)):
    return {
        "enabled": state.signal_executor.enabled,
        "config": state.signal_executor.config.to_dict(),
        "last_executions": state.signal_executor.last_executions[:10],
        "last_failures": state.signal_executor.last_failures[:10],
        "last_market_skip": state.signal_executor.last_market_skip,
    }


@router.post("/executor/toggle")
async def executor_toggle(req: dict, state=Depends(get_app_state)):
    enabled = bool(req.get("enabled"))
    state.signal_executor.set_enabled(enabled)
    return {"message": "AI 自动执行已开启" if enabled else "AI 自动执行已关闭", "enabled": enabled}


@router.post("/executor/scan-run")
async def executor_scan_run(state=Depends(get_app_state)):
    scan = state.matcher.scan(state.signal_executor.config.symbol, state.signal_executor.config.timeframe)
    state.last_scan = scan
    executed = await state.signal_executor.execute_scan(scan)
    failures = state.signal_executor.last_failures[:1]
    skip = state.signal_executor.last_market_skip
    message = f"扫描完成：{scan['scanned']} 个因子，执行 {len(executed)} 笔自动下单"
    if skip:
        message += f"；行情过滤拦截：{skip.get('label') or skip.get('reason')}"
    if failures:
        message += f"；最近失败：{failures[0]['error']}"
    return {
        "message": message,
        "scan": scan,
        "executed": executed,
        "failures": state.signal_executor.last_failures[:10],
        "market_skip": skip,
    }


@router.post("/matcher/start")
async def start_matcher(req: MatcherStartRequest, state=Depends(get_app_state)):
    state.matcher_symbol = req.symbol
    state.matcher_timeframe = req.timeframe
    current_config = state.signal_executor.config
    config_updates: dict[str, Any] = {}
    for field_name in req.model_fields_set:
        if hasattr(current_config, field_name):
            config_updates[field_name] = getattr(req, field_name)
    state.signal_executor.apply_runtime_config(replace(current_config, **config_updates))
    state.matcher_pattern_min_similarity = req.pattern_min_similarity
    state.matcher_pattern_min_samples = req.pattern_min_samples
    state.matcher_pattern_time_decay_days = req.pattern_time_decay_days
    state.matcher.set_pattern_config(
        min_similarity=req.pattern_min_similarity,
        min_samples=req.pattern_min_samples,
        time_decay_days=req.pattern_time_decay_days,
    )
    state.matcher_running = True
    state.repository.save_system_state(
        True,
        req.symbol,
        req.timeframe,
        {
            **state.signal_executor.config.to_dict(),
            "pattern_min_similarity": req.pattern_min_similarity,
            "pattern_min_samples": req.pattern_min_samples,
            "pattern_time_decay_days": req.pattern_time_decay_days,
        },
    )
    algo_warning = ""
    if state.mt5_gateway:
        account = state.mt5_gateway.get_account_info()
        if account and not account.get("trade_allowed"):
            algo_warning = "；注意：MT5 算法交易未启用，自动下单会被拒绝"
    return {
        "message": f"已启动实盘匹配引擎（{req.symbol} {req.timeframe}），正在监控行情并扫描激活因子{algo_warning}",
        "running": True,
        "symbol": req.symbol,
        "timeframe": req.timeframe,
        "config": state.signal_executor.config.to_dict(),
    }


@router.get("/matcher/recommend")
async def recommend_matcher(symbol: str, state=Depends(get_app_state)):
    """根据历史订单、因子库和当前波动率，推荐启动实盘匹配参数。"""

    symbol = symbol.strip().upper()
    trades = [t for t in state.repository.list_trades(limit=1000) if t.symbol == symbol]
    factors = state.repository.list_factors(status="active", symbol=symbol)
    cases = state.repository.list_pattern_cases(symbol=symbol, limit=200)

    def side_stats(rows: list[Any]) -> dict[str, Any]:
        n = len(rows)
        wins = sum(1 for t in rows if (t.pnl or 0) > 0)
        pnl = sum(float(t.pnl or 0.0) for t in rows)
        peaks = [
            float(t.peak_pnl or 0.0) / max(float(t.lots or 0.01), 0.01)
            for t in rows
            if t.peak_pnl is not None
        ]
        losses = [
            abs(float(t.pnl)) / max(float(t.lots or 0.01), 0.01)
            for t in rows
            if t.pnl is not None and float(t.pnl) < 0
        ]
        return {
            "n": n,
            "wins": wins,
            "pnl": round(pnl, 2),
            "win_rate": round(wins / n * 100, 1) if n else 0.0,
            "med_peak": _median(peaks),
            "med_loss": _median(losses),
        }

    long_stats = side_stats([t for t in trades if t.side in ("long", "buy")])
    short_stats = side_stats([t for t in trades if t.side in ("short", "sell")])
    total_n = len(trades)
    total_wins = long_stats["wins"] + short_stats["wins"]
    win_rate = round(total_wins / total_n * 100, 1) if total_n else 0.0

    direction = "both"
    if long_stats["n"] >= 3 and short_stats["n"] >= 3:
        if long_stats["pnl"] > 0 and short_stats["pnl"] < 0:
            direction = "long"
        elif short_stats["pnl"] > 0 and long_stats["pnl"] < 0:
            direction = "short"
    elif long_stats["n"] >= 3 and long_stats["pnl"] > 0 and short_stats["n"] < 3:
        direction = "long"
    elif short_stats["n"] >= 3 and short_stats["pnl"] > 0 and long_stats["n"] < 3:
        direction = "short"

    min_confidence = 0.65 if total_n >= 20 else 0.60
    if win_rate < 45 and total_n >= 10:
        min_confidence = 0.72
    max_positions = 3 if total_n >= 50 else 2
    risk_percent = 0.5 if (win_rate < 45 and total_n >= 10) else 1.0

    contract = 100.0
    atr = 0.0
    try:
        bars = state.market.get_bars(symbol, "M15", 300) or []
        if bars:
            df = pd.DataFrame(bars)
            df["close"] = df["close"].astype(float)
            df["high"] = df["high"].astype(float)
            df["low"] = df["low"].astype(float)
            series = atr_series(df).dropna()
            atr = float(series.iloc[-1]) if len(series) else 0.0
    except Exception:
        pass
    if state.mt5_gateway:
        try:
            info = state.mt5_gateway.get_symbol_info(symbol)
            if info:
                contract = float(info.get("contract_size") or 100.0)
        except Exception:
            pass

    med_loss = max(long_stats["med_loss"], short_stats["med_loss"])
    med_peak = max(long_stats["med_peak"], short_stats["med_peak"])
    stop_atr = 2.0
    take_atr = 3.0
    if atr > 0 and contract > 0:
        if med_loss > 0:
            stop_atr = round(min(max(med_loss / (contract * atr), 1.0), 3.0), 1)
        if med_peak > 0:
            take_atr = round(min(max(med_peak / (contract * atr), 1.0), 4.0), 1)

    has_pattern = len(cases) > 0 or any(bool(getattr(f, "sl_tp_strategy", {})) for f in factors)
    initial_sltp = "pattern" if has_pattern else "atr"
    pattern_similarity = 0.80 if len(cases) >= 5 else 0.85
    pattern_min_samples = 3 if len(cases) >= 5 else 0
    max_single_loss = round(min(max(med_loss * 1.5, 300.0), 1500.0), 0) if med_loss > 0 else 500.0

    reasons = []
    if total_n:
        reasons.append(f"历史 {total_n} 笔，胜率 {win_rate:.0f}%")
    reasons.append(f"多单 {long_stats['n']} 笔/盈亏 {long_stats['pnl']:.0f}，空单 {short_stats['n']} 笔/盈亏 {short_stats['pnl']:.0f}")
    if direction != "both":
        reasons.append(f"建议方向：{'只做多' if direction == 'long' else '只做空'}")
    reasons.append(f"建议止损 {stop_atr} ATR / 止盈 {take_atr} ATR")

    return {
        "symbol": symbol,
        "timeframe": "M15",
        "min_confidence": min_confidence,
        "direction": direction,
        "lot_mode": "risk",
        "fixed_lots": 0.01,
        "risk_percent": risk_percent,
        "max_positions": max_positions,
        "initial_sltp_source": initial_sltp,
        "stop_method": "atr",
        "take_method": "atr",
        "stop_atr_mult": stop_atr,
        "take_atr_mult": take_atr,
        "stop_points": 0.0,
        "take_points": 0.0,
        "level_buffer_pct": 0.1,
        "take_level_buffer_pct": 0.1,
        "delay_ms": 0,
        "max_daily_loss_pct": 3.0,
        "max_drawdown_pct": 20.0,
        "pattern_min_similarity": pattern_similarity,
        "pattern_min_samples": pattern_min_samples,
        "auto_close_enabled": True,
        "alert_enabled": True,
        "smart_stop": {
            "enabled": True,
            "manage_manual": True,
            "use_history_profile": True,
            "use_pattern_match": True,
            "partial_close_enabled": True,
            "ladder_take_enabled": True,
            "hard_stop_atr": stop_atr,
            "max_single_loss_usd": max_single_loss,
        },
        "reasons": reasons,
    }


@router.post("/matcher/stop")
async def stop_matcher(state=Depends(get_app_state)):
    state.matcher_running = False
    state.repository.save_system_state(
        False,
        state.matcher_symbol,
        state.matcher_timeframe,
        state.signal_executor.config.to_dict(),
    )
    return {"message": "已暂停系统交易匹配", "running": False}


@router.post("/matcher/paper/start")
async def start_paper(req: PaperStartRequest, state=Depends(get_app_state)):
    try:
        job = state.paper.start(req.factor_id, req.symbol, req.timeframe, req.equity)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"message": f"已启动模拟盘：{job.factor_name}", "job_id": job.id}


@router.post("/matcher/paper/stop")
async def stop_paper(req: PaperStopRequest, state=Depends(get_app_state)):
    jobs = state.paper.stop(req.job_id)
    return {"message": f"已暂停 {len(jobs)} 个模拟盘任务"}


@router.get("/matcher/paper/state")
async def paper_state(state=Depends(get_app_state)):
    return {"jobs": state.paper.state()}


@router.get("/trading/positions")
async def positions(state=Depends(get_app_state)):
    return {"positions": [p.model_dump() for p in await state.orders.positions()]}


@router.get("/trading/orders")
async def orders(state=Depends(get_app_state)):
    return {"orders": [o.model_dump() for o in state.orders.order_list()]}


@router.get("/orders/log")
async def order_log(limit: int = 200, status: str | None = None, symbol: str | None = None, repo=Depends(_repo)):
    """订单日志：每笔订单的提交/成交/拒绝/撤销/平仓动作与状态。"""

    logs = repo.list_order_logs(limit=limit, status=status, symbol=symbol)
    return {
        "logs": [
            {
                "id": log.id,
                "mt5_ticket": log.mt5_ticket,
                "symbol": log.symbol,
                "side": log.side,
                "order_type": log.order_type,
                "volume": log.volume,
                "price": log.price,
                "stoplimit_price": log.stoplimit_price,
                "sl": log.sl,
                "tp": log.tp,
                "status": log.status,
                "action": log.action,
                "factor_name": log.factor_name,
                "reason": log.reason,
                "message": log.message,
                "request_payload": log.request_payload or {},
                "response_data": log.response_data or {},
                "created_at": log.created_at.isoformat(),
                "updated_at": log.updated_at.isoformat(),
            }
            for log in logs
        ]
    }


@router.post("/trading/close-all")
async def close_all(state=Depends(get_app_state)):
    snapshot = state.market.snapshot("EURUSD", "M15")
    closed = await state.orders.close_all(snapshot.current_price)
    return {"message": f"一键平仓完成，共 {len(closed)} 个持仓", "closed": [o.model_dump() for o in closed]}


@router.get("/risk/calculator")
async def risk_calculator(symbol: str = "EURUSD", timeframe: str = "M15", direction: str = "long", state=Depends(get_app_state)):
    snapshot = state.market.snapshot(symbol, timeframe)
    from risk_sizing.sizing import compute_position

    sizing = compute_position(
        entry_price=snapshot.current_price,
        atr=snapshot.atr,
        direction=direction,
        config=RiskConfig(account_equity=state.settings.account_equity),
    )
    return sizing.model_dump()


@router.get("/replay/trades")
async def replay_trades(limit: int = 50, repo=Depends(_repo)):
    trades = repo.list_trades(limit=limit)
    return {"trades": [t.__dict__ | {"id": t.id} for t in trades]}


@router.post("/replay/trade-quality")
async def update_trade_quality(req: TradeQualityRequest, repo=Depends(_repo)):
    if req.quality not in ("ideal", "general", "poor"):
        raise HTTPException(status_code=400, detail="质量标签只能是 ideal/general/poor")
    if not repo.update_trade(req.trade_id, quality=req.quality):
        raise HTTPException(status_code=404, detail="交易记录不存在")
    return {"message": "质量标签已更新", "trade_id": req.trade_id, "quality": req.quality}


@router.post("/replay/trade-stats")
async def trade_stats(req: TradeStatsRequest, state=Depends(get_app_state), repo=Depends(_repo)):
    """按条件筛选订单，统计胜率、盈亏比等指标。"""

    accounts = repo.list_account_ids()
    if state.mt5_gateway:
        try:
            info = state.mt5_gateway.get_account_info()
        except Exception:
            info = None
        if info and info.get("login") is not None:
            current_login = str(info["login"])
            repo.backfill_account_id(current_login)
            if current_login not in accounts:
                accounts.append(current_login)

    trades = repo.list_trades_filtered(
        symbol=req.symbol,
        account_id=req.account_id,
        side=req.side,
        quality=req.quality,
        min_lots=req.min_lots,
        max_lots=req.max_lots,
        fixed_lots=req.fixed_lots,
        start_time=req.start_time,
        end_time=req.end_time,
    )
    pnls: list[float] = []
    peaks: list[float] = []
    troughs: list[float] = []
    holds: list[float] = []
    by_lots: dict[str, dict[str, Any]] = {}
    for t in trades:
        lots = float(t.lots or 0.0)
        scale = (1.0 / lots) if (req.normalize_to_one_lot and lots > 0) else 1.0
        pnl = float(t.pnl or 0.0) * scale
        pnls.append(pnl)
        if t.peak_pnl is not None:
            peaks.append(float(t.peak_pnl) * scale)
        if t.trough_pnl is not None:
            troughs.append(float(t.trough_pnl) * scale)
        start = t.entry_time
        end = t.exit_time
        if start and end:
            holds.append((end - start).total_seconds() / 60.0)
        lot_key = f"{round(lots, 2):.2f}"
        bucket = by_lots.setdefault(lot_key, {"count": 0, "wins": 0, "sum_pnl": 0.0, "sum_pos": 0.0, "sum_neg": 0.0})
        bucket["count"] += 1
        bucket["sum_pnl"] += pnl
        if pnl > 0:
            bucket["wins"] += 1
            bucket["sum_pos"] += pnl
        elif pnl < 0:
            bucket["sum_neg"] += abs(pnl)

    wins = sum(1 for p in pnls if p > 0)
    sum_pos = sum(p for p in pnls if p > 0)
    sum_neg = abs(sum(p for p in pnls if p < 0))
    profit_factor = round(sum_pos / sum_neg, 2) if sum_neg > 0 else None
    lot_rows = [
        {
            "lots": key,
            "count": b["count"],
            "win_rate_pct": round(b["wins"] / b["count"] * 100, 1) if b["count"] else 0.0,
            "profit_factor": round(b["sum_pos"] / b["sum_neg"], 2) if b["sum_neg"] > 0 else None,
            "total_pnl": round(b["sum_pnl"], 2),
        }
        for key, b in sorted(by_lots.items(), key=lambda x: float(x[0]))
    ]
    return {
        "count": len(trades),
        "filters": req.model_dump(),
        "accounts": sorted(set(accounts)),
        "normalized_to_one_lot": bool(req.normalize_to_one_lot),
        "win_count": wins,
        "win_rate_pct": round(wins / len(trades) * 100, 2) if trades else 0.0,
        "profit_factor": profit_factor,
        "total_pnl": round(sum(pnls), 2),
        "avg_pnl": round(sum(pnls) / len(pnls), 2) if pnls else 0.0,
        "avg_peak_pnl": round(sum(peaks) / len(peaks), 2) if peaks else 0.0,
        "avg_trough_pnl": round(sum(troughs) / len(troughs), 2) if troughs else 0.0,
        "avg_hold_minutes": round(sum(holds) / len(holds), 1) if holds else 0.0,
        "by_lots": lot_rows,
    }


@router.post("/replay/factor-stats")
async def factor_stats(req: FactorStatsRequest, repo=Depends(_repo)):
    factors = repo.list_factors(status=req.status, symbol=req.symbol)
    if req.timeframe:
        factors = [f for f in factors if f.timeframe == req.timeframe]

    def _row(f) -> dict[str, Any]:
        bt = f.backtest_stats or {}
        return {
            "factor_id": f.id,
            "factor_name": f.name,
            "symbol": f.symbol,
            "timeframe": f.timeframe,
            "status": f.status,
            "source": f.source,
            "model": f.model,
            "params": f.params,
            "tags": f.tags,
            "backtest_stats": bt,
            "sl_tp_strategy": f.sl_tp_strategy or {},
            "created_at": str(f.created_at),
        }

    rows = [_row(f) for f in factors]
    detail = None
    if req.factor_id:
        factor = repo.get_factor(req.factor_id)
        if factor:
            detail = _row(factor)
            detail["code"] = factor.code
            detail["description"] = factor.description
    return {"overall": {"count": len(rows)}, "factors": rows, "detail": detail}


@router.post("/replay/sltp-learn")
async def sltp_learn(req: SltpLearnRequest, repo=Depends(_repo)):
    trades = repo.get_trades_by_ids(req.trade_ids)
    cases = []
    for t in trades:
        entry = float(t.entry_price)
        raw_peak = float(t.peak_price) if t.peak_price else None
        raw_trough = float(t.trough_price) if t.trough_price else None
        exit_price = float(t.exit_price or entry)
        if t.side in ("long", "buy"):
            sl = raw_trough if raw_trough and raw_trough < entry else entry * 0.999
            tp = raw_peak if raw_peak and raw_peak > entry else (exit_price if exit_price > entry else entry * 1.001)
        else:
            sl = raw_trough if raw_trough and raw_trough > entry else entry * 1.001
            tp = raw_peak if raw_peak and raw_peak < entry else (exit_price if exit_price < entry else entry * 0.999)
        stop_usd = abs(entry - sl) * 100.0
        take_usd = abs(tp - entry) * 100.0
        strategy = {
            "stop_method": "level",
            "take_method": "level",
            "sl_price": round(sl, 5),
            "tp_price": round(tp, 5),
            "stop_usd_per_lot": round(stop_usd, 2),
            "take_usd_per_lot": round(take_usd, 2),
        }
        record = SltpCaseRecord(
            id=str(uuid.uuid4()),
            trade_id=t.id,
            factor_id=t.factor_id or "",
            symbol=t.symbol,
            side=t.side,
            entry_price=round(entry, 5),
            sl_price=round(sl, 5),
            tp_price=round(tp, 5),
            stop_usd_per_lot=round(stop_usd, 2),
            take_usd_per_lot=round(take_usd, 2),
            strategy=strategy,
            quality=getattr(t, "quality", "") or "",
        )
        repo.save_sltp_case(record)
        cases.append(record.__dict__ | {"id": record.id})
    return {"cases": cases, "saved": len(cases)}


@router.get("/replay/sltp-cases")
async def sltp_cases(limit: int = 200, repo=Depends(_repo)):
    cases = repo.list_sltp_cases(limit=limit)
    return {"cases": [c.__dict__ | {"id": c.id} for c in cases]}


@router.get("/sltp/strategies")
async def sltp_strategies(symbol: str | None = None, side: str | None = None, state=Depends(get_app_state)):
    return {"strategies": state.sltp_engine.strategies(symbol=symbol, side=side)}


@router.post("/sltp/learn")
async def sltp_learn_auto(state=Depends(get_app_state)):
    return await asyncio.to_thread(state.sltp_engine.learn)


@router.post("/sltp/train")
async def sltp_train_model(state=Depends(get_app_state)):
    return await asyncio.to_thread(state.sltp_engine.train)


@router.get("/sltp/model")
async def sltp_model_status(state=Depends(get_app_state)):
    return state.sltp_engine.model_status()


@router.post("/replay/delete-trades")
async def delete_trades(req: DeleteTradesRequest, repo=Depends(_repo)):
    if not req.trade_ids:
        raise HTTPException(status_code=400, detail="请选择要删除的交易记录")
    deleted = repo.delete_trades_by_ids(req.trade_ids)
    return {"message": f"已删除 {deleted} 条交易记录", "deleted": deleted}


@router.post("/replay/sync-mt5-trades")
async def sync_mt5_trades(req: SyncMt5TradesRequest, state=Depends(get_app_state)):
    """从 MT5 历史成交补录已平仓订单到复盘交易记录。"""

    if not state.mt5_gateway:
        raise HTTPException(status_code=503, detail="需要 MT5 数据源才能同步历史成交")
    account_id = ""
    try:
        info = state.mt5_gateway.get_account_info()
    except Exception:
        info = None
    if info and info.get("login") is not None:
        account_id = str(info["login"])
        state.repository.backfill_account_id(account_id)
    tickets = sorted(
        {
            str(log.mt5_ticket)
            for log in state.repository.list_order_logs(limit=10000)
            if log.mt5_ticket
        }
    )
    existing = state.repository.get_trade_tickets()
    imported = 0
    skipped = 0
    for ticket in tickets:
        if ticket in existing:
            skipped += 1
            continue
        data = state.mt5_gateway.get_closed_trade(int(ticket))
        if not data:
            continue
        entry_dt = datetime.fromtimestamp(int(data["entry_time"]), tz=timezone.utc)
        exit_dt = datetime.fromtimestamp(int(data["exit_time"]), tz=timezone.utc)
        extremes = compute_trade_extremes(
            state.mt5_gateway,
            data["symbol"],
            "M15",
            entry_dt,
            exit_dt,
            data["side"],
            float(data["volume"]),
            data["entry_price"],
            data["pnl"],
        )
        from ai_replay.replay import generate_replay_report

        report = generate_replay_report(
            {
                "id": ticket,
                "side": data["side"],
                "entry_time": entry_dt.isoformat(),
                "exit_time": exit_dt.isoformat(),
                "entry_price": data["entry_price"],
                "exit_price": data["exit_price"],
                "pnl": data["pnl"],
                "exit_reason": data["reason"],
            },
            factor_name="MT5 实盘交易",
            symbol=data["symbol"],
        )
        record = TradeRecord(
            id=str(uuid.uuid4()),
            mt5_ticket=ticket,
            account_id=account_id,
            factor_id="",
            factor_name="MT5 实盘交易",
            symbol=data["symbol"],
            side=data["side"],
            entry_time=entry_dt,
            entry_price=data["entry_price"],
            exit_time=exit_dt,
            exit_price=data["exit_price"],
            lots=float(data["volume"]),
            pnl=float(data["pnl"]),
            exit_reason=str(data["reason"])[:32],
            report_markdown=report.markdown,
            **extremes,
        )
        state.repository.save_trade(record)
        existing.add(ticket)
        imported += 1
    return {
        "message": f"同步完成：新增 {imported} 笔，已存在 {skipped} 笔",
        "imported": imported,
        "skipped": skipped,
    }


@router.post("/replay/migrate-account")
async def migrate_trade_account(state=Depends(get_app_state)):
    """将全部历史交易记录归属到当前 MT5 账户。"""

    if not state.mt5_gateway:
        raise HTTPException(status_code=503, detail="需要 MT5 数据源才能获取当前账户")
    try:
        info = state.mt5_gateway.get_account_info()
    except Exception:
        info = None
    if not info or info.get("login") is None:
        raise HTTPException(status_code=400, detail="当前 MT5 账户不可用")
    account_id = str(info["login"])
    updated = state.repository.reassign_account_id(account_id)
    return {
        "message": f"已将 {updated} 笔历史交易记录归属到账户 {account_id}",
        "updated": updated,
        "account_id": account_id,
    }


@router.get("/smart-stop/config")
async def get_smart_stop_config(state=Depends(get_app_state)):
    return {
        "config": state.smart_stop_config.to_dict(),
        "enabled": bool(state.smart_stop_config.enabled),
        "last_updates": (state.smart_stop.last_updates if state.smart_stop else [])[:20],
    }


@router.post("/system/restart")
async def restart_system(state=Depends(get_app_state)):
    """重启系统：先暂停匹配并保存状态，再关闭所有相关进程并自动启动新后端。"""

    state.matcher_running = False
    state.repository.save_system_state(
        False,
        state.matcher_symbol,
        state.matcher_timeframe,
        state.signal_executor.config.to_dict(),
    )
    script = Path(__file__).resolve().parents[1] / "restart_system.ps1"
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    powershell = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    subprocess.Popen(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-WindowStyle",
            "Hidden",
            "-File",
            str(script),
        ],
        cwd=Path(__file__).resolve().parents[1],
        creationflags=flags,
        close_fds=True,
    )
    return {"message": "系统正在重启，所有相关进程将关闭后自动重新启动", "ok": True}


@router.post("/smart-stop/config")
async def update_smart_stop_config(req: SmartStopConfigRequest, state=Depends(get_app_state)):
    config = SmartStopConfig.from_dict(req.config)
    state.smart_stop_config = config
    return {"message": "智能止损引擎配置已保存", "config": config.to_dict()}


@router.get("/sltp/recommend")
async def get_sltp_recommend(
    symbol: str = "EURUSD",
    timeframe: str = "M15",
    state=Depends(get_app_state),
):
    return recommend_sltp(symbol, timeframe, state.market_analysis)


@router.get("/sltp/policy")
async def get_sltp_policy(state=Depends(get_app_state)):
    market: dict[str, Any] = {}
    try:
        market = state.market_analysis.analyze(state.sltp_policy.symbol, state.sltp_policy.timeframe)
    except Exception:
        market = {}
    return {
        "config": state.sltp_policy.to_dict(),
        "enabled": bool(state.sltp_policy.enabled),
        "scan_interval": SltpPolicyManager.scan_interval(state.sltp_policy, market),
        "last_updates": (state.sltp_manager.last_updates if state.sltp_manager else [])[:20],
    }


@router.post("/sltp/policy")
async def update_sltp_policy(req: SltpPolicyRequest, state=Depends(get_app_state)):
    config = SltpPolicyConfig.from_dict(req.config)
    state.sltp_policy = config
    state.repository.save_sltp_policy(config.to_dict())
    return {
        "message": "止损止盈政策已保存（重启后自动恢复）",
        "config": config.to_dict(),
        "enabled": bool(config.enabled),
    }


@router.get("/sltp/state")
async def get_sltp_state(state=Depends(get_app_state)):
    positions: list[dict[str, Any]] = []
    if state.sltp_manager:
        try:
            positions = await state.sltp_manager.diagnose(state.sltp_policy)
        except Exception:
            positions = []
    return {
        "enabled": bool(state.sltp_policy.enabled),
        "policy": state.sltp_policy.to_dict(),
        "manager": bool(state.sltp_manager is not None),
        "positions": positions,
        "last_updates": (state.sltp_manager.last_updates if state.sltp_manager else [])[:20],
    }


@router.post("/replay/backfill-extremes")
async def backfill_trade_extremes(state=Depends(get_app_state)):
    trades = state.repository.list_trades(limit=1000)
    updated = 0
    for trade in trades:
        if not trade.exit_time or not state.mt5_gateway:
            continue
        extremes = compute_trade_extremes(
            state.mt5_gateway,
            trade.symbol,
            "M15",
            trade.entry_time,
            trade.exit_time,
            trade.side,
            trade.lots,
            trade.entry_price,
            trade.pnl,
        )
        if state.repository.update_trade(trade.id, **extremes):
            updated += 1
    return {"message": f"已回填 {updated} 笔交易的最高浮盈/最低浮亏", "updated": updated}


@router.post("/replay/export")
async def export_replay(req: ReplayExportRequest, repo=Depends(_repo)):
    from ai_replay.replay import generate_replay_report

    trade_id = req.trade_id
    if not trade_id:
        trades = repo.list_trades(limit=1)
        if not trades:
            raise HTTPException(status_code=404, detail="暂无交易记录")
        trade = trades[0]
    else:
        trade = next((t for t in repo.list_trades(limit=500) if t.id == trade_id), None)
        if not trade:
            raise HTTPException(status_code=404, detail="交易记录不存在")
    if trade.report_markdown:
        report = ReplayReport(
            trade_id=trade.id,
            factor_name=trade.factor_name,
            symbol=trade.symbol,
            markdown=trade.report_markdown,
            score=100.0,
            generated_by="AI 自动复盘",
        )
    else:
        report = generate_replay_report(
            {
                "id": trade.id,
                "side": trade.side,
                "entry_time": trade.entry_time.isoformat(),
                "exit_time": trade.exit_time.isoformat() if trade.exit_time else None,
                "entry_price": trade.entry_price,
                "exit_price": trade.exit_price,
                "pnl": trade.pnl,
                "exit_reason": trade.exit_reason,
            },
            factor_name=trade.factor_name,
            symbol=trade.symbol,
        )
    return report.model_dump()
