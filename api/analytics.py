# -*- coding: utf-8 -*-
"""收益日历：按日聚合账户收益。

数据源：MT5 账户账本为唯一事实源（已平仓成交 + 当前持仓浮动），
避免依赖配对记录（配对缺 AI 单/部分字段缺失，收益日历必须含全部单）。

口径（诚实标注）：
- 单笔收益按「平仓日」归属（实现盈亏在收盘结算日体现）。
- 今日浮动盈亏 = 当前持仓 profit 合计（盘中快照，非严格日终结算）。
- 时区：全部按 UTC（与 MT5/K 线一致）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query

from api.deps import get_app_state

router = APIRouter(prefix="/api/analytics", tags=["收益日历"])


def _day_bounds(day: str) -> tuple[int, int]:
    """'YYYY-MM-DD' -> (UTC 当日 00:00, 次日 00:00) epoch 秒。"""
    d = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    d2 = d + timedelta(days=1)
    return int(d.timestamp()), int(d2.timestamp())


def _empty_day(day: str) -> dict[str, Any]:
    return {
        "realized_pnl": 0.0,
        "floating_pnl": 0.0,
        "closed_count": 0,
        "win_count": 0,
        "loss_count": 0,
        "gross_win": 0.0,
        "gross_loss": 0.0,
        "details": [],
    }


@router.get("/calendar")
async def calendar(
    date_from: str = Query(default="", description="YYYY-MM-DD，默认近 7 天"),
    date_to: str = Query(default="", description="YYYY-MM-DD，默认今天"),
    state=Depends(get_app_state),
):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if not date_to:
        date_to = today
    if not date_from:
        d = datetime.strptime(date_to, "%Y-%m-%d") - timedelta(days=6)
        date_from = d.strftime("%Y-%m-%d")
    f0, t0 = _day_bounds(date_from)
    f1, t1 = _day_bounds(date_to)

    closed: list[dict[str, Any]] = []
    gateway = state.mt5_gateway
    if gateway and gateway.is_connected:
        span = max((t1 - f0) // 86400 + 1, 7)
        all_closed = gateway.get_closed_trades(days=min(span, 365))
        closed = [
            x for x in all_closed
            if f0 <= int(x.get("exit_time") or 0) < t1
        ]

    # 当前持仓浮动（归属今天——盘中快照）
    open_positions: list[dict[str, Any]] = []
    if gateway and gateway.is_connected:
        open_positions = gateway.get_positions()
    floating = round(sum(float(p.get("profit") or 0.0) for p in open_positions), 2)

    days: dict[str, dict[str, Any]] = {}
    for x in closed:
        day = datetime.fromtimestamp(
            int(x.get("exit_time") or 0), tz=timezone.utc
        ).strftime("%Y-%m-%d")
        d = days.setdefault(day, _empty_day(day))
        pnl = float(x.get("pnl") or 0.0)
        d["realized_pnl"] = round(d["realized_pnl"] + pnl, 2)
        d["closed_count"] += 1
        if pnl > 0:
            d["win_count"] += 1
            d["gross_win"] = round(d["gross_win"] + pnl, 2)
        elif pnl < 0:
            d["loss_count"] += 1
            d["gross_loss"] = round(d["gross_loss"] + abs(pnl), 2)
        d["details"].append(x)

    now_ts = int(datetime.now(timezone.utc).timestamp())
    if f1 <= now_ts < t1 and floating:
        d = days.setdefault(today, _empty_day(today))
        d["floating_pnl"] = floating

    result = []
    for day in sorted(days):
        d = days[day]
        total = round(d["realized_pnl"] + d["floating_pnl"], 2)
        win_rate = (
            round(d["win_count"] / d["closed_count"] * 100, 1)
            if d["closed_count"]
            else None
        )
        avg_win = round(d["gross_win"] / d["win_count"], 2) if d["win_count"] else None
        avg_loss = (
            round(d["gross_loss"] / d["loss_count"], 2) if d["loss_count"] else None
        )
        pf = round(d["gross_win"] / d["gross_loss"], 2) if d["gross_loss"] else None
        wins = [x.get("pnl", 0) for x in d["details"] if x.get("pnl", 0) > 0]
        losses = [x.get("pnl", 0) for x in d["details"] if x.get("pnl", 0) < 0]
        max_win = round(max(wins), 2) if wins else None
        max_loss = round(min(losses), 2) if losses else None
        result.append(
            {
                "date": day,
                "realized_pnl": d["realized_pnl"],
                "floating_pnl": d["floating_pnl"],
                "total_pnl": total,
                "closed_count": d["closed_count"],
                "win_count": d["win_count"],
                "loss_count": d["loss_count"],
                "win_rate": win_rate,
                "avg_win": avg_win,
                "avg_loss": avg_loss,
                "profit_factor": pf,
                "max_win": max_win,
                "max_loss": max_loss,
                "details": sorted(d["details"], key=lambda x: int(x.get("exit_time") or 0)),
            }
        )

    return {
        "date_from": date_from,
        "date_to": date_to,
        "floating_now": floating,
        "days": result,
        "total": round(sum(d["total_pnl"] for d in result), 2),
    }