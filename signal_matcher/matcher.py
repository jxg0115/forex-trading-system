"""因子激活共振引擎：扫描因子库，输出按置信度排序的匹配候选。"""

from __future__ import annotations

from typing import Any

import pandas as pd

from ai_engine.pattern import compute_fingerprint, extract_features, match_cases, swing_indices
from backtest_store.repository import FactorRepository
from market_data.service import MarketDataService
from models.factor import MatcherCandidate
from sandbox.ast_check import check_source
from sandbox.runner import execute_factor
from signal_matcher.market_filter import matches_market_filter
from signal_matcher.market_regime import classify


def mtf_soft_adjust(signal: str, market_analysis: dict[str, Any]) -> tuple[float, str]:
    """多周期软过滤（黄金回调窗增强）：逆 D1/H4 温和降置信、顺势加分、
    主层获 D1 支持且中间层刚回调 → 回调接单窗加分。返回 (Δ置信, 原因)。"""
    mtf = (market_analysis or {}).get("multi_timeframe") or {}
    layers = {str(l.get("key") or "").lower(): l for l in (mtf.get("layers") or [])}
    if not layers or signal not in ("long", "short"):
        return 0.0, ""
    is_up = signal == "long"
    d1 = layers.get("d1") or {}
    h4 = layers.get("h4") or {}
    d1_dir = str(d1.get("direction") or "flat")
    h4_dir = str(h4.get("direction") or "flat")
    d1_count = int(d1.get("count") or 0)
    h4_count = int(h4.get("count") or 0)
    delta = 0.0
    parts: list[str] = []
    if d1_dir in ("up", "down"):
        if (d1_dir == "up") == is_up:
            delta += 0.04
            parts.append("顺 D1")
        else:
            delta -= 0.08
            parts.append("逆 D1（回调接单小幅扣）")
    if h4_dir in ("up", "down"):
        if (h4_dir == "up") == is_up:
            delta += 0.03
            parts.append("顺 H4")
        else:
            delta -= 0.05
            parts.append("逆 H4")
    # 黄金回调接单窗：D1 成熟(count≥20) 且 H4 刚回调(count≤5、与 D1 反向)
    if d1_count >= 20 and h4_dir != "flat" and h4_dir != d1_dir and h4_count <= 5:
        delta += 0.05
        parts.append("黄金回调接单窗（D1 成熟 + H4 刚回调）")
    return round(delta, 3), "、".join(parts)


