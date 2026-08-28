"""订单生命周期管理：下单、平仓、撤单、状态同步与持久化订单日志。"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from backtest_store.repository import OrderLogRecord, get_repository
from models.factor import OrderModel
from observability.notifier import Notifier
from oems.base import Broker
from oems.paper_broker import PaperBroker


class OrderManager:
    def __init__(self, broker: Broker | None = None, notifier: Notifier | None = None) -> None:
        self.broker = broker or PaperBroker()
        self.notifier = notifier or Notifier()
        self._orders: list[OrderModel] = []

    @staticmethod
    def _save_log(record: OrderLogRecord) -> None:
        repo = get_repository()
        try:
            repo.save_order_log(record)
        finally:
            repo.close()

    @staticmethod
    def _update_log(order_id: str, **fields: Any) -> None:
        repo = get_repository()
        try:
            repo.update_order_log(order_id, **fields)
        finally:
            repo.close()

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
        idempotency_key: str | None = None,
    ) -> OrderModel:
        """提交订单并记录完整生命周期：submitted -> filled/pending/rejected。"""

        if idempotency_key:
            repo = get_repository()
            try:
                existing = repo.get_order_log_by_idempotency(idempotency_key)
                if existing and existing.status != "rejected":
                    return OrderModel(
                        id=existing.mt5_ticket or existing.id,
                        symbol=existing.symbol,
                        side=existing.side,  # type: ignore[arg-type]
                        lots=existing.volume,
                        entry_price=existing.price,
                        status=existing.status,  # type: ignore[arg-type]
                    )
            finally:
                repo.close()

        log_id = str(uuid.uuid4())
        request_payload = {
            "symbol": symbol,
            "side": side,
            "order_type": order_type,
            "volume": float(lots),
            "price": price,
            "stoplimit_price": stoplimit_price,
            "sl": stop_price,
            "tp": take_price,
            "factor_id": factor_id,
            "factor_name": factor_name,
            "reason": reason,
        }
        self._save_log(
            OrderLogRecord(
                id=log_id,
                symbol=symbol,
                side=side,
                order_type=order_type,
                volume=float(lots),
                price=price,
                stoplimit_price=stoplimit_price,
                sl=stop_price,
                tp=take_price,
                status="submitted",
                action="open",
                factor_id=factor_id or "",
                factor_name=factor_name,
                reason=reason,
                message="订单已提交至经纪商",
                idempotency_key=idempotency_key or "",
                request_payload=request_payload,
            )
        )
        try:
            order = await self.broker.place_order(
                symbol=symbol,
                side=side,
                lots=lots,
                stop_price=stop_price,
                take_price=take_price,
                factor_id=factor_id,
                factor_name=factor_name,
                reason=reason,
                order_type=order_type,
                price=price,
                stoplimit_price=stoplimit_price,
            )
        except Exception as exc:
            self._update_log(log_id, status="rejected", message=str(exc), response_data={"error": str(exc)})
            await self.notifier.send_alert("error", "订单提交失败", f"{symbol} {side} {lots} 手：{exc}")
            raise

        status = "filled" if order_type == "market" else "pending"
        self._orders.append(order)
        response_data = {
            "ticket": order.id,
            "volume": order.lots,
            "price": order.entry_price,
            "status": order.status,
            **(order.raw_response or {}),
        }
        self._update_log(
            log_id,
            status=status,
            mt5_ticket=order.id,
            price=order.entry_price or price,
            message=f"MT5 下单成功（{order_type}）",
            response_data=response_data,
        )
        await self.notifier.send_alert(
            "info",
            "新订单已成交" if status == "filled" else "新挂单已提交",
            f"{symbol} {side} {lots} 手（{order_type}），订单号 #{order.id}，触发因子：{factor_name or '手动'}",
        )
        return order

    async def close_position(self, order_id: str, fill_price: float, volume: float | None = None) -> OrderModel:
        """平仓并记录开仓单的 close 动作。"""

        self._update_log_by_ticket(order_id, status="closing", action="close", message="正在向 MT5 提交平仓")
        try:
            order = await self.broker.close_position(order_id, fill_price, volume)
        except Exception as exc:
            self._update_log_by_ticket(order_id, status="rejected", action="close", message=f"平仓失败：{exc}")
            raise
        if volume and order.lots and float(volume) < float(order.lots):
            self._update_log_by_ticket(order_id, status="filled", action="close", message=f"已部分平仓 {volume} 手 @ {fill_price}")
            await self.notifier.send_alert("info", "订单部分平仓", f"{order.symbol} #{order.id} 部分平仓 {volume} 手 @ {fill_price}")
        else:
            self._update_log_by_ticket(order_id, status="closed", action="close", message=f"已平仓 @ {fill_price}")
            await self.notifier.send_alert("info", "订单已平仓", f"{order.symbol} #{order.id} 以 {fill_price} 平仓")
        return order

    async def cancel_order(self, ticket: int) -> dict[str, Any]:
        """撤销挂单并记录 cancel 动作。"""

        self._update_log_by_ticket(str(ticket), status="canceling", action="cancel", message="正在撤销挂单")
        if not hasattr(self.broker, "gateway"):
            raise RuntimeError("模拟经纪商不支持挂单撤销")
        result = await self.broker.gateway.cancel_order(ticket)
        if result.get("success"):
            self._update_log_by_ticket(str(ticket), status="canceled", action="cancel", message=f"挂单 #{ticket} 已撤销")
        else:
            self._update_log_by_ticket(str(ticket), status="rejected", action="cancel", message=result.get("message", "撤单失败"))
        return result

    async def close_all(self, fill_price: float) -> list[OrderModel]:
        closed = await self.broker.close_all(fill_price)
        if closed:
            for order in closed:
                self._update_log_by_ticket(order.id, status="closed", action="close", message=f"一键平仓 @ {fill_price}")
            await self.notifier.send_alert("warning", "一键平仓", f"已平仓 {len(closed)} 个持仓")
        return closed

    async def positions(self) -> list[OrderModel]:
        return await self.broker.positions()

    async def modify_position(
        self,
        ticket: str,
        sl: float | None = None,
        tp: float | None = None,
    ) -> dict[str, Any]:
        """修改持仓止损/止盈（移动止损），并记录 modify 动作日志。"""

        repo = get_repository()
        try:
            logs = repo.list_order_logs(limit=500)
            log = next((l for l in logs if l.mt5_ticket == str(ticket)), None)
        finally:
            repo.close()
        symbol = log.symbol if log else ""
        side = log.side if log else ""
        volume = log.volume if log else 0.0
        request_payload = {"position": str(ticket), "sl": sl, "tp": tp}
        try:
            order = await self.broker.modify_position(str(ticket), sl=sl, tp=tp)
        except Exception as exc:
            self._save_log(
                OrderLogRecord(
                    id=str(uuid.uuid4()),
                    mt5_ticket=str(ticket),
                    symbol=symbol,
                    side=side,
                    order_type="market",
                    volume=volume,
                    sl=sl,
                    tp=tp,
                    status="rejected",
                    action="modify",
                    factor_name="移动止损",
                    reason="移动止损更新失败",
                    message=str(exc),
                    request_payload=request_payload,
                    response_data={"error": str(exc)},
                )
            )
            await self.notifier.send_alert("error", "移动止损更新失败", f"持仓 #{ticket}：{exc}")
            raise
        message_parts = []
        if sl is not None:
            message_parts.append(f"止损 {sl}")
        if tp is not None:
            message_parts.append(f"止盈 {tp}")
        message = "、".join(message_parts) + " 已更新" if message_parts else "止损止盈已同步"
        self._save_log(
            OrderLogRecord(
                id=str(uuid.uuid4()),
                mt5_ticket=str(ticket),
                symbol=symbol,
                side=side,
                order_type="market",
                volume=volume,
                sl=sl,
                tp=tp,
                status="modified",
                action="modify",
                factor_name="移动止损",
                reason="移动止损/止盈更新",
                message=message,
                request_payload=request_payload,
                response_data=order.raw_response or {},
            )
        )
        await self.notifier.send_alert("info", "移动止损/止盈已更新", f"持仓 #{ticket}：{message}")
        return {"success": True, "ticket": str(ticket), "sl": sl, "tp": tp}

    def order_list(self, limit: int = 100) -> list[OrderModel]:
        return list(reversed(self._orders))[:limit]

    def update_log_by_ticket(self, ticket: str, **fields: Any) -> None:
        self._update_log_by_ticket(ticket, **fields)

    def sync_from_mt5(self) -> None:
        """把 MT5 实时挂单/持仓状态同步回订单日志。"""

        if not hasattr(self.broker, "gateway"):
            return
        gateway = self.broker.gateway
        active_orders = {str(o["ticket"]) for o in gateway.get_orders()}
        active_positions = {str(p["ticket"]) for p in gateway.get_positions()}
        repo = get_repository()
        try:
            for log in repo.list_order_logs(limit=500):
                ticket = log.mt5_ticket
                if not ticket:
                    continue
                if log.status == "pending":
                    if ticket in active_positions:
                        repo.update_order_log(log.id, status="filled", action="open", message="挂单已成交并开仓")
                    elif ticket not in active_orders:
                        closed = gateway.get_closed_trade(int(ticket))
                        if closed:
                            repo.update_order_log(log.id, status="filled", action="open", message="挂单已成交")
                        else:
                            repo.update_order_log(log.id, status="canceled", action="cancel", message="挂单已失效或撤销")
                elif log.status == "filled" and log.action in ("open", "close"):
                    if ticket not in active_positions and gateway.get_closed_trade(int(ticket)):
                        repo.update_order_log(log.id, status="closed", action="close", message="MT5 已自动平仓")
        finally:
            repo.close()

    def _update_log_by_ticket(self, ticket: str, **fields: Any) -> None:
        repo = get_repository()
        try:
            for log in repo.list_order_logs(limit=500):
                if log.mt5_ticket == ticket:
                    repo.update_order_log(log.id, **fields)
                    return
        finally:
            repo.close()
