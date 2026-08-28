"""挂单超时自动撤单监控器。"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

from observability.notifier import Notifier

logger = logging.getLogger(__name__)


class PendingExpirationManager:
    """注册挂单超时追踪，按秒轮询并自动撤销过期挂单。"""

    def __init__(
        self,
        gateway: Any,
        notifier: Optional[Notifier] = None,
        bar_duration_seconds: int = 60,
    ) -> None:
        self.gateway = gateway
        self.notifier = notifier
        self.bar_duration_seconds = bar_duration_seconds
        self.pending_tracker: dict[int, dict[str, Any]] = {}

    def register_pending_order(self, ticket: int, symbol: str, max_bars: int = 3) -> None:
        expire_at = time.time() + max_bars * self.bar_duration_seconds
        self.pending_tracker[ticket] = {
            "ticket": ticket,
            "symbol": symbol,
            "expire_at": expire_at,
            "max_bars": max_bars,
        }
        logger.info("[挂单管理] 挂单 #%s (%s) 已注册超时追踪（%s 根 K 线）", ticket, symbol, max_bars)

    def check_and_clean_expired_orders(self) -> list[int]:
        now = time.time()
        cancelled: list[int] = []
        active_orders = self.gateway.get_orders() if self.gateway else []
        active_tickets = {o["ticket"] for o in active_orders} if active_orders else set()

        for ticket, info in list(self.pending_tracker.items()):
            if ticket not in active_tickets:
                del self.pending_tracker[ticket]
                continue
            if now >= info["expire_at"]:
                res = self.gateway.cancel_order(ticket)
                if res.get("success"):
                    cancelled.append(ticket)
                    del self.pending_tracker[ticket]
                    message = f"挂单 #{ticket} ({info['symbol']}) 超时 {info['max_bars']} 根 K 线未成交，系统已自动取消订单。"
                    logger.warning("[挂单管理] %s", message)
                    if self.notifier:
                        try:
                            loop = asyncio.get_running_loop()
                            loop.create_task(self.notifier.send_alert("warning", "超时挂单自动撤销", message))
                        except RuntimeError:
                            logger.warning("[挂单管理] 无事件循环，跳过告警推送")
        return cancelled
