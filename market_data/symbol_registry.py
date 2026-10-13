"""品种 → 交易所（venue）路由。

支持两种并存方式，按配置切换：
- **按 symbol 路由**（默认）：由 instId 形态判定归属，MT5 与 OKX 同时可用；
- **全局切换**：`force_venue` 指定后，所有品种一律走该交易所（适合只用加密或只用外汇的场景）。

之所以能"按 symbol 路由"而不给品种加前缀：`BTC-USDT-SWAP` 这类 instId 与
`XAUUSD` 在形态上天然不重名，因此因子库/成交库/前端都不需要改动键名。
"""

from __future__ import annotations

import re
from typing import Any, Optional

VENUE_MT5 = "mt5"
VENUE_OKX = "okx"

# OKX instId 形态：BASE-QUOTE[-SWAP | -YYMMDD | -C/-P]
_OKX_SWAP = re.compile(r"^[A-Z0-9]+-[A-Z0-9]+-SWAP$", re.I)
_OKX_FUT = re.compile(r"^[A-Z0-9]+-[A-Z0-9]+-\d{6}$", re.I)
_OKX_OPT = re.compile(r"^[A-Z0-9]+-[A-Z0-9]+-\d{6}-[CP]$", re.I)
# 现货：两段，且计价币是常见稳定币/主流币（避免把 "EURUSD" 之类误判进来——它不含连字符，
# 但仍保险起见限定计价币白名单）
_QUOTE_CCY = {"USDT", "USDC", "BTC", "ETH", "USD", "EUR", "DAI", "TUSD", "BUSD"}


def detect_venue(symbol: str) -> str:
    """按形态判定品种归属，不做网络请求、不查缓存，纯字符串判断。"""
    s = str(symbol or "").upper()
    if _OKX_SWAP.match(s) or _OKX_FUT.match(s) or _OKX_OPT.match(s):
        return VENUE_OKX
    if "-" in s:
        parts = s.split("-")
        if len(parts) == 2 and parts[1] in _QUOTE_CCY:
            return VENUE_OKX
    return VENUE_MT5


class SymbolRegistry:
    """把 symbol 映射到对应网关。

    gateways: {"mt5": MT5Gateway|None, "okx": OkxGateway|None}
    force_venue: None=按 symbol 路由；"mt5"/"okx"=全局切换
    """

    def __init__(
        self,
        gateways: dict[str, Any] | None = None,
        default_venue: str = VENUE_MT5,
        force_venue: Optional[str] = None,
    ) -> None:
        self.gateways: dict[str, Any] = gateways or {}
        self.default_venue = default_venue
        self.force_venue = force_venue

    def register(self, venue: str, gateway: Any) -> None:
        self.gateways[venue] = gateway

    @property
    def mode(self) -> str:
        return f"全局切换({self.force_venue})" if self.force_venue else "按 symbol 路由"

    def venue_of(self, symbol: str) -> str:
        if self.force_venue:
            return self.force_venue
        return detect_venue(symbol)

    def gateway_for(self, symbol: str) -> Any:
        return self.gateways.get(self.venue_of(symbol))

    def has_gateway(self, symbol: str) -> bool:
        return self.gateway_for(symbol) is not None

    def is_okx(self, symbol: str) -> bool:
        return self.venue_of(symbol) == VENUE_OKX

    def venues(self) -> list[str]:
        return [v for v, g in self.gateways.items() if g is not None]
