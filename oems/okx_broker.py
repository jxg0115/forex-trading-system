"""OKX 经纪商适配器：桥接 Broker 协议与 OkxGateway。

与 MT5Broker 的三点差异（使用方必须知道）：

1. **lots 参数是交易所原生单位**：衍生品为**张**，现货为**标的币数量**。
   若要按风险预算下单，请先用 `risk_sizing.crypto_sizing.size_from_symbol_info()` 换算。
2. **双向持仓**：buy → posSide=long，sell → posSide=short（网关按 pos_side_mode 决定）。
   因此同一品种可以同时持有多空两个仓，持仓标识是 `"<instId>|<posSide>"`。
3. **改止损是异步的**：`modify_position` 内部会先查 algoId 再 amend 条件单，
   没有已挂条件单时新建 oco。调用方不要假设「改完立即返回新价」。
"""

from __future__ import annotations

import asyncio
from typing import Any

from models.factor import OrderModel
from oems.base import Broker
from oems.okx_gateway import OkxGateway


class OkxBroker(Broker):
    name = "OKX"

    def __init__(self, gateway: OkxGateway | None = None, settings: Any = None) -> None:
        if gateway is not None:
            self.gateway = gateway
        else:
            from config import settings as app_settings

            cfg = settings or app_settings
            self.gateway = OkxGateway(
                api_key=cfg.okx_api_key,
                secret=cfg.okx_secret_key,
                passphrase=cfg.okx_passphrase,
                flag=cfg.okx_flag,
                td_mode=cfg.okx_td_mode,
                pos_side_mode=cfg.okx_pos_side_mode,
                proxy_url=(cfg.okx_proxy_url or None) if cfg.okx_proxy_url != "direct" else "",
            )

    async def place_order(
        self,
        symbol: str,
        side: str,
        lots: float,
        stop_price: float | None = None,
        take_price: float | None = None,
        factor_id: str | None = None,
        factor_name: str = "",
        reason: str = "",
        order_type: str = "market",
        price: float | None = None,
        stoplimit_price: float | None = None,
    ) -> OrderModel:
        side = str(side).lower()
        if side not in ("buy", "sell"):
            raise RuntimeError(f"不支持的方向：{side}")
        if order_type == "market":
            price = None
        elif price is None:
            raise RuntimeError("限价单必须指定 price")

        result = await asyncio.to_thread(
            self.gateway.place_order,
            symbol=symbol,
            action_type="BUY" if side == "buy" else "SELL",
            volume=float(lots),
            price=price,
            sl=stop_price,
            tp=take_price,
            comment=str(factor_name or reason or "manual")[:40],
        )
        if not result.get("success"):
            raise RuntimeError(result.get("message", "OKX 下单失败"))

        return OrderModel(
            id=str(result.get("ticket", "")),
            symbol=symbol,
            side=side,  # type: ignore[arg-type]
            lots=float(lots),
            entry_price=float(result.get("price") or 0.0),
            stop_price=stop_price,
            take_price=take_price,
            status="filled" if order_type == "market" else "pending",
            factor_id=factor_id,
            factor_name=factor_name,
            reason=reason,
            raw_response={
                k: result.get(k)
                for k in ("ticket", "volume", "price", "retcode", "comment", "cl_ord_id", "request")
            },
        )

    async def close_position(self, order_id: str, fill_price: float,
                             volume: float | None = None) -> OrderModel:
        result = await asyncio.to_thread(self.gateway.close_position, order_id, volume)
        if not result.get("success"):
            raise RuntimeError(result.get("message", "OKX 平仓失败"))
        return OrderModel(
            id=order_id,
            symbol=order_id.split("|")[0],
            side="buy",
            lots=0.0,
            entry_price=fill_price,
            status="closed",
            raw_response=result,
        )

    async def close_all(self, fill_price: float) -> list[OrderModel]:
        results = await asyncio.to_thread(self.gateway.close_all)
        closed: list[OrderModel] = []
        for res in results or []:
            if res.get("success"):
                ticket = str(res.get("ticket", ""))
                closed.append(OrderModel(
                    id=ticket,
                    symbol=ticket.split("|")[0],
                    side="buy",
                    lots=0.0,
                    status="closed",
                    raw_response=res,
                ))
        return closed

    async def positions(self) -> list[OrderModel]:
        positions = await asyncio.to_thread(self.gateway.get_positions)
        return [
            OrderModel(
                id=str(p["ticket"]),
                symbol=p["symbol"],
                side="buy" if p["type"] == "BUY" else "sell",
                lots=float(p["volume"]),
                entry_price=float(p["price_open"]),
                stop_price=p.get("sl"),
                take_price=p.get("tp"),
                status="filled",
                reason=str(p.get("pos_side") or ""),
                raw_response=p,
            )
            for p in positions
        ]

    async def modify_position(
        self,
        ticket: str,
        sl: float | None = None,
        tp: float | None = None,
    ) -> OrderModel:
        result = await asyncio.to_thread(self.gateway.modify_position, ticket, sl, tp)
        if not result.get("success"):
            raise RuntimeError(result.get("message", "OKX 修改止损失败"))
        return OrderModel(
            id=str(ticket),
            symbol=ticket.split("|")[0],
            side="buy",
            lots=0.0,
            stop_price=float(result["sl"]) if result.get("sl") else None,
            take_price=float(result["tp"]) if result.get("tp") else None,
            status="filled",
            raw_response=result,
        )

    def orders(self) -> list[dict[str, Any]]:
        return self.gateway.get_orders()

    def funding_rate(self, symbol: str) -> dict[str, Any] | None:
        return self.gateway.funding_rate(symbol)

    def instrument(self, symbol: str) -> dict[str, Any] | None:
        info = self.gateway.get_symbol_info(symbol)
        return info
