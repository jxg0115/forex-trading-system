"""外部信号 Webhook 接入（D8）：鉴权 → 事件窗口检查（D3）→ 决策链执行。"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel

from api.deps import get_app_state
from config import settings
from market_data.events import is_high_event_window

router = APIRouter(prefix="/api/webhook", tags=["外部信号"])


class WebhookSignalRequest(BaseModel):
    symbol: str                 # 品种（必须与决策链当前品种一致）
    side: str                   # long / short
    source: str                 # 信号源标识（tradingview / mt4-expert / 外部策略）
    timeframe: str = "M15"
    confidence: float = 0.6
    note: Optional[str] = None


@router.post("/signal")
async def receive_signal(
    req: WebhookSignalRequest,
    x_webhook_token: Optional[str] = Header(None),
    state=Depends(get_app_state),
) -> dict[str, Any]:
    """接收外部信号：鉴权 → 事件窗口禁单 → 决策链执行（复用 executor）。"""
    # 1. 鉴权（配置 WEBHOOK_TOKEN 后启用；空 = 测试环境不鉴权）
    token = getattr(settings, "webhook_token", "") or ""
    if token and x_webhook_token != token:
        return {"ok": False, "code": "auth_failed", "message": "webhook token 校验失败"}

    side = str(req.side).lower()
    if side not in ("long", "short"):
        return {"ok": False, "code": "bad_side", "message": "side 仅支持 long/short"}

    # 2. D3 事件过滤：高影响事件窗口内禁开仓
    in_win, ev = is_high_event_window(window_minutes=30)
    if in_win:
        return {
            "ok": False,
            "code": "event_window",
            "message": f"高影响事件窗口内禁开仓（{(ev or {}).get('title') or ''}）",
            "event": ev,
        }

    # 3. 决策链执行（复用 executor；symbol 必须与决策链当前品种一致）
    executor = getattr(state, "signal_executor", None)
    if executor is None:
        return {"ok": False, "code": "no_executor", "message": "信号执行器未就绪"}
    cfg = getattr(executor, "config", None)
    if cfg is None or not str(req.symbol).upper().startswith(str(cfg.symbol).upper()):
        cur = getattr(cfg, "symbol", "?") if cfg else "?"
        return {
            "ok": False,
            "code": "symbol_mismatch",
            "message": f"决策链当前品种 {cur}，与信号品种 {req.symbol} 不一致",
        }

    candidate = {
        "signal": side,
        "factor_id": f"webhook:{req.source}",
        "factor_name": f"Webhook {req.source}",
        "confidence": max(0.0, min(float(req.confidence), 1.0)),
        "reason": f"外部信号 Webhook ({req.source})",
    }
    scan = {"symbol": cfg.symbol, "timeframe": cfg.timeframe, "candidates": [candidate]}
    try:
        executed = await executor.execute_scan(scan)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "code": "execute_error", "message": str(exc)}
    return {"ok": True, "executed": len(executed), "results": executed[:5], "note": req.note}