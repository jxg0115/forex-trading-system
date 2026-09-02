"""行情相关路由。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from api.deps import get_app_state
from api.market_sessions import market_state

router = APIRouter(prefix="/api/market", tags=["行情"])


@router.get("/symbols")
async def list_symbols(state=Depends(get_app_state)):
    symbols = state.market.symbols()
    if not symbols:
        raise HTTPException(status_code=503, detail="MT5 未连接，无法获取交易品种列表")
    return {"symbols": symbols}


@router.get("/bars")
async def get_bars(symbol: str = "EURUSD", timeframe: str = "M15", limit: int = 500, state=Depends(get_app_state)):
    bars = state.market.get_bars(symbol, timeframe, limit=limit)
    if not bars:
        raise HTTPException(status_code=503, detail=f"MT5 未连接或无法获取 {symbol} K 线数据")
    return {"symbol": symbol, "timeframe": timeframe, "bars": bars}


@router.get("/snapshot")
async def get_snapshot(symbol: str = "EURUSD", timeframe: str = "M15", state=Depends(get_app_state)):
    try:
        return state.market.snapshot(symbol, timeframe).model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/status")
async def get_market_status(
    symbol: str = "XAUUSD",
    timeframe: str = "M15",
    state=Depends(get_app_state),
):
    # 市场状态/开市·收市倒计时（不依赖行情）+ 当前生效的行情过滤配置 + 最近拦截原因
    mkt = market_state()
    executor = getattr(state, "signal_executor", None)
    filter_config = {}
    skip = None
    if executor is not None:
        cfg = getattr(executor, "config", None)
        filter_config = (getattr(cfg, "market_filter", None) or {}) if cfg is not None else {}
        skip = getattr(executor, "last_market_skip", None)
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "market": mkt,
        "filter_config": filter_config,
        "last_market_skip": skip,
    }


@router.get("/analysis")
async def get_market_analysis(symbol: str = "EURUSD", timeframe: str = "M15", state=Depends(get_app_state)):
    return state.market_analysis.analyze(symbol, timeframe)
