"""形态指纹、相似度匹配与入场/出场/止盈止损学习。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from indicators.technical import adx_series, atr_series, bollinger, ema, macd, rsi

FINGERPRINT_POINTS = 60
EDGE_DECAY = 4.0


def _to_ts(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return float(parsed.timestamp())
    return float(value.timestamp())


def downsample(values: list[float], points: int = FINGERPRINT_POINTS) -> list[float]:
    if len(values) <= points:
        return list(values)
    indices = np.linspace(0, len(values) - 1, points)
    return [float(v) for v in np.interp(indices, np.arange(len(values)), np.asarray(values, dtype=float))]


def edge_weights(n: int, decay: float = EDGE_DECAY) -> np.ndarray:
    # Right-edge exponential weights: later bars matter more.
    if n <= 1:
        return np.ones(1)
    x = np.linspace(0.0, 1.0, n)
    w = np.exp(decay * x)
    return w / float(w.sum())


def compute_fingerprint(bars: list[dict[str, Any]]) -> dict[str, Any]:
    """计算形态指纹：收盘价 z-score 归一化序列。"""

    closes = [float(b["close"]) for b in bars]
    if not closes:
        return {"closes_z": [], "bars": 0}
    sampled = downsample(closes)
    arr = np.asarray(sampled, dtype=float)
    std = float(arr.std())
    mean = float(arr.mean())
    closes_z = [0.0] * len(arr) if std < 1e-12 else [round(float((v - mean) / std), 6) for v in arr]
    return {"closes_z": closes_z, "bars": len(bars)}


def similarity(a: dict[str, Any], b: dict[str, Any], weights: np.ndarray | None = None) -> float:
    # Weighted Pearson similarity in [0, 1]; weights emphasize the right edge.
    x = np.asarray(a.get("closes_z", []), dtype=float)
    y = np.asarray(b.get("closes_z", []), dtype=float)
    if len(x) < 3 or len(y) < 3 or len(x) != len(y):
        return 0.0
    if x.std() < 1e-9 or y.std() < 1e-9:
        return 0.0
    w = np.asarray(weights, dtype=float) if weights is not None else np.ones(len(x))
    if len(w) != len(x) or float(w.sum()) <= 0:
        w = np.ones(len(x))
    mx = float(np.average(x, weights=w))
    my = float(np.average(y, weights=w))
    cov = float(np.average((x - mx) * (y - my), weights=w))
    sx = float(np.sqrt(np.average((x - mx) ** 2, weights=w)))
    sy = float(np.sqrt(np.average((y - my) ** 2, weights=w)))
    corr = cov / (sx * sy) if sx > 0 and sy > 0 else 0.0
    if not np.isfinite(corr):
        return 0.0
    return round((corr + 1.0) / 2.0, 4)


def learn_timing(
    region: dict[str, Any],
    entry_points: list[dict[str, Any]],
    exit_points: list[dict[str, Any]],
    bars: list[dict[str, Any]],
) -> dict[str, Any]:
    """学习入场/出场时机：时间位置、价格分位、平均持仓时长。"""

    start = _to_ts(region.get("time_start"))
    end = _to_ts(region.get("time_end"))
    span = max(end - start, 1.0)
    high = float(region.get("price_top") or max((b["high"] for b in bars), default=0.0))
    low = float(region.get("price_bottom") or min((b["low"] for b in bars), default=0.0))
    price_span = max(high - low, 1e-12)

    def point_stats(points: list[dict[str, Any]]) -> list[dict[str, float]]:
        result = []
        for p in points:
            result.append(
                {
                    "time_fraction": round((_to_ts(p["time"]) - start) / span, 4),
                    "price_fraction": round((float(p["price"]) - low) / price_span, 4),
                }
            )
        return result

    entry_stats = point_stats(entry_points)
    exit_stats = point_stats(exit_points)
    timeframe_minutes = 15
    tf = region.get("timeframe", "M15")
    timeframe_minutes = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}.get(tf, 15)
    hold_bars: list[float] = []
    if entry_points and exit_points:
        for ep in entry_points:
            for xp in exit_points:
                if _to_ts(xp["time"]) >= _to_ts(ep["time"]):
                    hold_bars.append((_to_ts(xp["time"]) - _to_ts(ep["time"])) / 60.0 / timeframe_minutes)

    return {
        "entry_stats": entry_stats,
        "exit_stats": exit_stats,
        "avg_entry_time_fraction": round(float(np.mean([s["time_fraction"] for s in entry_stats])), 4) if entry_stats else 0.0,
        "avg_entry_price_fraction": round(float(np.mean([s["price_fraction"] for s in entry_stats])), 4) if entry_stats else 0.0,
        "avg_exit_time_fraction": round(float(np.mean([s["time_fraction"] for s in exit_stats])), 4) if exit_stats else 0.0,
        "avg_exit_price_fraction": round(float(np.mean([s["price_fraction"] for s in exit_stats])), 4) if exit_stats else 0.0,
        "avg_hold_bars": round(float(np.mean(hold_bars)), 2) if hold_bars else 0.0,
    }


def learn_stop_take(
    entry_points: list[dict[str, Any]],
    exit_points: list[dict[str, Any]],
    bars: list[dict[str, Any]],
) -> dict[str, Any]:
    """从标注的入场/出场点学习建议止损止盈距离。"""

    diffs: list[float] = []
    for ep in entry_points:
        for xp in exit_points:
            if _to_ts(xp["time"]) >= _to_ts(ep["time"]):
                entry = float(ep["price"])
                if entry > 0:
                    diffs.append(abs(float(xp["price"]) - entry) / entry)
    avg_take = float(np.mean(diffs)) if diffs else 0.0
    closes = np.asarray([float(b["close"]) for b in bars], dtype=float)
    ranges = np.abs(np.diff(closes))
    avg_range = float(np.mean(ranges)) if len(ranges) else 0.0
    last_close = float(closes[-1]) if len(closes) else 1.0
    atr_pct = avg_range / last_close if last_close else 0.0
    suggested_stop_pct = round(max(atr_pct * 2.0, 0.003), 6)
    suggested_take_pct = round(max(avg_take, atr_pct * 3.0), 6)
    return {
        "avg_take_pct": round(avg_take, 6),
        "suggested_stop_pct": suggested_stop_pct,
        "suggested_take_pct": suggested_take_pct,
        "atr_pct": round(atr_pct, 6),
    }


def learn_levels(
    region: dict[str, Any],
    entry_points: list[dict[str, Any]],
    bars: list[dict[str, Any]],
) -> dict[str, Any]:
    """学习支撑/压力位结构：数量、区间宽度、入场点相对关键位位置。"""

    supports = [float(l["price"]) for l in region.get("support_levels", [])]
    resistances = [float(l["price"]) for l in region.get("resistance_levels", [])]
    closes = np.asarray([float(b["close"]) for b in bars], dtype=float)
    last_close = float(closes[-1]) if len(closes) else 1.0
    all_levels = supports + resistances
    if all_levels:
        range_width_pct = (max(all_levels) - min(all_levels)) / last_close * 100.0
    else:
        ranges = np.abs(np.diff(closes))
        range_width_pct = float(np.mean(ranges)) / last_close * 100.0 if len(ranges) else 0.0

    entry_to_support = 0.0
    entry_to_resistance = 0.0
    if entry_points:
        price = float(entry_points[0]["price"])
        below = [p for p in supports if p <= price]
        above = [p for p in resistances if p >= price]
        if below:
            entry_to_support = (price - max(below)) / price * 100.0
        if above:
            entry_to_resistance = (min(above) - price) / price * 100.0

    return {
        "support_count": len(supports),
        "resistance_count": len(resistances),
        "support_prices": [round(p, 5) for p in supports],
        "resistance_prices": [round(p, 5) for p in resistances],
        "range_width_pct": round(range_width_pct, 4),
        "entry_to_support_pct": round(entry_to_support, 4),
        "entry_to_resistance_pct": round(entry_to_resistance, 4),
    }


def _env_bonus(regime: str | None, region_stats: dict[str, Any]) -> float:
    if not regime or not region_stats:
        return 0.0
    bonus = 0.0
    if region_stats.get("trend") in regime:
        bonus += 0.03
    if region_stats.get("volatility") in regime:
        bonus += 0.02
    return bonus


def _decay_factor(created_at: Any, now: Any, decay_days: int) -> float:
    if not created_at or decay_days <= 0:
        return 1.0
    if isinstance(created_at, str):
        try:
            created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except ValueError:
            return 1.0
    if hasattr(created_at, "tzinfo") and created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    if now is None:
        now = datetime.now(timezone.utc)
    age_days = max((now - created_at).total_seconds() / 86400.0, 0.0)
    return max(0.5, 1.0 - age_days / decay_days)


def _cheap_prefilter(case_features: dict[str, Any], current_features: dict[str, Any]) -> bool:
    cs = case_features.get("structure", {})
    ns = current_features.get("structure", {})
    cb = cs.get("bar_count", 0)
    nb = ns.get("bar_count", 0)
    if cb and nb and (cb < nb * 0.4 or cb > nb * 2.5):
        return False
    if abs(cs.get("swing_high_count", 0) - ns.get("swing_high_count", 0)) > 3:
        return False
    if abs(cs.get("swing_low_count", 0) - ns.get("swing_low_count", 0)) > 3:
        return False
    return True


def match_cases(
    current_fp: dict[str, Any],
    cases: list[Any],
    min_similarity: float = 0.85,
    min_samples: int = 0,
    regime: str | None = None,
    now: datetime | None = None,
    decay_days: int = 365,
) -> list[dict[str, Any]]:
    """带适配门槛的形态匹配：相似度 + 最小样本 + 市场环境 + 时间衰减。"""

    matches: list[dict[str, Any]] = []
    for case in cases:
        case_features = getattr(case, "features", None) or {}
        current_features = current_fp.get("features") if isinstance(current_fp, dict) else None
        if current_features and case_features and not _cheap_prefilter(case_features, current_features):
            continue
        if case_features and isinstance(current_fp, dict) and current_fp.get("features"):
            base = weighted_similarity(current_fp["features"], case_features)
        else:
            current_shape = current_fp.get("fingerprint", current_fp) if isinstance(current_fp, dict) else current_fp
            base = similarity(current_shape, case.fingerprint)
        if base <= 0:
            continue
        stats = case.statistics or {}
        samples = int(stats.get("samples", 0))
        if samples < min_samples:
            continue
        wins = int(stats.get("wins", 0))
        losses = int(stats.get("losses", 0))
        completion = 1.0
        if isinstance(current_fp, dict):
            timing = (current_fp.get("features") or {}).get("timing") or {}
            completion = float(timing.get("pattern_completion", 1.0))
            completion = min(1.0, max(0.0, completion))
        env = _env_bonus(regime, case.region_stats or {})
        decay = _decay_factor(getattr(case, "created_at", None), now, decay_days)
        score = min(base * (1.0 + env) * decay * (0.85 + 0.15 * completion), 1.0)
        if samples >= 5 and samples > 0 and wins / samples < 0.35:
            score *= 0.85
        if score < min_similarity:
            continue
        matches.append(
            {
                "case_id": case.id,
                "factor_id": case.factor_id,
                "similarity": round(score, 4),
                "base_similarity": round(base, 4),
                "sample_count": samples,
                "win_rate": round(wins / samples * 100.0, 2) if samples else 0.0,
                "avg_pnl": round(float(stats.get("avg_pnl", 0.0)), 2),
                "expected_value": round(float(stats.get("avg_pnl", 0.0)), 2),
                "pattern_type": getattr(case, "pattern_type", "通用形态"),
                "pattern_subtype": getattr(case, "pattern_subtype", "自定义"),
                "recognition_confidence": float(getattr(case, "recognition_confidence", 0.0) or 0.0),
                "human_confirmed": bool(getattr(case, "human_confirmed", False)),
                "learned": case.learned,
                "region_stats": case.region_stats,
                "created_at": case.created_at.isoformat() if hasattr(case.created_at, "isoformat") else str(case.created_at),
            }
        )
    matches.sort(key=lambda m: m["similarity"], reverse=True)
    return matches


# ---------- 完整形态特征向量 ----------


def smooth(values: list[float], window: int = 3) -> list[float]:
    arr = np.asarray(values, dtype=float)
    if window <= 1 or len(arr) <= window:
        return list(arr)
    kernel = np.ones(window) / window
    return list(np.convolve(arr, kernel, mode="same"))


def swing_indices(values: list[float], window: int = 2) -> tuple[list[int], list[int]]:
    highs: list[int] = []
    lows: list[int] = []
    n = len(values)
    for i in range(window, n - window):
        segment = values[i - window : i + window + 1]
        if values[i] == max(segment) and segment.count(values[i]) == 1:
            highs.append(i)
        if values[i] == min(segment) and segment.count(values[i]) == 1:
            lows.append(i)
    return highs, lows


def structure_signature(closes: list[float]) -> dict[str, Any]:
    highs, lows = swing_indices(closes, window=2)
    events: list[tuple[int, float, str]] = [(i, closes[i], "high") for i in highs] + [(i, closes[i], "low") for i in lows]
    events.sort(key=lambda e: e[0])
    labels: list[str] = []
    prev_high = None
    prev_low = None
    for _, price, kind in events:
        if kind == "high":
            if prev_high is not None:
                labels.append("高更高" if price > prev_high else "高更低")
            prev_high = price
        else:
            if prev_low is not None:
                labels.append("低更高" if price > prev_low else "低更低")
            prev_low = price
    last_swing = events[-1][2] if events else "无"
    return {
        "swing_high_count": len(highs),
        "swing_low_count": len(lows),
        "structure_sequence": labels[:12],
        "last_swing": last_swing,
        "last_high": prev_high,
        "last_low": prev_low,
    }


def compute_fingerprints(bars: list[dict[str, Any]]) -> dict[str, Any]:
    closes = [float(b["close"]) for b in bars]
    highs = [float(b["high"]) for b in bars]
    lows = [float(b["low"]) for b in bars]
    result: dict[str, Any] = {}
    for scale in (10, 20, 40, 60):
        sampled = downsample(smooth(closes, 3), scale)
        arr = np.asarray(sampled, dtype=float)
        std = float(arr.std())
        mean = float(arr.mean())
        result[f"scale_{scale}"] = {
            "closes_z": [0.0] * len(arr) if std < 1e-12 else [round(float((v - mean) / std), 6) for v in arr],
            "high_z": [0.0] * len(arr) if std < 1e-12 else [round(float((v - mean) / std), 6) for v in downsample(highs, scale)],
            "low_z": [0.0] * len(arr) if std < 1e-12 else [round(float((v - mean) / std), 6) for v in downsample(lows, scale)],
        }
    return result


def dtw_distance(
    x: list[float],
    y: list[float],
    weights_x: np.ndarray | None = None,
    weights_y: np.ndarray | None = None,
) -> float:
    a = np.asarray(x, dtype=float)
    b = np.asarray(y, dtype=float)
    if len(a) == 0 or len(b) == 0:
        return 1e9
    n, m = len(a), len(b)
    wx = weights_x if weights_x is not None else np.ones(n)
    wy = weights_y if weights_y is not None else np.ones(m)
    dp = np.full((n + 1, m + 1), np.inf)
    dp[0, 0] = 0.0
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = abs(a[i - 1] - b[j - 1]) * (wx[i - 1] + wy[j - 1]) / 2.0
            dp[i, j] = cost + min(dp[i - 1, j], dp[i, j - 1], dp[i - 1, j - 1])
    return float(dp[n, m]) / max(n, m)


def shape_similarity(a: dict[str, Any], b: dict[str, Any]) -> float:
    keys = [k for k in a.keys() if k.startswith("scale_") and k in b]
    if not keys:
        return similarity(a, b)
    scores = []
    for key in keys:
        n = len(a[key].get("closes_z", []))
        weights = edge_weights(n)
        corr = similarity(a[key], b[key], weights)
        dist = dtw_distance(a[key].get("closes_z", []), b[key].get("closes_z", []), weights_x=weights, weights_y=weights)
        dtw_score = 1.0 / (1.0 + dist)
        scores.append(0.6 * corr + 0.4 * dtw_score)
    return round(float(np.mean(scores)), 4)


def structure_similarity(fa: dict[str, Any], fb: dict[str, Any]) -> float:
    sa = fa.get("structure", {})
    sb = fb.get("structure", {})
    seq_a = sa.get("structure_sequence", [])
    seq_b = sb.get("structure_sequence", [])
    seq_score = 1.0 if seq_a == seq_b else 0.3 if seq_a and seq_b else 0.5
    count_diff = abs(sa.get("swing_high_count", 0) - sb.get("swing_high_count", 0)) + abs(
        sa.get("swing_low_count", 0) - sb.get("swing_low_count", 0)
    )
    count_score = max(0.0, 1.0 - count_diff * 0.15)
    last_score = 1.0 if sa.get("last_swing") == sb.get("last_swing") else 0.5
    return round(0.4 * seq_score + 0.35 * count_score + 0.25 * last_score, 4)


def level_similarity(fa: dict[str, Any], fb: dict[str, Any]) -> float:
    la = fa.get("levels", {})
    lb = fb.get("levels", {})
    score = 0.0
    score += 0.4 * (1.0 if la.get("support_count") == lb.get("support_count") else 0.5)
    score += 0.4 * (1.0 if la.get("resistance_count") == lb.get("resistance_count") else 0.5)
    da = abs(la.get("entry_to_support_pct", 0.0) - lb.get("entry_to_support_pct", 0.0))
    db = abs(la.get("entry_to_resistance_pct", 0.0) - lb.get("entry_to_resistance_pct", 0.0))
    score += 0.1 * max(0.0, 1.0 - da / 5.0)
    score += 0.1 * max(0.0, 1.0 - db / 5.0)
    return round(score, 4)


def market_similarity(fa: dict[str, Any], fb: dict[str, Any]) -> float:
    sa = fa.get("market", {})
    sb = fb.get("market", {})
    trend = 1.0 if sa.get("trend") == sb.get("trend") else 0.5
    vol = 1.0 if sa.get("volatility") == sb.get("volatility") else 0.5
    return round(0.5 * trend + 0.5 * vol, 4)


def weighted_similarity(fa: dict[str, Any], fb: dict[str, Any]) -> float:
    shape = shape_similarity(fa.get("fingerprints", {}), fb.get("fingerprints", {}))
    structure = structure_similarity(fa, fb)
    levels = level_similarity(fa, fb)
    market = market_similarity(fa, fb)
    return round(0.4 * shape + 0.3 * structure + 0.2 * levels + 0.1 * market, 4)


def extract_features(
    bars: list[dict[str, Any]],
    region: dict[str, Any],
    entry_points: list[dict[str, Any]],
    exit_points: list[dict[str, Any]],
    support_levels: list[dict[str, Any]],
    resistance_levels: list[dict[str, Any]],
) -> dict[str, Any]:
    df = pd.DataFrame(bars)
    closes = [float(v) for v in df["close"].tolist()]
    highs = [float(v) for v in df["high"].tolist()]
    lows = [float(v) for v in df["low"].tolist()]
    opens = [float(v) for v in df["open"].tolist()]
    volumes = [float(v) for v in df["volume"].tolist()]
    close_series = df["close"]
    last = float(closes[-1])
    first = float(closes[0])
    ret = (last / first - 1.0) * 100.0 if first else 0.0
    trend_slope = (last - first) / first * 1000.0 if first else 0.0

    structure = structure_signature(closes)
    rsi_series = rsi(close_series, 14)
    macd_line, macd_signal, macd_hist = macd(close_series)
    bb_upper, bb_mid, bb_lower = bollinger(close_series)
    adx = adx_series(df, 14)
    atr = atr_series(df, 14)
    ema_fast = ema(close_series, 12)
    ema_slow = ema(close_series, 26)

    bodies = [abs(c - o) for c, o in zip(closes, opens)]
    wicks = [abs(h - l) - b for h, l, b in zip(highs, lows, bodies)]
    doji_count = sum(1 for i, b in enumerate(bodies) if highs[i] - lows[i] > 0 and b / (highs[i] - lows[i]) < 0.1)
    long_upper = sum(1 for i in range(len(closes)) if highs[i] - max(opens[i], closes[i]) > bodies[i] * 2)
    long_lower = sum(1 for i in range(len(closes)) if min(opens[i], closes[i]) - lows[i] > bodies[i] * 2)
    bull_ratio = sum(1 for c, o in zip(closes, opens) if c > o) / len(closes) if closes else 0.0
    vol_5 = sum(volumes[-5:]) / 5 if len(volumes) >= 5 else 1.0
    vol_20 = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else vol_5
    vol_ratio = vol_5 / vol_20 if vol_20 else 1.0

    supports = [float(l["price"]) for l in support_levels]
    resistances = [float(l["price"]) for l in resistance_levels]
    range_high = max(highs)
    range_low = min(lows)
    price_position = (last - range_low) / (range_high - range_low) if range_high > range_low else 0.5
    range_atr = (range_high - range_low) / float(atr.iloc[-1]) if atr.iloc[-1] and atr.iloc[-1] > 0 else 0.0
    nearest_support = max([p for p in supports if p <= last], default=None)
    nearest_resistance = min([p for p in resistances if p >= last], default=None)
    support_distance = (last - nearest_support) / last * 100.0 if nearest_support else 0.0
    resistance_distance = (nearest_resistance - last) / last * 100.0 if nearest_resistance else 0.0

    timing = learn_timing(region, entry_points, exit_points, bars)
    stop_take = learn_stop_take(entry_points, exit_points, bars)
    levels_learned = learn_levels(region, entry_points, bars)
    swing_high_idx, swing_low_idx = swing_indices(closes)
    recent_pivot_distance = min(
        [abs(len(closes) - 1 - i) for i in swing_high_idx + swing_low_idx],
        default=0,
    )
    last_atr = float(atr.iloc[-1]) if atr.iloc[-1] and np.isfinite(atr.iloc[-1]) else 0.0
    vol_arr = np.asarray(volumes, dtype=float)
    obv = np.cumsum(np.where(np.diff(np.asarray(closes + [last], dtype=float)) > 0, vol_arr, -vol_arr))
    obv_slope = float(np.polyfit(np.arange(len(obv)), obv, 1)[0]) if len(obv) > 1 else 0.0

    return {
        "structure": {
            **structure,
            "bar_count": len(bars),
            "return_pct": round(ret, 4),
            "trend_slope": round(trend_slope, 4),
            "price_position": round(price_position, 4),
            "range_width_pct": levels_learned["range_width_pct"],
            "range_atr": round(range_atr, 4),
        },
        "levels": {
            "support_count": len(supports),
            "resistance_count": len(resistances),
            "support_touches": [int(l.get("touched", 0)) for l in support_levels],
            "resistance_touches": [int(l.get("touched", 0)) for l in resistance_levels],
            "support_distance_pct": round(support_distance, 4),
            "resistance_distance_pct": round(resistance_distance, 4),
            **levels_learned,
        },
        "candles": {
            "bull_ratio": round(bull_ratio, 4),
            "avg_body_pct": round(float(np.mean(bodies)) / last * 100.0, 4) if bodies else 0.0,
            "avg_wick_pct": round(float(np.mean(wicks)) / last * 100.0, 4) if wicks else 0.0,
            "long_upper": long_upper,
            "long_lower": long_lower,
            "doji_count": doji_count,
            "volume_ratio_5_20": round(vol_ratio, 4),
            "avg_range_atr": round(float(np.mean([(h - l) for h, l in zip(highs, lows)])) / last_atr if last_atr else 0.0, 4),
        },
        "indicators": {
            "rsi": round(float(rsi_series.iloc[-1]), 4) if pd.notna(rsi_series.iloc[-1]) else 0.0,
            "rsi_high": round(float(rsi_series.max()), 4) if pd.notna(rsi_series.max()) else 0.0,
            "rsi_low": round(float(rsi_series.min()), 4) if pd.notna(rsi_series.min()) else 0.0,
            "macd_line": round(float(macd_line.iloc[-1]), 6) if pd.notna(macd_line.iloc[-1]) else 0.0,
            "macd_signal": round(float(macd_signal.iloc[-1]), 6) if pd.notna(macd_signal.iloc[-1]) else 0.0,
            "macd_hist_change": round(float(macd_hist.iloc[-1] - macd_hist.iloc[-2]), 6) if len(macd_hist) > 1 else 0.0,
            "ema_diff_pct": round((float(ema_fast.iloc[-1]) - float(ema_slow.iloc[-1])) / last * 100.0, 4),
            "adx": round(float(adx.iloc[-1]), 4),
            "bb_width_pct": round((float(bb_upper.iloc[-1]) - float(bb_lower.iloc[-1])) / last * 100.0, 4),
            "bb_position": round((last - float(bb_lower.iloc[-1])) / (float(bb_upper.iloc[-1]) - float(bb_lower.iloc[-1])) if bb_upper.iloc[-1] > bb_lower.iloc[-1] else 0.5, 4),
            "obv_slope": round(obv_slope, 4),
        },
        "timing": {
            **timing,
            "duration_bars": len(bars),
            "duration_minutes": len(bars) * {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}.get(region.get("timeframe", "M15"), 15),
            "recent_pivot_distance": recent_pivot_distance,
        },
        "market": {
            "trend": "上升趋势" if ret > 0.5 else "下降趋势" if ret < -0.5 else "震荡",
            "volatility": "高波动" if last_atr / last > 0.001 else "低波动",
        },
        "execution": {
            "suggested_stop_pct": stop_take["suggested_stop_pct"],
            "suggested_take_pct": stop_take["suggested_take_pct"],
            "max_hold_bars": round(timing["avg_hold_bars"], 2),
            "entry_to_support_pct": levels_learned["entry_to_support_pct"],
            "entry_to_resistance_pct": levels_learned["entry_to_resistance_pct"],
        },
        "fingerprints": compute_fingerprints(bars),
        "normalized": {
            "close_z": compute_fingerprint(bars)["closes_z"],
            "high_z": downsample([round((v - float(np.mean(highs))) / (float(np.std(highs)) or 1), 6) for v in downsample(highs, 60)], 60),
            "low_z": downsample([round((v - float(np.mean(lows))) / (float(np.std(lows)) or 1), 6) for v in downsample(lows, 60)], 60),
            "range_position": [round((v - range_low) / (range_high - range_low), 4) if range_high > range_low else 0.5 for v in downsample(closes, 60)],
        },
    }


def classify_pattern(features: dict[str, Any]) -> dict[str, Any]:
    structure = features.get("structure", {})
    levels = features.get("levels", {})
    candles = features.get("candles", {})
    indicators = features.get("indicators", {})
    adx = indicators.get("adx", 0.0)
    price_position = structure.get("price_position", 0.5)
    support_dist = levels.get("support_distance_pct", 0.0)
    resistance_dist = levels.get("resistance_distance_pct", 0.0)
    swing_high = structure.get("swing_high_count", 0)
    swing_low = structure.get("swing_low_count", 0)
    range_atr = structure.get("range_atr", 0.0)
    volume_ratio = candles.get("volume_ratio_5_20", 1.0)
    trend_slope = structure.get("trend_slope", 0.0)
    rsi_val = indicators.get("rsi", 50.0)

    if swing_high >= 2 and swing_low >= 2 and price_position > 0.8 and volume_ratio > 1.3:
        return {"pattern_type": "突破", "pattern_subtype": "向上突破", "confidence": round(min(0.55 + volume_ratio * 0.15, 0.95), 2)}
    if trend_slope > 0.5 and support_dist > 0 and support_dist < 1.5 and 40 <= rsi_val <= 60:
        return {"pattern_type": "回踩", "pattern_subtype": "回踩支撑", "confidence": round(min(0.55 + (1.5 - support_dist) * 0.15, 0.92), 2)}
    if swing_high >= 2 and swing_low >= 2 and range_atr < 6:
        return {"pattern_type": "收敛三角形", "pattern_subtype": "对称收敛", "confidence": round(min(0.5 + (6 - range_atr) * 0.06, 0.9), 2)}
    if swing_low >= 2 and adx < 22 and levels.get("support_count", 0) >= 1 and levels.get("resistance_count", 0) >= 1:
        return {"pattern_type": "区间震荡", "pattern_subtype": "支撑压力区间", "confidence": round(min(0.5 + levels.get("support_count", 0) * 0.1, 0.9), 2)}
    if trend_slope > 1.5 and 0.45 <= price_position <= 0.7:
        return {"pattern_type": "趋势中继", "pattern_subtype": "上升通道", "confidence": 0.7}
    return {"pattern_type": "通用形态", "pattern_subtype": "自定义", "confidence": round(max(0.4, 0.55 - abs(price_position - 0.5) * 0.2), 2)}
