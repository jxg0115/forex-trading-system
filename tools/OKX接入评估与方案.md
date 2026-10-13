# OKX（欧易）接入评估与方案

> 本次为**只读评估**：未修改任何业务代码。所有改造点均标注到文件与行号，供后续决策。
> 评估日期：2026-09-18 ｜ 范围：后端连接方案 + 前端方案 + 风险与工作量

---

## 0. 结论先行

**能接，但不是"加一个适配器"这么简单。**

| 判断项 | 结论 |
|---|---|
| Broker 抽象层是否够用 | ✅ 够用。`oems/base.py` 只有 5 个抽象方法，接口干净，可扩展 |
| 真正的难点在哪 | ❌ 不在 Broker，在 **Gateway 层** 与 **全系统的 MT5 语义假设**（手数 / pip / 休市 / ticket） |
| 需要新增的代码量 | 约 **1200~1600 行**新增 + **9 个文件**改造 |
| 分阶段工期 | 只读行情 1 天 → 下单 3~4 天 → 止损体系 3~5 天 → 实盘观察 |
| 最大陷阱 | **改止损**：MT5 一个 `modify_position(ticket)` 搞定，OKX 必须先查到 `algoId` 再 amend 算法单。移动止损引擎要重写映射层 |
| 策略层面（重要） | ⚠️ 现有 11 个因子是在 XAUUSD 上挖出来的，样本外验证已证明跨品种即失效。**这次接入是"工程打通"，不等于"策略能用"** |

**一句话建议**：分 4 期做，第一期只打通"只读行情 + 沙盒下单"，不要一上来就把现有引擎切过去。

---

## 1. 现状勘察（证据）

### 1.1 三层结构

```
Broker(ABC, 5 方法)          oems/base.py:10-44        ← 干净，可扩展
   └─ MT5Broker              oems/mt5_broker.py:14     ← 薄适配层（153 行）
        └─ MT5Gateway        oems/mt5_gateway.py:27    ← 真正的重活（594 行，21 个方法）
```

`MT5Gateway` 实际提供的方法（OKX 网关需逐一提供对应实现）：

| 类别 | 方法 |
|---|---|
| 连接 | `connect` `disconnect` |
| 账户/品种 | `get_account_info` `get_symbols` `get_symbol_info` `get_tick` |
| 行情 | `get_rates` `get_rates_range` `copy_ticks_range` |
| 交易 | `place_order` `close_position` `close_all` `modify_position` `cancel_order` |
| 持仓/挂单 | `get_positions` `get_orders` |
| 历史成交 | `get_closed_trade` `get_closed_trades` |

### 1.2 耦合面统计

**后端**（`grep -c "mt5|MT5|MetaTrader5"`，取前 15）：

| 文件 | 引用数 | 性质 |
|---|---|---|
| `oems/mt5_gateway.py` | 116 | 本体 |
| `api/app.py` | 41 | **组装中枢，需重点改造** |
| `api/mt5.py` | 55 | 专用路由（可整体复制/泛化） |
| `api/trading.py` | 36 | 交易路由 |
| `oems/mt5_broker.py` | 12 | 适配层 |
| `oems/order_manager.py` | 12 | **`mt5_ticket` 数据模型字段** |
| `config.py` | 6 | 配置项 |
| `market_data/service.py` | 6 | **行情唯一入口** |
| `api/ai.py` | 7 | 学习链路 |
| `oems/position_monitor.py` / `backtest_store/repository.py` / `api/tick_archive.py` | 7 / 5 / 5 | 复盘链路 |
| `signal_matcher/executor.py` | 2 | 直接 `from oems.mt5_gateway import MT5Gateway` |

**前端**：

