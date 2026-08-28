"""MetaTrader 5 数据底座与交易网关（合并自 MT5 引擎项目，保持中文注释）。"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta
from typing import Any, Optional

logger = logging.getLogger(__name__)

RETCODE_HINTS = {
    10013: "交易被经纪商禁止",
    10014: "请求超时，请稍后重试",
    10016: "无效请求参数",
    10018: "市场已关闭（休市），请等待交易时段再下单",
    10019: "当前无报价，请检查品种是否可交易",
    10021: "价格被经纪商拒绝，请检查价格有效性",
    10022: "价格无效",
    10027: "MT5 算法交易未启用，请在终端工具栏点击算法交易按钮",
    10030: "不支持的订单填充模式",
    10036: "买卖方向发生变更，请重新下单",
}


class MT5Gateway:
    """MT5 官方 SDK 封装：连接、账户、品种、K 线、下单、持仓、平仓、撤单。"""

    TIMEFRAME_NAMES = ("M1", "M5", "M15", "M30", "H1", "H4", "D1")

    def __init__(
        self,
        login: int = 0,
        password: str = "",
        server: str = "",
        path: str = "",
        magic: int = 202608,
    ) -> None:
        self.login = login
        self.password = password
        self.server = server
        self.path = path
        self.magic = magic
        self.is_connected = False
        self.package_available = False
        self._last_connect_attempt = 0.0
        self._last_path_log = 0.0
        self._mt5: Any = None

    def _require_mt5(self) -> Any:
        if self._mt5 is None:
            try:
                import MetaTrader5 as mt5  # type: ignore
            except ImportError as exc:
                self.package_available = False
                raise RuntimeError("未安装 MetaTrader5 Python 包，无法连接 MT5") from exc
            self._mt5 = mt5
            self.package_available = True
        return self._mt5

    def _timeframe_map(self) -> dict[str, Any]:
        mt5 = self._require_mt5()
        return {
            "M1": mt5.TIMEFRAME_M1,
            "M5": mt5.TIMEFRAME_M5,
            "M15": mt5.TIMEFRAME_M15,
            "M30": mt5.TIMEFRAME_M30,
            "H1": mt5.TIMEFRAME_H1,
            "H4": mt5.TIMEFRAME_H4,
            "D1": mt5.TIMEFRAME_D1,
        }

    def connect(self) -> bool:
        """初始化并建立与 MT5 客户端的连接。"""

        now = time.time()
        if now - self._last_connect_attempt < 5:
            return self.is_connected
        self._last_connect_attempt = now

        if self.path and not os.path.exists(self.path):
            if now - self._last_path_log > 30:
                logger.warning("[MT5Gateway] 终端路径不存在，跳过连接：%s", self.path)
                self._last_path_log = now
            self.is_connected = False
            return False

        try:
            mt5 = self._require_mt5()
        except RuntimeError as exc:
            logger.error("[MT5Gateway] %s", exc)
            self.is_connected = False
            return False

        init_kwargs = {"path": self.path} if self.path else {}
        if not mt5.initialize(**init_kwargs):
            logger.error("[MT5Gateway] MT5 初始化失败, 错误码: %s", mt5.last_error())
            self.is_connected = False
            return False

        if self.login and self.password and self.server:
            authorized = mt5.login(login=self.login, password=self.password, server=self.server)
            if not authorized:
                logger.error("[MT5Gateway] 账户登录失败 #%s, 错误码: %s", self.login, mt5.last_error())
                self.is_connected = False
                return False

        self.is_connected = True
        logger.info("[MT5Gateway] MT5 数据底座与交易网关连接成功")
        return True

    def disconnect(self) -> None:
        if self._mt5 is not None:
            self._mt5.shutdown()
        self.is_connected = False

    def get_account_info(self) -> Optional[dict[str, Any]]:
        if not self.is_connected and not self.connect():
            return None
        mt5 = self._require_mt5()
        acc = mt5.account_info()
        if acc is None:
            self.is_connected = False
            return None
        return {
            "balance": acc.balance,
            "equity": acc.equity,
            "profit": acc.profit,
            "margin": acc.margin,
            "free_margin": acc.margin_free,
            "leverage": acc.leverage,
            "currency": acc.currency,
            "login": acc.login,
            "server": acc.server,
            "trade_allowed": bool(acc.trade_allowed),
        }

    def get_symbols(self) -> list[str]:
        if not self.is_connected and not self.connect():
            return []
        mt5 = self._require_mt5()
        symbols = mt5.symbols_get()
        return [s.name for s in symbols or []]

    def get_symbol_info(self, symbol: str) -> Optional[dict[str, Any]]:
        if not self.is_connected and not self.connect():
            return None
        mt5 = self._require_mt5()
        symbol_info = mt5.symbol_info(symbol)
        if symbol_info is None:
            return None
        if not symbol_info.visible:
            mt5.symbol_select(symbol, True)

        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return None

        point = symbol_info.point
        tick_size = symbol_info.trade_tick_size if symbol_info.trade_tick_size > 0 else point
        tick_value = symbol_info.trade_tick_value
        pip_value = (tick_value / tick_size) * (point * 10 if symbol_info.digits in (3, 5) else point)

        return {
            "symbol": symbol,
            "bid": tick.bid,
            "ask": tick.ask,
            "spread_points": symbol_info.spread,
            "digits": symbol_info.digits,
            "point": point,
            "min_lot": symbol_info.volume_min,
            "max_lot": symbol_info.volume_max,
            "lot_step": symbol_info.volume_step,
            "contract_size": symbol_info.trade_contract_size,
            "pip_value": pip_value if pip_value > 0 else 10.0,
            "filling_mode": symbol_info.filling_mode,
        }

    def get_tick(self, symbol: str) -> Optional[dict[str, Any]]:
        """获取品种最新 Bid/Ask、点差与盘口信息（板块二）。"""

        if not self.is_connected and not self.connect():
            return None
        mt5 = self._require_mt5()
        symbol_info = mt5.symbol_info(symbol)
        if symbol_info is None:
            return None
        if not symbol_info.visible:
            mt5.symbol_select(symbol, True)
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return None
        return {
            "symbol": symbol,
            "bid": tick.bid,
            "ask": tick.ask,
            "last": tick.last,
            "volume": tick.volume,
            "spread_points": symbol_info.spread,
            "time": tick.time,
        }

    def get_rates(self, symbol: str, timeframe: str = "M15", count: int = 300) -> list[dict[str, Any]]:
        """拉取历史/实时 OHLCV K 线序列。"""

        if not self.is_connected and not self.connect():
            return []
        mt5 = self._require_mt5()
        tf = self._timeframe_map().get(timeframe.upper(), mt5.TIMEFRAME_M15)
        rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
        if rates is None or len(rates) == 0:
            logger.error("[MT5Gateway] 无法获取 %s %s 数据: %s", symbol, timeframe, mt5.last_error())
            return []

        return self._rates_to_list(rates)

    def get_rates_range(
        self,
        symbol: str,
        timeframe: str = "M15",
        date_from: Any = None,
        date_to: Any = None,
    ) -> list[dict[str, Any]]:
        """按日期范围拉取历史 K 线，用于策略测试器风格的回测。"""

        if not self.is_connected and not self.connect():
            return []
        mt5 = self._require_mt5()
        tf = self._timeframe_map().get(timeframe.upper(), mt5.TIMEFRAME_M15)

        def _naive(value: Any) -> Any:
            if value is None:
                return None
            if hasattr(value, "tzinfo") and value.tzinfo is not None:
                return value.replace(tzinfo=None)
            return value

        date_from = _naive(date_from)
        date_to = _naive(date_to)
        if date_from is None:
            date_from = datetime.now() - timedelta(days=30)
        if date_to is None:
            date_to = datetime.now()
        rates = mt5.copy_rates_range(symbol, tf, date_from, date_to)
        if rates is None or len(rates) == 0:
            logger.error("[MT5Gateway] 无法获取 %s %s 区间数据: %s", symbol, timeframe, mt5.last_error())
            return []
        return self._rates_to_list(rates)

    def _rates_to_list(self, rates: Any) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for row in rates:
            result.append(
                {
                    "time": str(pd_ts(row["time"])),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row["tick_volume"]),
                    "tick_volume": int(row["tick_volume"]),
                    "spread": int(row["spread"]),
                }
            )
        return result

    def _determine_filling_mode(self, symbol_info: Any) -> Any:
        mt5 = self._require_mt5()
        filling_mode = symbol_info.filling_mode
        # MT5 的 filling_mode 是位掩码：bit0=FOK，bit1=IOC；订单枚举值 FOK=0, IOC=1, RETURN=2
        if filling_mode & 1:
            return mt5.ORDER_FILLING_FOK
        if filling_mode & 2:
            return mt5.ORDER_FILLING_IOC
        return mt5.ORDER_FILLING_RETURN

    def place_order(
        self,
        symbol: str,
        action_type: str,
        volume: float,
        price: Optional[float] = None,
        sl: Optional[float] = None,
        tp: Optional[float] = None,
        comment: str = "AI_Exec",
        stoplimit_price: Optional[float] = None,
    ) -> dict[str, Any]:
        """统一执行交易指令，支持市价单与 Limit/Stop 挂单。"""

        if not self.is_connected and not self.connect():
            return {"success": False, "message": "MT5 终端未连接"}
        mt5 = self._require_mt5()

        symbol_info = mt5.symbol_info(symbol)
        if symbol_info is None:
            return {"success": False, "message": f"找不到交易品种: {symbol}"}
        if not symbol_info.visible:
            mt5.symbol_select(symbol, True)

        order_type_map = {
            "BUY": mt5.ORDER_TYPE_BUY,
            "SELL": mt5.ORDER_TYPE_SELL,
            "BUY_LIMIT": mt5.ORDER_TYPE_BUY_LIMIT,
            "SELL_LIMIT": mt5.ORDER_TYPE_SELL_LIMIT,
            "BUY_STOP": mt5.ORDER_TYPE_BUY_STOP,
            "SELL_STOP": mt5.ORDER_TYPE_SELL_STOP,
            "BUY_STOP_LIMIT": mt5.ORDER_TYPE_BUY_STOP_LIMIT,
            "SELL_STOP_LIMIT": mt5.ORDER_TYPE_SELL_STOP_LIMIT,
        }
        if action_type not in order_type_map:
            return {"success": False, "message": f"不支持的订单类型: {action_type}"}

        tick = mt5.symbol_info_tick(symbol)
        if price is None or price <= 0:
            if action_type == "BUY":
                exec_price = tick.ask
            elif action_type == "SELL":
                exec_price = tick.bid
            else:
                return {"success": False, "message": f"挂单类型 {action_type} 必须指定价格"}
        else:
            exec_price = price

        is_pending = "LIMIT" in action_type or "STOP" in action_type
        if action_type in ("BUY_STOP_LIMIT", "SELL_STOP_LIMIT"):
            if price is None or price <= 0 or stoplimit_price is None or stoplimit_price <= 0:
                return {"success": False, "message": f"止损限价单 {action_type} 必须同时指定限价 price 与触发价 stoplimit"}
        request: dict[str, Any] = {
            "action": mt5.TRADE_ACTION_PENDING if is_pending else mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(volume),
            "type": order_type_map[action_type],
            "price": float(exec_price),
            "deviation": 20,
            "magic": self.magic,
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._determine_filling_mode(symbol_info),
        }
        if sl and sl > 0:
            request["sl"] = float(sl)
        if tp and tp > 0:
            request["tp"] = float(tp)
        if action_type in ("BUY_STOP_LIMIT", "SELL_STOP_LIMIT"):
            request["stoplimit"] = float(stoplimit_price)

        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            err_msg = result.comment if result else str(mt5.last_error())
            retcode = result.retcode if result else -1
            hint = RETCODE_HINTS.get(retcode)
            suffix = f"；{hint}" if hint else ""
            return {"success": False, "message": f"发单失败 [RetCode:{retcode}]: {err_msg}{suffix}"}
        return {
            "success": True,
            "ticket": result.order,
            "volume": result.volume,
            "price": result.price,
            "message": "订单发送成功",
            "retcode": result.retcode,
            "comment": result.comment,
            "filling_mode": request.get("type_filling"),
            "request": request,
        }

    def get_positions(self) -> list[dict[str, Any]]:
        if not self.is_connected and not self.connect():
            return []
        mt5 = self._require_mt5()
        positions = mt5.positions_get()
        if positions is None:
            return []
        result: list[dict[str, Any]] = []
        for pos in positions:
            result.append(
                {
                    "ticket": pos.ticket,
                    "symbol": pos.symbol,
                    "type": "BUY" if pos.type == mt5.POSITION_TYPE_BUY else "SELL",
                    "volume": pos.volume,
                    "price_open": pos.price_open,
                    "price_current": pos.price_current,
                    "sl": pos.sl,
                    "tp": pos.tp,
                    "profit": pos.profit,
                    "time": pos.time,
                    "comment": pos.comment,
                }
            )
        return result

    def get_orders(self) -> list[dict[str, Any]]:
        """获取未成交挂单池。"""

        if not self.is_connected and not self.connect():
            return []
        mt5 = self._require_mt5()
        orders = mt5.orders_get()
        if orders is None:
            return []
        type_map = {
            mt5.ORDER_TYPE_BUY_LIMIT: "BUY_LIMIT",
            mt5.ORDER_TYPE_SELL_LIMIT: "SELL_LIMIT",
            mt5.ORDER_TYPE_BUY_STOP: "BUY_STOP",
            mt5.ORDER_TYPE_SELL_STOP: "SELL_STOP",
        }
        result: list[dict[str, Any]] = []
        for order in orders:
            result.append(
                {
                    "ticket": order.ticket,
                    "symbol": order.symbol,
                    "type": type_map.get(order.type, "PENDING"),
                    "volume_initial": order.volume_initial,
                    "price_open": order.price_open,
                    "sl": order.sl,
                    "tp": order.tp,
                    "comment": order.comment,
                }
            )
        return result

    def close_position(self, ticket: int, volume: Optional[float] = None) -> dict[str, Any]:
        if not self.is_connected and not self.connect():
            return {"success": False, "message": "MT5 未连接"}
        mt5 = self._require_mt5()
        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            return {"success": False, "message": f"未找到持仓单 #{ticket}"}

        pos = positions[0]
        close_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY
        tick = mt5.symbol_info_tick(pos.symbol)
        close_price = tick.bid if pos.type == mt5.POSITION_TYPE_BUY else tick.ask
        close_volume = float(volume) if volume and float(volume) > 0 else float(pos.volume)
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "position": ticket,
            "symbol": pos.symbol,
            "volume": close_volume,
            "type": close_type,
            "price": close_price,
            "deviation": 20,
            "magic": self.magic,
            "comment": "AI_PartialClose" if volume and float(volume) < float(pos.volume) else "AI_Close",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._determine_filling_mode(mt5.symbol_info(pos.symbol)),
        }
        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            retcode = result.retcode if result else -1
            hint = RETCODE_HINTS.get(retcode)
            suffix = f"；{hint}" if hint else ""
            return {"success": False, "message": f"平仓失败, RetCode: {retcode}{suffix}"}
        return {"success": True, "ticket": ticket, "message": "平仓成功"}

    def modify_position(self, ticket: int, sl: Optional[float] = None, tp: Optional[float] = None) -> dict[str, Any]:
        """修改持仓的止损/止盈价格，用于移动止损。"""

        if not self.is_connected and not self.connect():
            return {"success": False, "message": "MT5 未连接"}
        mt5 = self._require_mt5()
        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            return {"success": False, "message": f"未找到持仓单 #{ticket}"}
        pos = positions[0]
        new_sl = float(sl) if sl and sl > 0 else pos.sl
        new_tp = float(tp) if tp and tp > 0 else pos.tp
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": ticket,
            "symbol": pos.symbol,
            "sl": new_sl,
            "tp": new_tp,
            "magic": self.magic,
            "comment": "AI_Trailing",
        }
        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            retcode = result.retcode if result else -1
            hint = RETCODE_HINTS.get(retcode)
            suffix = f"；{hint}" if hint else ""
            return {"success": False, "message": f"修改止损失败 [RetCode:{retcode}]: {(result.comment if result else '')}{suffix}"}
        return {"success": True, "ticket": ticket, "sl": new_sl, "tp": new_tp, "message": "止损已更新"}

    def cancel_order(self, ticket: int) -> dict[str, Any]:
        if not self.is_connected and not self.connect():
            return {"success": False, "message": "MT5 未连接"}
        mt5 = self._require_mt5()
        result = mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": ticket})
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            retcode = result.retcode if result else -1
            hint = RETCODE_HINTS.get(retcode)
            suffix = f"；{hint}" if hint else ""
            return {"success": False, "message": f"撤单失败, RetCode: {retcode}{suffix}"}
        return {"success": True, "ticket": ticket, "message": "撤单成功"}

    def close_all(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for pos in self.get_positions():
            results.append(self.close_position(pos["ticket"]))
        return results

    def get_closed_trade(self, ticket: int) -> Optional[dict[str, Any]]:
        """按持仓单号查询历史成交，返回可复盘的单笔交易记录。"""

        if not self.is_connected and not self.connect():
            return None
        mt5 = self._require_mt5()
        deals = mt5.history_deals_get(position=ticket)
        if not deals:
            return None
        entry_deal = next((d for d in deals if d.entry == mt5.DEAL_ENTRY_IN), None)
        exit_deals = [d for d in deals if d.entry == mt5.DEAL_ENTRY_OUT and d.profit is not None]
        if entry_deal is None or not exit_deals:
            return None
        exit_deal = max(exit_deals, key=lambda d: d.time)
        return {
            "ticket": ticket,
            "symbol": entry_deal.symbol,
            "side": "long" if entry_deal.type == mt5.DEAL_TYPE_BUY else "short",
            "volume": float(entry_deal.volume),
            "entry_time": int(entry_deal.time),
            "entry_price": float(entry_deal.price),
            "exit_time": int(exit_deal.time),
            "exit_price": float(exit_deal.price),
            "pnl": round(sum(d.profit for d in exit_deals), 2),
            "reason": str(exit_deal.comment or "MT5 平仓"),
        }

    def get_closed_trades(self, days: int = 30) -> list[dict[str, Any]]:
        """拉取最近 N 天内已平仓的持仓记录，用于复盘补录。"""

        if not self.is_connected and not self.connect():
            return []
        mt5 = self._require_mt5()
        date_from = datetime.now() - timedelta(days=max(int(days), 1))
        date_to = datetime.now()
        deals = mt5.history_deals_get(date_from, date_to)
        if not deals:
            return []
        tickets: set[int] = set()
        for deal in deals:
            if deal.entry == mt5.DEAL_ENTRY_OUT and deal.profit is not None:
                position_id = int(getattr(deal, "position_id", 0) or 0)
                if position_id:
                    tickets.add(position_id)
        results: list[dict[str, Any]] = []
        for ticket in sorted(tickets):
            data = self.get_closed_trade(ticket)
            if data:
                results.append(data)
        return results


def pd_ts(epoch_seconds: int) -> Any:
    """把 MT5 epoch 秒转成 ISO 字符串，避免在未安装 pandas 时依赖其格式化。"""

    from datetime import datetime, timezone

    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).isoformat()
