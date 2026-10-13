"""MT5 实盘网关路由：数据底座、OEMS、风控与中文告警。"""

from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from config import BASE_DIR, settings
from api.deps import get_app_state
from dotenv import set_key
from market_data.service import MarketDataService
from oems.mt5_broker import MT5Broker
from oems.mt5_gateway import MT5Gateway
from oems.order_manager import OrderManager
from oems.pending_manager import PendingExpirationManager
from oems.position_monitor import MT5PositionMonitor
from oems.smart_stop import SmartStopEngine, _atr_of
from oems.trailing_stop import TrailingStopService
from paper_trading.service import PaperTradingService
from signal_matcher.executor import SignalExecutor
from signal_matcher.matcher import MatcherService
from risk_sizing.sizing import calculate_position_size

router = APIRouter(prefix="/api/mt5", tags=["MT5 实盘"])


class MT5OrderRequest(BaseModel):
    symbol: str = Field(..., description="交易品种，例如 EURUSD")
    action_type: str = Field(..., description="BUY / SELL / BUY_LIMIT / SELL_LIMIT / BUY_STOP / SELL_STOP")
    volume: float = Field(..., description="手数")
    price: Optional[float] = Field(None, description="挂单价格；市价单可留空")
    stoplimit_price: Optional[float] = Field(None, description="止损限价单触发价")
    sl: Optional[float] = Field(None, description="止损价")
    tp: Optional[float] = Field(None, description="止盈价")
    expiration_bars: Optional[int] = Field(3, description="挂单超时 K 线根数")
    comment: str = Field("AI_Exec", description="订单备注")
    idempotency_key: Optional[str] = Field(None, description="幂等键，重复提交同一键不会重复下单")


class MT5TicketRequest(BaseModel):
    ticket: int = Field(..., description="MT5 订单/持仓编号")


class MT5ApplySltpRequest(BaseModel):
    ticket: int = Field(..., description="MT5 持仓编号")


class MT5SetupRequest(BaseModel):
    mt5_path: str = ""
    login: str = ""
    password: str = ""
    server: str = ""


def _reconfigure_mt5(state, req: MT5SetupRequest):
    """按面板配置重建 MT5 网关并热替换运行状态，不需要重启系统。"""

    gw = MT5Gateway(
        login=int(req.login or 0),
        password=req.password,
        server=req.server,
        path=req.mt5_path,
        magic=settings.mt5_magic,
    )
    connected = gw.connect()
    account = gw.get_account_info()
    if not connected and not account:
        return None

    broker = MT5Broker(settings)
    broker.gateway = gw
    state.broker_override = "mt5"
    state.mt5_gateway = gw
    state.orders = OrderManager(broker, state.notifier)
    state.market = MarketDataService(gw)
    state.pending_mgr = PendingExpirationManager(gw, state.notifier)
    state.paper = PaperTradingService(state.market, state.repository, state.orders)
    state.matcher = MatcherService(
        state.market,
        state.repository,
        state.model_pool,
        pattern_min_similarity=state.matcher_pattern_min_similarity,
        pattern_min_samples=state.matcher_pattern_min_samples,
        pattern_time_decay_days=state.matcher_pattern_time_decay_days,
    )
    state.signal_executor = SignalExecutor(
        state.market,
        state.repository,
        state.orders,
        state.notifier,
        gw,
        settings,
    )
    state.position_monitor = MT5PositionMonitor(gw, state.repository, state.notifier)
    state.trailing = TrailingStopService(gw, state.orders, state.notifier, state.repository)
    state.smart_stop = SmartStopEngine(gw, state.orders, state.notifier, state.repository, state.ai_manager)
    state.paper.trailing_config = state.signal_executor.config
    return account


def _gateway(state):
    if state.mt5_gateway is None:
        raise HTTPException(
            status_code=400,
            detail="当前为模拟经纪商模式，请在 .env 中设置 BROKER=mt5 并配置 MT5 账户",
        )
    return state.mt5_gateway


@router.get("/setup")
async def get_mt5_setup(state=Depends(get_app_state)):
    gw = state.mt5_gateway
    return {
        "mt5_path": settings.mt5_path,
        "login": settings.mt5_login,
        "server": settings.mt5_server,
        "broker": state.broker_override or settings.broker,
        "connected": bool(gw and gw.is_connected),
        "account": gw.get_account_info() if gw else None,
    }