| 文件 | 引用数 |
|---|---|
| `ui_layer/src/components/Mt5Panel.tsx` | 28 |
| `ui_layer/src/api.ts` | 全部 `mt5*` 端点（约 20 个方法） |
| `ui_layer/src/App.tsx` | 11 |
| `ui_layer_v2/src/panels.tsx` | 17 |
| `SltpPolicyPanel.tsx` / `FactorMiningPanel.tsx` / `OrderLogPanel.tsx` | 2 / 3 / 1 |

> ⚠️ **发现两套前端**：`ui_layer`（React 19 + lightweight-charts，`api/app.py:382` 挂载的是 `ui_layer/dist`）与 `ui_layer_v2`（React 18 + antd + klinecharts）。`app.py` 只挂载 v1，**改造前需先确认哪套是主线**，否则会白做一遍。

### 1.3 三个致命耦合点（精确坐标）

```python
# ① 行情唯一入口 —— 硬引用
market_data/service.py:12   from oems.mt5_gateway import MT5Gateway
market_data/service.py:18   def __init__(self, gateway: Optional[MT5Gateway] = None)
market_data/service.py:25/30/48/49  全走 self.gateway

# ② 组装中枢 —— 单一 gateway 实例贯穿 12 个组件
api/app.py:50      mt5_gateway: MT5Gateway | None
api/app.py:94-149  MT5Broker / OrderManager / PendingExpirationManager /
                   MT5PositionMonitor / SmartStopEngine / SltpEngine /
                   SltpPolicyManager / SignalExecutor 全部吃同一个 mt5_gateway

# ③ 前端品种列表 —— 硬绑 MT5
ui_layer/src/App.tsx:449   const res = await api.mt5Symbols();
ui_layer/src/App.tsx:437   const data = await api.mt5Klines(symbol, timeframe, 500);
```

---

## 2. 语义冲突清单（本次评估的核心产出）

这 14 条是真正的成本来源，每一条都必须在代码里显式处理，否则会静默出错。

| # | 维度 | MT5 / 外汇现状 | OKX 现实 | 处理方案 |
|---|---|---|---|---|
| 1 | 品种标识 | `XAUUSD` `EURUSD` | `BTC-USDT-SWAP`（永续）/ `BTC-USDT`（现货）/ `BTC-USDT-260926`（交割） | 加 `SymbolRegistry` 按形态路由，DB 不加前缀 |
| 2 | 仓位单位 | `lots` 手（0.01 起，1 手 = 10 万单位） | **张**（`sz`），1 张 = `ctVal` 个标的币（BTC-USDT-SWAP: `ctVal=0.01`） | 新增 `crypto_sizing.py`，用 `ctVal` 换算 |
| 3 | 最小步进 | `volume_step` | `lotSz`（张数步进）+ `minSz` + `maxMktSz` | 三重夹逼后取整 |
| 4 | pip 概念 | `pip_size` / `pip_value_per_lot` | **不存在** | 仓位口径改为「美元风险 ÷ (止损距离 × ctVal)」 |
| 5 | 价格精度 | `digits` / `point` | `tickSz`（如 0.1） | 前端格式化 + 下单前对齐 |
| 6 | 点差 | `spread_points`（整数点） | 盘口 `bidSz/askSz`，`spread = ask − bid`，随深度变 | `spread_alert_threshold=50`（点）对加密无意义，需改按 **百分比** 阈值 |
| 7 | 开仓带 SL/TP | 一条请求带 `sl` `tp` | 主单不带；用 `attachAlgoOrds`（成交后挂算法单）或 `/api/v5/trade/order-algo`（`ordType=oco`） | `place_order` 内部拆成「下单 + 挂止损」两步 |
| 8 | **改止损** | `modify_position(ticket, sl, tp)` 一条请求 | 必须 `amend-algo-order` + `algoId`；algoId 需先查 `orders-algo-pending` | **最大陷阱**。需维护 `ticket → algoId` 映射表 |
| 9 | 平仓 | 反向同量 + `position` ticket | `close-position`（全平）或 `reduceOnly=true` 反向单（部分平） | `close_position` 内部按 volume 分派 |
| 10 | 订单标识 | `ticket` int | `ordId` / `algoId` 字符串（19 位） | `OrderLog.mt5_ticket` 是 str，✅ 可直接存；但比较逻辑需改 |
| 11 | 交易时段 | **24/5**，周末闭市 + 每日 23:00~01:00 UTC 维护窗口（`api/market_sessions.py:25`） | **24/7 无休市** | 加密品种 bypass 时段判断，否则周末信号全被拦 |
| 12 | 账户 | 单一货币（USD），`leverage` 单值 | 多币种，**逐仓/全仓**（`tdMode`），币种级杠杆 | 账户面板改造；`setLeverage` 需按 instId 单独设置 |
| 13 | 资金费率 | 无（外汇是隔夜利息） | 每 8h 结算一次，多头付费率 | **新增**：持仓成本模型 + 前端倒计时 |
| 14 | 时间/K线 | 秒级时间戳，服务器时区；`copy_rates_from_pos` 返回**升序** | 毫秒 UTC；`/api/v5/market/candles` 返回**降序**（最新在前），`limit` 上限 300，历史需 `after/before` 分页或走 `history-candles` | **必须 reverse**；长历史要分页循环 |

