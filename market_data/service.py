"""行情数据服务：完全基于 MT5 网关，不再使用模拟行情。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

import pandas as pd

from indicators.technical import atr_series
from market_data.models import MarketSnapshot
from oems.mt5_gateway import MT5Gateway


class MarketDataService:
    """MT5 数据适配器：K 线、品种、快照均来自 MT5 终端。"""

    def __init__(self, gateway: Optional[MT5Gateway] = None) -> None:
        self.gateway = gateway

    @property
    def is_live(self) -> bool:
        return bool(self.gateway and self.gateway.is_connected)

    def get_bars(self, symbol: str, timeframe: str, limit: int = 500) -> list[dict[str, Any]]:
        if not self.gateway:
            return []
        return self.gateway.get_rates(symbol, timeframe, limit)

    def symbols(self) -> list[dict[str, Any]]:
        if not self.gateway:
            return []
        return [{"symbol": name, "name": name} for name in self.gateway.get_symbols()]

    def dataframe(self, symbol: str, timeframe: str, limit: int | None = None) -> pd.DataFrame:
        bars = self.get_bars(symbol, timeframe, limit or 500)
        if not bars:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        df = pd.DataFrame(bars)
        if "time" in df.columns:
            df["time"] = pd.to_datetime(df["time"], utc=True)
            df = df.set_index("time")
        return df

    def snapshot(self, symbol: str, timeframe: str) -> MarketSnapshot:
        if not self.gateway:
            raise ValueError("MT5 网关未启用，请检查 BROKER 配置")
        tick = self.gateway.get_tick(symbol)
        bars = self.gateway.get_rates(symbol, timeframe, 60)
        if tick is None and not bars:
            raise ValueError(f"无法获取 {symbol} 行情数据，请确认 MT5 已连接")

        current = float(tick.get("ask") or tick.get("bid") or (bars[-1]["close"] if bars else 0.0))
        change = 0.0
        if len(bars) >= 25:
            prev = float(bars[-25]["close"])
            change = (current - prev) / prev * 100.0 if prev else 0.0
        atr = 0.0
        atr_pct = 0.0
        if bars:
            df = self.dataframe(symbol, timeframe, len(bars))
            atr_values = atr_series(df, 14).dropna()
            atr = float(atr_values.iloc[-1]) if len(atr_values) else 0.0
            atr_pct = atr / current * 100.0 if current else 0.0
        latest = {"time": bars[-1]["time"], "open": bars[-1]["open"], "high": bars[-1]["high"], "low": bars[-1]["low"], "close": bars[-1]["close"], "volume": bars[-1].get("volume", bars[-1].get("tick_volume", 0))} if bars else None
        return MarketSnapshot(
            symbol=symbol,
            timeframe=timeframe,
            current_price=current,
            change_pct_24h=round(change, 4),
            atr=round(atr, 6),
            atr_pct=round(atr_pct, 4),
            latest_bar=latest,
            bars=len(bars),
            updated_at=datetime.now(timezone.utc),
            extra={"spread_points": tick.get("spread_points")} if tick else {},
        )

