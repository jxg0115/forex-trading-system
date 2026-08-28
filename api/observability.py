"""系统状态与告警路由。"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from api.deps import get_app_state
from config import settings

router = APIRouter(prefix="/api", tags=["系统"])


@router.get("/health")
async def health(state=Depends(get_app_state)):
    return {
        "status": "ok",
        "app_name": state.settings.app_name,
        "version": state.settings.version,
        "llm_provider": state.factor_generator.client.provider,
        "time": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/system/state")
async def system_state(state=Depends(get_app_state)):
    account = state.mt5_gateway.get_account_info() if state.mt5_gateway else None
    return {
        "market_live": state.market.is_live,
        "matcher_running": state.matcher_running,
        "matcher_symbol": state.matcher_symbol,
        "matcher_timeframe": state.matcher_timeframe,
        "matcher_pattern_min_similarity": state.matcher_pattern_min_similarity,
        "matcher_pattern_min_samples": state.matcher_pattern_min_samples,
        "matcher_pattern_time_decay_days": state.matcher_pattern_time_decay_days,
        "paper_jobs": len(state.paper.jobs),
        "active_positions": len(await state.orders.positions()),
        "account_equity": float(account["equity"]) if account else state.settings.account_equity,
        "account_balance": float(account["balance"]) if account else None,
        "account_profit": float(account["profit"]) if account else None,
        "account_currency": account["currency"] if account else "USD",
        "account_login": account["login"] if account else None,
        "max_positions": state.settings.max_positions,
        "last_scan": state.last_scan,
        "mt5_connected": bool(state.mt5_gateway and state.mt5_gateway.is_connected),
        "executor_enabled": state.signal_executor.enabled,
        "executor_config": state.signal_executor.config.to_dict(),
        "risk_tripped": state.risk_tripped,
        "risk_reasons": state.risk_reasons,
        "last_executions": state.signal_executor.last_executions[:10],
        "executor_failures": state.signal_executor.last_failures[:5],
        "last_spread": state.last_spread,
    }


@router.get("/observability/alerts")
async def alerts(state=Depends(get_app_state)):
    return {"alerts": [a.model_dump() for a in state.notifier.recent()]}


@router.get("/observability/config")
async def alert_config():
    channels: list[str] = []
    if settings.telegram_bot_token and settings.telegram_chat_id:
        channels.append("telegram")
    if settings.wechat_webhook:
        channels.append("wechat")
    if settings.dingtalk_webhook:
        channels.append("dingtalk")
    if settings.feishu_webhook:
        channels.append("feishu")
    if settings.smtp_host and settings.alert_email_to:
        channels.append("email")
    return {"channels": channels}


@router.post("/observability/test")
async def test_alert(state=Depends(get_app_state)):
    alert = await state.notifier.send_alert("info", "系统测试", "这是一条来自外汇 AI 交易系统的测试告警")
    return alert.model_dump()