> 第 14 条最容易写出"看起来能跑、实际全错"的代码：K 线顺序反了，均线/ATR/因子信号全部反向。

---

## 3. 后端连接方案

### 3.1 目标架构

```
                    ┌──────────────────────────────┐
前端 /api/broker/*  │   GatewayRouter              │
        ▲           │   resolve(symbol) → gateway  │
        │           └───────┬──────────────┬───────┘
   api/broker.py            │              │
        │            ┌──────▼─────┐  ┌─────▼──────┐
   AppState          │ MT5Gateway │  │ OkxGateway │
        │            │  (594 行)  │  │  (新增)    │
        │            └──────┬─────┘  └─────┬──────┘
        │                   │              │
        │            ┌──────▼──────────────▼──────┐
        └───────────►│  GatewayProtocol (结构化)   │
                     └────────────────────────────┘
                     ▲                             ▲
              MT5Broker                       OkxBroker
              (既有)                          (新增，实现 Broker ABC)
```

**关键设计决策：按 symbol 路由，不做全局切换。**

理由：
- 现有 DB（factors / trades / sltp_cases）都以 `symbol` 为键，全局切换会导致数据串味
- `BTC-USDT-SWAP` 这类 instId 与 `XAUUSD` 天然不重名，冲突概率 ≈ 0，**无需加前缀、无需改 DB**
- 路由规则极简：

```python
# market_data/symbol_registry.py（新增，约 40 行）
import re
_OKX_SWAP  = re.compile(r"^[A-Z0-9]+-[A-Z0-9]+-SWAP$")
_OKX_SPOT  = re.compile(r"^[A-Z0-9]+-[A-Z0-9]+$")
_OKX_FUT   = re.compile(r"^[A-Z0-9]+-[A-Z0-9]+-\d{6}$")

def venue_of(symbol: str) -> str:
    s = symbol.upper()
    if _OKX_SWAP.match(s) or _OKX_FUT.match(s):
        return "okx"
    if _OKX_SPOT.match(s) and s.split("-")[1] in {"USDT", "USDC", "BTC", "ETH"}:
        return "okx"
    return "mt5"
```

### 3.2 新增文件清单

| 文件 | 行数估算 | 职责 |
|---|---|---|
| `oems/okx_gateway.py` | ~450 | REST 客户端 + 签名 + 21 个方法对标 `MT5Gateway` |
| `oems/okx_broker.py` | ~180 | 实现 `Broker` ABC，桥接 `OkxGateway` |
| `oems/gateway_protocol.py` | ~60 | `Protocol` 定义共同契约（让 `MarketDataService` 不再依赖具体类） |
| `oems/okx_specs.py` | ~120 | 合约规格缓存：`ctVal / lotSz / minSz / tickSz / lever / ctType`，带 TTL |
| `market_data/symbol_registry.py` | ~40 | symbol → venue 路由 |
| `risk_sizing/crypto_sizing.py` | ~90 | 加密口径仓位换算 |
| `api/broker.py` | ~200 | 泛化路由（`/api/broker/*`），现有 `/api/mt5/*` 保留为兼容别名 |

