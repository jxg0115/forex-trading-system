"""止损止盈推荐值：基于当前品种与周期的实时行情/指标自动生成推荐参数（R/ATR 单位）。

对应 docs/止损止盈重构设计方案.md 定稿：
- 输入：symbol + timeframe + MarketAnalysisEngine 实时行情（趋势/ADX/波动/环境评分）；
- 规则：强趋势 → 放宽追踪、延迟阶梯、关闭分批、拉长持有；震荡 → 提前锁利、快速分批、缩短持有；
        高波动 → 放宽回落容忍；低波动 → 收紧；
- 输出：完整 SltpPolicyConfig 字典（前端可直接填入表单），附带行情快照与推荐理由。
"""

from __future__ import annotations

from typing import Any

from oems.sltp_policy import SltpPolicyConfig


def recommend_sltp(symbol: str, timeframe: str, market_analysis: Any = None) -> dict[str, Any]:
    """按当前品种/周期的实时行情与指标生成推荐政策配置。"""
    cfg = SltpPolicyConfig()
    base = cfg.to_dict()

    market: dict[str, Any] = {}
    if market_analysis:
        try:
            market = market_analysis.analyze(symbol, timeframe)
        except Exception:
            market = {}

    trend = str(market.get("trend_direction") or "flat")
    adx = float(market.get("adx") or 0.0)
    volatility = str(market.get("volatility") or "中等波动")
    score = float(market.get("environment_score") or 0.0)
    strong = adx >= cfg.adx_strong and trend in ("up", "down")
    range_regime = "震荡" in str(market.get("trend") or "")
    high_vol = "高波动" in volatility
    low_vol = "低波动" in volatility

    reasons: list[str] = []

    # —— 止盈激进/保守基调 ——
    if strong:
        base["take_r_mult"] = 3.0
        base["breakeven_trigger_r"] = 0.6
        base["trailing_stop_atr"] = 1.5
        base["trailing_take_atr"] = 0.8
        base["ladder_tiers"] = [
            {"trigger_r": 1.2, "raise_to_r": 0.0, "gate": {}},
            {"trigger_r": 2.5, "raise_to_r": 1.2, "gate": {}},
            {"trigger_r": 4.0, "raise_to_r": 2.5, "gate": {}},
        ]
        base["partial_close_enabled"] = False
        base["time_stop_bars"] = 180
        reasons.append("强趋势延续 → 放宽追踪、阶梯缓进、关闭分批、拉长持有（让利润奔跑）")
    elif range_regime:
        base["take_r_mult"] = 2.0
        base["risk_mult"] = 1.2
        base["breakeven_trigger_r"] = 0.4
        base["trailing_stop_atr"] = 0.8
        base["trailing_take_atr"] = 0.4
        base["ladder_tiers"] = [
            {"trigger_r": 0.8, "raise_to_r": 0.2, "gate": {}},
            {"trigger_r": 1.5, "raise_to_r": 0.8, "gate": {}},
            {"trigger_r": 2.5, "raise_to_r": 1.5, "gate": {}},
        ]
        base["partial_close_tiers"] = [
            {"trigger_r": 1.0, "close_pct": 40, "gate": {}},
            {"trigger_r": 1.6, "close_pct": 30, "gate": {}},
            {"trigger_r": 2.2, "close_pct": 40, "gate": {}},
        ]
        base["time_stop_bars"] = 90
        reasons.append("震荡整理 → 提前锁利、阶梯快进、分批加速、缩短持有")
    else:
        reasons.append("趋势中性 → 常规标准参数")

    # —— 波动维度：回落容忍 ——
    if high_vol:
        base["ladder_retrace_atr"] = 1.2
        base["hw_retrace_atr"] = 2.0
        reasons.append("高波动 → 放宽回落容忍（防假突破扫损）")
    elif low_vol:
        base["ladder_retrace_atr"] = 0.8
        base["hw_retrace_atr"] = 1.0
        reasons.append("低波动 → 收紧回落容忍（价格迟钝、尽快锁利）")
    else:
        base["ladder_retrace_atr"] = 1.0
        base["hw_retrace_atr"] = 1.5

    # —— 环境评分兜底说明 ——
    if score >= 0.7:
        reasons.append(f"多维度共振（环境评分 {score:.2f}）→ 支持顺势持仓")
    elif score <= 0.3:
        reasons.append(f"环境评分偏低（{score:.2f}）→ 建议保守档位或控制仓位")

    base["symbol"] = symbol
    base["timeframe"] = timeframe
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "market": market,
        "config": base,
        "reason": "；".join(reasons),
    }