"""OKX（欧易）v5 REST 网关：对标 MT5Gateway 的方法集，供上层无差别调用。

与 MT5 的关键差异（每一条都在代码里有对应处理，改动前请先读）：

1. 品种标识：MT5 用 `XAUUSD`，OKX 用 `instId`（`BTC-USDT-SWAP` / `BTC-USDT` / `BTC-USDT-260926`）。
2. 合约类型：SPOT / SWAP(永续) / FUTURES(交割) 全部支持，由 instId 后缀自动判定。
3. 持仓模式：**双向持仓**（posSide=long/short），与外汇"单方向+正负手数"不同。
   因此本网关用 `"<instId>|<posSide>"` 作为持仓/平仓的标识（对外仍叫 ticket，便于上层复用）。
4. 仓位单位：衍生品是**张**（1 张 = ctVal 个标的币），现货是**标的币数量**。
   换算由 risk_sizing/crypto_sizing.py 负责，本网关只接受交易所单位。
5. K 线顺序：OKX 返回**降序**（最新在前），本网关统一反转为升序，与 MT5 一致。
6. 止损止盈：主单不带 SL/TP，用 attachAlgoOrds 挂条件单；改止损必须先查到 algoId 再 amend。
7. 时间戳：毫秒 UTC。
8. 响应信封 `{code,msg,data[]}`：HTTP 200 也可能带回业务错误，必须判 code != "0"。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

BASE_URL = "https://www.okx.com"

# 项目周期 → OKX bar
BAR_MAP = {
    "M1": "1m", "M3": "3m", "M5": "5m", "M15": "15m", "M30": "30m",
    "H1": "1H", "H2": "2H", "H4": "4H", "H6": "6H", "H12": "12H",
    "D1": "1D", "W1": "1W",
}
# OKX bar → 毫秒
BAR_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1H": 3_600_000, "2H": 7_200_000, "4H": 14_400_000, "6H": 21_600_000,
    "12H": 43_200_000, "1D": 86_400_000, "1W": 604_800_000,
}
CANDLE_LIMIT = 300        # /market/candles 单次上限（历史端点为 100）
INST_TYPES = ("SPOT", "SWAP", "FUTURES", "OPTION")

# OKX 前置 Cloudflare：不带浏览器 UA 会返回 403 / error code: 1010
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _now_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat()


def detect_system_proxy() -> str:
    """读取 Windows 系统代理（即浏览器使用的那个），形如 "http://127.0.0.1:3067"。

    为什么要单独探测：环境变量 HTTP_PROXY 指向的未必是同一个代理。
    实测本机 HTTP_PROXY=59004 对 okx.com 返回 502，而系统代理 3067 可以正常访问。
    """
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        )
        enable, _ = winreg.QueryValueEx(key, "ProxyEnable")
        server, _ = winreg.QueryValueEx(key, "ProxyServer")
    except Exception:  # noqa: BLE001
        return ""
    if not enable or not server:
        return ""
    raw = str(server).strip()
    if "=" in raw:      # 形如 "http=127.0.0.1:8080;https=127.0.0.1:8080"
        pairs = dict(p.split("=", 1) for p in raw.split(";") if "=" in p)
        raw = pairs.get("https") or pairs.get("http") or ""
    if not raw:
        return ""
    return raw if "://" in raw else "http://" + raw


def inst_type_of(inst_id: str) -> str:
    """由 instId 后缀判定合约类型（决定 sz 单位与 tdMode 默认值）。"""
    s = str(inst_id).upper()
    if s.endswith("-SWAP"):
        return "SWAP"
    if s.endswith("-C") or s.endswith("-P"):
        return "OPTION"
    parts = s.split("-")
    if len(parts) == 3 and parts[2].isdigit():
        return "FUTURES"
    return "SPOT"


class OkxError(RuntimeError):
    """OKX 业务错误（HTTP 200 但 code != "0"）。"""

    def __init__(self, code: str, msg: str) -> None:
        super().__init__(f"OKX 错误 [{code}] {msg}")
        self.code = code
        self.msg = msg


class OkxGateway:
    """OKX v5 REST 封装：账户、品种、K 线、下单、持仓、平仓、改止损、撤单。"""

    TIMEFRAME_NAMES = ("M1", "M5", "M15", "M30", "H1", "H4", "D1")

    def __init__(
        self,
        api_key: str = "",
        secret: str = "",
        passphrase: str = "",
        flag: str = "1",            # "0" 实盘 / "1" 模拟盘（x-simulated-trading）
        td_mode: str = "cross",     # cross 全仓 / isolated 逐仓 / cash 现货
        pos_side_mode: str = "both",  # both 双向持仓 / net 单向
        proxy_url: str | None = None,   # None=自动探测系统代理；""=强制直连；否则用指定代理
        timeout: int = 15,
    ) -> None:
        self.api_key = api_key
        self.secret = secret
        self.passphrase = passphrase
        self.flag = str(flag)
        self.td_mode = td_mode
        self.pos_side_mode = pos_side_mode
        self.timeout = timeout
        self.is_connected = False
        self.package_available = True   # 纯 HTTP 实现，无外部依赖
        if proxy_url is None:
            proxy_url = detect_system_proxy()
        self.proxy_url = proxy_url or ""
        if self.proxy_url:
            self._opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": self.proxy_url, "https": self.proxy_url})
            )
        else:
            self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self._spec_cache: dict[str, dict[str, Any]] = {}
        self._spec_ts = 0.0
        self._last_error = ""

    # ---------- 底层 ----------

    def _headers(self, method: str, path: str, body: str = "") -> dict[str, str]:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + \
            f"{datetime.now(timezone.utc).microsecond // 1000:03d}Z"
        pre = f"{ts}{method.upper()}{path}{body}"
        sign = base64.b64encode(
            hmac.new(self.secret.encode(), pre.encode(), hashlib.sha256).digest()
        ).decode()
        return {
            "OK-ACCESS-KEY": self.api_key,
            "OK-ACCESS-SIGN": sign,
            "OK-ACCESS-TIMESTAMP": ts,
            "OK-ACCESS-PASSPHRASE": self.passphrase,
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "x-simulated-trading": "1" if self.flag == "1" else "0",
        }

    def _request(self, method: str, path: str, params: dict | None = None,
                 body: dict | None = None, auth: bool = False) -> list[dict | list]:
        """统一请求；返回 data 数组。业务错误抛 OkxError。"""
        query = ""
        if params:
            query = "?" + urllib.parse.urlencode(params)
        body_str = json.dumps(body, separators=(",", ":")) if body is not None else ""
        url = BASE_URL + path + query
        headers = self._headers(method, path + query, body_str) if auth else {
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }
        data = body_str.encode() if body_str else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
        try:
            with self._opener.open(req, timeout=self.timeout) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:200]
            raise OkxError(str(exc.code), f"HTTP {exc.code}: {detail}") from exc
        except Exception as exc:  # noqa: BLE001
            raise OkxError("-1", f"{type(exc).__name__}: {exc}") from exc

        # 关键：HTTP 200 也可能带回业务错误
        if str(raw.get("code")) != "0":
            raise OkxError(str(raw.get("code")), str(raw.get("msg")))
        return raw.get("data") or []

    def _public(self, path: str, params: dict | None = None) -> list[dict | list]:
        return self._request("GET", path, params=params, auth=False)

    def _private(self, method: str, path: str, body: dict | None = None) -> list[dict | list]:
        if not self.api_key:
            raise OkxError("-1", "未配置 OKX API Key")
        return self._request(method, path, body=body, auth=True)

    # ---------- 连接 / 账户 ----------

    def connect(self) -> bool:
        try:
            self._private("GET", "/api/v5/account/balance")
            self.is_connected = True
            logger.info("[OkxGateway] OKX 连接成功（flag=%s 模拟盘=%s）",
                        self.flag, self.flag == "1")
            return True
        except OkxError as exc:
            self.is_connected = False
            self._last_error = str(exc)
            logger.error("[OkxGateway] 连接失败：%s", exc)
            return False

    def disconnect(self) -> None:
        self.is_connected = False

    def get_account_info(self) -> Optional[dict[str, Any]]:
        try:
            data = self._private("GET", "/api/v5/account/balance")
        except OkxError as exc:
            logger.error("[OkxGateway] 获取账户失败：%s", exc)
            return None
        if not data:
            return None
        acc = data[0]
        details = acc.get("details") or []
        total_eq = float(acc.get("totalEq") or 0.0)
        # 现货/合约混用时代币种类多，取 USDT 作为主计价，另附全量
        usdt = next((d for d in details if d.get("ccy") == "USDT"), None)
        return {
            "balance": total_eq,
            "equity": total_eq,
            "profit": float(acc.get(" upl") or acc.get("upl") or 0.0),
            "margin": float(acc.get("imr") or 0.0),
            "free_margin": float(acc.get("availEq") or acc.get("availBal") or 0.0),
            "leverage": 0,
            "currency": "USDT",
            "login": self.api_key[:8] + "***",
            "server": "OKX" + ("-模拟盘" if self.flag == "1" else "-实盘"),
            "trade_allowed": True,
            "usdt": {
                "avail": float((usdt or {}).get("availBal") or 0.0),
                "equity": float((usdt or {}).get("eq") or 0.0),
            },
            "details": details,
            "pos_side_mode": self.pos_side_mode,
        }

    # ---------- 品种 / 规格 ----------

    def get_symbols(self, inst_type: str = "SWAP") -> list[str]:
        try:
            data = self._public("/api/v5/public/instruments", {"instType": inst_type})
        except OkxError:
            return []
        return [str(d.get("instId")) for d in data if d.get("instId")]

    def instrument(self, inst_id: str) -> Optional[dict[str, Any]]:
        kind = inst_type_of(inst_id)
        now = time.time()
        if inst_id in self._spec_cache and now - self._spec_ts < 600:
            return self._spec_cache[inst_id]
        try:
            data = self._public("/api/v5/public/instruments", {"instType": kind, "instId": inst_id})
        except OkxError:
            return None
        if not data:
            return None
        self._spec_cache[inst_id] = data[0]
        self._spec_ts = now
        return data[0]

    def get_symbol_info(self, symbol: str) -> Optional[dict[str, Any]]:
        """映射成 MT5 风格字段，让上层（尤其是手数换算）能复用同一套代码。"""
        spec = self.instrument(symbol)
        if not spec:
            return None
        tick = self.get_tick(symbol) or {}
        ct_val = float(spec.get("ctVal") or 0.0) or 1.0
        bid = float(tick.get("bid") or 0.0)
        ask = float(tick.get("ask") or 0.0)
        digits = self._digits_of(float(spec.get("tickSz") or 1.0))
        return {
            "symbol": symbol,
            "inst_type": inst_type_of(symbol),
            "bid": bid,
            "ask": ask,
            "spread_points": round(ask - bid, digits) if ask and bid else 0.0,
            "digits": digits,
            "point": float(spec.get("tickSz") or 1.0),
            # 下面三项是「交易所单位」的最小/步长/上限：衍生品为张，现货为币
            "min_lot": float(spec.get("minSz") or 0.0),
            "max_lot": float(spec.get("maxMktSz") or 0.0) or float(spec.get("maxLmtSz") or 0.0),
            "lot_step": float(spec.get("lotSz") or 1.0),
            "contract_size": ct_val,          # 1 张 = ctVal 个标的币（现货为 1）
            "ct_val": ct_val,
            "ct_val_ccy": spec.get("ctValCcy") or "",
            "ct_type": spec.get("ctType") or "",   # linear 正向 / inverse 反向
            "tick_sz": float(spec.get("tickSz") or 1.0),
            "max_leverage": float(spec.get("lever") or 0.0),
            "pip_value": ct_val,              # 加密无 pip：以「每单位价格变动 × ctVal」近似
            "state": spec.get("state") or "",
        }

    @staticmethod
    def _digits_of(tick_sz: float) -> int:
        s = f"{tick_sz:.10f}".rstrip("0")
        return len(s.split(".")[1]) if "." in s else 0

    def get_tick(self, symbol: str) -> Optional[dict[str, Any]]:
        try:
            data = self._public("/api/v5/market/ticker", {"instId": symbol})
        except OkxError:
            return None
        if not data:
            return None
        d = data[0]
        bid = float(d.get("bidPx") or 0.0)
        ask = float(d.get("askPx") or 0.0)
        return {
            "symbol": symbol,
            "bid": bid,
            "ask": ask,
            "last": float(d.get("last") or 0.0),
            "volume": float(d.get("vol24h") or 0.0),
            "spread_points": round(ask - bid, 8) if ask and bid else 0.0,
            "time": int(d.get("ts") or 0),
        }

    def funding_rate(self, symbol: str) -> Optional[dict[str, Any]]:
        """永续/交割的资金费率（现货返回 None）。"""
        if inst_type_of(symbol) == "SPOT":
            return None
        try:
            data = self._public("/api/v5/public/funding-rate", {"instId": symbol})
        except OkxError:
            return None
        if not data:
            return None
        d = data[0]
        return {
            "symbol": symbol,
            "funding_rate": float(d.get("fundingRate") or 0.0),
            "next_funding_time": int(d.get("fundingTime") or 0),
            "premium": float(d.get("premium") or 0.0),
            "settle": _now_iso(int(d.get("fundingTime") or 0)),
        }

    # ---------- K 线 ----------

    @staticmethod
    def _candle_to_bar(row: list) -> dict[str, Any]:
        return {
            "time": _now_iso(int(row[0])),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
        }

    def get_rates(self, symbol: str, timeframe: str = "M15", count: int = 300) -> list[dict[str, Any]]:
        """拉取 K 线，统一返回**升序**（旧→新），与 MT5Gateway 一致。"""
        bar = BAR_MAP.get(str(timeframe).upper(), "15m")
        collected: list[list] = []
        after: Optional[int] = None
        while len(collected) < count:
            want = min(CANDLE_LIMIT, count - len(collected))
            params = {"instId": symbol, "bar": bar, "limit": str(want)}
            if after:
                params["after"] = str(after)
            try:
                data = self._public("/api/v5/market/candles", params)
            except OkxError as exc:
                logger.warning("[OkxGateway] K 线拉取失败 %s %s: %s", symbol, timeframe, exc)
                break
            # /market/candles 只覆盖最近约 1440 根，深度耗尽后必须转 /market/history-candles
            if not data:
                data = self._history_candles(symbol, bar, min(100, want), after)
                if not data:
                    break
                collected.extend(data)
                after = int(data[-1][0])
                if len(data) < 100:
                    break
                time.sleep(0.12)              # 限频保护（历史端点更严格）
                continue

            collected.extend(data)
            after = int(data[-1][0])          # 本批最早一根的时间戳
            if len(data) < want:
                # 常规端点到底了，尝试用历史端点继续往回翻
                more = self._history_candles(symbol, bar, min(100, count - len(collected)), after)
                if more:
                    collected.extend(more)
                    after = int(more[-1][0])
                    if len(more) < 100:
                        break
                    time.sleep(0.12)
                    continue
                break
            time.sleep(0.05)                  # 限频保护

        bars = [self._candle_to_bar(r) for r in collected]
        bars.reverse()                        # 降序 → 升序（关键！）
        return bars[-count:]

    def _history_candles(self, symbol: str, bar: str, want: int,
                         after: Optional[int]) -> list[list]:
        params = {"instId": symbol, "bar": bar, "limit": str(min(100, want))}
        if after:
            params["after"] = str(after)
        try:
            return self._public("/api/v5/market/history-candles", params)
        except OkxError:
            return []

    def get_rates_range(self, symbol: str, timeframe: str = "M15",
                        date_from: Any = None, date_to: Any = None) -> list[dict[str, Any]]:
        """按日期区间拉取：先按根数粗估，再裁剪到区间。"""
        bar = BAR_MAP.get(str(timeframe).upper(), "15m")
        step = BAR_MS.get(bar, 900_000)
        if date_to is None:
            date_to = datetime.now(timezone.utc)
        if date_from is None:
            date_from = date_to - timedelta(days=30)
        span_ms = (date_to - date_from).total_seconds() * 1000.0
        need = int(span_ms / step) + 2
        bars = self.get_rates(symbol, timeframe, max(need, 2))
        start_ms = date_from.timestamp() * 1000
        end_ms = date_to.timestamp() * 1000
        return [b for b in bars
                if start_ms <= datetime.fromisoformat(b["time"]).timestamp() * 1000 <= end_ms]

    # ---------- 下单 ----------

    def _pos_side(self, side: str) -> str:
        if self.pos_side_mode == "net":
            return "net"
        return "long" if side.lower() == "buy" else "short"

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
        """下单。volume 语义：衍生品=张数，现货=标的币数量。

        SL/TP 通过 attachAlgoOrds 挂载（成交后生效）；ordPx=-1 表示触发后市价成交。
        """
        kind = inst_type_of(symbol)
        td_mode = "cash" if kind == "SPOT" else self.td_mode
        side_map = {"BUY": "buy", "SELL": "sell"}
        if action_type not in side_map:
            return {"success": False, "message": f"暂不支持的订单类型: {action_type}"}
        side = side_map[action_type]

        body: dict[str, Any] = {
            "instId": symbol,
            "tdMode": td_mode,
            "side": side,
            "ordType": "market" if price is None else "limit",
            "sz": self._fmt_sz(symbol, volume),
        }
        if kind != "SPOT" and self.pos_side_mode != "net":
            body["posSide"] = self._pos_side(side)
        if price is not None:
            body["px"] = str(price)

        # 止损止盈：OKX 主单不带，必须挂条件单
        if sl or tp:
            algo: dict[str, Any] = {}
            if tp:
                algo.update({"tpTriggerPx": str(tp), "tpOrdPx": "-1", "tpTriggerPxType": "last"})
            if sl:
                algo.update({"slTriggerPx": str(sl), "slOrdPx": "-1", "slTriggerPxType": "last"})
            if algo:
                body["attachAlgoOrds"] = [algo]

        try:
            data = self._private("POST", "/api/v5/trade/order", body)
        except OkxError as exc:
            return {"success": False, "message": f"发单失败: {exc}", "code": exc.code}
        row = data[0] if data else {}
        if str(row.get("sCode")) not in ("0", "", "None"):
            return {
                "success": False,
                "message": f"发单失败 [{row.get('sCode')}]: {row.get('sMsg')}",
            }
        return {
            "success": True,
            "ticket": str(row.get("ordId") or ""),
            "volume": volume,
            "price": price or 0.0,
            "message": "订单已提交",
            "retcode": row.get("sCode"),
            "comment": comment,
            "cl_ord_id": row.get("clOrdId"),
            "request": body,
        }

    def _fmt_sz(self, symbol: str, volume: float) -> str:
        """按 lotSz 精度格式化数量，避免被拒单。"""
        spec = self.instrument(symbol)
        lot_sz = float((spec or {}).get("lotSz") or 1.0)
        decimals = self._digits_of(lot_sz)
        return f"{round(float(volume), decimals):.{decimals}f}"

    # ---------- 持仓 / 平仓 / 改止损 ----------

    @staticmethod
    def make_pos_key(inst_id: str, pos_side: str) -> str:
        return f"{inst_id}|{pos_side}"

    @staticmethod
    def parse_pos_key(ticket: str) -> tuple[str, str]:
        if "|" in ticket:
            inst_id, pos_side = ticket.split("|", 1)
            return inst_id, pos_side
        return ticket, ""

    def get_positions(self) -> list[dict[str, Any]]:
        try:
            data = self._private("GET", "/api/v5/account/positions")
        except OkxError as exc:
            logger.error("[OkxGateway] 获取持仓失败：%s", exc)
            return []
        out: list[dict[str, Any]] = []
        for p in data:
            size = float(p.get("pos") or 0.0)
            if abs(size) < 1e-12:
                continue
            pos_side = p.get("posSide") or ("long" if size > 0 else "short")
            out.append({
                "ticket": self.make_pos_key(p.get("instId", ""), pos_side),
                "symbol": p.get("instId"),
                "type": "BUY" if pos_side == "long" else "SELL",
                "pos_side": pos_side,
                "volume": abs(size),
                "price_open": float(p.get("avgPx") or 0.0),
                "price_current": float(p.get("last") or p.get("markPx") or 0.0),
                "sl": float(p.get("slTriggerPx") or 0.0) or None,
                "tp": float(p.get("tpTriggerPx") or 0.0) or None,
                "profit": float(p.get("upl") or 0.0),
                "margin": float(p.get("margin") or 0.0),
                "leverage": float(p.get("lever") or 0.0),
                "margin_mode": p.get("mgnMode"),
                "liquidation_px": float(p.get("liqPx") or 0.0) or None,
                "time": int(p.get("uTime") or 0),
                "comment": "",
            })
        return out

    def get_orders(self) -> list[dict[str, Any]]:
        try:
            data = self._private("GET", "/api/v5/trade/orders-pending")
        except OkxError:
            return []
        return [{
            "ticket": str(o.get("ordId") or ""),
            "symbol": o.get("instId"),
            "type": o.get("ordType"),
            "volume_initial": float(o.get("sz") or 0.0),
            "price_open": float(o.get("px") or 0.0),
            "sl": float(o.get("slTriggerPx") or 0.0) or None,
            "tp": float(o.get("tpTriggerPx") or 0.0) or None,
            "pos_side": o.get("posSide"),
            "comment": "",
        } for o in data]

    def _pending_algos(self, symbol: str) -> list[dict[str, Any]]:
        kind = inst_type_of(symbol)
        try:
            data = self._private("GET", "/api/v5/trade/orders-algo-pending", body=None)
        except OkxError:
            data = []
        if data is None:
            data = []
        return [a for a in data if a.get("instId") == symbol] or \
               [a for a in self._algo_list(kind, symbol) if a.get("instId") == symbol]

    def _algo_list(self, inst_type: str, symbol: str) -> list[dict[str, Any]]:
        try:
            return self._request(
                "GET", "/api/v5/trade/orders-algo-pending",
                params={"instType": inst_type, "instId": symbol}, auth=True,
            )
        except OkxError:
            return []

    def close_position(self, ticket: str, volume: Optional[float] = None) -> dict[str, Any]:
        inst_id, pos_side = self.parse_pos_key(ticket)
        if not inst_id:
            return {"success": False, "message": f"无效的持仓标识: {ticket}"}
        kind = inst_type_of(inst_id)
        td_mode = "cash" if kind == "SPOT" else self.td_mode

        if volume is None and kind != "SPOT":
            # 全平专用接口（更快、不占保证金）
            body = {"instId": inst_id, "posSide": pos_side, "mgnMode": td_mode, "ccy": "USDT"}
            try:
                self._private("POST", "/api/v5/trade/close-position", body)
                return {"success": True, "ticket": ticket, "message": "已全平"}
            except OkxError as exc:
                return {"success": False, "message": f"全平失败: {exc}"}

        # 部分平仓 / 现货：反向单
        side = "sell" if pos_side == "long" else "buy"
        body = {
            "instId": inst_id,
            "tdMode": td_mode,
            "side": side,
            "ordType": "market",
            "sz": self._fmt_sz(inst_id, volume or 0.0),
            "reduceOnly": True,
        }
        if kind != "SPOT" and self.pos_side_mode != "net":
            body["posSide"] = pos_side
        try:
            data = self._private("POST", "/api/v5/trade/order", body)
        except OkxError as exc:
            return {"success": False, "message": f"平仓失败: {exc}"}
        row = data[0] if data else {}
        if str(row.get("sCode")) not in ("0", "", "None"):
            return {"success": False, "message": f"平仓失败 [{row.get('sCode')}]: {row.get('sMsg')}"}
        return {"success": True, "ticket": ticket, "message": "平仓已提交"}

    def close_all(self) -> list[dict[str, Any]]:
        return [self.close_position(p["ticket"]) for p in self.get_positions()]

    def modify_position(self, ticket: str, sl: Optional[float] = None,
                        tp: Optional[float] = None) -> dict[str, Any]:
        """改止损/止盈。

        ⚠️ 与 MT5 最大差异：OKX 不能直接改主单，必须 amend 挂着的算法单（algoId）。
        若没有已挂条件单（开仓时未带 SL/TP），则新建一张 oco 条件单。
        """
        inst_id, pos_side = self.parse_pos_key(ticket)
        if not inst_id:
            return {"success": False, "message": f"无效的持仓标识: {ticket}"}
        kind = inst_type_of(inst_id)
        td_mode = "cash" if kind == "SPOT" else self.td_mode

        algos = self._pending_algos(inst_id)
        target = None
        for a in algos:
            if self.pos_side_mode == "net" or not pos_side or a.get("posSide") == pos_side:
                target = a
                break

        if target:
            body = {"instId": inst_id, "algoId": str(target.get("algoId"))}
            if sl:
                body["newSlTriggerPx"] = str(sl)
            if tp:
                body["newTpTriggerPx"] = str(tp)
            try:
                self._private("POST", "/api/v5/trade/amend-algo-order", body)
                return {"success": True, "ticket": ticket, "sl": sl, "tp": tp,
                        "message": "止损已更新", "algo_id": target.get("algoId")}
            except OkxError as exc:
                return {"success": False, "message": f"改单失败: {exc}"}

        # 没有已挂条件单 → 新建 oco
        close_side = "sell" if pos_side == "long" else "buy"
        body = {
            "instId": inst_id, "tdMode": td_mode, "ordType": "oco",
            "side": close_side, "sz": self._fmt_sz(inst_id, self._pos_size(inst_id, pos_side)),
        }
        if kind != "SPOT" and self.pos_side_mode != "net":
            body["posSide"] = pos_side
        if sl:
            body["slTriggerPx"] = str(sl)
        if tp:
            body["tpTriggerPx"] = str(tp)
        try:
            self._private("POST", "/api/v5/trade/order-algo", body)
            return {"success": True, "ticket": ticket, "sl": sl, "tp": tp, "message": "已新建止损单"}
        except OkxError as exc:
            return {"success": False, "message": f"新建止损单失败: {exc}"}

    def _pos_size(self, inst_id: str, pos_side: str) -> float:
        for p in self.get_positions():
            if p["symbol"] == inst_id and (not pos_side or p["pos_side"] == pos_side):
                return p["volume"]
        return 0.0

    def cancel_order(self, ticket: str) -> dict[str, Any]:
        inst_id, _ = self.parse_pos_key(ticket)
        try:
            self._private("POST", "/api/v5/trade/cancel-order",
                          {"instId": inst_id, "ordId": ticket})
            return {"success": True, "ticket": ticket, "message": "撤单成功"}
        except OkxError as exc:
            return {"success": False, "message": f"撤单失败: {exc}"}

    # ---------- 历史成交（复盘） ----------

    def get_closed_trade(self, ticket: str) -> Optional[dict[str, Any]]:
        return None   # OKX 需按 instId+时间窗查成交明细，复盘链路待接入时实现

    def get_closed_trades(self, days: int = 30) -> list[dict[str, Any]]:
        try:
            data = self._private("GET", "/api/v5/trade/fills-history",
                                 {"instType": "SWAP"})
        except OkxError:
            return []
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max(int(days), 1))).timestamp() * 1000
        out: list[dict[str, Any]] = []
        for f in data or []:
            ts = float(f.get("ts") or 0)
            if ts < cutoff:
                continue
            out.append({
                "ticket": f.get("ordId"),
                "symbol": f.get("instId"),
                "side": "long" if (f.get("posSide") or "") == "long" else "short",
                "volume": float(f.get("fillSz") or 0.0),
                "entry_time": int(ts),
                "entry_price": float(f.get("fillPx") or 0.0),
                "exit_time": int(ts),
                "exit_price": float(f.get("fillPx") or 0.0),
                "pnl": float(f.get("fillPnl") or 0.0),
                "reason": "OKX 成交明细",
            })
        return out
