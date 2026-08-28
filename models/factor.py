"""因子、标注区域、回测与交易相关数据模型。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


class PatternPoint(BaseModel):
    """K 线图上的一个标注点。"""

    time: datetime
    price: float
    label: str = ""
    reason: str = ""


class PriceLevel(BaseModel):
    """水平压力位或支撑位。"""

    price: float
    label: str = ""
    touched: int = 0


class ChartRegion(BaseModel):
    """前端拖拽框选的 K 线区域及人工标注。"""

    symbol: str = "EURUSD"
    timeframe: str = "M15"
    time_start: datetime
    time_end: datetime
    price_top: float | None = None
    price_bottom: float | None = None
    entry_points: list[PatternPoint] = Field(default_factory=list)
    exit_points: list[PatternPoint] = Field(default_factory=list)
    support_levels: list[PriceLevel] = Field(default_factory=list)
    resistance_levels: list[PriceLevel] = Field(default_factory=list)


class RegionStats(BaseModel):
    """框选区域的统计特征，用于 Prompt 构造与模板因子选择。"""

    bar_count: int = 0
    return_pct: float = 0.0
    high: float = 0.0
    low: float = 0.0
    avg_range_pct: float = 0.0
    trend: str = "震荡"
    volatility: str = "中等波动"


class SandboxCheckResult(BaseModel):
    ok: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    node_count: int = 0
    max_depth: int = 0


class FactorExecutionResult(BaseModel):
    ok: bool
    entry_count: int = 0
    exit_count: int = 0
    entry_values: list[float | None] = Field(default_factory=list)
    exit_values: list[float | None] = Field(default_factory=list)
    entry_sample: list[float] = Field(default_factory=list)
    exit_sample: list[float] = Field(default_factory=list)
    message: str = ""
    execution_ms: float = 0.0


class FactorDraft(BaseModel):
    """AI 逆向生成后的因子草稿。"""

    name: str
    description: str
    code: str
    model: str = "simulated"
    source: str = "simulated"
    params: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    sandbox: SandboxCheckResult | None = None
    execution: FactorExecutionResult | None = None


class FactorCreate(BaseModel):
    name: str
    description: str
    code: str
    symbol: str = "EURUSD"
    timeframe: str = "M15"
    source: str = "ai"
    model: str = "simulated"
    params: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    market_adapt: dict[str, Any] = Field(default_factory=dict)
    chart_stats: dict[str, Any] = Field(default_factory=dict)
    prompt_snapshot: dict[str, Any] = Field(default_factory=dict)
    generated_region: dict[str, Any] = Field(default_factory=dict)
    backtest_stats: dict[str, Any] | None = None
    sl_tp_strategy: dict[str, Any] = Field(default_factory=dict)


class FactorOut(FactorCreate):
    id: str
    status: Literal["active", "disabled"] = "active"
    version: int = 1
    created_at: datetime
    updated_at: datetime


class TradeDetail(BaseModel):
    entry_time: datetime
    entry_price: float
    side: Literal["long", "short"]
    exit_time: datetime | None = None
    exit_price: float | None = None
    pnl: float | None = None
    pnl_pct: float | None = None
    bars_held: int | None = None
    exit_reason: str = ""


class BacktestMetrics(BaseModel):
    total_return_pct: float
    annual_return_pct: float = 0.0
    sharpe: float = 0.0
    max_drawdown_pct: float = 0.0
    win_rate_pct: float = 0.0
    profit_factor: float = 0.0
    num_trades: int = 0
    avg_trade_pct: float = 0.0
    max_consecutive_losses: int = 0


class BacktestResult(BaseModel):
    ok: bool
    message: str = ""
    metrics: BacktestMetrics | None = None
    trades: list[TradeDetail] = Field(default_factory=list)
    equity_curve: list[dict[str, Any]] = Field(default_factory=list)
    code_hash: str = ""


class RiskConfig(BaseModel):
    account_equity: float = 10_000.0
    risk_per_trade_pct: float = 1.0
    max_daily_loss_pct: float = 3.0
    max_positions: int = 5
    leverage: int = 30
    contract_size: int = 100_000
    pip_size: float = 0.0001
    pip_value_per_lot: float = 10.0


class PositionSizingResult(BaseModel):
    lots: float
    risk_amount: float
    stop_price: float
    take_price: float
    stop_distance_pips: float
    risk_pct: float
    margin_used: float
    message: str


class OrderModel(BaseModel):
    id: str = ""
    symbol: str
    side: Literal["buy", "sell"]
    lots: float
    entry_price: float | None = None
    stop_price: float | None = None
    take_price: float | None = None
    status: Literal["pending", "submitted", "filled", "rejected", "closed"] = "pending"
    factor_id: str | None = None
    factor_name: str = ""
    reason: str = ""
    raw_response: dict[str, Any] | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MatcherCandidate(BaseModel):
    factor_id: str
    factor_name: str
    signal: Literal["long", "short", "none"]
    confidence: float
    regime: str
    reasons: list[str] = Field(default_factory=list)
    support_levels: list[float] = Field(default_factory=list)
    resistance_levels: list[float] = Field(default_factory=list)
    suggested_stop_pct: float | None = None
    suggested_take_pct: float | None = None


class ReplayReport(BaseModel):
    trade_id: str
    factor_name: str
    symbol: str
    markdown: str
    score: float
    generated_by: str = "本地规则引擎"
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AlertModel(BaseModel):
    id: str
    level: Literal["info", "warning", "error", "success"]
    title: str
    message: str
    created_at: datetime
    delivered_to: list[str] = Field(default_factory=list)
