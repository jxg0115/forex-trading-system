"""移动止损服务测试。"""

from __future__ import annotations

import asyncio

from oems.trailing_stop import TrailingStopService
from signal_matcher.executor import ExecutorRuntimeConfig


class FakeGateway:
    def __init__(self, positions):
        self.positions = positions
        self.modified = []

    def get_positions(self):
        return self.positions

    def get_rates(self, symbol, timeframe, limit=300):
        return [
            {
                "time": f"2026-01-01T{i // 60:02d}:{i % 60:02d}:00Z",
                "open": 1.1,
                "high": 1.11,
                "low": 1.09,
                "close": 1.1,
                "volume": 1000.0,
            }
            for i in range(80)
        ]


class FakeOrders:
    def __init__(self):
        self.modified = []
        self.closed = []

    async def modify_position(self, ticket, sl=None, tp=None):
        self.modified.append({"ticket": str(ticket), "sl": sl, "tp": tp})
        return {"success": True, "ticket": str(ticket), "sl": sl, "tp": tp}

    async def close_position(self, ticket, fill_price, volume=None):
        self.closed.append({"ticket": str(ticket), "fill_price": fill_price, "volume": volume})
        return {"success": True, "ticket": str(ticket), "volume": volume}


class FakeRepository:
    def __init__(self, factor=None, logs=None):
        self.factor = factor
        self.logs = logs or []

    def list_order_logs(self, limit=500):
        return self.logs

    def get_factor(self, factor_id):
        return self.factor if factor_id == "f1" else None


class FakeLog:
    def __init__(self, mt5_ticket, factor_id):
        self.mt5_ticket = mt5_ticket
        self.factor_id = factor_id


def _config(**overrides):
    defaults = dict(
        trailing_enabled=True,
        trailing_unit="pct",
        trailing_activation_pct=0.5,
        trailing_retrace_pct=0.3,
        trailing_take_retrace_pct=0.3,
        trailing_take_buffer_pct=0.2,
    )
    defaults.update(overrides)
    return ExecutorRuntimeConfig(**defaults)


def test_trailing_updates_long_stop_after_activation():
    gateway = FakeGateway([
        {
            "ticket": 1,
            "symbol": "EURUSD",
            "type": "BUY",
            "volume": 0.1,
            "price_open": 100.0,
            "price_current": 101.0,
            "sl": 99.0,
            "tp": 0.0,
            "profit": 10.0,
            "time": 0,
            "comment": "",
        }
    ])
    orders = FakeOrders()
    service = TrailingStopService(gateway, orders)

    updates = asyncio.run(service.update(_config()))
    assert len(updates) == 1
    assert updates[0]["ticket"] == 1
    assert orders.modified[0]["ticket"] == "1"
    assert orders.modified[0]["sl"] > 100.0


def test_trailing_skips_before_activation():
    gateway = FakeGateway([
        {
            "ticket": 1,
            "symbol": "EURUSD",
            "type": "BUY",
            "volume": 0.1,
            "price_open": 100.0,
            "price_current": 100.2,
            "sl": 99.0,
            "tp": 0.0,
            "profit": 1.0,
            "time": 0,
            "comment": "",
        }
    ])
    orders = FakeOrders()
    service = TrailingStopService(gateway, orders)

    updates = asyncio.run(service.update(_config()))
    assert updates == []
    assert orders.modified == []


def test_trailing_updates_short_stop_after_activation():
    gateway = FakeGateway([
        {
            "ticket": 2,
            "symbol": "EURUSD",
            "type": "SELL",
            "volume": 0.1,
            "price_open": 100.0,
            "price_current": 99.0,
            "sl": 101.0,
            "tp": 0.0,
            "profit": 10.0,
            "time": 0,
            "comment": "",
        }
    ])
    orders = FakeOrders()
    service = TrailingStopService(gateway, orders)

    updates = asyncio.run(service.update(_config()))
    assert len(updates) == 1
    assert orders.modified[0]["ticket"] == "2"
    assert orders.modified[0]["sl"] < 100.0


def test_trailing_stop_never_worse_than_entry():
    gateway = FakeGateway([
        {
            "ticket": 3,
            "symbol": "EURUSD",
            "type": "BUY",
            "volume": 0.1,
            "price_open": 100.0,
            "price_current": 100.8,
            "sl": 99.0,
            "tp": 0.0,
            "profit": 8.0,
            "time": 0,
            "comment": "",
        }
    ])
    orders = FakeOrders()
    service = TrailingStopService(gateway, orders)
    # 极端波动下 ATR 很大，candidate 会低于入场价，止损至少回到保本价。
    config = _config(trailing_retrace_pct=100.0)

    updates = asyncio.run(service.update(config))
    assert len(updates) == 1
    assert orders.modified[0]["sl"] == 100.0


