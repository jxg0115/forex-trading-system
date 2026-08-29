"""事件日历服务（阶段二：数据补缺）。

无自动数据源（联网检索暂不可用）的现实方案：
- 内置**确定性规则**：非农（NFP）= 每月第一个周五 13:30 UTC；
- 其余高影响事件（FOMC / CPI / 央行利率决议等发布日不固定）由**手工维护入口**
  upsert 补充，持久化到 data/events.json；
- 预留自动导入接口（联网检索恢复后可从公开经济日历同步，source=auto）。

事件 dt 一律 UTC（ISO 格式，如 2026-09-04T13:30:00Z）。
"""

from __future__ import annotations

import calendar as _cal
import json
import os
from datetime import datetime, timezone
from typing import Any

from config import BASE_DIR

EVENTS_FILE = os.path.join(BASE_DIR, "data", "events.json")

# 周几常量：0=Mon ... 4=Fri ... 6=Sun
_WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}

# 周期性规则预置（key -> (weekday, ordinal, hour, minute)）
ROUTINE_RULES: dict[str, tuple[str, int, int, int]] = {
    "nfp": ("fri", 1, 13, 30),  # 非农：每月第一个周五 13:30 UTC
}


def _ordinal_weekday(year: int, month: int, weekday: int, ordinal: int) -> datetime | None:
    """第 ordinal 个 weekday（0=Mon..6=Sun）的日期；不存在则返回 None。"""
    days = [d for d in range(1, _cal.monthrange(year, month)[1] + 1) if _cal.weekday(year, month, d) == weekday]
    if ordinal <= len(days):
        return datetime(year, month, days[ordinal - 1])
    return None


def _routine_events(year_from: int, year_to: int) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for year in range(year_from, year_to + 1):
        for month in range(1, 13):
            for key, (wd, ordinal, hour, minute) in ROUTINE_RULES.items():
                day = _ordinal_weekday(year, month, _WEEKDAYS[wd], ordinal)
                if day is None:
                    continue
                dt = day.replace(hour=hour, minute=minute)
                events.append(
                    {
                        "id": f"{key}-{year}-{month:02d}",
                        "dt": dt.isoformat() + "Z",
                        "type": key,
                        "title": "非农就业报告 (NFP)" if key == "nfp" else key,
                        "importance": "high",
                        "symbols": ["GOLD", "EURUSD", "USDJPY"],
                        "source": "routine",
                    }
                )
    return events


def _load() -> dict[str, Any]:
    if os.path.exists(EVENTS_FILE):
        try:
            with open(EVENTS_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"events": [], "manual": []}


def _save(data: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(EVENTS_FILE), exist_ok=True)
    with open(EVENTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def get_events(
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    event_type: str | None = None,
    importance: str | None = None,
) -> list[dict[str, Any]]:
    """按时间范围/类型/重要性过滤事件（UTC aware 比较），按时间升序。"""
    data = _load()
    # 统一比较基准为 aware UTC（API 解析可能产出 naive datetime）
    if date_from is not None and date_from.tzinfo is None:
        date_from = date_from.replace(tzinfo=timezone.utc)
    if date_to is not None and date_to.tzinfo is None:
        date_to = date_to.replace(tzinfo=timezone.utc)
    out: list[dict[str, Any]] = []
    for e in data["events"] + data["manual"]:
        dt = _parse_dt(e["dt"])
        if date_from is not None and dt < date_from:
            continue
        if date_to is not None and dt > date_to:
            continue
        if event_type and e.get("type") != event_type:
            continue
        if importance and e.get("importance") != importance:
            continue
        out.append(e)
    out.sort(key=lambda x: x["dt"])
    return out


def rebuild_routine(year_from: int, year_to: int) -> int:
    """重建规则事件（不重复叠加），保留手工事件。"""
    data = _load()
    data["events"] = [e for e in data["events"] if e.get("source") != "routine"]
    data["events"].extend(_routine_events(year_from, year_to))
    _save(data)
    return len(data["events"]) + len(data["manual"])


def upsert_manual(event: dict[str, Any]) -> dict[str, Any]:
    """手工维护：新增或按 id 覆盖。"""
    data = _load()
    eid = event.get("id") or f"manual-{len(data['manual']) + 1}"
    event["id"] = eid
    event.setdefault("source", "manual")
    event.setdefault("importance", "high")
    event.setdefault("symbols", ["GOLD", "EURUSD"])
    data["manual"] = [e for e in data["manual"] if e.get("id") != eid]
    data["manual"].append(event)
    _save(data)
    return event


def next_event(after: datetime | None = None) -> dict[str, Any] | None:
    """下一个事件（默认从当前 UTC 时间起）。"""
    after = after or datetime.now(timezone.utc)
    events = get_events(date_from=after)
    return events[0] if events else None


def is_high_event_window(
    now: datetime | None = None,
    window_minutes: int = 30,
) -> tuple[bool, dict[str, Any] | None]:
    """当前时间是否落在高影响事件窗口内（±window_minutes 分钟）。

    用于决策链 D3 事件过滤：事件窗口内禁开仓（NFP/FOMC/利率决议等）。
    """
    now = now or datetime.now(timezone.utc)
    window_sec = max(int(window_minutes), 0) * 60
    for e in get_events():
        if e.get("importance") != "high":
            continue
        dt = _parse_dt(e["dt"])
        if abs((dt - now).total_seconds()) <= window_sec:
            return True, e
    return False, None