"""AI 交易助手：文字对话、K线图片识别与因子生成预览。"""

from __future__ import annotations

import json
from typing import Any

import pandas as pd
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.deps import get_app_state
from ai_engine.prompts import extract_python_code
from ai_engine.trade_learner import parse_strategy_json
from backtest_store.engine import Backtester
from models.factor import FactorDraft
from sandbox.ast_check import check_source
from sandbox.runner import execute_factor

router = APIRouter(prefix="/api/ai/chat", tags=["AI 助手"])


class ChatMetadata(BaseModel):
    symbol: str = ""
    timeframe: str = "M15"
    start_time: str = ""
    end_time: str = ""
    direction: str = ""


class ChatRequest(BaseModel):
    message: str = ""
    image_data_url: str | None = None
    metadata: ChatMetadata = ChatMetadata()


def _format_bars(bars: list[dict[str, Any]], max_rows: int = 30) -> str:
    if not bars:
        return "无精确 K 线数据"
    head = "| 时间 | 开 | 高 | 低 | 收 | 量 |"
    lines = [head, "|---|---|---|---|---|---|"]
    step = max(1, len(bars) // max_rows)
    for row in bars[::step][-max_rows:]:
        lines.append(
            f"| {row['time']} | {float(row['open']):.5f} | {float(row['high']):.5f} "
            f"| {float(row['low']):.5f} | {float(row['close']):.5f} | {float(row['volume']):.0f} |"
        )
    return "\n".join(lines)


def _build_prompt(req: ChatRequest, bars: list[dict[str, Any]]) -> str:
    meta = req.metadata
    parts = [
        "你是外汇 AI 交易助手，所有输出使用简体中文。",
        f"用户消息：{req.message or '（用户上传了K线图片）'}",
        f"元数据：品种={meta.symbol or '未提供'}，周期={meta.timeframe or '未提供'}，"
        f"时间范围={meta.start_time or '未提供'} 至 {meta.end_time or '未提供'}，方向={meta.direction or '未提供'}",
        f"最近K线：\n{_format_bars(bars)}",
    ]
    if req.image_data_url:
        parts.append("用户已上传K线图片，请结合图片与K线数据识别形态、支撑/压力位、入场/出场时机。")
    parts.append(
        "如果用户要求生成因子或上传了K线图片，请先输出完整 Python 因子代码块（```python），"
        "再输出止损止盈策略 JSON 块（```json）。否则只输出普通中文回答。"
    )
    return "\n\n".join(parts)


def _default_strategy() -> dict[str, Any]:
    return {
        "stop_method": "atr",
        "take_method": "atr",
        "stop_atr_mult": 2.0,
        "take_atr_mult": 3.0,
        "trailing_enabled": True,
        "max_hold_bars": 120,
    }


def _run_preview(
    code: str,
    strategy: dict[str, Any],
    bars: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not bars:
        return None
    execution = execute_factor(code, bars, {})
    if not execution.ok:
        return None
    df = pd.DataFrame(bars)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df = df.set_index("time")
    entry = pd.Series(execution.entry_values, index=df.index)
    exit_sig = pd.Series(execution.exit_values, index=df.index) if execution.exit_values else None
    params = {
        "initial_equity": 10_000.0,
        "risk_per_trade_pct": 1.0,
        "leverage": 30,
        "stop_atr_mult": float(strategy.get("stop_atr_mult", 2.0)),
        "take_atr_mult": float(strategy.get("take_atr_mult", 3.0)),
        "max_hold_bars": int(strategy.get("max_hold_bars", 120)),
    }
    return Backtester().run(df, entry, exit_sig, params).model_dump()


@router.post("")
async def chat(req: ChatRequest, state=Depends(get_app_state)):
    ai_config = state.ai_manager.active_for("chat_assistant") or state.ai_manager.active_for("factor_learning")
    if not ai_config:
        return {
            "ok": False,
            "reply": "还没有配置 AI 助手。请到 AI 管理中给模型勾选“AI 交易助手”或“K线框选学习”角色。",
            "has_image": bool(req.image_data_url),
        }

    bars: list[dict[str, Any]] = []
    if req.metadata.symbol:
        try:
            bars = state.mt5_gateway.get_rates(req.metadata.symbol, req.metadata.timeframe or "M15", 500) or []
        except Exception:
            bars = []
        if not bars:
            try:
                bars = state.market.get_bars(req.metadata.symbol, req.metadata.timeframe or "M15", limit=500)
            except Exception:
                bars = []

    prompt = _build_prompt(req, bars)
    try:
        raw = await state.ai_manager.generate(
            ai_config["id"],
            prompt,
            req.image_data_url,
            system_prompt="你是一名严谨的外汇量化交易助手，所有输出使用简体中文。",
        )
    except Exception as exc:
        return {
            "ok": False,
            "reply": f"AI 调用失败：{str(exc)[:300]}。请检查 AI 配置的 API Key、额度或网络连接。",
            "has_image": bool(req.image_data_url),
        }
    code = extract_python_code(raw)
    strategy = _default_strategy()
    strategy_data = parse_strategy_json(raw)
    if strategy_data:
        strategy.update({k: v for k, v in strategy_data.items() if k in strategy})

    factor_draft = None
    sandbox = None
    if code:
        sandbox = check_source(code)
        if sandbox.ok:
            factor_draft = FactorDraft(
                name=f"AI 对话因子_{req.metadata.symbol or '未知'}_{req.metadata.timeframe or 'M15'}",
                description="由 AI 交易助手通过文字/K线图片识别生成的因子草稿。",
                code=code,
                model=ai_config.get("model", "qwen3.8-max"),
                source="ai_chat",
                params={},
                tags=["ai_chat"],
                sandbox=sandbox,
                execution=execute_factor(code, bars, {}) if bars else None,
            )
    backtest = _run_preview(code, strategy, bars) if code and sandbox and sandbox.ok else None

    return {
        "ok": True,
        "reply": raw,
        "has_image": bool(req.image_data_url),
        "factor_draft": factor_draft.model_dump() if factor_draft else None,
        "sl_tp_strategy": strategy,
        "backtest": backtest,
        "ai_model": ai_config.get("model"),
    }