def test_trailing_disabled_does_nothing():
    gateway = FakeGateway([
        {
            "ticket": 1,
            "symbol": "EURUSD",
            "type": "BUY",
            "volume": 0.1,
            "price_open": 100.0,
            "price_current": 101.0,
            "sl": 99.0,
            "tp": 0.0,
            "profit": 10.0,
            "time": 0,
            "comment": "",
        }
    ])
    orders = FakeOrders()
    service = TrailingStopService(gateway, orders)

    updates = asyncio.run(service.update(_config(trailing_enabled=False)))
    assert updates == []
    assert orders.modified == []


def test_trailing_long_anchors_to_support_level():
    gateway = FakeGateway([
        {
            "ticket": 4,
            "symbol": "EURUSD",
            "type": "BUY",
            "volume": 0.1,
            "price_open": 100.0,
            "price_current": 101.0,
            "sl": 99.0,
            "tp": 0.0,
            "profit": 10.0,
            "time": 0,
            "comment": "",
        }
    ])
    orders = FakeOrders()
    factor = type("Factor", (), {"prompt_snapshot": {"support_levels": [{"price": 100.4}], "resistance_levels": []}})()
    repository = FakeRepository(factor=factor, logs=[FakeLog("4", "f1")])
    service = TrailingStopService(gateway, orders, repository=repository)

    updates = asyncio.run(service.update(_config(trailing_retrace_pct=100.0)))
    assert len(updates) == 1
    assert orders.modified[0]["sl"] == 100.4


def test_trailing_short_anchors_to_resistance_level():
    gateway = FakeGateway([
        {
            "ticket": 5,
            "symbol": "EURUSD",
            "type": "SELL",
            "volume": 0.1,
            "price_open": 100.0,
            "price_current": 99.0,
            "sl": 101.0,
            "tp": 0.0,
            "profit": 10.0,
            "time": 0,
            "comment": "",
        }
    ])
    orders = FakeOrders()
    factor = type("Factor", (), {"prompt_snapshot": {"support_levels": [], "resistance_levels": [{"price": 99.6}]}})()
    repository = FakeRepository(factor=factor, logs=[FakeLog("5", "f1")])
    service = TrailingStopService(gateway, orders, repository=repository)

    updates = asyncio.run(service.update(_config(trailing_retrace_pct=100.0)))
    assert len(updates) == 1
    assert orders.modified[0]["sl"] == 99.6


def test_trailing_phase2_locks_stop_at_first_take():
    gateway = FakeGateway([
        {
            "ticket": 6,
            "symbol": "EURUSD",
            "type": "BUY",
            "volume": 0.1,
            "price_open": 100.0,
            "price_current": 101.02,
            "sl": 99.0,
            "tp": 101.0,
            "profit": 10.0,
            "time": 0,
            "comment": "",
        }
    ])
    orders = FakeOrders()
    service = TrailingStopService(gateway, orders)
    # 首次触达止盈价后，止损锁到该止盈价，止盈随极值顺势上移。
    updates = asyncio.run(service.update(_config()))
    assert len(updates) == 1
    assert orders.modified[0]["sl"] == 101.0
    assert orders.modified[0]["tp"] == round(101.02 * 1.003, 5)


def test_trailing_phase3_moves_take_and_stop_together():
    gateway = FakeGateway([
        {
            "ticket": 7,
            "symbol": "EURUSD",
            "type": "BUY",
            "volume": 0.1,
            "price_open": 100.0,
            "price_current": 101.97,
            "sl": 100.0,
            "tp": 101.0,
            "profit": 15.0,
            "time": 0,
            "comment": "",
        }
    ])
    orders = FakeOrders()
    service = TrailingStopService(gateway, orders)
    # 预置更高极值，模拟价格先冲高 102 后回落但仍高于原止盈。
    service._state[7] = {"extreme": 102.0, "last_stop": 100.0, "last_take": 101.0}
    updates = asyncio.run(service.update(_config()))
    assert len(updates) == 1
    assert orders.modified[0]["sl"] == round(102.0 * (1.0 - 0.003), 5)
    assert orders.modified[0]["tp"] == round(102.0 * 1.003, 5)


def test_trailing_phase3_short_moves_take_and_stop_together():
    gateway = FakeGateway([
        {
            "ticket": 8,
            "symbol": "EURUSD",
            "type": "SELL",
            "volume": 0.1,
            "price_open": 100.0,
            "price_current": 98.03,
            "sl": 100.0,
            "tp": 99.0,
            "profit": 15.0,
            "time": 0,
            "comment": "",
        }
    ])
    orders = FakeOrders()
    service = TrailingStopService(gateway, orders)
    # 预置更低极值，模拟价格先跌到 98 后反弹但仍低于原止盈。
    service._state[8] = {"extreme": 98.0, "last_stop": 100.0, "last_take": 99.0}
    updates = asyncio.run(service.update(_config()))
    assert len(updates) == 1
    assert orders.modified[0]["sl"] == round(98.0 * 1.003, 5)
    assert orders.modified[0]["tp"] == round(98.0 * 0.997, 5)


