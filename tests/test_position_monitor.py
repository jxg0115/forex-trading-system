"""板块五：MT5 平仓监控与自动复盘测试。"""

from __future__ import annotations

from oems.position_monitor import MT5PositionMonitor


class FakeGateway:
    def __init__(self, positions=None, closed=None):
        self.positions_store = positions or []
        self.closed = closed or {}

    def get_positions(self):
        return list(self.positions_store)

    def get_closed_trade(self, ticket):
        return self.closed.get(ticket)


class FakeRepository:
    def __init__(self):
        self.saved: list = []

    def save_trade(self, record):
        self.saved.append(record)


def test_position_monitor_records_closed_trade():
    gateway = FakeGateway(
        positions=[{"ticket": 1001}],
        closed={
            1001: {
                "ticket": 1001,
                "symbol": "EURUSD",
                "side": "long",
                "volume": 0.1,
                "entry_time": 1786147000,
                "entry_price": 1.15,
                "exit_time": 1786147600,
                "exit_price": 1.16,
                "pnl": 10.0,
                "reason": "AI_Close",
            }
        },
    )
    repo = FakeRepository()
    monitor = MT5PositionMonitor(gateway, repo)

    assert monitor.poll() == []
    gateway.positions_store = []
    results = monitor.poll()

    assert len(results) == 1
    assert len(repo.saved) == 1
    assert repo.saved[0].pnl == 10.0
    assert repo.saved[0].report_markdown.startswith("# AI 交易复盘报告")