@router.post("/setup")
async def setup_mt5(req: MT5SetupRequest, state=Depends(get_app_state)):
    env_file = BASE_DIR / ".env"
    set_key(str(env_file), "BROKER", "mt5")
    if req.mt5_path:
        set_key(str(env_file), "MT5_PATH", req.mt5_path)
    if req.login:
        set_key(str(env_file), "MT5_LOGIN", req.login)
    if req.password:
        set_key(str(env_file), "MT5_PASSWORD", req.password)
    if req.server:
        set_key(str(env_file), "MT5_SERVER", req.server)

    account = _reconfigure_mt5(state, req)
    if account is None:
        raise HTTPException(status_code=400, detail="无法连接 MT5，请检查终端路径、账号、服务器和密码")
    return {
        "success": True,
        "message": "MT5 已自动配置并连接成功",
        "connected": state.mt5_gateway.is_connected,
        "account": account,
        "symbols": state.mt5_gateway.get_symbols()[:50],
    }


@router.get("/status")
async def status(state=Depends(get_app_state)):
    gw = _gateway(state)
    account = gw.get_account_info()
    algo_enabled = bool(account and account.get("trade_allowed"))
    return {
        "broker_mode": "mt5",
        "connected": gw.is_connected,
        "account": account,
        "algo_trading_enabled": algo_enabled,
        "algo_trading_hint": "MT5 算法交易已启用" if algo_enabled else "MT5 算法交易未启用，请在终端点击算法交易按钮",
        "message": "MT5 网关已连接" if gw.is_connected else "MT5 网关未连接",
    }


@router.get("/account")
async def account(state=Depends(get_app_state)):
    gw = _gateway(state)
    info = gw.get_account_info()
    if not info:
        raise HTTPException(status_code=500, detail="无法连接 MT5 终端或获取账户信息")
    return {"success": True, "data": info}


@router.get("/symbols")
async def symbols(state=Depends(get_app_state)):
    gw = _gateway(state)
    return {"success": True, "symbols": gw.get_symbols()}


@router.get("/symbol_info")
async def symbol_info(symbol: str = Query("EURUSD"), state=Depends(get_app_state)):
    gw = _gateway(state)
    info = gw.get_symbol_info(symbol)
    if not info:
        raise HTTPException(status_code=404, detail=f"无法获取品种 {symbol} 信息")
    return {"success": True, "data": info}


@router.get("/klines")
async def klines(
    symbol: str = Query("EURUSD"),
    timeframe: str = Query("M15"),
    count: int = Query(300),
    state=Depends(get_app_state),
):
    gw = _gateway(state)
    return {"success": True, "symbol": symbol, "timeframe": timeframe, "bars": gw.get_rates(symbol, timeframe, count)}


@router.get("/tick")
async def tick(symbol: str = Query("EURUSD"), state=Depends(get_app_state)):
    """板块二：实时 Tick 行情与盘口。"""

    gw = _gateway(state)
    data = gw.get_tick(symbol)
    if not data:
        raise HTTPException(status_code=404, detail=f"无法获取 {symbol} 实时行情")
    return {"success": True, "data": data}


@router.websocket("/ws/tick")
async def ws_tick(websocket: WebSocket, symbol: str = "EURUSD"):
    """板块二：WebSocket 实时 Tick 推送。"""

    await websocket.accept()
    state = websocket.app.state.state
    try:
        while True:
            gw = state.mt5_gateway
            if gw is None:
                await websocket.send_json({"symbol": symbol, "error": "MT5 网关未启用，当前为模拟经纪商模式"})
            else:
                data = await asyncio.to_thread(gw.get_tick, symbol)
                await websocket.send_json({"symbol": symbol, "data": data})
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass


