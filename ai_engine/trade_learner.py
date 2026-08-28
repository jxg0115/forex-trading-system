"""AI 交易学习：从选中的历史订单中提炼新因子与止损止盈策略。"""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

from ai_engine.factor_generator import TEMPLATES
from ai_engine.prompts import SYSTEM_PROMPT, extract_python_code
from backtest_store.engine import Backtester
from backtest_store.trade_optimizer import optimize_trade
from backtest_store.trade_extremes import _as_utc
from backtest_store.validation import run_validation
from models.factor import FactorDraft
from sandbox.ast_check import check_source
from sandbox.runner import execute_factor


def _dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _strategy_defaults() -> dict[str, Any]:
    return {
        "initial_sltp_source": "pattern",
        "stop_method": "atr",
        "take_method": "atr",
        "stop_atr_mult": 2.0,
        "take_atr_mult": 3.0,
        "level_buffer_pct": 0.1,
        "take_level_buffer_pct": 0.1,
        "trailing_enabled": True,
        "trailing_unit": "atr",
        "trailing_activation_pct": 0.3,
        "trailing_retrace_pct": 0.3,
        "trailing_take_retrace_pct": 0.3,
        "trailing_take_buffer_pct": 0.2,
        "trailing_activation_atr": 0.5,
        "trailing_stop_atr": 1.0,
        "trailing_take_atr": 0.5,
        "trailing_take_buffer_atr": 0.5,
        "max_hold_bars": 120,
        "hw_activation_profit": 200.0,
        "hw_max_retrace_pct": 20.0,
        "description": "由 AI 根据选中历史订单学习得到的止损止盈策略",
    }


def summarize_trades(trades: list[Any]) -> dict[str, Any]:
    pnls = [_num(t.pnl) for t in trades if t.pnl is not None]
    peaks = [_num(t.peak_pnl) for t in trades if t.peak_pnl is not None]
    troughs = [_num(t.trough_pnl) for t in trades if t.trough_pnl is not None]
    wins = [p for p in pnls if p > 0]
    holds: list[float] = []
    ratios: list[float] = []
    for t in trades:
        start = _dt(t.entry_time)
        end = _dt(t.exit_time)
        if start and end:
            holds.append((end - start).total_seconds() / 60.0)
        peak = _num(t.peak_pnl)
        trough = _num(t.trough_pnl)
        denom = max(abs(trough), 1.0)
        if peak and denom:
            ratios.append(abs(peak) / denom)
    symbol_counts: dict[str, int] = {}
    quality_counts: dict[str, int] = {"ideal": 0, "general": 0, "poor": 0}
    for t in trades:
        symbol_counts[t.symbol or "未知"] = symbol_counts.get(t.symbol or "未知", 0) + 1
        quality = getattr(t, "quality", "") or ""
        if quality in quality_counts:
            quality_counts[quality] += 1
    top_symbol = max(symbol_counts, key=symbol_counts.get) if symbol_counts else "EURUSD"
    return {
        "count": len(trades),
        "long_count": sum(1 for t in trades if t.side in ("long", "buy")),
        "short_count": sum(1 for t in trades if t.side in ("short", "sell")),
        "win_count": len(wins),
        "win_rate_pct": round(len(wins) / len(pnls) * 100, 1) if pnls else 0.0,
        "avg_pnl": round(sum(pnls) / len(pnls), 2) if pnls else 0.0,
        "avg_peak_pnl": round(sum(peaks) / len(peaks), 2) if peaks else 0.0,
        "avg_trough_pnl": round(sum(troughs) / len(troughs), 2) if troughs else 0.0,
        "max_peak_pnl": round(max(peaks), 2) if peaks else 0.0,
        "min_trough_pnl": round(min(troughs), 2) if troughs else 0.0,
        "avg_hold_minutes": round(sum(holds) / len(holds), 1) if holds else 0.0,
        "avg_peak_trough_ratio": round(sum(ratios) / len(ratios), 2) if ratios else 0.0,
        "symbol_counts": symbol_counts,
        "quality_counts": quality_counts,
        "top_symbol": top_symbol,
    }


