# -*- coding: utf-8 -*-
"""市场交易时段判定（开市/闭市 + 每日休市窗口 + 市场时间/北京时间）。

规则（按经纪商服务器时段近似，UTC 基准）：
- 周末闭市：周六/周日全天停（外汇/贵金属 24/5）。
- 每日休市窗口：多数零售平台每日有换日维护窗口（默认 21:59~23:00 UTC）。
  若你的平台实际时段不同（如 22:00~24:00 UTC），只改下面两个常量即可，
  无需改其他代码。窗口不支持跨午夜（跨午夜的平台请拆成两段描述，暂不处理）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

MONDAY = 0
TUESDAY = 1
WEDNESDAY = 2
THURSDAY = 3
FRIDAY = 4
WEEKDAY_OPEN_DAYS = {MONDAY, TUESDAY, WEDNESDAY, THURSDAY, FRIDAY}

# 每日休市窗口（分钟，[start, end) 区间，0<=start<end<=1440；支持跨午夜拆多段）
# 2026-09-02→09-03 M5 实测缺口 22:55→01:00 UTC（单日样本，缓存补齐后复核）：
# 实际换日维护窗口 ≈ 23:00~24:00 + 00:00~01:00 UTC（与默认 21:59~23:00 不符，已校准）
DAILY_CLOSE_WINDOWS = [(23 * 60, 24 * 60), (0, 1 * 60)]  # 23:00~24:00 + 00:00~01:00 UTC
DAILY_CLOSE_LABEL = "每日 23:00~24:00+00:00~01:00 UTC 休市窗口（换日维护，M5 实测校准）"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def market_state(now: datetime | None = None) -> dict:
    """返回市场状态：开闭市（含每日窗口）、市场时间、北京时间、下一开/收市时间戳。"""
    if now is None:
        now = _utc_now()
    else:
        now = now.astimezone(timezone.utc)

    weekday = now.weekday()
    minute_of_day = now.hour * 60 + now.minute
    in_daily_close = any(s <= minute_of_day < e for s, e in DAILY_CLOSE_WINDOWS)
    weekend = weekday not in WEEKDAY_OPEN_DAYS

    if weekend:
        is_open = False
        session = "weekend_closed"
    elif in_daily_close:
        is_open = False
        session = "daily_closed"
    else:
        is_open = True
        session = "open"

    # 下一收市时刻（最近一个未开始的休市窗口起点）
    next_close: datetime | None = None
    if is_open:
        upcoming = [s for s, e in DAILY_CLOSE_WINDOWS if minute_of_day < s]
        if upcoming:
            s = min(upcoming)
            next_close = now.replace(hour=s // 60, minute=s % 60, second=0, microsecond=0)
        elif weekday == FRIDAY:
            # 已过今日窗口（周五开市段 23:00 之后）-> 本周末收市
            days_to_sat = 1
            next_close = (now + timedelta(days=days_to_sat)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
        else:
            # 已过今日窗口（周一~周四）-> 明日首个窗口开始
            next_close = (now + timedelta(days=1)).replace(
                hour=DAILY_CLOSE_WINDOWS[0][0] // 60,
                minute=DAILY_CLOSE_WINDOWS[0][0] % 60,
                second=0, microsecond=0,
            )

    # 下一开市时刻
    next_open: datetime | None = None
    if not is_open:
        if in_daily_close and not weekend:
            # 每日窗口内 -> 当前窗口结束即开（跨午夜段按当前所在段取终点）
            cur_end = next((e for s, e in DAILY_CLOSE_WINDOWS if s <= minute_of_day < e), None)
            if cur_end is not None:
                next_open = now.replace(
                    hour=cur_end // 60,
                    minute=cur_end % 60,
                    second=0, microsecond=0,
                )
            else:  # 兜底：次日首窗口后
                next_open = now.replace(
                    hour=DAILY_CLOSE_WINDOWS[0][0] // 60,
                    minute=DAILY_CLOSE_WINDOWS[0][0] % 60,
                    second=0, microsecond=0,
                ) + timedelta(days=1)
        else:
            # 周末闭市 -> 下周一 00:00 UTC
            days_to_mon = (7 - weekday) % 7
            days_to_mon = days_to_mon if days_to_mon else 7
            next_open = (now + timedelta(days=days_to_mon)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )

    beijing = now + timedelta(hours=8)

    return {
        "is_open": is_open,
        "session": session,
        "now_ts": int(now.timestamp()),
        "market_time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "market_time_iso": now.isoformat(),
        "beijing_time": beijing.strftime("%Y-%m-%d %H:%M:%S"),
        "beijing_time_iso": beijing.isoformat(),
        "weekday": weekday,
        "next_open_ts": int(next_open.timestamp()) if next_open else None,
        "next_close_ts": int(next_close.timestamp()) if next_close else None,
        "weekly_hours": "24/5（周六/周日闭市）",
        "daily_close_window": DAILY_CLOSE_LABEL,
        "in_daily_close_window": in_daily_close,
    }