### 3.3 逐文件改造点

| 文件:行 | 现状 | 改法 | 风险 |
|---|---|---|---|
| `market_data/service.py:12,18` | 硬引用 `MT5Gateway` | 构造注入 `GatewayRouter`，类型标注改为 `GatewayProtocol` | 低 |
| `market_data/service.py:25,30,35,45` | `self.gateway` 单实例 | 改为 `self._gw(symbol)` | 低 |
| `market_data/service.py:76` | `extra={"spread_points": ...}` | 加密分支改为 `{"spread": ask-bid, "funding_rate": ...}` | 低 |
| `api/app.py:50` | `mt5_gateway: MT5Gateway \| None` | 增 `gateways: dict[str, Any]`，`mt5_gateway` 保留为 `gateways["mt5"]` 别名 | **中**（12 处引用） |
| `api/app.py:90-149` | 单一 gateway 组装 | 按 venue 分发；`MT5PositionMonitor` / `SmartStopEngine` 等需支持双实例或按当前活跃 venue 构造 | **高** |
| `api/app.py:173-259` | `_system_loop` 里 MT5 心跳/重连/点差告警 | 抽出 `_venue_health(gw)`，加密分支跳过休市告警 | 中 |
| `config.py:56-61` | MT5 配置组 | 新增 `okx_api_key / okx_secret / okx_passphrase / okx_flag(0实盘/1模拟) / okx_td_mode / okx_pos_side` | 低 |
| `risk_sizing/sizing.py:10-27` | `lots = risk/(sl_pips × pip_value)` | 增加 `mode` 分支；加密走 `crypto_sizing.calculate_contracts()` | 中 |
| `oems/trailing_stop.py`（3 处 mt5） | 调 `modify_position` | 需先解析 algoId → 改 `amend-algo-order` | **高** |
| `oems/smart_stop.py`（2 处） | 同上 | 同上 | **高** |
| `oems/sltp_manager.py`（1 处） | 同上 | 同上 | **高** |
| `api/market_sessions.py:33-53` | 周末 + 每日窗口判定 | 入参加 `venue`，OKX 直接返回 `is_open=True, session="always_open"` | 低 |
| `signal_matcher/executor.py:17` | `from oems.mt5_gateway import MT5Gateway` | 改为 protocol 类型标注（仅类型） | 低 |
| `oems/order_manager.py:60,136,200-298` | `mt5_ticket` 匹配 | 字段名保留（已是 str），但**不要**再假设可 `int()` 转换 | 中 |

### 3.4 OKX 鉴权实现要点

```python
# oems/okx_gateway.py 核心签名（示意，未写入项目）
import base64, hashlib, hmac, time
from datetime import datetime, timezone

def _sign(self, method: str, path: str, body: str = "") -> dict[str, str]:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
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
        "x-simulated-trading": "1" if self.flag == "1" else "0",
    }
```

**统一响应校验**：所有响应都是 `{code, msg, data[]}`，**必须判 `code != "0"` 抛错**——OKX 在 HTTP 200 里返回业务错误，不校验会静默拿到空 data。

### 3.5 关键映射：下单（最需要小心的一处）

MT5 现状（一条请求）：
```python
gateway.place_order(symbol="XAUUSD", action_type="BUY", volume=0.10, sl=..., tp=...)
```

