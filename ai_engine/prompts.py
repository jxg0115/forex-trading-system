"""中文 Prompt 模板与代码提取工具。"""

from __future__ import annotations

import re
from typing import Any

from models.factor import ChartRegion, RegionStats

SYSTEM_PROMPT = """你是外汇量化交易系统的资深量化工程师，擅长把交易员标注的 K 线形态逆向推演为通用、无硬编码的 Python 因子代码。
所有输出必须使用简体中文。只输出 Python 代码，不要输出解释文字。
代码必须符合以下要求：
1. 只能 import pandas（别名 pd）与 numpy（别名 np），禁止使用其他库、文件、网络、进程与动态执行能力。
2. 必须定义函数 calculate(df, params) -> dict，df 为包含 open/high/low/close/volume 的 DataFrame，params 为参数字典。
3. 返回值必须是 {"entry": pandas.Series, "exit": pandas.Series}；entry 中 1.0 表示多头入场，-1.0 表示空头入场，0.0 表示无信号；exit 中 1.0 表示平多，-1.0 表示平空，0.0 表示持有。exit 可省略。
4. 必须使用 shift(1) 等防未来数据泄漏写法：第 t 根 K 线产生的信号只能使用 t 及之前的数据，实盘将在第 t+1 根 K 线开盘执行。
5. 优先使用 pandas 向量化运算，避免 for/while 循环；代码长度控制在 60 行以内。
6. 参数通过 params.get("参数名", 默认值) 读取，不要硬编码具体行情价格。"""


def _format_table(rows: list[dict[str, Any]], max_rows: int = 40) -> str:
    head = "| 时间 | 开盘 | 最高 | 最低 | 收盘 | 成交量 |"
    sep = "|---|---|---|---|---|---|"
    lines = [head, sep]
    step = max(1, len(rows) // max_rows) if rows else 1
    for row in rows[::step][-max_rows:]:
        lines.append(
            f"| {row['time']} | {row['open']:.5f} | {row['high']:.5f} "
            f"| {row['low']:.5f} | {row['close']:.5f} | {row['volume']:.0f} |"
        )
    return "\n".join(lines)


def build_factor_prompt(
    region: ChartRegion,
    stats: RegionStats,
    bars: list[dict[str, Any]],
    entry_points: list[dict[str, Any]] | None = None,
    exit_points: list[dict[str, Any]] | None = None,
) -> str:
    entry_text = "\n".join(
        f"- 入场点：{p['time']}，价格 {p['price']:.5f}" for p in (entry_points or [])
    ) or "无"
    exit_text = "\n".join(
        f"- 出场点：{p['time']}，价格 {p['price']:.5f}" for p in (exit_points or [])
    ) or "无"
    support_text = "\n".join(
        f"- 支撑位：{p.price:.5f}（触碰 {getattr(p, 'touched', 0)} 次）" for p in getattr(region, "support_levels", [])
    ) or "无"
    resistance_text = "\n".join(
        f"- 压力位：{p.price:.5f}（触碰 {getattr(p, 'touched', 0)} 次）" for p in getattr(region, "resistance_levels", [])
    ) or "无"
    return f"""请根据交易员框选的 K 线区域，逆向推演一个可复用的外汇量化因子。

## 交易品种与周期
- 品种：{region.symbol}
- 周期：{region.timeframe}
- 框选时间范围：{region.time_start} 至 {region.time_end}

## 区域统计特征
- K 线数量：{stats.bar_count}
- 区间涨跌幅：{stats.return_pct:.2f}%
- 区间最高：{stats.high:.5f}，最低：{stats.low:.5f}
- 平均振幅：{stats.avg_range_pct:.2f}%
- 趋势特征：{stats.trend}
- 波动特征：{stats.volatility}

## 交易员人工标注
入场点：
{entry_text}

出场点：
{exit_text}

人工标注支撑位：
{support_text}

人工标注压力位：
{resistance_text}

## 区域 OHLCV 数字快照
{_format_table(bars)}

## 输出要求
把这段形态背后可复用的交易逻辑抽象为通用因子：识别同类形态、给出入场与离场信号，参数默认值应接近该区域统计特征推导出的合理取值。直接输出完整 Python 代码，用 ```python 代码块包裹。"""


def extract_python_code(text: str) -> str:
    """从大模型输出中提取 ```python ... ``` 代码块。"""
    match = re.search(r"```(?:python)?\s*\n(.*?)```", text, flags=re.S)
    if match:
        return match.group(1).strip()
    match = re.search(r"```python(.*?)```", text, flags=re.S)
    if match:
        return match.group(1).strip()
    return text.strip()
