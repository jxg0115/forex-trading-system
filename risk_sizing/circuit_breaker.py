"""权益风控熔断：单日亏损与最大回撤超限自动暂停匹配。"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any


class EquityRiskGuard:
    def __init__(self, max_daily_loss_pct: float = 3.0, max_drawdown_pct: float = 20.0) -> None:
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_drawdown_pct = max_drawdown_pct
        self._day: date | None = None
        self._day_start_equity: float | None = None
        self._peak: float | None = None

    def set_limits(self, max_daily_loss_pct: float = 3.0, max_drawdown_pct: float = 20.0) -> None:
        self.max_daily_loss_pct = max(float(max_daily_loss_pct), 0.0)
        self.max_drawdown_pct = max(float(max_drawdown_pct), 0.0)

    def update(self, equity: float, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        day = now.date()
        if self._day != day:
            self._day = day
            self._day_start_equity = equity
            self._peak = equity
        self._peak = max(self._peak or equity, equity)

        daily_loss_pct = (equity / self._day_start_equity - 1.0) * 100.0 if self._day_start_equity else 0.0
        drawdown_pct = (self._peak - equity) / self._peak * 100.0 if self._peak else 0.0
        reasons: list[str] = []
        if daily_loss_pct <= -self.max_daily_loss_pct:
            reasons.append(f"单日亏损 {daily_loss_pct:.2f}% 超过限制 {self.max_daily_loss_pct:.2f}%")
        if drawdown_pct >= self.max_drawdown_pct:
            reasons.append(f"最大回撤 {drawdown_pct:.2f}% 超过限制 {self.max_drawdown_pct:.2f}%")
        return {
            "tripped": bool(reasons),
            "reasons": reasons,
            "daily_loss_pct": round(daily_loss_pct, 4),
            "drawdown_pct": round(drawdown_pct, 4),
            "day_start_equity": self._day_start_equity,
            "peak_equity": self._peak,
        }