OKX 等价（**语义拆成两步**）：
```http
POST /api/v5/trade/order
{
  "instId": "BTC-USDT-SWAP",
  "tdMode": "cross",            # cash(现货) / cross(全仓) / isolated(逐仓)
  "side": "buy",
  "ordType": "market",          # market|limit|post_only|fok|ioc
  "sz": "12",                   # 张数（不是币数！= 目标币量 / ctVal）
  "posSide": "net",             # net(单向) / long / short(双向持仓)
  "tgtCcy": "base_ccy",         # 可选：想以"币"为单位下单时指定，省去手算张数
  "attachAlgoOrds": [{
      "tpTriggerPx": "68500", "tpOrdPx": "-1", "tpTriggerPxType": "last",
      "slTriggerPx": "64200", "slOrdPx": "-1", "slTriggerPxType": "last"
  }]
}
```
- `tpOrdPx: "-1"` / `slOrdPx: "-1"` = 触发后市价成交
- **返回的是 `ordId`，不是 algoId**。止损单的 `algoId` 要事后查 `GET /api/v5/trade/orders-algo-pending?ordType=conditional` 才能拿到
- 因此 `OkxBroker` 内部必须维护 `ordId → algoId` 映射（建议落 DB，`OrderLog.raw_response` 有字段可存）

### 3.6 关键映射：改止损（本方案的头号陷阱）

| 步骤 | MT5 | OKX |
|---|---|---|
| 1 | `modify_position(ticket, sl, tp)` | 查 `GET /api/v5/trade/orders-algo-pending` 找该持仓的 `algoId` |
| 2 | — | `POST /api/v5/trade/amend-algo-order` `{instId, algoId, newSlTriggerPx, newTpTriggerPx}` |
| 3 | 同步返回 | **异步**：需确认 `sCode == "0"` |

> 现有 `trailing_stop.py`（422 行）、`smart_stop.py`（821 行）、`sltp_manager.py`（610 行）都以「同步改单」为前提编写。
> **建议：这三处的 OKX 支持放到第 3 期，前两期加密品种禁用移动止损**，先用「开仓即挂死 SL/TP」跑通。

### 3.7 仓位换算公式

```python
# 现状（risk_sizing/sizing.py:24）
raw_lot = risk_amount / (sl_pips * pip_value)        # 外汇

# 加密（拟新增 risk_sizing/crypto_sizing.py）
# U 本位 linear 合约：1 张名义价值 = ctVal 个标的币
#   例：BTC-USDT-SWAP，ctVal=0.01 BTC，价格 67000 → 1 张 = 670 USD
def calculate_contracts(equity, risk_pct, entry, stop, ct_val, lot_sz, min_sz, max_sz):
    risk_usd   = equity * risk_pct / 100.0
    stop_dist  = abs(entry - stop)                  # 价格差（不是 pip）
    loss_per_c = stop_dist * ct_val                 # 每张的亏损
    raw        = risk_usd / loss_per_c if loss_per_c > 0 else 0.0
    n          = round(raw / lot_sz) * lot_sz       # 对齐 lotSz
    return max(min_sz, min(max_sz, n))              # 三重夹逼
```

前端需同步：手数输入框要支持 **张 / 币 / USDT 名义** 三种模式（对应 `tgtCcy` 参数）。

### 3.8 限频与实时

| 项 | 数值 | 应对 |
|---|---|---|
| REST 下单 | 约 20 次 / 2s | 令牌桶；下单走串行队列 |
| REST 行情 | 约 20 次 / 2s | K 线本地缓存（当前 `market_tick_ms=3000` 轮询，OKX 可直接复用，无需立刻上 WS） |
| WS 心跳 | 25s 发一次 `ping` | 断线重连需带退避 |
| 建议频道 | `candle1H`（K线）、`tickers`、`orders`（订单回推）、`funding-rate` | 第 3 期再上 |

---

## 4. 前端方案

### 4.1 现状

