# -*- coding: utf-8 -*-
"""市场交易时段判定（开市/闭市 + 下一开市/收市时间）。

规则：外汇/贵金属 24/5 —— 周一 00:00 UTC 至 周五 24:00 UTC 开市，
周六/周日闭市。时段表后续可按品种细化（如每日休市窗口），此处先通用。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

MONDAY = 0
TUESDAY = 1
WEDNESDAY = 2
THURSDAY = 3
FRIDAY = 4
# 交易日集合（周一~周五开市；周六/周日闭市）——WEEKDAY 而非 WEEKEND，避免命名误导
WEEKDAY_OPEN_DAYS = {MONDAY, TUESDAY, WEDNESDAY, THURSDAY, FRIDAY}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def market_state(now: datetime | None = None) -> dict:
    """返回市场状态：is_open/session/next_open/next_close（UTC 时间戳秒）。"""
    if now is None:
        now = _utc_now()
    else:
        now = now.astimezone(timezone.utc)

    weekday = now.weekday()  # 0=周一 ... 6=周日
    is_open = weekday in WEEKDAY_OPEN_DAYS

    # 收市 = 本周五结束（周六 00:00 UTC）
    days_to_sat = (5 - weekday) % 7  # 到本周六的天数（周一~周五=5..1，周六=0，周日=6）
    this_sat = (now + timedelta(days=days_to_sat)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    # 开市 = 下周一 00:00 UTC
    days_to_mon = (7 - weekday) % 7  # 周一=0 ... 周日=6
    days_to_mon = days_to_mon if days_to_mon else 7
    next_mon = (now + timedelta(days=days_to_mon)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    next_close = this_sat  # 本周末（收市时刻）
    if is_open:
        next_open = next_mon
    else:
        # 闭市（周六/周日）：下次开市 = 下周一
        next_open = next_mon
        # 若现在正处周六/周日，收市时间已过，返回下周一的收市时刻无意义——置为距最近一次收市？
        # 语义上闭市时“下一收市”没有意义，返回 None（前端只显示开市倒计时）。
        next_close = None

    return {
        "is_open": is_open,
        "session": "open" if is_open else "closed",
        "now_ts": int(now.timestamp()),
        "next_open_ts": int(next_open.timestamp()),
        "next_close_ts": int(next_close.timestamp()) if next_close else None,
        "weekday": weekday,
    }