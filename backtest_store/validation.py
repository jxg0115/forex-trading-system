"""回测可信度验证：样本分离、多品种交叉验证与统计置信区间。"""

from __future__ import annotations

import random
from typing import Any

import pandas as pd

from backtest_store.costs import cost_defaults
from backtest_store.engine import Backtester
from backtest_store.trade_extremes import _as_utc, _to_local_naive
from sandbox.runner import execute_factor


def _bt_run(
    bars: list[dict[str, Any]],
    code: str,
    params: dict[str, Any],
    strategy: dict[str, Any],
    with_costs: bool = True,
    slippage: float = 0.0,
    symbol: str = "",
) -> dict[str, Any] | None:
    try:
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
            "commission_pct": 0.0002 if with_costs else 0.0,
            "slippage_price": slippage if with_costs else 0.0,
            "symbol": symbol,
            **params,
        }
        return Backtester().run(df, entry, exit_sig, bt_params).model_dump()
    except Exception:
        return None


def fetch_bars_after(
    market: Any,
    gateway: Any,
    symbol: str,
    timeframe: str,
    learning_end: Any,
    limit: int = 600,
) -> tuple[list[dict[str, Any]], bool]:
    """优先拉取学习期之后的行情；无法严格分离时返回最近 K 线并标记可能重叠。"""

    end_dt = _as_utc(learning_end)
    bars: list[dict[str, Any]] = []
    disjoint = False
    if gateway and end_dt is not None:
        local_from = _to_local_naive(end_dt)
        try:
            bars = gateway.get_rates_range(symbol, timeframe, local_from, None) or []
        except Exception:
            bars = []
        if bars:
            filtered = []
            for bar in bars:
                bar_dt = _as_utc(bar.get("time"))
                if bar_dt is not None and bar_dt >= end_dt:
                    filtered.append(bar)
            bars = filtered
            disjoint = True
    if not bars:
        try:
            bars = market.get_bars(symbol, timeframe, limit=limit) or []
        except Exception:
            bars = []
    return bars[-limit:], disjoint


def bootstrap_metrics(
    trades: list[dict[str, Any]],
    samples: int = 1000,
    seed: int = 42,
) -> dict[str, Any]:
    pnls = [float(t.get("pnl") or 0.0) for t in trades]
    if len(pnls) < 5:
        return {
            "num_trades": len(pnls),
            "samples": 0,
            "win_rate_ci": [0.0, 0.0],
            "profit_factor_ci": [0.0, 0.0],
            "insufficient": True,
        }
    rng = random.Random(seed)
    win_rates: list[float] = []
    profit_factors: list[float] = []
    for _ in range(samples):
        sample = [rng.choice(pnls) for _ in pnls]
        wins = [p for p in sample if p > 0]
        loss = abs(sum(p for p in sample if p < 0))
        win_rates.append(len(wins) / len(sample) * 100.0)
        profit_factors.append(min(sum(wins) / loss, 100.0) if loss > 0 else 100.0)

    def _ci(values: list[float]) -> list[float]:
        ordered = sorted(values)
        lo = ordered[max(0, int(0.025 * len(ordered)))]
        hi = ordered[min(len(ordered) - 1, int(0.975 * len(ordered)) - 1)]
        return [round(lo, 2), round(hi, 2)]

    return {
        "num_trades": len(pnls),
        "samples": samples,
        "win_rate_ci": _ci(win_rates),
        "profit_factor_ci": _ci(profit_factors),
        "insufficient": False,
    }


def _random_baseline(
    bars: list[dict[str, Any]],
    num_trades: int,
    samples: int = 20,
    seed: int = 7,
) -> float:
    if len(bars) < 3 or num_trades < 1:
        return 0.0
    rng = random.Random(seed)
    total = 0.0
    for _ in range(samples):
        equity = 10_000.0
        count = min(num_trades, len(bars) - 2)
        for i in rng.sample(range(1, len(bars) - 1), count):
            entry = float(bars[i]["open"])
            hold = rng.randint(1, min(20, len(bars) - i - 1))
            exit_price = float(bars[i + hold]["close"])
            equity += (exit_price / entry - 1.0) * 100.0
        total += (equity / 10_000.0 - 1.0) * 100.0
    return round(total / samples, 2)