- 主线：`ui_layer`（React 19 + Vite + lightweight-charts + lucide-react），12 个源文件
- 品种是**单一全局 state**：`App.tsx:175 const [symbol, setSymbol] = useState("EURUSD")`，列表来自 `api.mt5Symbols()`（:449）
- K 线：`App.tsx:437 api.mt5Klines(symbol, timeframe, 500)`
- 有 `Mt5Panel.tsx`（28 处 MT5 引用）：账户、持仓、下单、手数计算全在这里

### 4.2 改造清单（按优先级）

| 优先级 | 改动 | 说明 |
|---|---|---|
| P0 | **交易所切换器**（顶栏下拉：MT5 / OKX） | 决定 symbol 列表来源与所有面板的数据源。只在 `App.tsx` 增加 1 个 state `venue` |
| P0 | **品种列表分流** | `loadSymbols()` 按 venue 调 `/api/broker/symbols?venue=okx`。OKX 返回几百个，需支持搜索 + 按 `instType` 过滤（SWAP/SPOT/FUTURES） |
| P0 | **K 线接入** | 复用 `api.mt5Klines` 的返回结构即可（`Bar[]`），**后端负责 reverse 成升序**，前端零改动 |
| P1 | **合约规格提示** | 下单面板显示 `ctVal / lotSz / minSz / tickSz / 最大杠杆`，避免用户按外汇习惯输入 0.01 手（加密最小就是 `minSz` 张） |
| P1 | **手数输入三态** | 张 / 币 / USDT 名义，实时回显名义价值与占用保证金 |
| P1 | **时段面板 bypass** | `MarketStatusPanel` 对加密显示「7×24 连续交易，无休市窗口」，隐藏周末闭市倒计时 |
| P2 | **资金费率条** | 新增组件：当前费率 + 下次结算倒计时（8h/次）。影响持仓成本，必须可见 |
| P2 | **账户面板改造** | 从「余额/净值/杠杆/货币」单值 → 「USDT 可用 / 保证金率 / 维持保证金 / 逐仓-全仓模式」 |
| P2 | **持仓面板扩展** | 增加 `posSide`（多/空）、`mgnMode`（cross/isolated）、**强平价**、未实现盈亏 |
| P3 | **K 线精度与量纲** | 价格按 `tickSz` 定小数位；成交量单位从 `tick_volume` 改为「张」或「币」，tooltip 要标注 |
| P3 | **`Mt5Panel` 泛化** | 重命名为 `BrokerPanel`，内部按 venue 切换字段。或直接新增 `OkxPanel.tsx`（改动小、风险低，**推荐**） |

### 4.3 新增 API 契约

建议**统一前缀 `/api/broker/*`**，而不是复制一套 `/api/okx/*`。现有 `/api/mt5/*` 保留为兼容别名，新代码一律走 `/api/broker/*`。

| 端点 | 方法 | 用途 |
|---|---|---|
| `/api/broker/venues` | GET | 返回可用交易所及连接状态 |
| `/api/broker/symbols?venue=okx&instType=SWAP` | GET | 品种列表（带 instType 过滤） |
| `/api/broker/instrument?symbol=BTC-USDT-SWAP` | GET | 合约规格 `ctVal/lotSz/minSz/tickSz/lever/ctType` |
| `/api/broker/funding-rate?symbol=...` | GET | 当前 + 预测资金费率、下次结算时间 |
| `/api/broker/account?venue=okx` | GET | 多币种余额 |
| `/api/broker/positions?venue=okx` | GET | 持仓（含 posSide / mgnMode / 强平价） |
| `/api/broker/order` | POST | 下单（内部处理 attachAlgoOrds） |
| `/api/broker/close` | POST | 平仓（全平 / 部分） |
| `/api/broker/amend-sl` | POST | 改止损（内部解析 algoId） |
| `/api/broker/calc-size` | GET | 手数换算（三态回显） |

前端 `api.ts` 对应新增约 10 个方法，`Mt5Panel` 之外的组件基本不用动。

