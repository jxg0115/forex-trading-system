"""板块四：ML 模型池训练与预测 API。"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from api.deps import get_app_state

router = APIRouter(prefix="/api/models", tags=["ML 模型池"])


class ModelRequest(BaseModel):
    symbol: str = "EURUSD"
    timeframe: str = "M15"
    count: int = 300


def _dataframe(state, symbol: str, timeframe: str, count: int):
    df = state.market.dataframe(symbol, timeframe, count)
    if df.empty:
        raise HTTPException(status_code=503, detail=f"MT5 未连接或无法获取 {symbol} K 线数据")
    return df


@router.get("")
async def list_models(state=Depends(get_app_state)):
    return {"success": True, "models": state.model_pool.list_models()}


@router.post("/{name}/train")
async def train_model(name: str, req: ModelRequest, state=Depends(get_app_state)):
    try:
        df = _dataframe(state, req.symbol, req.timeframe, req.count)
        result = state.model_pool.train(name, df)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, "symbol": req.symbol, "timeframe": req.timeframe, **result}


@router.post("/{name}/predict")
async def predict_model(name: str, req: ModelRequest, state=Depends(get_app_state)):
    try:
        df = _dataframe(state, req.symbol, req.timeframe, req.count)
        result = state.model_pool.predict(name, df)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, "symbol": req.symbol, "timeframe": req.timeframe, **result}

