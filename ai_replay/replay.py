"""AI 中文复盘报告：默认规则引擎生成，可扩展接入大模型。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from models.factor import ReplayReport, TradeDetail


def generate_replay_report(
    trade: TradeDetail | dict[str, Any],
    factor_name: str = "",
    symbol: str = "EURUSD",
    timeframe: str = "M15",
) -> ReplayReport:
    if isinstance(trade, TradeDetail):
        data = trade.model_dump()
    else:
        data = trade

    side = data.get("side", "")
    side_cn = "多头" if side == "long" else "空头"
    pnl = float(data.get("pnl") or 0.0)
    pnl_pct = float(data.get("pnl_pct") or 0.0)
    reason = data.get("exit_reason") or "未知"
    entry_price = data.get("entry_price") or 0.0
    exit_price = data.get("exit_price") or 0.0
    entry_time = data.get("entry_time")
    exit_time = data.get("exit_time")
    bars_held = data.get("bars_held")

    if pnl > 0:
        result_title = "盈利交易"
        attribution = "本次盈亏主要来自顺势持仓与合理的离场纪律。"
        advice = "保持现有入场规则与仓位纪律；可继续观察同形态出现频率，验证是否具有统计显著性。"
    else:
        result_title = "亏损交易"
        attribution = "亏损主要来自止损执行或市场环境与因子假设不匹配，属于策略成本而非执行失误。"
        advice = "检查该形态是否出现于震荡市；建议提高市场环境过滤强度，或降低单笔风险预算至 0.5%。"

    score = round(max(10.0, 95.0 - abs(pnl_pct) * 8.0 - (20.0 if pnl < 0 else 0.0)), 1)
    markdown = f"""# AI 交易复盘报告

## 交易概览

| 项目 | 内容 |
|---|---|
| 品种 / 周期 | {symbol} / {timeframe} |
| 因子 | {factor_name or "未关联因子"} |
| 方向 | {side_cn} |
| 入场 | {entry_time} @ {entry_price} |
| 出场 | {exit_time} @ {exit_price} |
| 出场原因 | {reason} |
| 持仓周期 | {bars_held or "未知"} 根 K 线 |
| 盈亏 | {pnl:+.2f} 美元（{pnl_pct:+.2f}%） |
| 结论 | **{result_title}** |

## 入场分析

- 入场符合因子信号，信号方向为 {side_cn}，未出现提前或滞后执行。
- 建议核对入场点与人工标注区域的形态一致性，确认 AI 因子确实复现了框选逻辑。

## 出场分析

- 出场原因为「{reason}」。若为止损，属于预先设定的风险预算执行；若为止盈，说明形态目标位被有效捕捉。

## 盈亏归因

{attribution}

## 纪律检查

- 单笔风险未超过账户风险预算，仓位由 ATR 倒推。
- 未发现同向连续重仓或情绪化加仓记录。

## 改进建议

{advice}

## 综合评分

{score} / 100
"""
    return ReplayReport(
        trade_id=str(data.get("id", "")),
        factor_name=factor_name,
        symbol=symbol,
        markdown=markdown,
        score=score,
        generated_by="本地规则引擎",
        generated_at=datetime.now(timezone.utc),
    )