---

## 5. 实施路线（4 期）

| 期 | 目标 | 交付物 | 预估 |
|---|---|---|---|
| **0** | 只读行情打通 | `OkxGateway` 公开端点（行情/品种/规格）+ 前端能看到 BTC K 线、能跑指标与因子回测 | 1 天 |
| **1** | 鉴权 + 沙盒交易 | 签名、账户、持仓、下单、平仓（`x-simulated-trading: 1`）；SL/TP 用 attachAlgoOrds | 2~3 天 |
| **2** | 引擎接入 | `GatewayRouter` + `OkxBroker` + `symbol_registry` + `crypto_sizing` + `api/broker.py`；**移动止损暂禁用** | 2~3 天 |
| **3** | 止损体系适配 | `trailing_stop` / `smart_stop` / `sltp_manager` 的 algoId 映射与异步确认；时段 bypass；前端资金费率、账户、持仓面板 | 3~5 天 |

**第 0 期值得单独做**：公开行情端点**不需要 API Key**，可以零风险验证「现有因子/指标在 BTC 上跑出来是什么样」。这一步如果结果很差，后面三期就该重新评估投入。

---

## 6. 风险

| 级别 | 风险 | 说明 |
|---|---|---|
| 🔴 高 | **策略迁移风险** | 现有 11 个因子在 XAUUSD 上挖出；样本外验证中 EURUSD 与 XAGUSD 均失效。**不要预期搬过去就能赚**。接入的工程价值 > 策略价值 |
| 🔴 高 | **合规** | 虚拟货币交易在不同司法辖区监管差异大，境内相关政策需自行评估后再决定是否实盘 |
| 🟠 中 | API Key 安全 | 必须：IP 白名单 + 只开「交易」权限、**关闭提币** + 独立子账户 |
| 🟠 中 | 改止损异步化 | 现有 3 个止损引擎假设同步改单，改造面 1800+ 行 |
| 🟡 低 | K 线顺序 / 时间戳 | 一字之差导致全文指标反了；必须在网关层统一并加单测 |
| 🟡 低 | 资金费率成本 | 持仓过夜成本模型未建，回测会高估收益 |
| 🟡 低 | 两套前端 | `ui_layer` vs `ui_layer_v2`，改造前需确认主线 |

---

## 7. 待你确认（这 5 个决定会改变方案结构）

1. **合约类型**：永续 SWAP（U 本位，推荐）/ 现货 / 交割？
2. **持仓模式**：单向 `net`（推荐，与外汇逻辑最接近）/ 双向 `long`/`short`？
3. **并存方式**：按 symbol 路由并存（推荐，DB 不动）/ 全局切换 `BROKER=okx`？
4. **前端主线**：`ui_layer` / `ui_layer_v2`？
5. **是否第 0 期先跑一次「BTC 上的现有因子回测」**？（建议先做，成本极低，能提前暴露策略迁移问题）

---

---

## 8. 实施进度（评估后的落地记录）

用户已拍板：合约类型**全部可选**｜持仓模式**双向**｜并存方式**路由 + 全局切换都要**｜
前端以 `ui_layer` 为主线｜先跑第 0 期。

### 8.1 第 0 期结论（重要：推翻了本文第 6 节的"策略迁移风险"判断）

详见 `tools/第0期加密因子验证报告.md`。核心数据：

| 窗口 | 行情 | 均值 PF | 均值收益 | PF≥1 占比 |
|---|---|---|---|---|
| 加密上涨（+24~43%） | 涨 | 0.84 | −18.80% | 11% |
| 加密下跌（−16~−40%） | 跌 | **1.07** | **+5.48%** | 56% |
| 黄金 M30（−15.35%） | 跌 | 1.01 | −1.86% | 56% |

多空分解：下跌段空头 600 笔 **+9437**（单笔 +15.73），多头 570 笔 −4507；
上涨段多头 647 笔 −6926、空头 588 笔 −9562。

