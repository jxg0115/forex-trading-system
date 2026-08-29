"""API 请求模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from models.factor import ChartRegion


class FactorGenerationRequest(BaseModel):
    region: ChartRegion
    chart_image_data_url: str | None = None


class LookaheadCheckRequest(BaseModel):
    code: str
    symbol: str = "EURUSD"
    timeframe: str = "M15"
    bars: int = 200
    params: dict[str, Any] = Field(default_factory=dict)


class BacktestRunRequest(BaseModel):
    code: str | None = None
    factor_id: str | None = None
    symbol: str = "EURUSD"
    timeframe: str = "M15"
    bars: int = 500
    start_time: datetime | None = None
    end_time: datetime | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    initial_equity: float = 10_000.0
    risk_per_trade_pct: float = 1.0
    leverage: int = 30
    delay_ms: int = 0
    slippage_points: float = 0.0


class BacktestOptimizeRequest(BaseModel):
    code: str | None = None
    factor_id: str | None = None
    symbol: str = "EURUSD"
    timeframe: str = "M15"
    bars: int = 500
    start_time: datetime | None = None
    end_time: datetime | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    initial_equity: float = 10_000.0
    leverage: int = 30
    delay_ms: int = 0
    slippage_points: float = 0.0
    objective: Literal["win_rate", "profit", "composite"] = "composite"
    target_win_rate_pct: float = 0.0
    target_total_return_pct: float = 0.0
    target_profit_factor: float = 0.0
    target_max_drawdown_pct: float = 100.0
    max_iterations: int = 30
    early_stop_rounds: int = 5
    walk_forward: bool = True
    train_ratio: float = 0.7
    walk_forward_folds: int = 3
    min_trades: int = 0
    auto_expand: bool = True
    complexity_penalty: float = 0.01
    max_deviation_pct: float = 30.0
    fidelity_weight: float = 0.4
    beam_width: int = 10
    search_rounds: int = 5
    param_ranges: dict[str, dict[str, float]] | None = None


class PaperStartRequest(BaseModel):
    factor_id: str
    symbol: str = "EURUSD"
    timeframe: str = "M15"
    equity: float = 10_000.0


class PaperStopRequest(BaseModel):
    job_id: str | None = None


class LiveStartRequest(BaseModel):
    symbol: str = "EURUSD"
    timeframe: str = "M15"


class ReplayExportRequest(BaseModel):
    trade_id: str


class PortfolioBacktestRequest(BaseModel):
    """组合历史回测：启用因子 + 多周期方向过滤 + 实盘 sltp 策略整体验证（只读离线）。"""
    symbol: str = "GOLD"
    timeframe: str = "M30"
    date_from: str | None = None
    date_to: str | None = None
    market_filter: dict[str, Any] = Field(default_factory=dict)
    max_factors: int = 200
