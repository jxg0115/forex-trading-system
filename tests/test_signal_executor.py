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


def test_executor_mirror_swaps_stop_take_and_side():
    """镜像=对手盘：方向取反，且止损价落在原止盈价、止盈价落在原止损价。"""
    base = dict(
        symbol="EURUSD",
        timeframe="M15",
        stop_method="atr",
        take_method="atr",
        lot_mode="fixed",
        fixed_lots=0.01,
        stop_atr_mult=2.0,
        take_atr_mult=3.0,
    )
    executor = _executor()
    executor.apply_runtime_config(ExecutorRuntimeConfig(**base))
    plain = executor._position("EURUSD", 1.1, 0.002, "long")

    mirrored = _executor()
    mirrored.apply_runtime_config(ExecutorRuntimeConfig(**base, mirror=True))
    flip = mirrored._position("EURUSD", 1.1, 0.002, "long", mirror=True)

    assert plain["stop_price"] == round(1.1 - 0.002 * 2.0, 5)
    assert plain["take_price"] == round(1.1 + 0.002 * 3.0, 5)
    # 镜像单：止损跑到原止盈价、止盈跑到原止损价，构成严格对手盘
    assert flip["stop_price"] == plain["take_price"]
    assert flip["take_price"] == plain["stop_price"]
    # 空头结构：止损在上方、止盈在下方
    assert flip["stop_price"] > 1.1 > flip["take_price"]


def test_executor_mirror_flips_execution_side_and_reason():
    executor = _executor()
    executor.apply_runtime_config(ExecutorRuntimeConfig(
        symbol="EURUSD",
        timeframe="M15",
        stop_method="atr",
        take_method="atr",
        lot_mode="fixed",
        fixed_lots=0.01,
        stop_atr_mult=2.0,
        take_atr_mult=3.0,
        mirror=True,
    ))

    async def run():
        return await executor.execute_scan(_scan_result(signal="long"))

    executed = asyncio.run(run())
    assert len(executed) == 1
    assert executed[0]["side"] == "sell"
    assert executed[0]["mirror"] is True
    assert executed[0]["raw_signal"] == "long"
    assert executor.orders.placed[0].reason == "AI 自动执行[镜像]"


def test_executor_mirror_scope_filters_direction():
    """scope=long 只镜像多单信号，scope=short 只镜像空单信号。"""
    base = dict(
        symbol="EURUSD",
        timeframe="M15",
        stop_method="atr",
        take_method="atr",
        lot_mode="fixed",
        fixed_lots=0.01,
        stop_atr_mult=2.0,
        take_atr_mult=3.0,
    )

    def _run(scope, signal):
        ex = _executor()
        ex.apply_runtime_config(ExecutorRuntimeConfig(**base, mirror=True, mirror_scope=scope))
        return asyncio.run(ex.execute_scan(_scan_result(signal=signal)))[0]

    # 只镜像多单：long 信号翻成 sell，short 信号保持 sell（不翻）
    assert _run("long", "long")["side"] == "sell"
    assert _run("long", "long")["mirror"] is True
    short_untouched = _run("long", "short")
    assert short_untouched["side"] == "sell"
    assert short_untouched["mirror"] is False

    # 只镜像空单：short 信号翻成 buy，long 信号保持 buy（不翻）
    assert _run("short", "short")["side"] == "buy"
    assert _run("short", "short")["mirror"] is True
    long_untouched = _run("short", "long")
    assert long_untouched["side"] == "buy"
    assert long_untouched["mirror"] is False


def test_executor_direction_filters_blocked_side():
    """direction=short 屏蔽多单、direction=long 屏蔽空单：被屏蔽的信号完全不执行。"""
    base = dict(
        symbol="EURUSD",
        timeframe="M15",
        stop_method="atr",
        take_method="atr",
        lot_mode="fixed",
        fixed_lots=0.01,
        stop_atr_mult=2.0,
        take_atr_mult=3.0,
    )

    def _run(direction, signal):
        ex = _executor()
        ex.apply_runtime_config(ExecutorRuntimeConfig(**base, direction=direction))
        return asyncio.run(ex.execute_scan(_scan_result(signal=signal)))

    # 只做空：多单信号被过滤（0 笔），空单信号照常执行
    assert _run("short", "long") == []
    short_ok = _run("short", "short")
    assert len(short_ok) == 1
    assert short_ok[0]["side"] == "sell"

    # 只做多：空单信号被过滤（0 笔）
    assert _run("long", "short") == []
    long_ok = _run("long", "long")
    assert len(long_ok) == 1
    assert long_ok[0]["side"] == "buy"

    # 双向：两个方向都放行
    assert len(_run("both", "long")) == 1
    assert len(_run("both", "short")) == 1


def test_executor_mirror_off_keeps_original_direction():
    executor = _executor()
    executor.apply_runtime_config(ExecutorRuntimeConfig(
        symbol="EURUSD",
        timeframe="M15",
        stop_method="atr",
        take_method="atr",
        lot_mode="fixed",
        fixed_lots=0.01,
        stop_atr_mult=2.0,
        take_atr_mult=3.0,
        mirror=False,
    ))

    async def run():
        return await executor.execute_scan(_scan_result(signal="long"))

    executed = asyncio.run(run())
    assert len(executed) == 1
    assert executed[0]["side"] == "buy"
    assert executed[0]["mirror"] is False
    assert executor.orders.placed[0].reason == "AI 自动执行"