def parse_strategy_json(text: str) -> dict[str, Any]:
    match = re.search(r"```json\s*\n(.*?)```", text, flags=re.S)
    raw = match.group(1) if match else None
    if raw is None:
        match = re.search(r"\{.*\}", text, flags=re.S)
        raw = match.group(0) if match else None
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
        except Exception:
            return {}
    return {}


def _format_trade_table(trades: list[Any], max_rows: int = 20) -> str:
    head = "| 品种 | 方向 | 入场 | 出场 | 实际盈亏 | 最高浮盈 | 最低浮亏 | 持仓(分) |"
    sep = "|---|---|---|---|---|---|---|---|"
    lines = [head, sep]
    step = max(1, len(trades) // max_rows) if trades else 1
    for t in trades[::step][-max_rows:]:
        start = _dt(t.entry_time)
        end = _dt(t.exit_time)
        hold = round((end - start).total_seconds() / 60.0, 1) if start and end else 0.0
        side = "多头" if t.side in ("long", "buy") else "空头"
        lines.append(
            f"| {t.symbol} | {side} | {_num(t.entry_price):.5f} | {_num(t.exit_price):.5f} "
            f"| {_num(t.pnl):.2f} | {_num(t.peak_pnl):.2f} | {_num(t.trough_pnl):.2f} | {hold} |"
        )
    return "\n".join(lines)


def _format_bars(bars: list[dict[str, Any]], max_rows: int = 30) -> str:
    head = "| 时间 | 开盘 | 最高 | 最低 | 收盘 | 成交量 |"
    sep = "|---|---|---|---|---|---|"
    lines = [head, sep]
    step = max(1, len(bars) // max_rows) if bars else 1
    for row in bars[::step][-max_rows:]:
        lines.append(
            f"| {row['time']} | {_num(row['open']):.5f} | {_num(row['high']):.5f} "
            f"| {_num(row['low']):.5f} | {_num(row['close']):.5f} | {_num(row['volume']):.0f} |"
        )
    return "\n".join(lines)


def build_prompt(
    trades: list[Any],
    stats: dict[str, Any],
    symbol: str,
    timeframe: str,
    bars: list[dict[str, Any]],
    optimizations: list[dict[str, Any]] | None = None,
) -> str:
    opt_text = "\n".join(
        f"- {o['symbol']} {o['side']}：原始盈亏 {o['current_pnl']} → 优化后 {o['optimized_pnl']}"
        f"（止损 {o['best_params']['stop_atr_mult']} ATR / 止盈 {o['best_params']['take_atr_mult']} ATR / "
        f"最多持仓 {o['best_params']['max_hold_bars']} 根）"
        for o in (optimizations or [])
    ) or "无"
    return f"""请从以下选中的历史订单中学习共性规律，输出两部分内容：

## 一、可复用 Python 因子代码
识别这些订单入场前共同出现的市场状态，抽象为通用信号因子。代码要求与系统一致：
1. 只能 import pandas（别名 pd）与 numpy（别名 np）。
2. 必须定义 calculate(df, params) -> dict，df 包含 open/high/low/close/volume。
3. 返回值必须包含 entry：1.0 多头入场、-1.0 空头入场、0.0 无信号；可返回 exit 平仓信号。
4. 禁止使用未来数据，信号只能用当前及之前 K 线，实盘第 t+1 根开盘执行。
5. 使用 pandas/numpy 向量化运算，代码控制在 60 行内。

## 二、止损止盈策略 JSON
根据这些订单的最高浮盈、最低浮亏、实际盈亏和持仓时长，给出更合理的策略参数，JSON 示例：
```json
{{
  "initial_sltp_source": "pattern",
  "stop_method": "atr",
  "take_method": "atr",
  "stop_atr_mult": 2.0,
  "take_atr_mult": 3.0,
  "trailing_enabled": true,
  "trailing_unit": "atr",
  "trailing_activation_atr": 0.5,
  "trailing_stop_atr": 1.0,
  "trailing_take_atr": 0.5,
  "max_hold_bars": 120,
  "description": "策略中文说明"
}}
```

## 选中订单统计
- 订单数量：{stats["count"]}（多头 {stats["long_count"]} / 空头 {stats["short_count"]}）
- 胜率：{stats["win_rate_pct"]}%
- 平均实际盈亏：{stats["avg_pnl"]}
- 平均最高浮盈：{stats["avg_peak_pnl"]}，最大浮盈：{stats["max_peak_pnl"]}
- 平均最低浮亏：{stats["avg_trough_pnl"]}，最小浮亏：{stats["min_trough_pnl"]}
- 平均持仓：{stats["avg_hold_minutes"]} 分钟
- 平均浮盈/浮亏比：{stats["avg_peak_trough_ratio"]}
- 品种分布：{json.dumps(stats["symbol_counts"], ensure_ascii=False)}
- 质量分布：理想 {stats["quality_counts"].get("ideal", 0)} / 一般 {stats["quality_counts"].get("general", 0)} / 不理想 {stats["quality_counts"].get("poor", 0)}

## 不理想订单模拟优化建议
以下不理想订单已固定入场点，在历史 K 线上模拟尝试不同止损/止盈/持仓参数，请把优化后的参数作为止损止盈策略的重要参考，并学习“怎样的参数能改善这类单子”：
{opt_text}

## 选中订单明细
{_format_trade_table(trades)}

## 最近行情快照（{symbol} / {timeframe}）
{_format_bars(bars)}

## 输出格式
先输出 ```python 代码块，再输出 ```json 策略块，不要输出其他解释。"""


def _sample_bars(count: int = 300) -> list[dict[str, Any]]:
    price = 100.0
    bars = []
    for i in range(count):
        price *= 1.001 if i % 3 else 0.999
        bars.append(
            {
                "time": f"2026-01-01T00:{i % 60:02d}:00Z",
                "open": round(price, 2),
                "high": round(price * 1.002, 2),
                "low": round(price * 0.998, 2),
                "close": round(price, 2),
                "volume": 1000.0,
            }
        )
    return bars


def _rule_based_result(
    stats: dict[str, Any],
    symbol: str,
    timeframe: str,
    bars: list[dict[str, Any]],
) -> tuple[FactorDraft, dict[str, Any]]:
    long_bias = stats["long_count"] >= stats["short_count"]
    if stats["avg_peak_trough_ratio"] >= 1.5:
        key = "breakout"
    elif long_bias:
        key = "pullback"
    else:
        key = "mean_reversion"
    code = TEMPLATES[key][0]
    params: dict[str, Any] = {
        "atr_period": 14,
        "lookback": 20,
        "k": 1.2,
        "fast": 10,
        "slow": 50,
        "period": 20,
    }
    template_name = {"breakout": "突破加速", "pullback": "趋势回踩", "mean_reversion": "均值回归"}[key]
    draft = FactorDraft(
        name=f"AI 学习因子_{symbol}_{timeframe}",
        description=(
            f"基于 {stats['count']} 笔历史订单（胜率 {stats['win_rate_pct']:.1f}%）"
            f"学习生成的{template_name}因子，平均持仓 {stats['avg_hold_minutes']:.0f} 分钟。"
        ),
        code=code,
        model="本地规则模板",
        source="ai_learn",
        params=params,
        tags=["ai_learn", key],
    )
    strategy = _strategy_defaults()
    strategy["description"] = (
        f"基于 {stats['count']} 笔订单学习：平均最高浮盈 {stats['avg_peak_pnl']}，"
        f"平均最低浮亏 {stats['avg_trough_pnl']}，建议先控制回撤再追求止盈。"
    )
    if bars:
        execution = execute_factor(code, bars, params)
        if execution.ok:
            draft.sandbox = check_source(code)
            draft.execution = execution
    return draft, strategy


def run_backtest(
    market: Any,
    gateway: Any,
    code: str,
    symbol: str,
    timeframe: str,
    params: dict[str, Any],
    strategy: dict[str, Any],
) -> dict[str, Any] | None:
    try:
        bars = market.get_bars(symbol, timeframe, limit=500)
        if not bars:
            return None
        execution = execute_factor(code, bars, params)
        if not execution.ok:
            return None
        df = pd.DataFrame(bars)
        df["time"] = pd.to_datetime(df["time"], utc=True)
        df = df.set_index("time")
        entry = pd.Series(execution.entry_values, index=df.index)
        exit_sig = pd.Series(execution.exit_values, index=df.index) if execution.exit_values else None
        bt_params = {
            "initial_equity": 10_000.0,
            "risk_per_trade_pct": 1.0,
            "leverage": 30,
            "stop_atr_mult": float(strategy.get("stop_atr_mult", 2.0)),
            "take_atr_mult": float(strategy.get("take_atr_mult", 3.0)),
            "max_hold_bars": int(strategy.get("max_hold_bars", 120)),
            **params,
        }
        result = Backtester().run(df, entry, exit_sig, bt_params)
        return result.model_dump()
    except Exception:
        return None


async def learn_from_trades(
    state: Any,
    trade_ids: list[str],
    symbol: str | None = None,
    timeframe: str = "M15",
    period_mode: str = "all",
    start_time: Any = None,
    end_time: Any = None,
    recent_months: int = 6,
    recent_bars: int = 500,
    gate_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    trades = state.repository.get_trades_by_ids(trade_ids)
    if not trades:
        raise ValueError("未找到选中的交易记录")
    if symbol:
        trades = [t for t in trades if str(t.symbol or "").upper() == str(symbol).upper()]
    now = datetime.now(timezone.utc)
    window_start: datetime | None = None
    window_end: datetime | None = None
    if period_mode == "recent_months":
        window_start = now - timedelta(days=30 * max(int(recent_months or 6), 1))
    elif period_mode == "recent_months_12":
        window_start = now - timedelta(days=365)
    elif period_mode == "recent_bars":
        bar_seconds = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400}.get(
            str(timeframe).upper(), 900
        )
        window_start = now - timedelta(seconds=max(int(recent_bars or 500), 1) * bar_seconds)
    elif period_mode == "custom":
        window_start = _as_utc(start_time)
        window_end = _as_utc(end_time)
    if window_start is not None or window_end is not None:
        trades = [
            t
            for t in trades
            if (window_start is None or (_as_utc(t.entry_time) or now) >= window_start)
            and (window_end is None or (_as_utc(t.entry_time) or now) <= window_end)
        ]
    if len(trades) < 3:
        raise ValueError(
            f"当前设置下可用学习样本不足（{len(trades)} 笔），请调整时间范围或门槛，至少需要 3 笔"
        )

    stats = summarize_trades(trades)
    symbol = symbol or stats["top_symbol"]
    stats["timeframe"] = timeframe

    bars: list[dict[str, Any]] = []
    if state.mt5_gateway:
        try:
            bars = state.mt5_gateway.get_rates(symbol, timeframe, 500) or []
        except Exception:
            bars = []
    if not bars:
        try:
            bars = state.market.get_bars(symbol, timeframe, limit=500)
        except Exception:
            bars = []
    snapshot = bars[-30:] if bars else _sample_bars(30)
    optimizations: list[dict[str, Any]] = []
    if state.mt5_gateway:
        poor_trades = [t for t in trades if (getattr(t, "quality", "") or "") == "poor"]
        for t in poor_trades[:5]:
            saved_opt = getattr(t, "optimization", None) or {}
            if saved_opt.get("ok"):
                optimizations.append(saved_opt)
                continue
            try:
                opt = optimize_trade(
                    state.mt5_gateway,
                    t.symbol,
                    timeframe,
                    t.entry_time,
                    t.exit_time,
                    t.side,
                    t.lots,
                    t.entry_price,
                    t.pnl,
                )
                if opt.get("ok"):
                    optimizations.append(opt)
                    state.repository.update_trade(t.id, optimization=opt)
            except Exception:
                continue
    stats["optimizations_count"] = len(optimizations)

    draft: FactorDraft | None = None
    strategy = _strategy_defaults()
    ai_model: str | None = None
    warning = ""
    ai_config = state.ai_manager.active_for("factor_learning")
    if ai_config:
        prompt = build_prompt(trades, stats, symbol, timeframe, snapshot, optimizations)
        ai_model = ai_config.get("model")
        for attempt in range(2):
            try:
                raw = await state.ai_manager.generate(
                    ai_config["id"],
                    prompt,
                    system_prompt=SYSTEM_PROMPT,
                )
                code = extract_python_code(raw)
                sandbox = check_source(code)
                execution = execute_factor(code, bars or _sample_bars(), {})
                if sandbox.ok and execution.ok:
                    strategy_data = parse_strategy_json(raw)
                    if strategy_data:
                        merged = {**strategy, **strategy_data}
                        strategy = {k: merged[k] for k in strategy if k in merged}
                    draft = FactorDraft(
                        name=f"AI 学习因子_{symbol}_{timeframe}",
                        description=(
                            f"基于 {stats['count']} 笔历史订单（胜率 {stats['win_rate_pct']:.1f}%）"
                            f"学习生成的因子，平均持仓 {stats['avg_hold_minutes']:.0f} 分钟。"
                        ),
                        code=code,
                        model=ai_config.get("model", "qwen3.8-max"),
                        source="ai_learn",
                        params={},
                        tags=["ai_learn"],
                        sandbox=sandbox,
                        execution=execution,
                    )
                    break
                detail = "；".join((sandbox.errors or [])[:3]) or execution.message
                if attempt == 0:
                    prompt = prompt + f"\n\n你上次生成的代码未通过校验：{detail}。请修复后重新输出完整 Python 代码块和 JSON 策略块。"
                else:
                    warning = f"AI 生成的因子未通过沙盒（{detail}），已使用本地规则模板兜底"
            except Exception as exc:
                if attempt == 1:
                    warning = f"AI 调用失败：{str(exc)[:200]}，已使用本地规则模板兜底"

    if draft is None:
        draft, strategy = _rule_based_result(stats, symbol, timeframe, bars or _sample_bars())
        if ai_config is None and not warning:
            warning = "未配置 AI 学习板块，已使用本地规则模板生成"

    learning_end = max(
        (_as_utc(t.exit_time) or _as_utc(t.entry_time) for t in trades),
        default=None,
    )
    base_symbols = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]
    alt_symbols = [s for s in base_symbols if s != symbol][:2] or base_symbols[:2]
    tf_map = {"M1": "M5", "M5": "M15", "M15": "M30", "M30": "H1", "H1": "H4", "H4": "D1", "D1": "M15"}
    cross_combos = [(s, timeframe) for s in alt_symbols] + [(symbol, tf_map.get(timeframe.upper(), "M30"))]
    validation = run_validation(
        state.market,
        state.mt5_gateway,
        draft.code,
        symbol,
        timeframe,
        draft.params,
        strategy,
        learning_end,
        cross_combos,
        gate_config,
    )
    return {
        "ok": True,
        "trade_summary": stats,
        "optimizations": optimizations,
        "factor_draft": draft.model_dump(),
        "sl_tp_strategy": strategy,
        "backtest": validation.get("validation"),
        "out_of_sample_backtest": validation.get("out_of_sample"),
        "cross_validation": validation.get("cross_validation", []),
        "validation_stats": validation,
        "ai_model": ai_model,
        "warning": warning,
        "symbol": symbol,
        "timeframe": timeframe,
    }
