"""模拟经纪商：即时成交，用于模拟盘与演示。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from models.factor import OrderModel
from oems.base import Broker


class PaperBroker(Broker):
    name = "模拟经纪商"

    def __init__(self) -> None:
        self._orders: dict[str, OrderModel] = {}

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
        order = OrderModel(
            id=str(uuid.uuid4()),
            symbol=symbol,
            side=side,  # type: ignore[arg-type]
            lots=lots,
            stop_price=stop_price,
            take_price=take_price,
            status="filled",
            factor_id=factor_id,
            factor_name=factor_name,
            reason=reason,
            entry_price=price,
            raw_response={"broker": "paper", "order_type": order_type, "price": price},
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        self._orders[order.id] = order
        return order

    async def close_position(self, order_id: str, fill_price: float, volume: float | None = None) -> OrderModel:
        order = self._orders.get(order_id)
        if not order:
            raise ValueError(f"订单不存在：{order_id}")
        if volume and float(volume) > 0 and order.lots - float(volume) > 1e-9:
            order.lots = round(order.lots - float(volume), 2)
            order.updated_at = datetime.now(timezone.utc)
        else:
            order.status = "closed"
            order.entry_price = fill_price
            order.updated_at = datetime.now(timezone.utc)
        return order

    async def close_all(self, fill_price: float) -> list[OrderModel]:
        closed: list[OrderModel] = []
        for order in self._orders.values():
            if order.status == "filled":
                order.status = "closed"
                order.entry_price = fill_price
                order.updated_at = datetime.now(timezone.utc)
                closed.append(order)
        return closed

    async def positions(self) -> list[OrderModel]:
        return [o for o in self._orders.values() if o.status == "filled"]

    async def modify_position(
        self,
        ticket: str,
        sl: float | None = None,
        tp: float | None = None,
    ) -> OrderModel:
        order = self._orders.get(ticket)
        if not order:
            raise ValueError(f"订单不存在：{ticket}")
        order.stop_price = sl
        order.take_price = tp
        order.updated_at = datetime.now(timezone.utc)
        return order
