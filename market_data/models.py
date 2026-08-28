"""K 线与行情快照模型（行情数据全部来自 MT5）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class Bar(BaseModel):
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class SymbolInfo(BaseModel):
    symbol: str
    name: str
    base_price: float
    digits: int
    pip_size: float


class MarketSnapshot(BaseModel):
    symbol: str
    timeframe: str
    current_price: float
    change_pct_24h: float = 0.0
    atr: float = 0.0
    atr_pct: float = 0.0
    latest_bar: Bar | None = None
    bars: int = 0
    updated_at: datetime
    extra: dict[str, Any] = Field(default_factory=dict)
