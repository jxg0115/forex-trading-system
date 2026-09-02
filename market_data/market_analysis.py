"""行情分析引擎：趋势、波动、量能、位置与大周期背景。"""

from __future__ import annotations

import time
from typing import Any

import pandas as pd

from indicators.technical import adx_series, atr_series
from signal_matcher.market_regime import classify

# 锚周期自适应层序（黄金主周期场景上限 D1）
LAYER_ORDER = ["M1", "M5", "M15", "M30", "H1", "H4", "D1"]


def _mtf_arrow(direction: str) -> str:
    return "▲" if direction == "up" else "▼" if direction == "down" else "→"


def _mtf_layer(df: pd.DataFrame, timeframe: str, main: bool = False) -> dict[str, Any]:
    """单层方向判定（黄金自适应）：EMA50 + 0.5×ATR50 死区；样本不足自动降级（<20 根标数据不足）。"""
    n = len(df)
    close = df["close"].astype(float)
    if n < 20:
        return {
            "key": timeframe.lower(), "timeframe": timeframe, "direction": "flat",
            "count": 0, "label": "数据不足", "volatility": "--", "adx": 0.0,
            "main": main, "insufficient": True,
        }
    ema = close.ewm(span=min(50, max(10, n - 1)), adjust=False).mean()
    atr_vals = atr_series(df, 14).dropna()
    atr = float(atr_vals.iloc[-1]) if len(atr_vals) and n >= 25 else None
    deadband = 0.5 * atr if atr else float(close.iloc[-1]) * 0.005  # ATR 不足时 0.5% 保底
    last = float(close.iloc[-1])
    ema_last = float(ema.iloc[-1])
    if last > ema_last + deadband:
        direction = "up"
    elif last < ema_last - deadband:
        direction = "down"
    else:
        direction = "flat"
    count = 0
    for i in range(n - 1, -1, -1):
        c = float(close.iloc[i])
        e = float(ema.iloc[i])
        if direction == "up":
            same = c > e + deadband
        elif direction == "down":
            same = c < e - deadband
        else:
            same = abs(c - e) <= deadband
        if not same:
            break
        count += 1
    adx_vals = adx_series(df, 14).dropna()
    adx = float(adx_vals.iloc[-1]) if len(adx_vals) else 0.0
    pcts = (atr_series(df, 14) / close).dropna()
    if len(pcts):
        q = float((pcts <= pcts.iloc[-1]).mean() * 100.0)
        vol = "高波动" if q >= 75 else "低波动" if q <= 25 else "中等波动"
    else:
        vol = "中等波动"
    label = "上行趋势" if direction == "up" else "下行趋势" if direction == "down" else "震荡"
    return {
        "key": timeframe.lower(), "timeframe": timeframe, "direction": direction,
        "count": count, "label": label, "volatility": vol, "adx": round(adx, 1),
        "main": main, "insufficient": False,
    }