**结论：不是"品种过拟合"，而是"多头腿失效、空头腿有效"。**
上线前必须加方向门控，并在加密上重新挖掘多头腿。

### 8.2 环境事实（阻塞项）

`www.okx.com` 在本机 **DNS 被污染**（解析到不可路由的 `169.254.0.2`），代理亦 502。
→ 第 0 期改用 Binance 现货公开数据验证；**OKX 联调与实盘需换网络环境**（境外 VPS / 可用代理）。
→ `oems/okx_gateway.py` 支持 `proxy=True` 走系统代理，便于换环境后复用。

### 8.3 已实施

新增：`oems/okx_gateway.py`、`oems/okx_broker.py`、`market_data/symbol_registry.py`、
`risk_sizing/crypto_sizing.py`、`api/broker.py`、`ui_layer/src/components/OkxPanel.tsx`
改造：`config.py`、`market_data/service.py`、`api/app.py`、`ui_layer/src/api.ts`、`ui_layer/src/App.tsx`
修复：`backtest_store/engine.py` 的 `dyn_max_hold` 未初始化 P0（默认参数下回测必崩）

配置方式（`.env`）：
```
OKX_ENABLE=1
OKX_API_KEY=...  OKX_SECRET_KEY=...  OKX_PASSPHRASE=...
OKX_FLAG=1           # 1=模拟盘（强烈建议先跑沙盒）
OKX_TD_MODE=cross    # cross 全仓 / isolated 逐仓
OKX_POS_SIDE_MODE=both   # 双向持仓
VENUE_MODE=route     # route=按 symbol 自动路由；mt5/okx=全局强制切换
```

### 8.4 前端密钥配置界面（免手改 .env）

前端「交易中心 → 欧易 API」页签 = `ui_layer/src/components/OkxSettings.tsx`，
后端 `api/okx_settings.py` 提供 3 个端点：

| 端点 | 作用 |
|---|---|
| `GET /api/okx-settings/config` | 返回掩码配置 + 网关连接状态（明文 Secret 永不下发） |
| `POST /api/okx-settings/config` | 写回 `.env` → 同步 `os.environ` 与内存 `settings` → **热重载网关** |
| `POST /api/okx-settings/test` | 只读连通性测试（公开时间 → 私有余额），不下单 |

要点：
- 密钥字段传空串 = 保持原值（方便只改环境开关）；清空要显式传 `clear_secrets`。
- 热重载把新网关挂回 `SymbolRegistry`；若下单链路当前是 `OkxBroker`，同步换掉它的 `gateway`。
- `.env` 必须 **UTF-8**（python-dotenv 1.2.2 严格按 UTF-8 解码，GBK 会让 `import config` 直接抛
  `UnicodeDecodeError`），写入时保留原有 BOM。
- 代理语义与 `AppState._build_okx_gateway` 一致：空=自动探测系统代理，`direct`=强制直连。

### 8.5 验证

- 后端 pytest：105 passed / 2 failed（两个失败为既有问题，与本次改动无关）
- 前端 `tsc --noEmit`：0 error；`npm run build` 通过（6.7s）
- 路由：OpenAPI 共 125 个 path，其中 12 个 `/api/broker/*` + 2 个 `/api/okx-settings/*`
- 仓位换算：权益 10000、风险 1%、止损 1000 点、ctVal 0.01 → 10 张、名义 8000 USDT
- 实网络回归：用假 Key 走通「启用→重载→鉴权失败」全链路，返回 50111 并给出环境不匹配提示

### 8.6 尚未做（第 3 期）

`trailing_stop` / `smart_stop` / `sltp_manager` 的 OKX 适配——改止损需要先查 `algoId`
再异步 amend，这三个模块目前假设同步改单。**加密品种上线前请先禁用移动止损**。
`ui_layer_v2` 未同步改造。
