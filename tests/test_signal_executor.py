"""板块四 -> 板块八 自动执行桥测试。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from models.factor import OrderModel
from signal_matcher.executor import ExecutorRuntimeConfig, SignalExecutor


class FakeRepository:
    def __init__(self):
        self.factor = SimpleNamespace(id="f1", name="测试因子", params={})

    def get_factor(self, factor_id):
        return self.factor if factor_id == "f1" else None


class FakeOrders:
    def __init__(self):
        self.positions_store: list[OrderModel] = []
        self.placed: list[OrderModel] = []

    async def positions(self):
        return list(self.positions_store)

    async def place_order(self, **kwargs):
        order = OrderModel(id=f"ord-{len(self.placed) + 1}", **{k: v for k, v in kwargs.items() if k in OrderModel.model_fields})
        self.placed.append(order)
        self.positions_store.append(order)
        return order


class FakeMarket:
    def get_bars(self, symbol, timeframe, limit=300):
        return [{"time": f"2026-01-01T00:{i:02d}:00Z", "open": 1.1, "high": 1.11, "low": 1.09, "close": 1.1, "volume": 1000.0} for i in range(80)]


def _scan_result(signal="long", confidence=0.9, support_levels=None, resistance_levels=None):
    return {
        "symbol": "EURUSD",
        "timeframe": "M15",
        "regime": "强趋势",
        "candidates": [
            {
                "factor_id": "f1",
                "factor_name": "测试因子",
                "signal": signal,
                "confidence": confidence,
                "regime": "强趋势",
                "reasons": [],
                "support_levels": support_levels or [],
                "resistance_levels": resistance_levels or [],
            }
        ],
        "scanned": 1,
    }


def test_executor_places_order_once_with_cooldown():
    executor = SignalExecutor(FakeMarket(), FakeRepository(), FakeOrders(), settings=SimpleNamespace(
        auto_trade_enabled=True,
        auto_trade_min_confidence=0.5,
        auto_trade_max_positions=5,
        auto_trade_risk_percent=1.0,
        auto_trade_symbol="EURUSD",
        auto_trade_timeframe="M15",
        account_equity=10000,
        max_daily_loss_pct=3.0,
        max_drawdown_pct=20.0,
    ))

    async def run():
        first = await executor.execute_scan(_scan_result())
        second = await executor.execute_scan(_scan_result())
        return first, second

    first, second = asyncio.run(run())
    assert len(first) == 1
    assert first[0]["side"] == "buy"
    assert executor.orders.placed[0].lots > 0
    assert second == []


def test_executor_skips_low_confidence():
    executor = SignalExecutor(FakeMarket(), FakeRepository(), FakeOrders(), settings=SimpleNamespace(
        auto_trade_enabled=True,
        auto_trade_min_confidence=0.8,
        auto_trade_max_positions=5,
        auto_trade_risk_percent=1.0,
        auto_trade_symbol="EURUSD",
        auto_trade_timeframe="M15",
        account_equity=10000,
        max_daily_loss_pct=3.0,
        max_drawdown_pct=20.0,
    ))

    async def run():
        return await executor.execute_scan(_scan_result(confidence=0.6))

    assert asyncio.run(run()) == []


def test_executor_records_order_failure():
    class RejectingOrders(FakeOrders):
        async def place_order(self, **kwargs):
            raise RuntimeError("发单失败 [RetCode:10027]: AutoTrading disabled by client")

    executor = SignalExecutor(FakeMarket(), FakeRepository(), RejectingOrders(), settings=SimpleNamespace(
        auto_trade_enabled=True,
        auto_trade_min_confidence=0.5,
        auto_trade_max_positions=5,
        auto_trade_risk_percent=1.0,
        auto_trade_symbol="EURUSD",
        auto_trade_timeframe="M15",
        account_equity=10000,
        max_daily_loss_pct=3.0,
        max_drawdown_pct=20.0,
    ))

    async def run():
        return await executor.execute_scan(_scan_result())

    assert asyncio.run(run()) == []
    assert len(executor.last_failures) == 1
    assert "AutoTrading" in executor.last_failures[0]["error"]


def _executor():
    return SignalExecutor(FakeMarket(), FakeRepository(), FakeOrders(), settings=SimpleNamespace(
        auto_trade_enabled=True,
        auto_trade_min_confidence=0.5,
        auto_trade_max_positions=5,
        auto_trade_risk_percent=1.0,
        auto_trade_symbol="EURUSD",
        auto_trade_timeframe="M15",
        account_equity=10000,
        max_daily_loss_pct=3.0,
        max_drawdown_pct=20.0,
    ))


def test_executor_levels_stop_take_long():
    executor = _executor()
    executor.apply_runtime_config(ExecutorRuntimeConfig(
        symbol="EURUSD",
        timeframe="M15",
        stop_method="levels",
        take_method="levels",
        level_buffer_pct=0.1,
        take_level_buffer_pct=0.1,
        lot_mode="fixed",
        fixed_lots=0.01,
        stop_atr_mult=2.0,
        take_atr_mult=3.0,
    ))

    sizing = executor._position("EURUSD", 1.1, 0.002, "long", support_levels=[1.09], resistance_levels=[1.12])
    assert sizing["stop_price"] == round(1.09 * 0.999, 5)
    assert sizing["take_price"] == round(1.12 * 0.999, 5)


def test_executor_levels_stop_take_short():
    executor = _executor()
    executor.apply_runtime_config(ExecutorRuntimeConfig(
        symbol="EURUSD",
        timeframe="M15",
        stop_method="levels",
        take_method="levels",
        level_buffer_pct=0.1,
        take_level_buffer_pct=0.1,
        lot_mode="fixed",
        fixed_lots=0.01,
        stop_atr_mult=2.0,
        take_atr_mult=3.0,
    ))

    sizing = executor._position("EURUSD", 1.1, 0.002, "short", support_levels=[1.09], resistance_levels=[1.12])
    assert sizing["stop_price"] == round(1.12 * 1.001, 5)
    assert sizing["take_price"] == round(1.09 * 1.001, 5)


def test_executor_levels_fallback_to_atr_without_levels():
    executor = _executor()
    executor.apply_runtime_config(ExecutorRuntimeConfig(
        symbol="EURUSD",
        timeframe="M15",
        stop_method="levels",
        take_method="levels",
        level_buffer_pct=0.1,
        take_level_buffer_pct=0.1,
        lot_mode="fixed",
        fixed_lots=0.01,
        stop_atr_mult=2.0,
        take_atr_mult=3.0,
    ))

    sizing = executor._position("EURUSD", 1.1, 0.002, "long", support_levels=[], resistance_levels=[])
    assert sizing["stop_price"] == round(1.1 - 0.002 * 2.0, 5)
    assert sizing["take_price"] == round(1.1 + 0.002 * 3.0, 5)


def test_executor_executes_with_levels_from_candidate():
    executor = _executor()
    executor.apply_runtime_config(ExecutorRuntimeConfig(
        symbol="EURUSD",
        timeframe="M15",
        stop_method="levels",
        take_method="levels",
        level_buffer_pct=0.1,
        take_level_buffer_pct=0.1,
        lot_mode="fixed",
        fixed_lots=0.01,
        stop_atr_mult=2.0,
        take_atr_mult=3.0,
    ))

    async def run():
        return await executor.execute_scan(_scan_result(support_levels=[1.09], resistance_levels=[1.12]))

    executed = asyncio.run(run())
    assert len(executed) == 1
    order = executor.orders.placed[0]
    assert order.stop_price == round(1.09 * 0.999, 5)
    assert order.take_price == round(1.12 * 0.999, 5)


def test_executor_atr_stop_with_levels_take():
    executor = _executor()
    executor.apply_runtime_config(ExecutorRuntimeConfig(
        symbol="EURUSD",
        timeframe="M15",
        stop_method="atr",
        take_method="levels",
        stop_atr_mult=2.0,
        take_level_buffer_pct=0.1,
        lot_mode="fixed",
        fixed_lots=0.01,
    ))

    sizing = executor._position("EURUSD", 1.1, 0.002, "long", support_levels=[1.09], resistance_levels=[1.12])
    assert sizing["stop_price"] == round(1.1 - 0.002 * 2.0, 5)
    assert sizing["take_price"] == round(1.12 * 0.999, 5)


def test_executor_uses_pattern_suggested_sltp():
    executor = _executor()
    executor.apply_runtime_config(ExecutorRuntimeConfig(
        symbol="EURUSD",
        timeframe="M15",
        initial_sltp_source="pattern",
        lot_mode="fixed",
        fixed_lots=0.01,
    ))
    scan = _scan_result()
    scan["candidates"][0]["suggested_stop_pct"] = 0.02
    scan["candidates"][0]["suggested_take_pct"] = 0.03

    async def run():
        return await executor.execute_scan(scan)

    executed = asyncio.run(run())
    assert len(executed) == 1
    order = executor.orders.placed[0]
    assert order.stop_price == round(1.1 * (1 - 0.02), 5)
    assert order.take_price == round(1.1 * (1 + 0.03), 5)
