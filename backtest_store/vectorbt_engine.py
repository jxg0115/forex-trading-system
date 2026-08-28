"""可选 VectorBT 回测引擎：安装 vectorbt 后启用，未安装时返回 None 由自研引擎接管。"""

from __future__ import annotations

from typing import Any

import pandas as pd

from models.factor import BacktestResult


def try_vectorbt(
    df: pd.DataFrame,
    entry: pd.Series,
    exit_signal: pd.Series | None,
    params: dict[str, Any] | None = None,
) -> BacktestResult | None:
    try:
        import vectorbt as vbt  # type: ignore
    except ImportError:
        return None

    p = params or {}
    size = float(p.get("risk_per_trade_pct", 1.0)) / max(float(p.get("stop_atr_mult", 2.0)) * 0.01, 1e-9)
    exits = exit_signal.to_numpy() if exit_signal is not None else None
    portfolio = vbt.Portfolio.from_signals(
        df["close"],
        entries=(entry > 0).to_numpy(),
        exits=exits,
        size=size,
        fees=float(p.get("commission_pct", 0.0001)),
        init_cash=float(p.get("initial_equity", 10000)),
    )
    stats = portfolio.stats()
    return BacktestResult(
        ok=True,
        message="VectorBT 回测完成",
        metrics=None,
        trades=[],
        equity_curve=[],
        code_hash="",
    )

