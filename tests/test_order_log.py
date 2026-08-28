"""订单日志持久化与订单生命周期测试。"""

import asyncio
import uuid
from types import SimpleNamespace

from backtest_store.repository import OrderLogRecord, get_repository
from models.factor import OrderModel
import oems.order_manager as order_manager_module
from observability.notifier import Notifier
from oems.order_manager import OrderManager


class FakeBroker:
    async def place_order(self, **kwargs):
        return OrderModel(
            id="ticket-1",
            symbol=kwargs["symbol"],
            side=kwargs["side"],
            lots=kwargs["lots"],
            entry_price=kwargs.get("price") or 1.15,
            status="filled",
        )

    async def positions(self):
        return []

    async def close_position(self, order_id, fill_price):
        return OrderModel(id=order_id, symbol="EURUSD", side="buy", lots=0.1, status="closed")

    async def close_all(self, fill_price):
        return []


class FakeRepo:
    def __init__(self):
        self.saved: list[OrderLogRecord] = []
        self.updated: list[dict] = []

    def save_order_log(self, record):
        self.saved.append(record)
        return record

    def update_order_log(self, order_id, **fields):
        self.updated.append({"id": order_id, **fields})
        return True

    def list_order_logs(self, limit=200, status=None, symbol=None):
        return []

    def get_order_log_by_idempotency(self, key):
        if key == "dup-key":
            return SimpleNamespace(
                id="dup-log",
                mt5_ticket="ticket-dup",
                symbol="EURUSD",
                side="buy",
                volume=0.1,
                price=1.15,
                status="filled",
            )
        return None

    def save_alert(self, alert):
        return alert

    def close(self):
        pass


def test_repository_order_log_crud():
    repo = get_repository()
    record = OrderLogRecord(
        id=f"order-log-{uuid.uuid4().hex[:10]}",
        symbol="EURUSD",
        side="buy",
        order_type="limit",
        volume=0.1,
        price=1.15,
        status="submitted",
        action="open",
        message="订单已提交至经纪商",
    )
    repo.save_order_log(record)
    assert repo.get_order_log(record.id) is not None
    assert repo.update_order_log(record.id, status="filled", message="MT5 下单成功")
    logs = repo.list_order_logs(limit=50)
    assert any(log.id == record.id and log.status == "filled" for log in logs)
    repo.close()


def test_order_manager_place_order_logs_submitted_then_filled(monkeypatch):
    fake_repo = FakeRepo()
    monkeypatch.setattr(order_manager_module, "get_repository", lambda: fake_repo)
    notifier = Notifier(repository=fake_repo)
    manager = OrderManager(FakeBroker(), notifier)

    async def run():
        return await manager.place_order(
            symbol="EURUSD",
            side="buy",
            lots=0.1,
            price=1.15,
            order_type="limit",
            reason="测试挂单",
        )

    order = asyncio.run(run())
    assert order.id == "ticket-1"
    assert len(fake_repo.saved) == 1
    assert fake_repo.saved[0].status == "submitted"
    assert fake_repo.saved[0].order_type == "limit"
    assert fake_repo.saved[0].request_payload["symbol"] == "EURUSD"
    assert fake_repo.updated[-1]["status"] == "pending"
    assert fake_repo.updated[-1]["mt5_ticket"] == "ticket-1"
    assert fake_repo.updated[-1]["response_data"]["ticket"] == "ticket-1"


def test_order_manager_idempotency_returns_existing(monkeypatch):
    fake_repo = FakeRepo()
    monkeypatch.setattr(order_manager_module, "get_repository", lambda: fake_repo)
    manager = OrderManager(FakeBroker(), Notifier(repository=fake_repo))

    async def run():
        return await manager.place_order(
            symbol="EURUSD",
            side="buy",
            lots=0.1,
            idempotency_key="dup-key",
        )

    order = asyncio.run(run())
    assert order.id == "ticket-dup"
    assert fake_repo.saved == []
