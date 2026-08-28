"""经纪商接口抽象。"""

from __future__ import annotations

from abc import ABC, abstractmethod

from models.factor import OrderModel


class Broker(ABC):
    name: str = "抽象经纪商"

    @abstractmethod
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
    ) -> OrderModel: ...

    @abstractmethod
    async def close_position(self, order_id: str, fill_price: float) -> OrderModel: ...

    @abstractmethod
    async def close_all(self, fill_price: float) -> list[OrderModel]: ...

    @abstractmethod
    async def positions(self) -> list[OrderModel]: ...

    @abstractmethod
    async def modify_position(
        self,
        ticket: str,
        sl: float | None = None,
        tp: float | None = None,
    ) -> OrderModel: ...