@router.post("/order")
async def send_order(req: MT5OrderRequest, state=Depends(get_app_state)):
    _gateway(state)
    side = "buy" if req.action_type.startswith("BUY") else "sell"
    if req.action_type in ("BUY", "SELL"):
        order_type = "market"
    elif "STOP_LIMIT" in req.action_type:
        order_type = "stop_limit"
    elif "LIMIT" in req.action_type:
        order_type = "limit"
    else:
        order_type = "stop"

    try:
        order = await state.orders.place_order(
            symbol=req.symbol,
            side=side,
            lots=req.volume,
            stop_price=req.sl,
            take_price=req.tp,
            reason=req.comment or "手动下单",
            order_type=order_type,
            price=req.price,
            stoplimit_price=req.stoplimit_price,
            idempotency_key=req.idempotency_key,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    ticket = order.id
    if order_type != "market" and state.pending_mgr:
        if req.expiration_bars and req.expiration_bars > 0:
            state.pending_mgr.register_pending_order(
                ticket=int(ticket),
                symbol=req.symbol,
                max_bars=req.expiration_bars,
            )

    return {
        "success": True,
        "data": {
            "ticket": ticket,
            "volume": order.lots,
            "price": order.entry_price,
            "order_type": order_type,
            "status": order.status,
        },
    }


@router.get("/positions")
async def positions(state=Depends(get_app_state)):
    gw = _gateway(state)
    return {"success": True, "positions": gw.get_positions()}


@router.get("/orders")
async def orders(state=Depends(get_app_state)):
    gw = _gateway(state)
    return {"success": True, "orders": gw.get_orders()}


@router.post("/close")
async def close_position(req: MT5TicketRequest, state=Depends(get_app_state)):
    gw = _gateway(state)
    res = gw.close_position(req.ticket)
    if res.get("success"):
        state.orders.update_log_by_ticket(str(req.ticket), status="closed", action="close", message=f"MT5 手动平仓完成（#{req.ticket}）")
        await state.notifier.send_alert("info", "MT5 手动平仓完成", f"持仓单 #{req.ticket} 已成功平仓")
    else:
        raise HTTPException(status_code=400, detail=res.get("message", "MT5 平仓失败"))
    return {"success": True, "data": res}


@router.post("/cancel")
async def cancel_order(req: MT5TicketRequest, state=Depends(get_app_state)):
    gw = _gateway(state)
    res = gw.cancel_order(req.ticket)
    if res.get("success"):
        state.orders.update_log_by_ticket(str(req.ticket), status="canceled", action="cancel", message=f"挂单 #{req.ticket} 已手动取消")
        await state.notifier.send_alert("info", "MT5 挂单已撤销", f"挂单 #{req.ticket} 已手动取消")
    else:
        raise HTTPException(status_code=400, detail=res.get("message", "MT5 撤单失败"))
    return {"success": True, "data": res}


@router.post("/close_all")
async def close_all(state=Depends(get_app_state)):
    gw = _gateway(state)
    results = gw.close_all()
    success = [r for r in results if r.get("success")]
    await state.notifier.send_alert("warning", "MT5 一键平仓", f"已平仓 {len(success)} / {len(results)} 个持仓")
    return {"success": True, "closed": success, "total": len(results)}


@router.get("/risk/calc_lot")
async def calc_lot(symbol: str = Query("EURUSD"), risk_percent: float = Query(1.0), sl_pips: float = Query(20.0), state=Depends(get_app_state)):
    gw = _gateway(state)
    acc = gw.get_account_info()
    info = gw.get_symbol_info(symbol)
    if not acc or not info:
        raise HTTPException(status_code=500, detail="获取 MT5 账户或品种风控数据失败")
    lot = calculate_position_size(
        equity=float(acc["equity"]),
        risk_percent=risk_percent,
        sl_pips=sl_pips,
        pip_value=float(info["pip_value"]),
        min_lot=float(info["min_lot"]),
        max_lot=float(info["max_lot"]),
        lot_step=float(info["lot_step"]),
    )
    return {
        "success": True,
        "calculated_lot": lot,
        "risk_amount": round(float(acc["equity"]) * (risk_percent / 100.0), 2),
        "pip_value_used": info["pip_value"],
    }


def _suggest_sltp_prices(gw, state, symbol: str, side: str, entry_price: float) -> dict:
    """按系统兜底口径（SltpPolicyConfig）计算 SL/TP，返回各档位距离。

    - R = risk_mult × ATR(14, M15)；SL = entry ∓ hard_stop_atr × ATR
    - TP = entry ± take_r_mult × R（盈亏比 ≈ 1:2，与系统初始硬止损兜底同口径）
    """
    policy = getattr(state, "sltp_policy", None)
    risk_mult = float(getattr(policy, "risk_mult", 1.5) or 1.5)
    take_r_mult = float(getattr(policy, "take_r_mult", 2.0) or 2.0)
    hard_stop_atr = float(getattr(policy, "hard_stop_atr", 1.5) or 1.5)
    timeframe = str(getattr(policy, "timeframe", "M15") or "M15")

    side_norm = "buy" if str(side).upper().startswith("BUY") else "sell"
    direction = 1.0 if side_norm == "buy" else -1.0

    info = gw.get_symbol_info(symbol) or {}
    digits = int(info.get("digits", 5) or 5)
    point = float(info.get("point", 0.00001) or 0.00001)

    atr = _atr_of(gw, symbol, timeframe, entry_price * 0.005)
    if atr <= 0:
        raise HTTPException(status_code=500, detail=f"无法计算 {symbol} {timeframe} ATR，请手动填写止损止盈")

    r_unit = max(risk_mult * atr, atr * 0.1)
    sl_dist = max(hard_stop_atr, 0.5) * atr
    tp_dist = take_r_mult * r_unit
    sl = entry_price - direction * sl_dist
    tp = entry_price + direction * tp_dist
    return {
        "symbol": symbol,
        "side": side_norm,
        "reference_price": round(entry_price, digits),
        "atr": round(atr, digits),
        "r_unit": round(r_unit, digits),
        "sl": round(sl, digits),
        "tp": round(tp, digits),
        "sl_distance": round(sl_dist, digits),
        "tp_distance": round(tp_dist, digits),
        "sl_points": round(sl_dist / point, 1),
        "tp_points": round(tp_dist / point, 1),
        "reward_risk_ratio": round(tp_dist / sl_dist, 2) if sl_dist else None,
        "policy": {
            "risk_mult": risk_mult,
            "take_r_mult": take_r_mult,
            "hard_stop_atr": hard_stop_atr,
            "timeframe": timeframe,
        },
    }


@router.get("/risk/suggest_sltp")
async def suggest_sltp(
    symbol: str = Query("EURUSD"),
    side: str = Query("buy"),
    entry_price: Optional[float] = Query(None),
    state=Depends(get_app_state),
):
    """为手动下单建议 SL/TP（系统兜底口径）；entry_price 缺省按实时盘口价。"""

    gw = _gateway(state)
    if entry_price is None or entry_price <= 0:
        tick = gw.get_tick(symbol)
        if not tick:
            raise HTTPException(status_code=500, detail=f"无法获取 {symbol} 实时行情，请手动填写止损止盈")
        side_norm = "buy" if str(side).upper().startswith("BUY") else "sell"
        entry_price = float(tick["ask"] if side_norm == "buy" else tick["bid"])
    return {"success": True, "data": _suggest_sltp_prices(gw, state, symbol, side, entry_price)}


@router.post("/position/apply_suggested_sltp")
async def apply_suggested_sltp(req: MT5ApplySltpRequest, state=Depends(get_app_state)):
    """为已有持仓一键补挂/重算 SL/TP（按持仓自身开仓价、系统兜底口径计算并直接修改）。"""

    gw = _gateway(state)
    positions = gw.get_positions() or []
    pos = next((p for p in positions if int(p.get("ticket", 0) or 0) == req.ticket), None)
    if not pos:
        raise HTTPException(status_code=404, detail=f"未找到持仓单 #{req.ticket}")

    entry = float(pos.get("price_open") or 0)
    if entry <= 0:
        raise HTTPException(status_code=500, detail=f"持仓 #{req.ticket} 开仓价异常，无法计算")
    suggestion = _suggest_sltp_prices(gw, state, str(pos.get("symbol")), str(pos.get("type")), entry)

    res = gw.modify_position(req.ticket, sl=suggestion["sl"], tp=suggestion["tp"])
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("message", "修改止损失败"))
    await state.notifier.send_alert(
        "info",
        "手动持仓已补挂止损止盈",
        f"持仓 #{req.ticket} {suggestion['symbol']} 已按系统口径设置 SL={suggestion['sl']} / TP={suggestion['tp']}",
    )
    return {"success": True, "data": {"ticket": req.ticket, "suggestion": suggestion, "applied": res}}
