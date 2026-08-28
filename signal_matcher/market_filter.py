"""行情过滤门：按行情环境判定是否允许开仓（matcher 因子适配与 executor 全局过滤共用）。

条件词汇沿用因子 market_adapt 的约定（trends / volatility / volume_state /
macro_direction / min_adx / atr_min / atr_max / symbols），并支持复数别名
（volatilities / volume_states / macro_directions），另加全局环境评分下限
min_environment_score。未列出的维度不限；行情数据缺失时放行（与既有行为一致）。
"""

from __future__ import annotations

from typing import Any


def matches_market_filter(conditions: dict[str, Any], market: dict[str, Any]) -> bool:
    """判定当前行情 market 是否满足开仓条件 conditions。"""
    if not conditions:
        return True
    if not market:
        return True

    trend = str(market.get("trend_direction") or "flat")
    volatility = str(market.get("volatility") or "")
    volume_state = str(market.get("volume_state") or "")
    macro = str(market.get("macro_trend") or "flat")
    adx = float(market.get("adx") or 0.0)
    atr_pct = float(market.get("atr_percentile") or 50.0)
    env_score = float(market.get("environment_score") or 0.0)
    symbol = str(market.get("symbol") or "")

    allowed_trends = conditions.get("trends") or conditions.get("allowed_trends") or []
    if isinstance(allowed_trends, str):
        allowed_trends = [allowed_trends]
    if allowed_trends and trend not in allowed_trends:
        return False

    allowed_vol = (
        conditions.get("volatility")
        or conditions.get("allowed_volatility")
        or conditions.get("volatilities")
        or []
    )
    if isinstance(allowed_vol, str):
        allowed_vol = [allowed_vol]
    if allowed_vol and volatility not in allowed_vol:
        return False

    allowed_volume = conditions.get("volume_state") or conditions.get("volume_states") or []
    if isinstance(allowed_volume, str):
        allowed_volume = [allowed_volume]
    if allowed_volume and volume_state not in allowed_volume:
        return False

    legacy_macro = conditions.get("macro_direction")
    if isinstance(legacy_macro, str) and legacy_macro:
        if legacy_macro == "long" and macro != "up":
            return False
        if legacy_macro == "short" and macro != "down":
            return False
    allowed_macro = conditions.get("macro_directions") or []
    if isinstance(allowed_macro, str):
        allowed_macro = [allowed_macro]
    if allowed_macro and macro not in allowed_macro:
        return False

    # 多周期层方向硬过滤（mtf_directions：{"d1": ["up"], "h4": ["down"]}）
    # 已配置的层方向不满足即拦截；未配置的层不限；key 对应 multi_timeframe.layers 的 key
    mtf_cond = conditions.get("mtf_directions") or {}
    if isinstance(mtf_cond, str):
        mtf_cond = {}
    if isinstance(mtf_cond, dict) and mtf_cond:
        mtf = market.get("multi_timeframe") or {}
        layer_map = {
            str(l.get("key") or "").lower(): str(l.get("direction") or "flat")
            for l in (mtf.get("layers") or [])
        }
        for key, allowed in mtf_cond.items():
            allowed_list = allowed if isinstance(allowed, list) else [allowed]
            if str(layer_map.get(str(key).lower()) or "flat") not in [str(a) for a in allowed_list]:
                return False

    min_adx = float(conditions.get("min_adx", 0) or 0)
    if min_adx > adx:
        return False

    atr_min = float(conditions.get("atr_min", 0) or 0)
    atr_max = float(conditions.get("atr_max", 100) or 100)
    if not (atr_min <= atr_pct <= atr_max):
        return False

    min_env_score = float(conditions.get("min_environment_score", 0) or 0)
    if min_env_score > env_score:
        return False

    allowed_symbols = conditions.get("symbols") or []
    if isinstance(allowed_symbols, str):
        allowed_symbols = [allowed_symbols]
    if allowed_symbols and symbol and symbol not in allowed_symbols:
        return False

    return True