def _compute_gate(
    validation: dict[str, Any] | None,
    out_of_sample: dict[str, Any] | None,
    gate_config: dict[str, Any] | None = None,
) -> tuple[bool, list[str]]:
    gate_config = gate_config or {}
    reasons: list[str] = []
    vm = (validation or {}).get("metrics")
    om = (out_of_sample or {}).get("metrics")
    if not vm:
        return False, ["验证期回测失败或无数据"]
    min_trades = int(gate_config.get("min_validation_trades", 10))
    min_pf = float(gate_config.get("min_profit_factor", 1.0))
    min_win = float(gate_config.get("min_win_rate_pct", 40.0))
    min_oos_trades = int(gate_config.get("min_oos_trades", 1))
    if vm["num_trades"] < min_trades:
        reasons.append(f"验证期交易样本不足（{vm['num_trades']} 笔），至少需要 {min_trades} 笔")
    if vm["profit_factor"] < min_pf:
        reasons.append(f"验证期盈亏比低于 {min_pf}")
    if vm["win_rate_pct"] < min_win:
        reasons.append(f"验证期胜率低于 {min_win}%")
    if not om:
        reasons.append("最终样本外回测失败")
    elif om["num_trades"] < min_oos_trades:
        reasons.append("最终样本外无交易，无法验证泛化能力")
    elif om["profit_factor"] < float(gate_config.get("min_oos_profit_factor", 0.8)):
        reasons.append("最终样本外盈亏比低于 0.8，疑似过拟合")
    return not reasons, reasons


