"""形态案例库：学习、保存、查询与实盘相似形态匹配。"""

from __future__ import annotations

import uuid
import math
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ai_engine.pattern import classify_pattern, compute_fingerprint, extract_features, learn_levels, learn_stop_take, learn_timing, match_cases
from ai_engine.region import analyze_region
from api.deps import get_app_state, repo_dependency
from backtest_store.repository import PatternCaseRecord

router = APIRouter(prefix="/api/patterns", tags=["形态案例库"])


def _clean(value: Any) -> Any:
    if isinstance(value, float):
        return 0.0 if not math.isfinite(value) else value
    if isinstance(value, list):
        return [_clean(v) for v in value]
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    return value


class PatternPoint(BaseModel):
    time: str
    price: float
    label: str = ""
    reason: str = ""


class PatternLearnRequest(BaseModel):
    symbol: str = "EURUSD"
    timeframe: str = "M15"
    time_start: str
    time_end: str
    price_top: float = 0.0
    price_bottom: float = 0.0
    entry_points: list[PatternPoint] = []
    exit_points: list[PatternPoint] = []
    support_levels: list[dict[str, Any]] = []
    resistance_levels: list[dict[str, Any]] = []


class PatternSaveRequest(PatternLearnRequest):
    factor_id: str = ""
    fingerprint: dict[str, Any] = {}
    region_stats: dict[str, Any] = {}
    learned: dict[str, Any] = {}
    fingerprints: dict[str, Any] = {}
    features: dict[str, Any] = {}
    ohlcv: list[dict[str, Any]] = []
    pattern_type: str = "通用形态"
    pattern_subtype: str = "自定义"
    recognition_confidence: float = 0.0


class PatternMatchRequest(BaseModel):
    symbol: str = "EURUSD"
    timeframe: str = "M15"
    window_bars: int = 60
    min_similarity: float = 0.85
    top_k: int = 5
    min_samples: int = 0
    decay_days: int = 365


class PatternConfirmRequest(BaseModel):
    confirmed: bool = True
    label: str = ""


def _region_dict(req: PatternLearnRequest) -> dict[str, Any]:
    return {
        "symbol": req.symbol,
        "timeframe": req.timeframe,
        "time_start": req.time_start,
        "time_end": req.time_end,
        "price_top": req.price_top,
        "price_bottom": req.price_bottom,
        "entry_points": [p.model_dump() for p in req.entry_points],
        "exit_points": [p.model_dump() for p in req.exit_points],
        "support_levels": req.support_levels,
        "resistance_levels": req.resistance_levels,
    }


def _fetch_bars(state, symbol: str, timeframe: str, start: str, end: str):
    if state.mt5_gateway:
        from datetime import datetime

        def parse(value: str):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))

        return state.mt5_gateway.get_rates_range(symbol, timeframe, parse(start), parse(end))
    return state.market.get_bars(symbol, timeframe, limit=500)


@router.post("/learn")
async def learn_pattern(req: PatternLearnRequest, state=Depends(get_app_state)):
    """根据框选区域与标注点，计算形态指纹和入场/出场/止盈止损学习结果。"""

    bars = _fetch_bars(state, req.symbol, req.timeframe, req.time_start, req.time_end)
    region = _region_dict(req)
    from models.factor import ChartRegion

    parsed_region = ChartRegion(
        symbol=req.symbol,
        timeframe=req.timeframe,
        time_start=req.time_start,
        time_end=req.time_end,
        price_top=req.price_top,
        price_bottom=req.price_bottom,
        entry_points=[],
        exit_points=[],
    )
    stats, sliced = analyze_region(parsed_region, bars)
    if not sliced:
        raise HTTPException(status_code=400, detail="框选区域内没有 K 线数据")
    fingerprint = compute_fingerprint(sliced)
    timing = learn_timing(region, [p.model_dump() for p in req.entry_points], [p.model_dump() for p in req.exit_points], sliced)
    stop_take = learn_stop_take([p.model_dump() for p in req.entry_points], [p.model_dump() for p in req.exit_points], sliced)
    levels = learn_levels(region, [p.model_dump() for p in req.entry_points], sliced)
    features = _clean(extract_features(
        sliced,
        region,
        [p.model_dump() for p in req.entry_points],
        [p.model_dump() for p in req.exit_points],
        req.support_levels,
        req.resistance_levels,
    ))
    classification = classify_pattern(features)
    return {
        "success": True,
        "symbol": req.symbol,
        "timeframe": req.timeframe,
        "fingerprint": fingerprint,
        "fingerprints": features["fingerprints"],
        "features": features,
        "ohlcv": sliced[-120:],
        "pattern_type": classification["pattern_type"],
        "pattern_subtype": classification["pattern_subtype"],
        "recognition_confidence": classification["confidence"],
        "region_stats": stats.model_dump(),
        "learned": {**timing, **stop_take, **levels},
        "bar_count": len(sliced),
    }


