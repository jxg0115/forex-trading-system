"""统一交易所路由（/api/broker/*）：MT5 与 OKX 共用一套前端契约。

设计意图：前端不要再为每家交易所复制一套端点（现在 /api/mt5/* 有约 20 个方法）。
新代码一律走 /api/broker/*，/api/mt5/* 保留为兼容别名，后续逐步迁移。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from api.deps import get_app_state
from market_data.symbol_registry import VENUE_MT5, VENUE_OKX

router = APIRouter(prefix="/api/broker", tags=["broker"])


def _state(state: Any = Depends(get_app_state)) -> Any:
    return state


def _gateway(state: Any, venue: str) -> Any:
    reg = getattr(state, "registry", None)
    gw = reg.gateways.get(venue) if reg else None
    if gw is None and venue == VENUE_MT5:
        gw = getattr(state, "mt5_gateway", None)
    if gw is None and venue == VENUE_OKX:
        gw = getattr(state, "okx_gateway", None)
    return gw


@router.get("/venues")
def venues(state: Any = Depends(get_app_state)) -> dict[str, Any]:
    """可用交易所、路由模式与各自连接状态。"""
    reg = getattr(state, "registry", None)
    items = []
    for venue in (VENUE_MT5, VENUE_OKX):
        gw = _gateway(state, venue)
        items.append({
            "venue": venue,
            "label": "MetaTrader 5" if venue == VENUE_MT5 else "OKX 欧易",
            "configured": gw is not None,
            "connected": bool(getattr(gw, "is_connected", False)),
        })
    return {
        "venues": items,
        "mode": getattr(reg, "mode", "按 symbol 路由") if reg else "单一网关",
        "okx_simulated": _okx_flag(state) == "1",
    }


def _okx_flag(state: Any) -> str:
    gw = getattr(state, "okx_gateway", None)
    return str(getattr(gw, "flag", "1"))


@router.get("/symbols")
def symbols(venue: str = "", state: Any = Depends(get_app_state)) -> dict[str, Any]:
    market = getattr(state, "market", None)
    if market is None:
        return {"symbols": [], "count": 0}
    items = market.symbols(venue=venue or None)
    return {"symbols": items, "count": len(items)}


@router.get("/instrument")
def instrument(symbol: str, state: Any = Depends(get_app_state)) -> dict[str, Any]:
    """合约规格：加密返回 ctVal/lotSz/minSz/tickSz，外汇返回 point/pip_value 等。"""
    market = getattr(state, "market", None)
    if market is None:
        raise HTTPException(status_code=503, detail="行情服务未就绪")
    venue = market.venue_of(symbol) if hasattr(market, "venue_of") else VENUE_MT5
    gw = _gateway(state, venue)
    if gw is None:
        raise HTTPException(status_code=503, detail=f"{venue} 网关未配置")
    info = gw.get_symbol_info(symbol)
    if not info:
        raise HTTPException(status_code=404, detail=f"未找到品种 {symbol}")
    info["venue"] = venue
    return {"success": True, "data": info}


@router.get("/funding-rate")
def funding_rate(symbol: str, state: Any = Depends(get_app_state)) -> dict[str, Any]:
    gw = getattr(state, "okx_gateway", None)
    if gw is None:
        raise HTTPException(status_code=503, detail="OKX 网关未配置")
    data = gw.funding_rate(symbol)
    if data is None:
        return {"success": True, "data": None, "message": "现货无资金费率"}
    return {"success": True, "data": data}


@router.get("/account")
def account(venue: str = VENUE_OKX, state: Any = Depends(get_app_state)) -> dict[str, Any]:
    gw = _gateway(state, venue)
    if gw is None:
        raise HTTPException(status_code=503, detail=f"{venue} 网关未配置")
    data = gw.get_account_info()
    if data is None:
        raise HTTPException(status_code=502, detail="获取账户失败，请检查连接与 API 权限")
    return {"success": True, "venue": venue, "data": data}


@router.get("/positions")
def positions(venue: str = VENUE_OKX, state: Any = Depends(get_app_state)) -> dict[str, Any]:
    gw = _gateway(state, venue)
    if gw is None:
        raise HTTPException(status_code=503, detail=f"{venue} 网关未配置")
    return {"success": True, "venue": venue, "positions": gw.get_positions()}


@router.get("/tick")
def tick(symbol: str, state: Any = Depends(get_app_state)) -> dict[str, Any]:
    market = getattr(state, "market", None)
    venue = market.venue_of(symbol) if market else VENUE_OKX
    gw = _gateway(state, venue)
    if gw is None:
        raise HTTPException(status_code=503, detail=f"{venue} 网关未配置")
    data = gw.get_tick(symbol)
    if data is None:
        raise HTTPException(status_code=404, detail=f"未获取到 {symbol} 行情")
    data["venue"] = venue
    return {"success": True, "data": data}


@router.get("/klines")
def klines(symbol: str, timeframe: str = "M15", limit: int = 500,
           state: Any = Depends(get_app_state)) -> dict[str, Any]:
    market = getattr(state, "market", None)
    if market is None:
        raise HTTPException(status_code=503, detail="行情服务未就绪")
    bars = market.get_bars(symbol, timeframe, limit)
    return {"success": True, "symbol": symbol, "timeframe": timeframe,
            "venue": market.venue_of(symbol), "bars": bars}


@router.get("/calc-size")
def calc_size(symbol: str, entry: float, stop: float, risk_pct: float = 1.0,
              equity: float = 0.0, state: Any = Depends(get_app_state)) -> dict[str, Any]:
    """加密口径手数换算：返回张数、名义价值与约束（前端下单面板用）。"""
    from config import settings
    from risk_sizing.crypto_sizing import size_from_symbol_info

    market = getattr(state, "market", None)
    venue = market.venue_of(symbol) if market else VENUE_OKX
    gw = _gateway(state, venue)
    if gw is None:
        raise HTTPException(status_code=503, detail=f"{venue} 网关未配置")
    info = gw.get_symbol_info(symbol)
    if not info:
        raise HTTPException(status_code=404, detail=f"未找到品种 {symbol}")
    result = size_from_symbol_info(
        equity=equity or settings.account_equity,
        risk_percent=risk_pct,
        entry_price=entry,
        stop_price=stop,
        symbol_info=info,
    )
    result["venue"] = venue
    result["symbol"] = symbol
    return {"success": True, "data": result}


@router.post("/order")
def place_order(payload: dict[str, Any], state: Any = Depends(get_app_state)) -> dict[str, Any]:
    """下单。size 语义：衍生品=张，现货=标的币数量（先用 /calc-size 换算）。"""
    symbol = str(payload.get("symbol") or "")
    side = str(payload.get("side") or "").lower()
    size = float(payload.get("size") or 0.0)
    if not symbol or side not in ("buy", "sell") or size <= 0:
        raise HTTPException(status_code=400, detail="参数不完整：symbol/side/size 必填")

    market = getattr(state, "market", None)
    venue = market.venue_of(symbol) if market else VENUE_OKX
    gw = _gateway(state, venue)
    if gw is None:
        raise HTTPException(status_code=503, detail=f"{venue} 网关未配置")

    result = gw.place_order(
        symbol=symbol,
        action_type="BUY" if side == "buy" else "SELL",
        volume=size,
        price=payload.get("price"),
        sl=payload.get("sl"),
        tp=payload.get("tp"),
        comment=str(payload.get("comment") or "AI")[:40],
    )
    result["venue"] = venue
    return result


@router.post("/close")
def close_position(payload: dict[str, Any], state: Any = Depends(get_app_state)) -> dict[str, Any]:
    """平仓。ticket 为 "<instId>|<posSide>"；size 留空=全平。"""
    ticket = str(payload.get("ticket") or "")
    if not ticket:
        raise HTTPException(status_code=400, detail="缺少 ticket")
    symbol = ticket.split("|")[0]
    market = getattr(state, "market", None)
    venue = market.venue_of(symbol) if market else VENUE_OKX
    gw = _gateway(state, venue)
    if gw is None:
        raise HTTPException(status_code=503, detail=f"{venue} 网关未配置")
    result = gw.close_position(ticket, payload.get("size"))
    result["venue"] = venue
    return result


@router.post("/amend-sl")
def amend_sl(payload: dict[str, Any], state: Any = Depends(get_app_state)) -> dict[str, Any]:
    """改止损/止盈（OKX 走 algo 单 amend，异步生效）。"""
    ticket = str(payload.get("ticket") or "")
    if not ticket:
        raise HTTPException(status_code=400, detail="缺少 ticket")
    symbol = ticket.split("|")[0]
    market = getattr(state, "market", None)
    venue = market.venue_of(symbol) if market else VENUE_OKX
    gw = _gateway(state, venue)
    if gw is None:
        raise HTTPException(status_code=503, detail=f"{venue} 网关未配置")
    result = gw.modify_position(ticket, payload.get("sl"), payload.get("tp"))
    result["venue"] = venue
    return result