class MarketAnalysisEngine:
    def __init__(self, market: Any) -> None:
        self.market = market
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}

    def analyze(self, symbol: str, timeframe: str = "M15") -> dict[str, Any]:
        key = f"{symbol}:{timeframe}"
        now = time.time()
        cached = self._cache.get(key)
        if cached and now - cached[0] < 30:
            return cached[1]

        bars = self.market.get_bars(symbol, timeframe, limit=300) or []
        if not bars:
            result = {
                "symbol": symbol,
                "timeframe": timeframe,
                "trend": "无行情",
                "trend_direction": "flat",
                "trend_strength": 0.0,
                "adx": 0.0,
                "atr": 0.0,
                "atr_percentile": 50.0,
                "volatility": "中等波动",
                "volume_state": "正常",
                "position_state": "区间中",
                "macro_trend": "flat",
                "multi_timeframe": {
                    "anchor": timeframe,
                    "layers": [],
                    "consensus": "不明",
                    "expand_reason": "",
                    "summary": "",
                },
                "environment_score": 50.0,
                "label": "无行情数据",
                "detail": "暂无 K 线数据",
            }
            self._cache[key] = (now, result)
            return result

        df = pd.DataFrame(bars)
        for col in ("open", "high", "low", "close", "volume"):
            df[col] = df[col].astype(float)

        regime = classify(df)
        close = float(df["close"].iloc[-1])
        atr_series_vals = atr_series(df, 14).dropna()
        atr = float(atr_series_vals.iloc[-1]) if len(atr_series_vals) else close * 0.002
        atr_percentile = float((atr_series_vals <= atr).mean() * 100.0) if len(atr_series_vals) else 50.0
        adx_vals = adx_series(df, 14).dropna()
        adx = float(adx_vals.iloc[-1]) if len(adx_vals) else 0.0

        vols = df["volume"]
        vol_ma = float(vols.tail(20).mean()) or 1.0
        recent_vol = float(vols.tail(5).mean())
        vol_ratio = recent_vol / vol_ma if vol_ma > 0 else 1.0
        if vol_ratio >= 1.2:
            volume_state = "放量"
        elif vol_ratio <= 0.8:
            volume_state = "缩量"
        else:
            volume_state = "正常"

        window = df.tail(100)
        high = float(window["high"].max())
        low = float(window["low"].min())
        if high > low:
            pos = (close - low) / (high - low)
            if pos >= 0.75:
                position_state = "高位"
            elif pos <= 0.25:
                position_state = "低位"
            else:
                position_state = "区间中"
        else:
            position_state = "区间中"

        macro_trend = "flat"
        macro_bars = self.market.get_bars(symbol, "H4", limit=250) or []
        if macro_bars:
            macro_df = pd.DataFrame(macro_bars)
            macro_close = macro_df["close"].astype(float)
            ema200 = macro_close.ewm(span=200, adjust=False).mean()
            if float(ema200.iloc[-1]) > 0:
                if float(macro_close.iloc[-1]) > float(ema200.iloc[-1]) * 1.002:
                    macro_trend = "up"
                elif float(macro_close.iloc[-1]) < float(ema200.iloc[-1]) * 0.998:
                    macro_trend = "down"

        # ---- 锚周期自适应多周期：主层 + 小佐证层 + 向上扩展直到方向明确 ----
        anchor_tf = str(timeframe or "M15").upper()
        if anchor_tf not in LAYER_ORDER:
            anchor_tf = "M15"
        ai = LAYER_ORDER.index(anchor_tf)
        layers: list[dict[str, Any]] = []
        main_layer = _mtf_layer(df, anchor_tf, main=True)
        layers.append(main_layer)
        if ai > 0:
            small_tf = LAYER_ORDER[ai - 1]
            small_bars = self.market.get_bars(symbol, small_tf, limit=200) or []
            if small_bars:
                layers.append(_mtf_layer(pd.DataFrame(small_bars), small_tf))
        anchor_dir = main_layer["direction"]
        confirm_layer = ""
        base_layer = ""
        expand_parts: list[str] = []
        for tf_name in LAYER_ORDER[ai + 1 :]:
            if tf_name == "H4":
                tf_bars = macro_bars  # 复用已拉取的 H4
            else:
                tf_bars = self.market.get_bars(symbol, tf_name, limit=200) or []
            if not tf_bars:
                expand_parts.append(f"{tf_name}无数据")
                continue
            layer = _mtf_layer(pd.DataFrame(tf_bars), tf_name)
            layers.append(layer)
            if layer["insufficient"]:
                expand_parts.append(f"{tf_name}数据不足（降级跳过）")
                continue
            if layer["direction"] == "flat":
                expand_parts.append(f"{tf_name}方向未明确")
                continue
            if anchor_dir != "flat" and layer["direction"] == anchor_dir:
                # 记录确认层，但不停：继续向上扩展，保证 H4/D1 层完整输出，
                # 供行情过滤的 mtf_directions（H4/D1 共振）真实检查。
                if not confirm_layer:
                    confirm_layer = tf_name
                    expand_parts.append(f"{tf_name}与主层同向，方向确认（继续向上扩展）")
                continue
            if anchor_dir == "flat" and not base_layer:
                base_layer = tf_name
                expand_parts.append(f"{tf_name}定义当前方向（主层等待入场）")
                continue
            expand_parts.append(f"{tf_name}与主层相反（向上寻求裁定）")
        if confirm_layer:
            consensus = "明确"
        elif anchor_dir == "flat" and base_layer:
            consensus = f"方向由{base_layer}定义"
        elif anchor_dir != "flat" and any(
        l["direction"] not in ("flat",) and not l["insufficient"] and l["direction"] != anchor_dir
        for l in layers if not l.get("main")
    ):
            consensus = "矛盾"
        else:
            consensus = "不明"
        parts = []
        for l in layers:
            star = "★" if l.get("main") else ""
            extra = f"({l['count']})" if l["direction"] in ("up", "down") else ""
            parts.append(f"{l['timeframe']}{_mtf_arrow(l['direction'])}{star}{extra}")
        summary = " · ".join(parts)
        expand_reason = "；".join(expand_parts) if expand_parts else "无需向上扩展"

        name = regime.name
        if "上行" in name:
            trend_direction = "up"
        elif "下行" in name:
            trend_direction = "down"
        elif name == "震荡":
            trend_direction = "range"
        else:
            trend_direction = "range" if adx < 20 else "flat"

        trend_strength = min(max(adx / 40.0, 0.0), 1.0)
        env_score = round(min(95.0, 50 + trend_strength * 25 + atr_percentile / 20), 1)
        label = f"{name} / {regime.volatility} / {volume_state} / 大周期{macro_trend}"

        result = {
            "symbol": symbol,
            "timeframe": timeframe,
            "trend": name,
            "trend_direction": trend_direction,
            "trend_strength": round(trend_strength, 3),
            "adx": round(adx, 1),
            "atr": round(atr, 6),
            "atr_percentile": round(atr_percentile, 1),
            "volatility": regime.volatility,
            "volume_state": volume_state,
            "position_state": position_state,
            "macro_trend": macro_trend,
            "multi_timeframe": {
                "anchor": anchor_tf,
                "layers": layers,
                "consensus": consensus,
                "expand_reason": expand_reason,
                "summary": summary,
            },
            "environment_score": env_score,
            "label": label,
            "detail": regime.detail,
        }
        self._cache[key] = (now, result)
        return result