@router.post("", status_code=201)
async def save_pattern(req: PatternSaveRequest, repo=Depends(repo_dependency)):
    """把学习完成的形态案例保存进案例库。"""

    record = PatternCaseRecord(
        id=str(uuid.uuid4()),
        factor_id=req.factor_id,
        symbol=req.symbol,
        timeframe=req.timeframe,
        time_start=datetime.fromisoformat(req.time_start.replace("Z", "+00:00")),
        time_end=datetime.fromisoformat(req.time_end.replace("Z", "+00:00")),
        price_top=req.price_top,
        price_bottom=req.price_bottom,
        bar_count=int(req.region_stats.get("bar_count", 0)),
        fingerprint=req.fingerprint,
        fingerprints=req.fingerprints,
        features=req.features,
        ohlcv=req.ohlcv,
        pattern_type=req.pattern_type,
        pattern_subtype=req.pattern_subtype,
        recognition_confidence=req.recognition_confidence,
        human_confirmed=False,
        labels=[],
        region_stats=req.region_stats,
        entry_points=[p.model_dump() for p in req.entry_points],
        exit_points=[p.model_dump() for p in req.exit_points],
        support_levels=req.support_levels,
        resistance_levels=req.resistance_levels,
        learned=req.learned,
        created_at=datetime.now(timezone.utc),
    )
    repo.save_pattern_case(record)
    return {"success": True, "case_id": record.id}


@router.get("")
async def list_patterns(symbol: str | None = None, timeframe: str | None = None, repo=Depends(repo_dependency)):
    cases = repo.list_pattern_cases(symbol=symbol, timeframe=timeframe)
    return {
        "success": True,
        "cases": [
            {
                "id": c.id,
                "factor_id": c.factor_id,
                "symbol": c.symbol,
                "timeframe": c.timeframe,
                "bar_count": c.bar_count,
                "region_stats": c.region_stats,
                "fingerprints": c.fingerprints,
                "features": c.features,
                "ohlcv": c.ohlcv,
                "pattern_type": c.pattern_type,
                "pattern_subtype": c.pattern_subtype,
                "recognition_confidence": c.recognition_confidence,
                "human_confirmed": c.human_confirmed,
                "labels": c.labels,
                "entry_points": c.entry_points,
                "exit_points": c.exit_points,
                "support_levels": c.support_levels,
                "resistance_levels": c.resistance_levels,
                "learned": c.learned,
                "created_at": c.created_at.isoformat(),
            }
            for c in cases
        ],
    }


@router.post("/match")
async def match_patterns(req: PatternMatchRequest, state=Depends(get_app_state), repo=Depends(repo_dependency)):
    """用当前最近 K 线形态与案例库匹配相似形态。"""

    bars = state.market.get_bars(req.symbol, req.timeframe, limit=max(req.window_bars, 30))
    if not bars:
        raise HTTPException(status_code=503, detail=f"无法获取 {req.symbol} 行情数据")
    current_fp = compute_fingerprint(bars)
    current_features = extract_features(
        bars,
        {
            "time_start": bars[0]["time"],
            "time_end": bars[-1]["time"],
            "timeframe": req.timeframe,
            "price_top": max(b["high"] for b in bars),
            "price_bottom": min(b["low"] for b in bars),
        },
        [],
        [],
        [],
        [],
    )
    current = {"fingerprint": current_fp, "features": current_features}
    cases = repo.list_pattern_cases(symbol=req.symbol, timeframe=req.timeframe)
    matches = match_cases(
        current,
        cases,
        min_similarity=req.min_similarity,
        min_samples=req.min_samples,
        decay_days=req.decay_days,
    )
    for match in matches:
        factor_name = ""
        if match["factor_id"]:
            factor = repo.get_factor(match["factor_id"])
            factor_name = factor.name if factor else ""
        match["factor_name"] = factor_name
        case = next((c for c in cases if c.id == match["case_id"]), None)
        match["entry_points"] = case.entry_points if case else []
        match["exit_points"] = case.exit_points if case else []
    return {"success": True, "matches": matches[: req.top_k]}


@router.delete("/{case_id}")
async def delete_pattern(case_id: str, repo=Depends(repo_dependency)):
    if not repo.delete_pattern_case(case_id):
        raise HTTPException(status_code=404, detail="形态案例不存在")
    return {"message": "形态案例已删除"}


@router.post("/{case_id}/confirm")
async def confirm_pattern(case_id: str, req: PatternConfirmRequest, repo=Depends(repo_dependency)):
    if not repo.confirm_pattern_case(case_id, confirmed=req.confirmed, label=req.label):
        raise HTTPException(status_code=404, detail="形态案例不存在")
    return {"message": "形态标签已确认", "confirmed": req.confirmed, "label": req.label}
