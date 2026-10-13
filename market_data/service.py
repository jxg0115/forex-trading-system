"""行情数据服务：支持多交易所（MT5 / OKX）按品种路由。

改造说明（保持向后兼容）：
- 旧调用 `MarketDataService(mt5_gateway)` 仍然有效，所有品种走该网关；
- 新调用传入 `registry`（SymbolRegistry）后，`get_bars/symbols/dataframe/snapshot`
  会按 symbol 自动选择 MT5 还是 OKX 网关。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

import pandas as pd

from indicators.technical import atr_series
from market_data.models import MarketSnapshot
from market_data.symbol_registry import SymbolRegistry, VENUE_MT5, VENUE_OKX


class MarketDataService:
    """多交易所行情适配器：K 线、品种、快照按 symbol 路由到对应网关。"""

    def __init__(
        self,
        gateway: Optional[Any] = None,
        registry: Optional[SymbolRegistry] = None,
    ) -> None:
        self.gateway = gateway            # 兼容旧用法（单一网关）
        self.registry = registry
        if registry is None and gateway is not None:
            self.registry = SymbolRegistry(gateways={VENUE_MT5: gateway})

    @property
    def is_live(self) -> bool:
        if self.registry:
            return any(
                getattr(g, "is_connected", False)
                for g in self.registry.gateways.values()
                if g is not None
            )
        return bool(self.gateway and self.gateway.is_connected)

    def _gw(self, symbol: str) -> Any:
        if self.registry:
            gw = self.registry.gateway_for(symbol)
            if gw is not None:
                return gw
        return self.gateway

    def venue_of(self, symbol: str) -> str:
        return self.registry.venue_of(symbol) if self.registry else VENUE_MT5

    def get_bars(self, symbol: str, timeframe: str, limit: int = 500) -> list[dict[str, Any]]:
        gw = self._gw(symbol)
        if not gw:
            return []
        return gw.get_rates(symbol, timeframe, limit)

    def symbols(self, venue: str | None = None) -> list[dict[str, Any]]:
        """列出品种。venue 为空时汇总所有已注册交易所。"""
        if not self.registry:
            if not self.gateway:
                return []
            return [{"symbol": n, "name": n, "venue": VENUE_MT5}
                    for n in self.gateway.get_symbols()]

        targets = [venue] if venue else list(self.registry.gateways.keys())
        out: list[dict[str, Any]] = []
        for v in targets:
            gw = self.registry.gateways.get(v)
            if gw is None:
                continue
            if v == VENUE_OKX:
                for inst_type in self._okx_inst_types():
                    for name in gw.get_symbols(inst_type) or []:
                        out.append({"symbol": name, "name": name,
                                    "venue": v, "inst_type": inst_type})
            else:
                for name in gw.get_symbols() or []:
                    out.append({"symbol": name, "name": name, "venue": v})
        return out

    @staticmethod
    def _okx_inst_types() -> list[str]:
        try:
            from config import settings

            return [t.strip() for t in settings.okx_inst_types.split(",") if t.strip()]
        except Exception:  # noqa: BLE001
            return ["SWAP", "SPOT", "FUTURES"]

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
        gw = self._gw(symbol)
        if not gw:
            raise ValueError("行情网关未启用，请检查 BROKER / OKX 配置")
        venue = self.venue_of(symbol)
        tick = gw.get_tick(symbol)
        bars = gw.get_rates(symbol, timeframe, 60)
        if tick is None and not bars:
            raise ValueError(f"无法获取 {symbol} 行情数据，请确认 {venue} 已连接")

        current = float((tick or {}).get("ask") or (tick or {}).get("bid")
                        or (bars[-1]["close"] if bars else 0.0))
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
        latest = {"time": bars[-1]["time"], "open": bars[-1]["open"], "high": bars[-1]["high"],
                  "low": bars[-1]["low"], "close": bars[-1]["close"],
                  "volume": bars[-1].get("volume", bars[-1].get("tick_volume", 0))} if bars else None

        extra: dict[str, Any] = {"venue": venue}
        if tick:
            extra["spread_points"] = tick.get("spread_points")
        if venue == VENUE_OKX:
            fr = None
            try:
                fr = gw.funding_rate(symbol)
            except Exception:  # noqa: BLE001
                fr = None
            if fr:
                extra["funding_rate"] = fr.get("funding_rate")
                extra["next_funding_time"] = fr.get("next_funding_time")

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
            extra=extra,
        )
