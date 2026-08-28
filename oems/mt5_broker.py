"""MetaTrader 5 经纪商适配器：桥接 Broker 协议与 MT5Gateway。"""

from __future__ import annotations

import asyncio
from typing import Any

from config import Settings, settings as app_settings
from models.factor import OrderModel
from oems.base import Broker
from oems.mt5_gateway import MT5Gateway


class MT5Broker(Broker):
    name = "MetaTrader 5"

    def __init__(self, settings: Settings | None = None) -> None:
        cfg = settings or app_settings
        self.gateway = MT5Gateway(
            login=cfg.mt5_login,
            password=cfg.mt5_password,
            server=cfg.mt5_server,
            path=cfg.mt5_path,
            magic=cfg.mt5_magic,
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
        action_map = {
            ("buy", "market"): "BUY",
            ("sell", "market"): "SELL",
            ("buy", "limit"): "BUY_LIMIT",
            ("sell", "limit"): "SELL_LIMIT",
            ("buy", "stop"): "BUY_STOP",
            ("sell", "stop"): "SELL_STOP",
            ("buy", "stop_limit"): "BUY_STOP_LIMIT",
            ("sell", "stop_limit"): "SELL_STOP_LIMIT",
        }
        action = action_map.get((side, order_type))
        if action is None:
            raise RuntimeError(f"不支持的订单类型：{side}/{order_type}")
        result = await asyncio.to_thread(
            self.gateway.place_order,
            symbol=symbol,
            action_type=action,
            volume=lots,
            price=price,
            sl=stop_price,
            tp=take_price,
            comment=f"AI:{factor_name or reason or 'manual'}",
            stoplimit_price=stoplimit_price,
        )
        if not result.get("success"):
            raise RuntimeError(result.get("message", "MT5 下单失败"))
        raw_response = {
            key: result.get(key)
            for key in ("ticket", "volume", "price", "retcode", "comment", "filling_mode", "request", "message")
        }
        return OrderModel(
            id=str(result.get("ticket", "")),
            symbol=symbol,
            side=side,  # type: ignore[arg-type]
            lots=float(result.get("volume") or lots),
            entry_price=float(result.get("price") or 0.0),
            stop_price=stop_price,
            take_price=take_price,
            status="filled",
            factor_id=factor_id,
            factor_name=factor_name,
            reason=reason,
            raw_response=raw_response,
        )

    async def close_position(self, order_id: str, fill_price: float, volume: float | None = None) -> OrderModel:
        result = await asyncio.to_thread(self.gateway.close_position, int(order_id), volume)
        if not result.get("success"):
            raise RuntimeError(result.get("message", "MT5 平仓失败"))
        return OrderModel(
            id=order_id,
            symbol="",
            side="buy",
            lots=0.0,
            entry_price=fill_price,
            status="closed",
        )

    async def close_all(self, fill_price: float) -> list[OrderModel]:
        results = await asyncio.to_thread(self.gateway.close_all)
        closed: list[OrderModel] = []
        for res in results:
            if res.get("success"):
                closed.append(
                    OrderModel(
                        id=str(res.get("ticket", "")),
                        symbol="",
                        side="buy",
                        lots=0.0,
                        status="closed",
                    )
                )
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
                stop_price=float(p["sl"]) if p.get("sl") else None,
                take_price=float(p["tp"]) if p.get("tp") else None,
                status="filled",
                reason=str(p.get("comment", "")),
            )
            for p in positions
        ]

    async def modify_position(
        self,
        ticket: str,
        sl: float | None = None,
        tp: float | None = None,
    ) -> OrderModel:
        result = await asyncio.to_thread(self.gateway.modify_position, int(ticket), sl, tp)
        if not result.get("success"):
            raise RuntimeError(result.get("message", "MT5 修改止损失败"))
        return OrderModel(
            id=str(ticket),
            symbol="",
            side="buy",
            lots=0.0,
            stop_price=float(result["sl"]) if result.get("sl") else None,
            take_price=float(result["tp"]) if result.get("tp") else None,
            status="filled",
            raw_response=result,
        )

    def orders(self) -> list[dict[str, Any]]:
        return self.gateway.get_orders()
