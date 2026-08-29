"""tick 存档 API（阶段二：数据补缺）：回填 / 实时录制启停 / 回放 / 状态。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from api.deps import get_app_state
from market_data.tick_archive import backfill, list_days, replay, recorder_status, start_recorder, stop_recorder

router = APIRouter(prefix="/api/tick_archive", tags=["tick 存档"])


@router.get("/status")
async def status(state=Depends(get_app_state)) -> dict[str, Any]:
    """录制状态 + 存档概况（文件数/行数）。"""
    return {"ok": True, **recorder_status()}


@router.post("/backfill")
async def do_backfill(symbol: str = "GOLD", days: int = 30, state=Depends(get_app_state)) -> dict[str, Any]:
    """回填最近 N 天历史 tick（MT5 copy_ticks_range，按日 JSONL）。"""
    gw = getattr(state, "mt5_gateway", None)
    if gw is None:
        return {"ok": False, "message": "MT5 网关未就绪"}
    n = backfill(gw, symbol, days)
    return {"ok": True, "count": n, "symbol": symbol, "days": days}


@router.post("/start")
async def start(symbol: str = "GOLD", interval: float = 5.0, state=Depends(get_app_state)) -> dict[str, Any]:
    """启动实时录制（后台任务：定时 get_tick → 当日文件追加）。"""
    gw = getattr(state, "mt5_gateway", None)
    if gw is None:
        return {"ok": False, "message": "MT5 网关未就绪"}
    started = start_recorder(gw, symbol, interval)
    return {"ok": True, "started": started, "message": "录制已启动" if started else "录制已在运行"}


@router.post("/stop")
async def stop() -> dict[str, Any]:
    """停止实时录制。"""
    stop_recorder()
    return {"ok": True, "message": "录制已停止"}


@router.get("/replay")
async def do_replay(symbol: str = "GOLD", date: str = "") -> dict[str, Any]:
    """按日回放（date 格式 YYYY-MM-DD，UTC；留空=最近一日）。"""
    days = list_days(symbol)
    if not days:
        return {"ok": True, "count": 0, "ticks": [], "files": []}
    target = date or days[-1].replace(symbol + "_", "").replace(".jsonl", "")
    ticks = replay(symbol, target)
    return {"ok": True, "date": target, "count": len(ticks), "files": days, "ticks": ticks[:200]}


@router.get("/files")
async def files(symbol: str = "GOLD") -> dict[str, Any]:
    """已存档日期文件列表。"""
    return {"ok": True, "files": list_days(symbol)}