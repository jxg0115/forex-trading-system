# -*- coding: utf-8 -*-
"""K 线形态独立模块接口。

- GET  /api/vpattern/state   模块状态（开关/参数/最近信号/独立统计）
- POST /api/vpattern/toggle  开关
- POST /api/vpattern/config  改风险参数（仓位、闸门等）
- GET  /api/vpattern/trades  独立账本明细
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.deps import get_app_state

router = APIRouter(prefix="/api/vpattern", tags=["K线形态"])


class ToggleRequest(BaseModel):
    enabled: bool


class ConfigRequest(BaseModel):
    config: dict[str, Any] = {}


def _engine(state: Any):
    return getattr(state, "vpattern", None)


@router.get("/state")
async def vpattern_state(state=Depends(get_app_state)):
    eng = _engine(state)
    if eng is None:
        return {"available": False, "enabled": False,
                "message": "K 线形态模块未初始化（需 MT5 连接）"}
    return {"available": True, **_eng_state(eng)}


def _eng_state(eng: Any) -> dict[str, Any]:
    try:
        return eng.state()
    except Exception as exc:  # noqa: BLE001
        return {"enabled": False, "last_error": f"{type(exc).__name__}: {exc}"}


@router.post("/toggle")
async def vpattern_toggle(req: ToggleRequest, state=Depends(get_app_state)):
    eng = _engine(state)
    if eng is None:
        return {"success": False, "message": "K 线形态模块未初始化"}
    eng.set_enabled(bool(req.enabled))
    return {
        "success": True,
        "enabled": bool(req.enabled),
        "message": "K 线形态自动交易已开启" if req.enabled else "K 线形态自动交易已关闭",
    }


@router.post("/config")
async def vpattern_config(req: ConfigRequest, state=Depends(get_app_state)):
    eng = _engine(state)
    if eng is None:
        return {"success": False, "message": "K 线形态模块未初始化"}
    cfg = eng.update_config(dict(req.config or {}))
    return {"success": True, "config": cfg.to_dict(), "message": "K 线形态参数已保存"}


@router.get("/trades")
async def vpattern_trades(state=Depends(get_app_state)):
    from backtest_store.repository import get_repository

    repo = get_repository()
    try:
        return {
            "trades": repo.list_vpattern_trades(limit=500),
            "stats": repo.vpattern_stats(),
            "daily": repo.vpattern_daily(),
        }
    finally:
        repo.close()