def test_trailing_keeps_first_take_lock_after_pullback():
    gateway = FakeGateway([
        {
            "ticket": 9,
            "symbol": "EURUSD",
            "type": "BUY",
            "volume": 0.1,
            "price_open": 100.0,
            "price_current": 100.55,
            "sl": 99.5,
            "tp": 101.0,
            "profit": 5.0,
            "time": 0,
            "comment": "",
        }
    ])
    orders = FakeOrders()
    service = TrailingStopService(gateway, orders)
    # 已触达过首档止盈 100.5，止盈已上移到 101.0，价格回落到 100.55 时止损仍锁在 100.5。
    service._state[9] = {"extreme": 101.0, "last_stop": 99.5, "last_take": 101.0, "take_hit": 100.5}
    updates = asyncio.run(service.update(_config()))
    assert len(updates) == 1
    assert orders.modified[0]["sl"] == 100.5
    assert orders.modified[0]["tp"] is None


def test_trailing_atr_unit_uses_atr_distance():
    gateway = FakeGateway([
        {
            "ticket": 10,
            "symbol": "EURUSD",
            "type": "BUY",
            "volume": 0.1,
            "price_open": 100.0,
            "price_current": 101.0,
            "sl": 99.0,
            "tp": 0.0,
            "profit": 10.0,
            "time": 0,
            "comment": "",
        }
    ])
    orders = FakeOrders()
    service = TrailingStopService(gateway, orders)
    updates = asyncio.run(
        service.update(_config(trailing_unit="atr", trailing_activation_atr=0.5, trailing_stop_atr=1.0))
    )
    assert len(updates) == 1
    assert orders.modified[0]["sl"] == 100.98


def test_high_watermark_closes_on_retrace():
    gateway = FakeGateway([
        {
            "ticket": 20,
            "symbol": "GOLD",
            "type": "BUY",
            "volume": 1.0,
            "price_open": 100.0,
            "price_current": 100.5,
            "sl": 99.0,
            "tp": 0.0,
            "profit": 300.0,
            "time": 0,
            "comment": "",
        }
    ])
    orders = FakeOrders()
    service = TrailingStopService(gateway, orders)
    service._state[20] = {
        "extreme": 101.0,
        "last_stop": 99.0,
        "last_take": 0.0,
        "take_hit": 0.0,
        "peak_profit": 500.0,
        "pc_closed_volume": 0.0,
    }
    updates = asyncio.run(
        service.update(
            _config(
                sl_tp_strategies=["high_watermark"],
                hw_activation_profit=200.0,
                hw_max_retrace_pct=20.0,
            )
        )
    )
    assert len(orders.closed) == 1
    assert orders.closed[0]["volume"] is None
    assert any(u.get("action") == "最高浮盈回落平仓" for u in updates)


def test_partial_close_tier1():
    gateway = FakeGateway([
        {
            "ticket": 21,
            "symbol": "GOLD",
            "type": "BUY",
            "volume": 1.0,
            "price_open": 100.0,
            "price_current": 100.5,
            "sl": 99.0,
            "tp": 0.0,
            "profit": 400.0,
            "time": 0,
            "comment": "",
        }
    ])
    orders = FakeOrders()
    service = TrailingStopService(gateway, orders)
    service._state[21] = {
        "extreme": 100.5,
        "last_stop": 99.0,
        "last_take": 0.0,
        "take_hit": 0.0,
        "peak_profit": 400.0,
        "pc_closed_volume": 0.0,
    }
    updates = asyncio.run(
        service.update(
            _config(
                trailing_unit="atr",
                trailing_activation_atr=1000.0,
                sl_tp_strategies=["partial_close"],
                pc_tier1_profit=400.0,
                pc_tier1_close_pct=50.0,
                pc_breakeven_buffer=0.5,
            )
        )
    )
    assert any(c["volume"] == 0.5 for c in orders.closed)
    assert any(m["sl"] == 100.5 for m in orders.modified)
    assert any(u.get("action") == "分批落袋第 1 档" for u in updates)


def test_high_watermark_scales_by_lot_size():
    gateway = FakeGateway([
        {
            "ticket": 22,
            "symbol": "GOLD",
            "type": "BUY",
            "volume": 0.1,
            "price_open": 100.0,
            "price_current": 100.2,
            "sl": 99.0,
            "tp": 0.0,
            "profit": 18.0,
            "time": 0,
            "comment": "",
        }
    ])
    orders = FakeOrders()
    service = TrailingStopService(gateway, orders)
    service._state[22] = {
        "extreme": 100.3,
        "last_stop": 99.0,
        "last_take": 0.0,
        "take_hit": 0.0,
        "peak_profit": 30.0,
        "pc_closed_volume": 0.0,
    }
    updates = asyncio.run(
        service.update(
            _config(
                sl_tp_strategies=["high_watermark"],
                hw_activation_profit=200.0,
                hw_max_retrace_pct=20.0,
            )
        )
    )
    assert len(orders.closed) == 1
    assert any(u.get("action") == "最高浮盈回落平仓" for u in updates)
