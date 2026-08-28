"""AI 因子生成与安全校验路由。"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.deps import get_app_state
from api.schemas import FactorGenerationRequest, LookaheadCheckRequest
from ai_engine.ai_manager import AI_ROLES, ROLE_KEYS
from ai_engine.prompts import _format_table, extract_python_code
from ai_engine.trade_learner import learn_from_trades
from ai_engine.trade_learner import parse_strategy_json
from backtest_store.engine import Backtester
from backtest_store.trade_optimizer import optimize_trade
from models.factor import FactorCreate, FactorDraft
from paper_trading.signal_validator import validate_no_lookahead
from sandbox.ast_check import check_source
from sandbox.runner import execute_factor

router = APIRouter(prefix="/api/ai", tags=["AI 因子"])
logger = logging.getLogger(__name__)


class SandboxCheckRequest(BaseModel):
    code: str


class SandboxDiagnoseRequest(BaseModel):
    code: str
    symbol: str = "GOLD"
    timeframe: str = "M15"
    bars_count: int = 300


class AiConfigPayload(BaseModel):
    name: str
    provider: str = "openai"
    model: str = "qwen3.8-max"
    api_key: str = ""
    base_url: str = ""
    roles: list[str] = []
    enabled: bool = True


class AiModelsRequest(BaseModel):
    api_key: str
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"


class LearnTradesRequest(BaseModel):
    trade_ids: list[str]
    symbol: str | None = None
    timeframe: str = "M15"
    period_mode: str = "all"
    start_time: Any | None = None
    end_time: Any | None = None
    recent_months: int = 6
    recent_bars: int = 500
    min_validation_trades: int = 10
    min_win_rate_pct: float = 40.0
    min_profit_factor: float = 1.0
    min_oos_trades: int = 0


class OrderChatRequest(BaseModel):
    trade_id: str
    message: str = ""
    mirror: bool = True
    timeframe: str = "M15"


class OptimizeTradeRequest(BaseModel):
    trade_id: str
    timeframe: str = "M15"


class SaveLearnedFactorRequest(BaseModel):
    factor: FactorCreate
    trade_ids: list[str] = []
    validation_stats: dict[str, Any] = {}


@router.get("/configs")
async def list_ai_configs(state=Depends(get_app_state)):
    return {"configs": state.ai_manager.list_configs()}


@router.post("/configs", status_code=201)
async def create_ai_config(req: AiConfigPayload, state=Depends(get_app_state)):
    roles = [r for r in req.roles if r in ROLE_KEYS]
    if not req.name.strip():
        raise HTTPException(status_code=400, detail="请填写 AI 名称备注")
    if not req.api_key.strip():
        raise HTTPException(status_code=400, detail="请填写 API Key")
    cfg = state.repository.create_ai_config(
        name=req.name.strip(),
        provider=req.provider,
        model=req.model.strip() or "qwen3.8-max",
        api_key=req.api_key.strip(),
        base_url=req.base_url,
        roles=roles,
        enabled=req.enabled,
    )
    return {"message": f"已创建 AI 配置：{cfg['name']}", "config": cfg}


@router.put("/configs/{config_id}")
async def update_ai_config(config_id: str, req: AiConfigPayload, state=Depends(get_app_state)):
    roles = [r for r in req.roles if r in ROLE_KEYS]
    cfg = state.repository.update_ai_config(
        config_id,
        name=req.name.strip(),
        provider=req.provider,
        model=req.model.strip() or "qwen3.8-max",
        api_key=req.api_key,
        base_url=req.base_url,
        roles=roles,
        enabled=req.enabled,
    )
    if not cfg:
        raise HTTPException(status_code=404, detail="AI 配置不存在")
    return {"message": f"已更新 AI 配置：{cfg['name']}", "config": cfg}


@router.delete("/configs/{config_id}")
async def delete_ai_config(config_id: str, state=Depends(get_app_state)):
    if not state.repository.delete_ai_config(config_id):
        raise HTTPException(status_code=404, detail="AI 配置不存在")
    return {"message": "AI 配置已删除"}


@router.post("/configs/{config_id}/test")
async def test_ai_config(config_id: str, state=Depends(get_app_state)):
    return await state.ai_manager.test_connection(config_id)


@router.get("/status")
async def ai_status(state=Depends(get_app_state)):
    return state.ai_manager.status()


@router.post("/models")
async def list_ai_models(req: AiModelsRequest):
    """读取 OpenAI 兼容接口的可用模型列表（如千问模型）。"""

    from openai import OpenAI

    def _fetch() -> list[str]:
        client = OpenAI(api_key=req.api_key, base_url=req.base_url or None, timeout=20)
        blocked = (
            "livetranslate",
            "realtime",
            "tts",
            "asr",
            "embedding",
            "rerank",
            "whisper",
            "ocr",
            "video",
            "image",
            "audio",
            "synthesis",
            "speech",
        )
        return [
            m.id
            for m in client.models.list().data
            if not any(token in str(m.id).lower() for token in blocked)
        ]

    try:
        models = await asyncio.to_thread(_fetch)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"获取模型列表失败：{str(exc)[:300]}")
    return {"models": models}


@router.post("/check")
async def check_sandbox(req: SandboxCheckRequest):
    """单独校验一段因子代码是否通过沙盒。"""

    return check_source(req.code).model_dump()


def _sample_bars(count: int = 200) -> list[dict]:
    price = 100.0
    bars = []
    for i in range(count):
        price *= 1.001 if i % 3 else 0.999
        bars.append(
            {
                "time": f"2026-01-01T00:{i % 60:02d}:00Z",
                "open": round(price, 2),
                "high": round(price * 1.002, 2),
                "low": round(price * 0.998, 2),
                "close": round(price, 2),
                "volume": 1000.0,
            }
        )
    return bars


@router.post("/sandbox-diagnose")
async def sandbox_diagnose(req: SandboxDiagnoseRequest, state=Depends(get_app_state)):
    """沙盒测试排查：静态检查 + 运行时执行 + 修复建议。"""

    static = check_source(req.code)
    bars: list[dict] = []
    data_source = "内置样例数据"
    if state.mt5_gateway and state.mt5_gateway.is_connected:
        bars = state.mt5_gateway.get_rates(req.symbol, req.timeframe, req.bars_count)
        if bars:
            data_source = f"MT5 {req.symbol} {req.timeframe}"
    if not bars:
        bars = _sample_bars(req.bars_count)

    execution = execute_factor(req.code, bars, {})
    if not static.ok:
        summary = "静态安全检查未通过"
    elif not execution.ok:
        summary = "静态检查通过，但运行时执行失败"
    else:
        summary = "沙盒测试通过"

    suggestions: list[str] = []
    if not static.ok:
        for error in static.errors:
            if "导入" in error:
                suggestions.append("只允许导入 pandas、numpy、math、statistics")
            elif "calculate" in error:
                suggestions.append("必须定义 calculate(df, params) 函数")
            elif "魔法属性" in error or "危险" in error:
                suggestions.append("删除对 __class__、eval、exec、open 等危险能力的访问")
            elif "while" in error:
                suggestions.append("把 while 循环改成 pandas/numpy 向量化写法")
            else:
                suggestions.append(error)
    elif not execution.ok:
        suggestions.append(f"运行时错误：{execution.message}")
        suggestions.append("检查列名是否使用 open/high/low/close/volume，并确保返回 {'entry': pandas.Series}")
        suggestions.append("避免除以 0、空序列运算和超长循环；优先使用 shift(1) 做滞后")

    return {
        "success": True,
        "summary": summary,
        "static": static.model_dump(),
        "execution": execution.model_dump(),
        "data_source": data_source,
        "bars_used": len(bars),
        "suggestions": suggestions,
    }


@router.post("/generate")
async def generate_factor(req: FactorGenerationRequest, state=Depends(get_app_state)):
    try:
        bars = state.market.get_bars(req.region.symbol, req.region.timeframe, limit=1500)
        draft = await state.factor_generator.generate(
            req.region,
            bars,
            chart_image_data_url=req.chart_image_data_url,
        )
        return draft.model_dump()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"因子生成失败：{exc}") from exc


@router.post("/learn-trades")
async def learn_trades(req: LearnTradesRequest, state=Depends(get_app_state)):
    """从选中的历史订单中学习，生成新因子与止损止盈策略草稿。"""

    try:
        return await learn_from_trades(
            state,
            req.trade_ids,
            req.symbol,
            req.timeframe,
            req.period_mode,
            req.start_time,
            req.end_time,
            req.recent_months,
            req.recent_bars,
            {
                "min_validation_trades": req.min_validation_trades,
                "min_win_rate_pct": req.min_win_rate_pct,
                "min_profit_factor": req.min_profit_factor,
                "min_oos_trades": req.min_oos_trades,
                "min_oos_profit_factor": 0.8,
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"AI 学习失败：{exc}") from exc


def _mirror_code(code: str) -> str:
    if "def calculate(" not in code:
        return code
    code = code.replace("def calculate(", "def _original_calculate(", 1)
    code += "\n\ndef calculate(df, params):\n    out = _original_calculate(df, params)\n    if 'entry' in out:\n        out['entry'] = -out['entry']\n    return out\n"
    return code


@router.post("/order-chat")
async def order_chat(req: OrderChatRequest, state=Depends(get_app_state)):
    trade = next((t for t in state.repository.list_trades(limit=1000) if t.id == req.trade_id), None)
    if not trade:
        raise HTTPException(status_code=404, detail="交易记录不存在")
    bars: list[dict[str, Any]] = []
    try:
        bars = state.mt5_gateway.get_rates(trade.symbol, req.timeframe, 500) or []
    except Exception:
        bars = []
    if not bars:
        try:
            bars = state.market.get_bars(trade.symbol, req.timeframe, limit=500)
        except Exception:
            bars = []
    info = {
        "symbol": trade.symbol,
        "side": trade.side,
        "entry_price": trade.entry_price,
        "exit_price": trade.exit_price,
        "pnl": trade.pnl,
        "peak_pnl": trade.peak_pnl,
        "trough_pnl": trade.trough_pnl,
        "entry_time": str(trade.entry_time),
        "exit_time": str(trade.exit_time),
        "message": req.message,
    }
    bar_snapshot = _format_table(bars, max_rows=30) if bars else "无 K 线数据"
    prompt = (
        f"你是外汇订单优化助手。请分析以下订单，并生成{'完全镜像操作因子' if req.mirror else '优化因子'}：\n"
        f"订单：{json.dumps(info, ensure_ascii=False)}\n"
        f"## 最近 K 线数据\n{bar_snapshot}\n"
        "## 输出格式（必须严格遵守）\n"
        "先输出 Python 代码块：\n```python\ndef calculate(df, params):\n    ...\n```\n"
        "再输出止损止盈策略 JSON 块：\n```json\n{\"stop_atr_mult\": 2.0, \"take_atr_mult\": 3.0}\n```\n"
        "要求：\n"
        "1. 如果生成镜像因子，方向必须完全反转（多单亏损→做空，空单亏损→做多），止损止盈完全镜像。\n"
        "2. 代码只能 import pandas/numpy，必须定义 calculate(df, params)；df 列名为 open/high/low/close/volume，时间为索引，禁止使用未来数据，使用向量化运算。\n"
        "3. 只输出代码和策略，不要输出任何解释文字。"
    )
    ai_config = state.ai_manager.active_for("chat_assistant") or state.ai_manager.active_for("factor_learning")
    raw = ""
    warning = ""
    if ai_config:
        try:
            raw = await state.ai_manager.generate(ai_config["id"], prompt, system_prompt="你是一名严谨的外汇量化工程师。")
        except Exception as exc:
            warning = f"AI 调用失败：{str(exc)[:200]}，已使用本地镜像因子兜底"
    elif not warning:
        warning = "未配置可用 AI，已使用本地镜像因子兜底"
    code = extract_python_code(raw) if raw else ""
    strategy = parse_strategy_json(raw) if raw else {}
    if ai_config and raw and (not code or not check_source(code).ok):
        logger.warning("[AI 订单优化] 首次输出未通过校验：%s", raw[:1200])
        retry_prompt = prompt + "\n\n警告：你上一次输出没有通过系统校验。请重新输出：第一个代码块必须是 ```python，第二个代码块必须是 ```json，不要输出任何解释文字。"
        try:
            raw = await state.ai_manager.generate(
                ai_config["id"],
                retry_prompt,
                system_prompt="你是一名严谨的外汇量化工程师，只输出代码和 JSON。",
            )
            code = extract_python_code(raw) if raw else code
            strategy = parse_strategy_json(raw) if raw else strategy
            if not code or not check_source(code).ok:
                logger.warning("[AI 订单优化] 重试后仍无效：%s", raw[:1200])
        except Exception as exc:
            if not warning:
                warning = f"AI 重试失败：{str(exc)[:120]}，已使用本地镜像因子兜底"
    if not code or not check_source(code).ok:
        original = state.repository.get_factor(trade.factor_id) if trade.factor_id else None
        base_code = original.code if original else ""
        if base_code:
            code = _mirror_code(base_code)
        else:
            code = _mirror_code("def calculate(df, params):\n    close = df['close']\n    ma = close.rolling(20).mean()\n    entry = (close < ma).astype(float) - (close > ma).astype(float)\n    return {'entry': entry}")
        if not warning:
            detail = "未提取到 Python 代码"
            if code:
                check = check_source(code)
                detail = "；".join(check.errors[:3]) or detail
            warning = f"AI 未返回有效代码（{detail}），已使用本地镜像因子兜底"
    sandbox = check_source(code)
    draft = None
    backtest = None
    if sandbox.ok:
        draft = FactorDraft(
            name=f"{'镜像' if req.mirror else '优化'}因子_{trade.symbol}_{req.timeframe}",
            description=f"基于订单 {trade.id[:8]} 的{'完全镜像' if req.mirror else '优化'}因子草稿。",
            code=code,
            model=ai_config.get("model", "本地规则") if ai_config else "本地规则",
            source="ai_order_chat",
            params={},
            tags=["ai_order_chat", "mirror" if req.mirror else "optimize"],
            sandbox=sandbox,
        )
        if bars:
            execution = execute_factor(code, bars, {})
            if execution.ok:
                df = pd.DataFrame(bars)
                df["time"] = pd.to_datetime(df["time"], utc=True)
                df = df.set_index("time")
                entry = pd.Series(execution.entry_values, index=df.index)
                exit_sig = pd.Series(execution.exit_values, index=df.index) if execution.exit_values else None
                backtest = Backtester().run(df, entry, exit_sig, {
                    "initial_equity": 10000.0,
                    "stop_atr_mult": float(strategy.get("stop_atr_mult", 2.0)),
                    "take_atr_mult": float(strategy.get("take_atr_mult", 3.0)),
                    "max_hold_bars": int(strategy.get("max_hold_bars", 120)),
                }).model_dump()
    return {
        "ok": True,
        "reply": raw or warning or "已生成镜像因子草稿",
        "warning": warning,
        "factor_draft": draft.model_dump() if draft else None,
        "sl_tp_strategy": strategy,
        "backtest": backtest,
        "mirrored": bool(req.mirror),
        "trade": info,
    }


@router.post("/optimize-trade")
async def optimize_single_trade(req: OptimizeTradeRequest, state=Depends(get_app_state)):
    """对单笔不理想订单做入场点固定的模拟优化。"""

    trade = next((t for t in state.repository.list_trades(limit=1000) if t.id == req.trade_id), None)
    if not trade:
        raise HTTPException(status_code=404, detail="交易记录不存在")
    if not state.mt5_gateway:
        raise HTTPException(status_code=503, detail="需要 MT5 数据源才能模拟优化")
    result = optimize_trade(
        state.mt5_gateway,
        trade.symbol,
        req.timeframe,
        trade.entry_time,
        trade.exit_time,
        trade.side,
        trade.lots,
        trade.entry_price,
        trade.pnl,
    )
    if result.get("ok"):
        state.repository.update_trade(trade.id, optimization=result)
    return result


@router.post("/learn-trades/save", status_code=201)
async def save_learned_factor(req: SaveLearnedFactorRequest, state=Depends(get_app_state)):
    """保存 AI 学习因子：必须通过可信回测验证，否则拒绝入库。"""

    stats = req.validation_stats or {}
    if not stats.get("ok") or not stats.get("pass_gate"):
        reasons = stats.get("gate_reasons") or ["未通过可信回测验证"]
        raise HTTPException(status_code=400, detail=f"因子未通过可信回测验证，不能入库：{'；'.join(reasons)}")
    sandbox = check_source(req.factor.code)
    if not sandbox.ok:
        raise HTTPException(status_code=400, detail=f"沙盒检查未通过：{'；'.join(sandbox.errors)}")
    factor = state.repository.create_factor(req.factor)
    return factor.model_dump()


@router.post("/validate-lookahead")
async def check_lookahead(req: LookaheadCheckRequest, state=Depends(get_app_state)):
    bars = state.market.get_bars(req.symbol, req.timeframe, limit=req.bars)
    return validate_no_lookahead(req.code, bars, req.params)
