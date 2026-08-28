"""因子生成器：LLM 逆向 + 模拟模板兜底，输出经过沙盒验证的因子草稿。"""

from __future__ import annotations

import asyncio
from typing import Any

from ai_engine.client import LLMClient
from ai_engine.prompts import SYSTEM_PROMPT, build_factor_prompt, extract_python_code
from ai_engine.region import analyze_region
from models.factor import ChartRegion, FactorDraft
from sandbox.ast_check import check_source
from sandbox.runner import execute_factor

_BREAKOUT_TEMPLATE = """\
import pandas as pd
import numpy as np

def calculate(df, params):
    lookback = int(params.get("lookback", 20))
    atr_period = int(params.get("atr_period", 14))
    k = float(params.get("k", 1.2))
    close = df["close"]
    high = df["high"].rolling(lookback).max().shift(1)
    low = df["low"].rolling(lookback).min().shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - close.shift(1)).abs(),
        (df["low"] - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(atr_period).mean()
    entry = pd.Series(0.0, index=df.index)
    entry.loc[close > high + k * atr] = 1.0
    entry.loc[close < low - k * atr] = -1.0
    return {"entry": entry}
"""

_PULLBACK_TEMPLATE = """\
import pandas as pd
import numpy as np

def calculate(df, params):
    fast = int(params.get("fast", 10))
    slow = int(params.get("slow", 50))
    rsi_period = int(params.get("rsi_period", 14))
    close = df["close"]
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(rsi_period).mean()
    loss = (-delta.clip(upper=0)).rolling(rsi_period).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    entry = pd.Series(0.0, index=df.index)
    uptrend = ema_fast > ema_slow
    downtrend = ema_fast < ema_slow
    entry.loc[uptrend & (rsi < 45) & (close < ema_fast)] = 1.0
    entry.loc[downtrend & (rsi > 55) & (close > ema_fast)] = -1.0
    return {"entry": entry}
"""

_MEAN_REVERSION_TEMPLATE = """\
import pandas as pd
import numpy as np

def calculate(df, params):
    period = int(params.get("period", 20))
    k = float(params.get("k", 2.0))
    close = df["close"]
    ma = close.rolling(period).mean()
    std = close.rolling(period).std()
    lower = ma - k * std
    upper = ma + k * std
    entry = pd.Series(0.0, index=df.index)
    entry.loc[close < lower] = 1.0
    entry.loc[close > upper] = -1.0
    return {"entry": entry}
"""

_VOLATILITY_TEMPLATE = """\
import pandas as pd
import numpy as np

def calculate(df, params):
    period = int(params.get("period", 20))
    z_threshold = float(params.get("z_threshold", 1.2))
    atr_period = int(params.get("atr_period", 14))
    close = df["close"]
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - close.shift(1)).abs(),
        (df["low"] - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(atr_period).mean()
    atr_ma = atr.rolling(period).mean()
    atr_std = atr.rolling(period).std().replace(0, np.nan)
    atr_z = (atr - atr_ma) / atr_std
    direction = np.sign(close.diff(5))
    entry = pd.Series(0.0, index=df.index)
    expansion = atr_z > z_threshold
    entry.loc[expansion & (direction > 0)] = 1.0
    entry.loc[expansion & (direction < 0)] = -1.0
    return {"entry": entry}
"""

TEMPLATES: dict[str, tuple[str, str]] = {
    "breakout": (_BREAKOUT_TEMPLATE, "突破加速"),
    "pullback": (_PULLBACK_TEMPLATE, "趋势回踩"),
    "mean_reversion": (_MEAN_REVERSION_TEMPLATE, "均值回归"),
    "volatility": (_VOLATILITY_TEMPLATE, "波动扩张"),
}


