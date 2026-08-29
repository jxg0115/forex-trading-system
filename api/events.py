"""事件日历 API（阶段二：数据补缺）：查询 / 下一个 / 手工维护 / 规则重建。"""

from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from fastapi import APIRouter
from pydantic import BaseModel

from market_data.events import get_events, next_event, rebuild_routine, upsert_manual

router = APIRouter(prefix="/api/events", tags=["事件日历"])


class EventUpsertRequest(BaseModel):
    id: Optional[str] = None          # 手工事件 id；留空自动生成
    dt: str                           # 事件时间（UTC，ISO：2026-09-04T13:30:00Z）
    type: str = "manual"              # nfp / cpi / fomc / rate / manual ...
    title: str                        # 事件标题
    importance: str = "high"          # high / medium / low
    symbols: list[str] = ["GOLD", "EURUSD"]
    note: Optional[str] = None


def _parse(v: str | None):
    if v is None or not str(v).strip():
        return None
    try:
        return pd.to_datetime(str(v).strip()).to_pydatetime()
    except Exception:
        return None


@router.get("")
async def list_events(
    date_from: str | None = None,
    date_to: str | None = None,
    event_type: str | None = None,
    importance: str | None = None,
) -> dict[str, Any]:
    """查询事件（时间范围/类型/重要性过滤，UTC）。"""
    events = get_events(
        date_from=_parse(date_from),
        date_to=_parse(date_to),
        event_type=event_type,
        importance=importance,
    )
    return {"count": len(events), "events": events}


@router.get("/next")
async def next() -> dict[str, Any]:
    """下一个事件（从当前 UTC 起）。"""
    ev = next_event()
    return {"ok": True, "event": ev}


@router.post("/upsert")
async def upsert(req: EventUpsertRequest) -> dict[str, Any]:
    """手工维护/覆盖一个事件（FOMC/CPI 等发布日不固定的用这里补充）。"""
    ev = upsert_manual(req.model_dump())
    return {"ok": True, "event": ev}


@router.post("/rebuild")
async def rebuild(year_from: int = 2025, year_to: int = 2027) -> dict[str, Any]:
    """重建规则事件（如非农=每月第一周五），保留手工事件。"""
    total = rebuild_routine(year_from, year_to)
    return {"ok": True, "total": total}