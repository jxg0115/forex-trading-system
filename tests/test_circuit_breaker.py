"""权益风控熔断测试。"""

from datetime import datetime, timezone

from risk_sizing.circuit_breaker import EquityRiskGuard


def test_daily_loss_trips():
    guard = EquityRiskGuard(max_daily_loss_pct=3.0, max_drawdown_pct=20.0)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert guard.update(10000, now)["tripped"] is False
    result = guard.update(9600, now)
    assert result["tripped"] is True
    assert any("单日亏损" in r for r in result["reasons"])


def test_drawdown_trips():
    guard = EquityRiskGuard(max_daily_loss_pct=10.0, max_drawdown_pct=20.0)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    guard.update(10000, now)
    guard.update(12000, now)
    result = guard.update(9000, now)
    assert result["tripped"] is True
    assert any("最大回撤" in r for r in result["reasons"])


def test_guard_resets_next_day():
    guard = EquityRiskGuard(max_daily_loss_pct=3.0, max_drawdown_pct=20.0)
    day1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    day2 = datetime(2026, 1, 2, tzinfo=timezone.utc)
    assert guard.update(10000, day1)["tripped"] is False
    assert guard.update(8000, day1)["tripped"] is True
    assert guard.update(8000, day2)["tripped"] is False


def test_set_limits_updates_thresholds():
    guard = EquityRiskGuard(max_daily_loss_pct=3.0, max_drawdown_pct=20.0)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    guard.update(10000, now)
    guard.set_limits(10.0, 20.0)
    assert guard.update(9500, now)["tripped"] is False
    assert guard.update(8800, now)["tripped"] is True