def _iso(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


class SimulatedFactorGenerator:
    """根据框选区域统计特征，选择模板并推断参数，离线生成因子。"""

    def generate(self, region: ChartRegion, stats, entry_points, exit_points) -> FactorDraft:
        template_key = self._choose_template(stats)
        code = TEMPLATES[template_key][0]
        template_name = TEMPLATES[template_key][1]
        params = self._infer_params(template_key, stats, region, entry_points, exit_points)

        desc = (
            f"{template_name}因子：根据框选区域（{stats.bar_count} 根 K 线，"
            f"区间涨跌 {stats.return_pct:+.2f}%）逆向推演的通用信号逻辑；"
            f"形态特征为{stats.trend}、{stats.volatility}。"
        )
        return FactorDraft(
            name=f"{template_name}因子_{region.symbol}_{region.timeframe}",
            description=desc,
            code=code,
            model="simulated-template",
            source="simulated",
            params=params,
            tags=[template_key, stats.trend, stats.volatility],
        )

    @staticmethod
    def _choose_template(stats) -> str:
        if stats.volatility == "高波动":
            return "volatility"
        if abs(stats.return_pct) > 0.8 and stats.bar_count >= 8:
            return "breakout"
        if abs(stats.return_pct) > 0.25:
            return "pullback"
        return "mean_reversion"

    @staticmethod
    def _infer_params(template_key: str, stats, region, entry_points, exit_points) -> dict[str, Any]:
        bars = max(stats.bar_count, 6)
        params: dict[str, Any] = {"atr_period": 14}
        if template_key == "breakout":
            params["lookback"] = min(max(bars, 8), 40)
            params["k"] = round(1.0 + stats.avg_range_pct / 2.0, 2)
        elif template_key == "pullback":
            params["fast"] = min(max(int(bars * 0.3), 5), 20)
            params["slow"] = min(max(int(bars * 1.2), 20), 80)
            params["rsi_period"] = 14
        elif template_key == "mean_reversion":
            params["period"] = min(max(bars, 10), 40)
            params["k"] = round(1.6 + stats.avg_range_pct, 2)
        elif template_key == "volatility":
            params["period"] = min(max(bars, 10), 40)
            params["z_threshold"] = round(1.0 + stats.avg_range_pct, 2)
        if entry_points:
            params["_entry_time"] = _iso(entry_points[0].get("time"))
        if exit_points:
            params["_exit_time"] = _iso(exit_points[-1].get("time"))
        return params


class FactorGenerator:
    """编排因子生成：优先 LLM，失败或未配置时回退到模拟模板。"""

    def __init__(self, client: LLMClient | None = None, ai_manager=None) -> None:
        self.client = client or LLMClient()
        self.ai_manager = ai_manager
        self.simulated = SimulatedFactorGenerator()

    async def generate(
        self,
        region: ChartRegion,
        bars: list[dict[str, Any]],
        chart_image_data_url: str | None = None,
        max_llm_attempts: int = 2,
    ) -> FactorDraft:
        stats, sliced = analyze_region(region, bars)
        if stats.bar_count == 0 or not sliced:
            raise ValueError("框选区域内没有 K 线数据，请重新框选")
        entry_points = [p.model_dump() for p in region.entry_points]
        exit_points = [p.model_dump() for p in region.exit_points]

        if self.ai_manager:
            ai_config = self.ai_manager.active_for("factor_learning")
            if ai_config:
                for attempt in range(max_llm_attempts):
                    prompt = build_factor_prompt(region, stats, sliced, entry_points, exit_points)
                    try:
                        raw = await asyncio.wait_for(
                            self.ai_manager.generate(
                                ai_config["id"],
                                prompt,
                                chart_image_data_url,
                                system_prompt=SYSTEM_PROMPT,
                            ),
                            timeout=90,
                        )
                        code = extract_python_code(raw)
                        draft = await self._validate_draft(
                            code,
                            sliced,
                            stats,
                            entry_points,
                            exit_points,
                            model_name=ai_config.get("model", "qwen3.8-max"),
                        )
                        if draft:
                            return draft
                    except Exception:
                        continue

        if self.client.provider != "simulated":
            for attempt in range(max_llm_attempts):
                prompt = build_factor_prompt(region, stats, sliced, entry_points, exit_points)
                try:
                    raw = await asyncio.wait_for(self.client.generate(prompt, chart_image_data_url), timeout=90)
                    code = extract_python_code(raw)
                    draft = await self._validate_draft(code, sliced, stats, entry_points, exit_points)
                    if draft:
                        return draft
                except Exception:
                    continue

        draft = self.simulated.generate(region, stats, entry_points, exit_points)
        return await self._validate_draft(
            draft.code, sliced, stats, entry_points, exit_points, base=draft
        ) or draft

    async def _validate_draft(
        self,
        code: str,
        bars: list[dict[str, Any]],
        stats,
        entry_points: list[dict[str, Any]],
        exit_points: list[dict[str, Any]],
        base: FactorDraft | None = None,
        model_name: str | None = None,
    ) -> FactorDraft | None:
        sandbox = check_source(code)
        if not sandbox.ok:
            return None

        params: dict[str, Any] = {}
        if entry_points:
            params["_entry_time"] = _iso(entry_points[0].get("time"))
        if exit_points:
            params["_exit_time"] = _iso(exit_points[-1].get("time"))

        execution = execute_factor(code, bars, params)
        if not execution.ok:
            return None

        if base:
            merged_params = dict(base.params)
            merged_params.update(params)
            return base.model_copy(
                update={
                    "code": code,
                    "params": merged_params,
                    "sandbox": sandbox,
                    "execution": execution,
                }
            )
        return FactorDraft(
            name=f"AI 逆向因子_{stats.bar_count}根K线",
            description=f"基于框选区域（{stats.bar_count} 根 K 线，{stats.trend}、{stats.volatility}）逆向生成的通用因子。",
            code=code,
            source="ai",
            model=model_name or self.client.provider,
            params=params,
            tags=["ai"],
            sandbox=sandbox,
            execution=execution,
        )
