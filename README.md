# 外汇 AI 量化交易系统

基于多模态 AI 逆向工程的外汇量化交易系统。交易员在 K 线图上拖拽框选形态，标注入场点与出场点，系统自动把该区域逆向推演为 Python 因子代码，经过 AST 安全沙盒校验后执行历史回测，存入因子特征库，并在模拟盘与实盘中自动匹配信号、计算仓位、管理订单；平仓后由 AI 生成中文复盘报告。

## 核心闭环

`前端框选标注 → 多模态 AI 因子逆向 → AST 安全沙盒 → 历史回测 → 因子特征库 → 信号匹配/风控/下单 → AI 中文复盘`

系统按 10 大核心板块组织，完整架构图谱与当前项目映射见 [docs/10大板块架构.md](docs/10大板块架构.md)。
完整安装、配置、操作与故障排查见 [docs/系统使用说明书.md](docs/系统使用说明书.md)。
最新一次完整测试结果见 [docs/测试报告.md](docs/测试报告.md)。

## 目录结构

```text
├── ai_engine/          # 多模态 Prompt 构造与 AI 逆向因子提取
├── sandbox/            # AST 静态安全检查与独立进程隔离沙盒
├── backtest_store/     # 回测引擎与因子特征库
├── market_data/        # K 线数据与模拟实时行情
├── paper_trading/      # 前向模拟盘与偷看未来校验
├── signal_matcher/     # 市场环境分类器与因子激活共振引擎
├── risk_sizing/        # 基于 ATR 的动态仓位倒推计算
├── oems/               # 经纪商 API 路由与订单生命周期管理
├── ai_replay/          # 亏损归因分析与智能中文复盘
├── observability/      # 心跳检测与中文告警推送
├── ui_layer/           # React 中文 K 线拖拽框选前端
└── main.py             # FastAPI 入口
```

## 快速开始

### 后端

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn main:app --reload
```

默认 `LLM_PROVIDER=simulated`，无需 API Key 即可跑通全流程。配置 `OPENAI_API_KEY` 或 `ANTHROPIC_API_KEY` 后，将 `LLM_PROVIDER` 设为 `auto` 即可启用真实多模态大模型。

### MT5 实盘接入

在 `.env` 中配置：

```ini
BROKER=mt5
MT5_LOGIN=你的账号
MT5_PASSWORD=你的密码
MT5_SERVER=经纪商服务器
MT5_PATH=C:\Program Files\MetaTrader 5\terminal64.exe
```

安装 MT5 Python 包后重启后端：

```bash
pip install MetaTrader5
```

系统已内置 `GET /api/mt5/tick` 实时盘口与 WebSocket 推送（`/api/mt5/ws/tick`），并在后台监控 MT5 连接状态，断线时自动重连并推送中文告警。

系统不再使用模拟行情，K 线、品种、账户与实时盘口全部来自 MT5。前端品种下拉框展示 MT5 全部交易品种并支持搜索；[MT5 实盘] 标签页提供账户资金、实时盘口、下单、持仓平仓、挂单撤单、一键平仓与按风险计算手数。未安装 `MetaTrader5` 或未连接终端时，行情接口返回中文错误提示，不会退回模拟数据。

自动下单前请确保 MT5 终端工具栏的“算法交易 (Algo Trading)”已启用，否则订单会被 MT5 以 `RetCode:10027` 拒绝；前端 [MT5 实盘] 面板会直接显示该状态。

AI 信号激活后可通过 `signal_matcher/executor.py` 自动下单（板块四 -> 板块八闭环），MT5 平仓后由 `oems/position_monitor.py` 自动生成中文复盘（板块五闭环）；统一技术指标与特征库位于 `indicators/technical.py`，接口为 `GET /api/indicators`；告警支持 Telegram、企业微信、钉钉、飞书与邮件。

板块四还提供统一 ML 模型池：`GET /api/models` 查看模型，`POST /api/models/{name}/train` 训练，`POST /api/models/{name}/predict` 预测；已内置逻辑回归动量模型与阈值动量模型，训练后的模型会参与因子扫描与自动执行。

### 前端

```bash
cd ui_layer
npm install
npm run dev
```

访问 `http://127.0.0.1:5173`。Vite 已配置代理，前端请求会转发到 `http://127.0.0.1:8000`。

生产部署时也可以直接访问 `http://127.0.0.1:8000`，FastAPI 会自动托管 `ui_layer/dist` 的构建产物。

## 使用流程

1. 在 K 线图上拖拽框选形态区域，点击[标记入场点] / [标记出场点] 在图上标注关键价位。
2. 点击[提取并生成 AI 因子]，系统把区域 OHLCV 快照（配置 LLM 后还会附带图表截图）交给大模型逆向推演 Python 因子代码。
3. 点击[运行历史回测]查看绩效指标与逐笔交易；满意后点击[存入因子特征库]。
4. 在[交易状态]面板点击[立即扫描]查看激活信号，选择因子后点击[启动模拟盘]做前向验证。
5. 模拟盘平仓后自动生成中文复盘报告，可在[复盘报告]面板导出。

## 核心接口速览

| 接口 | 说明 |
|---|---|
| `POST /api/ai/generate` | 框选区域 + 人工标注 → AI 因子草稿（含沙盒结果） |
| `POST /api/backtest/run` | 因子代码历史回测 |
| `POST /api/backtest/optimize` | 因子参数自动优化（多轮回测、评分排序、提前停止） |
| `GET/POST /api/factors` | 因子特征库管理 |
| `PUT /api/factors/{id}` | 编辑因子 |
| `DELETE /api/factors/{id}` | 删除因子 |
| `POST /api/factors/clear` | 清空因子库 |
| `POST /api/matcher/scan` | 市场环境分类与因子激活扫描 |
| `POST /api/matcher/paper/start` | 启动前向模拟盘 |
| `POST /api/trading/close-all` | 一键平仓 |
| `POST /api/replay/export` | 导出 AI 中文复盘报告 |
| `GET /api/observability/alerts` | 中文告警中心 |
| `GET/POST /api/mt5/*` | MT5 数据底座、OEMS 与风控接口 |
| `GET/POST /api/models/*` | ML 模型池：训练、预测、自动执行 |
| `GET /api/orders/log` | 订单日志：每笔订单状态与开平仓动作 |
| `POST /api/patterns/*` | 形态案例库：学习、保存、实盘相似形态匹配 |

## 安全沙盒

因子代码由 `sandbox/ast_check.py` 做静态白名单检查：只允许 pandas / numpy / math / statistics，禁止文件、网络、进程、反射、动态执行等危险能力。通过检查后，代码在独立子进程中以受限内置函数运行，设置超时保护。

## 生产组件

默认使用 SQLite 便于本地开发。生产环境可将 `DATABASE_URL` 切换为 PostgreSQL / TimescaleDB（时序表），并启用 Redis 做信号与特征缓存；VectorBT 位于可选依赖 `requirements-vectorbt.txt`，安装后可在 `backtest_store/engine.py` 中切换到 VectorBT 引擎。
