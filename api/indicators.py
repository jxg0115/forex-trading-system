"""板块六：统一技术指标与特征工程 API。"""

from __future__ import annotations

import json
from typing import Any

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import get_app_state
from indicators.technical import FEATURE_REGISTRY, compute_features
from indicators.cache import FeatureCache

router = APIRouter(prefix="/api", tags=["指标"])
_feature_cache = FeatureCache()


def _num(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
        return round(float(value), 6)
    except (TypeError, ValueError):
        return None


@router.get("/indicators")
async def indicators(
    symbol: str = Query("EURUSD"),
    timeframe: str = Query("M15"),
    count: int = Query(300),
    state=Depends(get_app_state),
):
    """返回 MA/RSI/MACD/ATR/ADX/布林带最新值与最近序列。"""

    cache_key = f"indicators:{symbol}:{timeframe}:{count}"
    cached = await _feature_cache.get(cache_key)
    if cached:
        return json.loads(cached)

    if state.mt5_gateway and state.mt5_gateway.is_connected:
        bars = state.mt5_gateway.get_rates(symbol, timeframe, count)
    else:
        bars = state.market.get_bars(symbol, timeframe, limit=count)
    if not bars:
        raise HTTPException(status_code=404, detail=f"无法获取 {symbol} {timeframe} K 线数据")

    df = pd.DataFrame(bars)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df = df.set_index("time")
    features = compute_features(df)
    payload = {
        "success": True,
        "symbol": symbol,
        "timeframe": timeframe,
        "count": int(len(features)),
        "latest": {k: _num(v) for k, v in features.iloc[-1].items()},
        "series": {
            k: [{"time": idx.isoformat(), "value": _num(v)} for idx, v in features[k].tail(50).items()]
            for k in features.columns
        },
        "registry": sorted(FEATURE_REGISTRY),
    }
    await _feature_cache.set(cache_key, json.dumps(payload, ensure_ascii=False, default=str))
    return payload
