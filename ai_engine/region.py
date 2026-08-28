"""框选区域统计分析与人工标注解析。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import numpy as np

from models.factor import ChartRegion, RegionStats


def analyze_region(region: ChartRegion, bars: list[dict[str, Any]]) -> tuple[RegionStats, list[dict[str, Any]]]:
    """从框选时间范围切出 K 线并计算统计特征。"""

    start_ts = region.time_start.timestamp()
    end_ts = region.time_end.timestamp()
    sliced = [
        bar
        for bar in bars
        if start_ts <= _ts(bar["time"]) <= end_ts
    ]
    if not sliced:
        return RegionStats(), []

    closes = np.array([b["close"] for b in sliced], dtype=float)
    highs = np.array([b["high"] for b in sliced], dtype=float)
    lows = np.array([b["low"] for b in sliced], dtype=float)
    ret = (closes[-1] / closes[0] - 1.0) * 100.0 if closes[0] else 0.0
    with np.errstate(invalid="ignore", divide="ignore"):
        avg_range = float(np.mean((highs - lows) / closes)) * 100.0

    trend = "上升趋势" if ret > 0.5 else "下降趋势" if ret < -0.5 else "震荡"
    with np.errstate(invalid="ignore", divide="ignore"):
        vol_std = float(np.std(np.diff(closes) / closes[:-1])) if len(closes) > 1 else 0.0
    if vol_std >= 0.0012:
        volatility = "高波动"
    elif vol_std >= 0.0006:
        volatility = "中等波动"
    else:
        volatility = "低波动"

    stats = RegionStats(
        bar_count=len(sliced),
        return_pct=round(ret, 4),
        high=float(highs.max()),
        low=float(lows.min()),
        avg_range_pct=round(avg_range, 4),
        trend=trend,
        volatility=volatility,
    )
    return stats, sliced


def _ts(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return float(parsed.timestamp())
    return float(value.timestamp())