class MatcherService:
    def __init__(
        self,
        market: MarketDataService,
        repository: FactorRepository,
        model_pool=None,
        market_analysis=None,
        pattern_min_similarity: float = 0.85,
        pattern_min_samples: int = 0,
        pattern_time_decay_days: int = 365,
    ) -> None:
        self.market = market
        self.repository = repository
        self.model_pool = model_pool
        self.market_analysis = market_analysis
        self.pattern_min_similarity = pattern_min_similarity
        self.pattern_min_samples = pattern_min_samples
        self.pattern_time_decay_days = pattern_time_decay_days

    def set_pattern_config(
        self,
        min_similarity: float = 0.85,
        min_samples: int = 0,
        time_decay_days: int = 365,
    ) -> None:
        self.pattern_min_similarity = min_similarity
        self.pattern_min_samples = min_samples
        self.pattern_time_decay_days = time_decay_days

    def scan(self, symbol: str, timeframe: str, limit: int = 300) -> dict[str, Any]:
        # 开启实盘时选定品种，就用因子库里的全部启用因子在选定品种上匹配。
        factors = self.repository.list_factors(status="active")
        df = self.market.dataframe(symbol, timeframe, limit)
        bars = self.market.get_bars(symbol, timeframe, limit)
        if not bars:
            return {
                "symbol": symbol,
                "timeframe": timeframe,
                "regime": "无行情",
                "regime_detail": "MT5 未连接或暂无 K 线数据",
                "macro_trend": "flat",
                "candidates": [],
                "pattern_matches": [],
                "errors": ["无行情数据，跳过扫描"],
                "scanned": 0,
            }
        regime = classify(df)
        macro_trend = self._macro_trend(symbol)
        market_analysis: dict[str, Any] = {}
        if self.market_analysis:
            try:
                market_analysis = self.market_analysis.analyze(symbol, timeframe)
            except Exception:
                market_analysis = {}
        candidates: list[MatcherCandidate] = []
        errors: list[str] = []

        for factor in factors:
            adapt = getattr(factor, "market_adapt", None) or {}
            if adapt and not self._matches_market(adapt, market_analysis):
                errors.append(f"{factor.name}：行情不匹配（{market_analysis.get('label', '无行情')}），已跳过")
                continue
            sandbox = check_source(factor.code)
            if not sandbox.ok:
                errors.append(f"{factor.name}：沙盒检查未通过，已跳过")
                continue
            execution = execute_factor(factor.code, bars, factor.params)
            if not execution.ok or not execution.entry_values:
                errors.append(f"{factor.name}：因子执行失败（{execution.message}）")
                continue
            signals = execution.entry_values
            recent = [s for s in signals[-5:] if s not in (None, 0)]
            if not recent:
                continue
            last = recent[-1]
            signal = "long" if last > 0 else "short" if last < 0 else "none"
            regime_bonus = self._regime_bonus(factor.tags, regime.name)
            strength = min(abs(float(last)), 1.0)
            confidence = round(min(0.35 + 0.45 * strength + regime_bonus, 0.99), 3)
            reasons = [
                f"市场环境：{regime.name}（{regime.volatility}，ATR 分位 {regime.atr_percentile:.0f}%）",
                f"最近信号强度：{strength:.2f}",
            ]
            reasons.append(f"全场因子匹配：{factor.name}（原品种 {factor.symbol}）已在 {symbol} 上扫描")
            if market_analysis:
                mtf_delta, mtf_reason = mtf_soft_adjust(signal, market_analysis)
                if mtf_delta:
                    confidence = round(min(max(0.35 + 0.45 * strength + regime_bonus + mtf_delta, 0.0), 0.99), 3)
                    reasons.append(f"多周期修正：{mtf_reason}（{mtf_delta:+.2f}）")
                reasons.append(f"行情适配：{market_analysis.get('label', '')}")
            if regime_bonus > 0:
                reasons.append(f"与因子标签「{factor.tags[0]}」匹配当前市场环境")
            candidates.append(
                MatcherCandidate(
                    factor_id=factor.id,
                    factor_name=factor.name,
                    signal=signal,  # type: ignore[arg-type]
                    confidence=confidence,
                    regime=regime.name,
                    reasons=reasons,
                    support_levels=self._level_prices(factor, "support_levels"),
                    resistance_levels=self._level_prices(factor, "resistance_levels"),
                )
            )

        if self.model_pool is not None:
            for model_name in self.model_pool.trained:
                prediction = self.model_pool.predict(model_name, df)
                if prediction.get("signal") in ("long", "short"):
                    candidates.append(
                        MatcherCandidate(
                            factor_id=f"model:{model_name}",
                            factor_name=f"ML 模型：{model_name}",
                            signal=prediction["signal"],  # type: ignore[arg-type]
                            confidence=float(prediction.get("confidence", 0.5)),
                            regime=regime.name,
                            reasons=[f"ML 模型 {model_name} 预测概率 {prediction.get('probability', prediction.get('z_score', 0))}"],
                        )
                    )

        pattern_matches: list[dict[str, Any]] = []
        cases = self.repository.list_pattern_cases(symbol=symbol, timeframe=timeframe)
        if cases and len(df) >= 20:
            closes_list = [float(b["close"]) for b in bars]
            highs_idx, lows_idx = swing_indices(closes_list)
            pivots = sorted(set(highs_idx + lows_idx))
            recent_pivots = [p for p in pivots if p >= len(bars) - 80]
            start_idx = max(0, len(bars) - 60)
            if len(recent_pivots) >= 2:
                candidate_start = recent_pivots[-2]
                width = len(bars) - candidate_start
                if 25 <= width <= 80:
                    start_idx = candidate_start
            recent_bars = [
                {
                    "time": b["time"],
                    "open": float(b["open"]),
                    "high": float(b["high"]),
                    "low": float(b["low"]),
                    "close": float(b["close"]),
                    "volume": float(b["volume"]),
                }
                for b in bars[start_idx:]
            ]
            window_pivots = [p - start_idx for p in recent_pivots if start_idx <= p < len(bars)]
            last_pivot_in_window = max(window_pivots, default=len(recent_bars) - 1)
            bars_since_pivot = len(recent_bars) - 1 - last_pivot_in_window
            pattern_completion = round(min(1.0, max(0.0, bars_since_pivot / 4.0)), 4)
            current_fp = compute_fingerprint(recent_bars)
            current_features = extract_features(
                recent_bars,
                {
                    "time_start": recent_bars[0]["time"],
                    "time_end": recent_bars[-1]["time"],
                    "timeframe": timeframe,
                    "price_top": max(b["high"] for b in recent_bars),
                    "price_bottom": min(b["low"] for b in recent_bars),
                },
                [],
                [],
                [],
                [],
            )
            current_features["timing"]["pattern_completion"] = pattern_completion
            current_profile = {"fingerprint": current_fp, "features": current_features}
            matched = match_cases(
                current_profile,
                cases,
                min_similarity=self.pattern_min_similarity,
                min_samples=self.pattern_min_samples,
                regime=regime.name,
                decay_days=self.pattern_time_decay_days,
            )
            for item in matched:
                factor_name = ""
                if item["factor_id"]:
                    factor = self.repository.get_factor(item["factor_id"])
                    factor_name = factor.name if factor else ""
                pattern_matches.append({**item, "factor_name": factor_name})
                for cand in candidates:
                    if item["factor_id"] and cand.factor_id == item["factor_id"]:
                        cand.confidence = min(cand.confidence + 0.1, 0.99)
                        cand.reasons.append(f"与历史形态相似度 {item['similarity']:.0%}（样本 {item['sample_count']}），已增强信号")
        pattern_matches.sort(key=lambda m: m["similarity"], reverse=True)

        suggestions: dict[str, dict[str, float]] = {}
        for item in pattern_matches:
            factor_id = item.get("factor_id")
            if not factor_id:
                continue
            similarity = float(item.get("similarity") or 0.0)
            learned = item.get("learned") or {}
            stop_pct = float(learned.get("suggested_stop_pct") or 0.0)
            take_pct = float(learned.get("suggested_take_pct") or 0.0)
            if stop_pct <= 0 or take_pct <= 0:
                continue
            agg = suggestions.setdefault(factor_id, {"stop": 0.0, "take": 0.0, "weight": 0.0})
            agg["stop"] += stop_pct * similarity
            agg["take"] += take_pct * similarity
            agg["weight"] += similarity
        for cand in candidates:
            agg = suggestions.get(cand.factor_id)
            if agg and agg["weight"] > 0:
                cand.suggested_stop_pct = round(agg["stop"] / agg["weight"], 6)
                cand.suggested_take_pct = round(agg["take"] / agg["weight"], 6)

        candidates.sort(key=lambda c: c.confidence, reverse=True)
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "regime": regime.name,
            "regime_detail": regime.detail,
            "macro_trend": macro_trend,
            "market_analysis": market_analysis,
            "candidates": [c.model_dump() for c in candidates[:10]],
            "pattern_matches": pattern_matches[:5],
            "errors": errors,
            "scanned": len(factors),
        }

    @staticmethod
    def _matches_market(adapt: dict[str, Any], market: dict[str, Any]) -> bool:
        return matches_market_filter(adapt, market)

    @staticmethod
    def _regime_bonus(tags: list[str], regime: str) -> float:
        mapping = {
            ("震荡", "mean_reversion"): 0.18,
            ("强趋势", "breakout"): 0.18,
            ("强趋势", "pullback"): 0.15,
            ("弱趋势", "pullback"): 0.08,
            ("高波动", "volatility"): 0.15,
        }
        for tag in tags:
            if (regime, tag) in mapping:
                return mapping[(regime, tag)]
        return 0.0

    def _macro_trend(self, symbol: str) -> str:
        bars = self.market.get_bars(symbol, "H4", 250)
        if not bars:
            return "flat"
        closes = pd.Series([float(b["close"]) for b in bars])
        ma = closes.ewm(span=200, adjust=False).mean().iloc[-1]
        last = float(closes.iloc[-1])
        if last > ma * 1.002:
            return "up"
        if last < ma * 0.998:
            return "down"
        return "flat"


    @staticmethod
    def _level_prices(factor, key: str) -> list[float]:
        """从因子保存时的形态快照提取支撑位/压力位价格。"""
        snapshot = getattr(factor, "prompt_snapshot", None) or {}
        levels = snapshot.get(key, []) or []
        prices: list[float] = []
        for level in levels:
            try:
                price = float(level["price"])
            except (KeyError, TypeError, ValueError):
                continue
            if price > 0:
                prices.append(price)
        return prices
