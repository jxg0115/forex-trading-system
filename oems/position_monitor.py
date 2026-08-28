"""MT5 持仓平仓监控：平仓后自动生成中文复盘并入库（板块五闭环）。"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from ai_replay.replay import generate_replay_report
from backtest_store.repository import FactorRepository, TradeRecord
from backtest_store.trade_extremes import compute_trade_extremes
from observability.notifier import Notifier

logger = logging.getLogger(__name__)


class MT5PositionMonitor:
    def __init__(
        self,
        gateway: Any,
        repository: FactorRepository,
        notifier: Optional[Notifier] = None,
    ) -> None:
        self.gateway = gateway
        self.repository = repository
        self.notifier = notifier
        self._known: set[int] = set()
        self._initialized = False
        self.closed_records: list[dict[str, Any]] = []

    def poll(self) -> list[dict[str, Any]]:
        """对比当前持仓池，发现消失的持仓单后拉取历史成交并自动复盘。"""

        current = {p["ticket"] for p in self.gateway.get_positions()}
        if not self._initialized:
            self._known = current
            self._initialized = True
            return []

        closed = self._known - current
        results: list[dict[str, Any]] = []
        account_id = ""
        try:
            info = self.gateway.get_account_info() or {}
            account_id = str(info.get("login") or "")
        except Exception:
            account_id = ""
        for ticket in closed:
            data = self.gateway.get_closed_trade(ticket)
            if not data:
                continue
            extremes = compute_trade_extremes(
                self.gateway,
                data["symbol"],
                "M15",
                datetime.fromtimestamp(data["entry_time"], tz=timezone.utc),
                datetime.fromtimestamp(data["exit_time"], tz=timezone.utc),
                "long" if data["side"] == "buy" else "short",
                float(data["volume"]),
                data["entry_price"],
                data["pnl"],
            )
            report = generate_replay_report(
                {
                    "id": str(ticket),
                    "side": data["side"],
                    "entry_time": datetime.fromtimestamp(data["entry_time"], tz=timezone.utc).isoformat(),
                    "exit_time": datetime.fromtimestamp(data["exit_time"], tz=timezone.utc).isoformat(),
                    "entry_price": data["entry_price"],
                    "exit_price": data["exit_price"],
                    "pnl": data["pnl"],
                    "exit_reason": data["reason"],
                },
                factor_name="MT5 实盘交易",
                symbol=data["symbol"],
            )
            record = TradeRecord(
                id=str(uuid.uuid4()),
                mt5_ticket=str(ticket),
                account_id=account_id,
                factor_id="",
                factor_name="MT5 实盘交易",
                symbol=data["symbol"],
                side=data["side"],
                entry_time=datetime.fromtimestamp(data["entry_time"], tz=timezone.utc),
                entry_price=data["entry_price"],
                exit_time=datetime.fromtimestamp(data["exit_time"], tz=timezone.utc),
                exit_price=data["exit_price"],
                lots=float(data["volume"]),
                pnl=float(data["pnl"]),
                exit_reason=data["reason"][:32],
                report_markdown=report.markdown,
                **extremes,
            )
            self.repository.save_trade(record)
            item = {"ticket": ticket, "report_id": record.id, "pnl": data["pnl"], "reason": data["reason"]}
            self.closed_records.insert(0, item)
            self.closed_records = self.closed_records[:50]
            results.append(item)
            logger.info("[MT5 平仓监控] 持仓 #%s 已平仓并生成复盘报告", ticket)

        self._known = current
        if results and self.notifier:
            import asyncio

            try:
                loop = asyncio.get_running_loop()
                loop.create_task(
                    self.notifier.send_alert(
                        "info",
                        "MT5 平仓自动复盘",
                        f"已为 {len(results)} 笔平仓生成中文复盘报告，累计盈亏 {sum(r['pnl'] for r in results):+.2f}",
                    )
                )
            except RuntimeError:
                pass
        return results