def run_validation(
    market: Any,
    gateway: Any,
    code: str,
    symbol: str,
    timeframe: str,
    params: dict[str, Any],
    strategy: dict[str, Any],
    learning_end: Any,
    cross_combos: list[tuple[str, str]] | None = None,
    gate_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    bars, disjoint = fetch_bars_after(market, gateway, symbol, timeframe, learning_end)
    if not bars:
        return {
            "ok": False,
            "message": "学习期之后暂无足够行情，无法生成可信回测",
            "validation": None,
            "out_of_sample": None,
            "cross_validation": [],
            "statistics": None,
            "warnings": ["学习期之后无数据"],
        }
    fallback_full_history = False
    if len(bars) < 300:
        try:
            recent = market.get_bars(symbol, timeframe, limit=1000) or []
            if len(recent) > len(bars):
                bars = recent
                disjoint = False
                fallback_full_history = True
        except Exception:
            pass

    split = max(1, int(len(bars) * 0.8))
    validation_bars = bars[:split]
    oos_bars = bars[split:]
    slippage = 0.0
    try:  # 成本口径中心：按品种默认（GOLD 滑点 2 点/点差 10 点），不再依赖网关实时 point
        slippage = cost_defaults(symbol)["slippage_price"]
    except Exception:
        slippage = 0.0
    validation_gross = _bt_run(validation_bars, code, params, strategy, with_costs=False, symbol=symbol)
    validation = _bt_run(validation_bars, code, params, strategy, with_costs=True, slippage=slippage, symbol=symbol)
    out_of_sample = _bt_run(oos_bars, code, params, strategy, with_costs=True, slippage=slippage, symbol=symbol)

    oos_metrics = (out_of_sample or {}).get("metrics") or {}
    if oos_bars and oos_metrics.get("num_trades", 0) == 0 and len(bars) >= 100:
        split = max(1, int(len(bars) * 0.7))
        validation_bars = bars[:split]
        oos_bars = bars[split:]
        validation_gross = _bt_run(validation_bars, code, params, strategy, with_costs=False, symbol=symbol)
        validation = _bt_run(validation_bars, code, params, strategy, with_costs=True, slippage=slippage, symbol=symbol)
        out_of_sample = _bt_run(oos_bars, code, params, strategy, with_costs=True, slippage=slippage, symbol=symbol)
        expanded_oos = True
    else:
        expanded_oos = False

    min_trades = int((gate_config or {}).get("min_validation_trades", 10))
    if len((validation or {}).get("trades") or []) < min_trades and len(bars) < 1000:
        try:
            recent = market.get_bars(symbol, timeframe, limit=1000) or []
            if len(recent) > len(bars):
                bars = recent
                disjoint = False
                fallback_full_history = True
                split = max(1, int(len(bars) * 0.7))
                validation_bars = bars[:split]
                oos_bars = bars[split:]
                validation_gross = _bt_run(validation_bars, code, params, strategy, with_costs=False, symbol=symbol)
                validation = _bt_run(validation_bars, code, params, strategy, with_costs=True, slippage=slippage, symbol=symbol)
                out_of_sample = _bt_run(oos_bars, code, params, strategy, with_costs=True, slippage=slippage, symbol=symbol)
        except Exception:
            pass

    validation_trades = (validation or {}).get("trades") or []
    statistics = bootstrap_metrics(validation_trades)
    warnings: list[str] = []
    if fallback_full_history:
        warnings.append("学习期后行情不足，已改用全部历史行情验证，结果可能包含学习期数据")
    if expanded_oos:
        warnings.append("样本外无交易，已扩大样本外区间重试")
    if len(validation_trades) < 30:
        warnings.append(f"验证期交易样本不足（{len(validation_trades)} 笔），胜率和盈亏比仅供参考")
    if not disjoint:
        warnings.append("验证期数据可能与学习期重叠，建议补充更多学习之后的行情数据")
    if (out_of_sample or {}).get("metrics") and not (out_of_sample or {}).get("metrics", {}).get("num_trades"):
        warnings.append("样本外区间没有产生交易，无法验证泛化能力")

    benchmark = {
        "buy_hold_return_pct": round((float(bars[-1]["close"]) / float(bars[0]["close"]) - 1.0) * 100.0, 2),
        "random_baseline_return_pct": _random_baseline(validation_bars, len(validation_trades)),
    }
    pass_gate, gate_reasons = _compute_gate(validation, out_of_sample, gate_config)
    if gate_reasons:
        warnings.extend([f"未通过可信门槛：{r}" for r in gate_reasons])

    cross_validation: list[dict[str, Any]] = []
    for csymbol, ctf in (cross_combos or []):
        if csymbol == symbol and ctf == timeframe:
            continue
        cbars, cdisjoint = fetch_bars_after(market, gateway, csymbol, ctf, learning_end, limit=300)
        if not cbars:
            continue
        cslippage = cost_defaults(csymbol)["slippage_price"]
        cresult = _bt_run(cbars, code, params, strategy, with_costs=True, slippage=cslippage, symbol=symbol)
        if cresult is None:
            continue
        cross_validation.append(
            {
                "symbol": csymbol,
                "timeframe": ctf,
                "backtest": cresult,
                "disjoint": cdisjoint,
            }
        )

    return {
        "ok": True,
        "validation": validation,
        "validation_gross": validation_gross,
        "out_of_sample": out_of_sample,
        "cross_validation": cross_validation,
        "statistics": statistics,
        "warnings": warnings,
        "benchmark": benchmark,
        "cost_assumptions": {
            "commission_pct": 0.0002,
            "slippage": round(slippage, 6),
            "spread_points": cost_defaults(symbol)["spread_points"],
            "execution_delay_bars": 0,
        },
        "pass_gate": pass_gate,
        "gate_reasons": gate_reasons,
        "learning_end": learning_end.isoformat() if hasattr(learning_end, "isoformat") else str(learning_end),
        "validation_bars": len(validation_bars),
        "out_of_sample_bars": len(oos_bars),
    }
