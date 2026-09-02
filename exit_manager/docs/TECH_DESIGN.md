# 止损止盈引擎 —— 技术说明文档（算法与系统设计）

> 本文档定义整个系统的**算法全景**：系统用到了哪些算法、每个算法被哪个功能
> 使用、各功能之间的算法如何搭配成一条数据管线。
> 
> 姊妹文档：`THEORY.md`（为什么这样设计的理论依据）；本文档讲**怎么配**。
> 代码骨架对应：`framework/`、`strategy/`、`backtest/`、`integration/`。

---

## 1. 系统总体架构

```
                         ┌─────────────────────────────────────────────┐
   数据双流              │             决策协议（L0–L3 仲裁）            │
 ┌────────────┐  ① 收益K线 │  ┌───────┐  浮盈 vs 门槛   ┌──────────┐    │
 │ tick 流     ├──────────►│  │ 门槛合成 │──────────────►│   决策     │    │
 │ (MT5/OANDA)│  采样引擎  │  │(算法库) │                │ (策略层)  │    │
 └────────────┘           │  └───┬───┘                └────┬─────┘    │
 ┌────────────┐  ② 市场指标 │      │输出七档门槛              │指令       │
 │ 1min K线    ├──────────►│ ┌────▼─────────────┐   L3 AI建议 │          │
 └────────────┘  聚合+估计 │ │ 状态估计算法库     │◄──────────┤          │
                          │ │ (σ,μ,state,q,边界) │  L2 规则决策│          │
                          │ └────────┬──────────┘   L1 护栏   │          │
                          └──────────┼───────────────────────┼─────────┘
                                     │ L0 人工介入（无条件覆盖，随时）
                         ┌───────────▼────────────┐
                         │ 执行层：券商指令 + 成本模型 │──► MT5/回测引擎
                         └────────────────────────┘
```

**三层角色**：
- **状态估计层（算法库）**：把 tick/指标数据实时转成 σ(t)、μ(t)、状态、分布分位数、最优边界——全是可审计的估计量；
- **门槛合成层**：把估计量合成"七档动态门槛"（分批/保本/断熔/时间/追踪/出Bar）；
- **决策仲裁层（L0–L3）**：人工 > 护栏 > 规则决策 > AI 建议，逐级否决。

**多仓托管模型（本轮升级）**：本系统是"止盈止损管理器"——同一引擎可**同时托管多笔被监控订单**。每笔订单为独立的
`OrderBundle`（订单状态 + 收益K线生成器 + 估计器 + 动态门槛 + 护栏快照），收益K线/估计/门槛/护栏**逐单隔离**；
`OrderManager` 以 `ticket → OrderBundle` 字典维护，逐 tick 对每笔独立驱动（`_drive_order`）：
- 外部持仓（MT5）：同步时对 account **全量接管**，每笔独立 `adopt` → 独立收益K线起点；
- 自动开仓（Sim 演示）：保持**单仓纪律**（已有托管单则不开新单），演示基线不受影响；
- 人工介入（L0）：`set_manual_override(action, reason, ticket)` 按单注入，`ticket=None` 作用于主单（最近托管一笔）；
- 兼容层：`mgr.order / mgr.gen / mgr._thr` = 主单属性，旧单仓消费者零改动可跑。

---

## 2. 算法总目录（按功能域）

### 域 A：数据采样（服务功能①收益K线）

| 算法 | 文献 | 输入 → 输出 | 角色 | 状态 |
|---|---|---|---|---|
| 信息驱动 Bar 采样（dollar-bar 思想） | López de Prado, AFML ch.2（2018） | 订单浮盈快照流 → 等量|ΔP&L| 的收益K线（OHLC+峰值+回撤，含时间护栏） | 介入快的根基：决策频率∝浮盈波动速度 | **默认** |

### 域 B：状态估计（服务功能②③④⑤⑥ —— 全部门槛的输入来源）

| 算法 | 文献 | 输入 → 输出 | 角色 | 状态 |
|---|---|---|---|---|
| 滚动统计 z-score 归一（IncrementalStats） | 通用因果统计 | 序列 → 因果 mean/std/z | 所有特征的因果归一化（防前视） | **默认** |
| EWMA 波动率估计 | RiskMetrics（1996） | 收益K线 Δζ 序列 → σ(t) | 门槛/止损宽度的波动标定 | **默认** |
| GARCH(1,1) / GJR-GARCH / EGARCH | Bollerslev 1986 / Glosten et al. 1993 / Nelson 1991 | 同上 → 带聚集/杠杆效应的 σ(t) | σ 估计升级件 | 可选增强 |
| 已实现波动率 + HAR-RV | Andersen et al. 2003 / Corsi 2009 | 收益K线内路径价格 → RV、多尺度 σ | 高频信息 σ 估计 | 可选增强 |
| 滚动均值漂移估计 | 通用 | Δζ 序列 → μ(t) | 停时边界/趋势判别的漂移输入 | **默认** |
| 动态线性模型 DLM（状态空间） | West & Harrison 1997 | Δζ 序列 → 带噪声滤波的 μ(t) | μ 在线估计升级件 | 可选增强 |
| HMM 马尔可夫切换 | Hamilton 1989 | 指标/Δζ 序列 → 状态后验概率 | 状态（趋势/震荡/高波动）依赖 | **默认** |
| CUSUM 变点检测 | Page 1954 | 序列 → 变点报警 | 轻量状态检测替代件 | 可选 |
| 贝叶斯在线变点检测 | Adams & MacKay 2007 | 序列 → 每时刻新状态后验 | 状态估计升级件 | 可选增强 |
| 在线分位数（t-digest / 滑窗分位） | Dunning & Ertl 2019 | Δζ 序列 → q_up(0.75), q_dn(0.05) | 分批/断熔门槛的分布基准 | **默认** |
| 分位数回归森林（条件分位） | Meinshausen, JMLR 2006 | 当前特征 → 条件化浮盈分布分位 | 分布门槛升级件（响应行情结构） | 可选增强 |
| 极值理论 POT（GPD 尾估计） | McNeil & Frey 2000；Embrechts et al. 1997 | Δζ 左尾 → 厚尾正确的最差值估计 | 断熔左尾的厚尾正确化（XAU/USD 适用） | **默认**（与分位数互补） |

### 域 C：最优决策边界（服务功能③分批④追踪⑤断熔⑥时间 —— 门槛的理论内核）

| 算法 | 文献 | 输入 → 输出 | 角色 | 状态 |
|---|---|---|---|---|
| 最优停时/自由边界问题（障碍 PDE 数值解→查表） | Peskir & Shiryaev 2006 | σ(t), μ(t), 机会成本率 r, 剩余时间 → 上/下最优边界 b_up(t), b_dn(t) | 兑现/断熔边界的理论主体：σ越大越宽、μ越正越宽、时间越短越窄 | **默认**（离线求解+在线查表） |
| 美式期权二叉树分批定价（等价于提前行权） | Cox, Ross, Rubinstein 1979 | 当前浮盈(内在价值)、继续持有预期(时间价值)、σ、r → 两条价值曲线交叉点=分批档 | 分批档位动态定价：只在时间价值>内在价值时继续持有 | **默认** |
| SPRT 序贯概率比检验 | Wald, *Sequential Analysis* 1947 | 逐 Bar 浮盈证据 → H₀(继续) vs H₁(结算) 的似然比判定 | "该不该现在结束"的统计化判定（天然事件驱动=介入快） | 可选增强 |
| Kaminski-Lo 止损最优性条件 | Kaminski & Lo, J. Fin. Markets 2014 | 收益自相关/可预测性估计 → 止损规则是否有效的开关 | 断熔/止损门槛的"收紧或放宽"开关（负自相关收紧、正趋势放宽） | **默认**（作门槛状态因子） |
| Chandelier Exit（峰值−k·ATR） | LeBeau & Lucas 1992 / Wilder 1978 | 峰值、ATR(t) → 追踪止损价 | 动态止损价格的主干 | **默认** |
| Volatility Targeting 缩放 | Moreira & Muir, JF 2017 | σ(t), σ_target → 宽度缩放因子 k₀·σ_target/σ(t) | 止损宽度/门槛距离的波动率目标化 | **默认** |
| 成本门控（预期净值判据） | Kyle 1985；Glosten-Milgrom 1985 | 该档盈利、点差+滑点 → 该档净预期值 ≥ 0.15R 才执行 | 防止"为分批而分批"（成本感知） | **默认** |
| 固定比率分批（R 里程碑） | Jones 1999；AFML ch.3 三屏障 | 浮盈 vs 里程碑 → 分批档位 | 分批结构的经典骨架（档位数值由域B/C算法替换） | **默认**（作为结构而非数值） |

### 域 D：学习/自适应（服务功能⑧ AI 辅助⑨门槛自校准）

| 算法 | 文献 | 输入 → 输出 | 角色 | 状态 |
|---|---|---|---|---|
| 行为克隆（BC）预训练 | 监督学习 / AFML ch.3 三屏障标签 | (状态, 历史订单标签) → 初始策略 | 用"三屏障好退出"教初始策略，样本高效 | **默认**（训练时） |
| PPO（近端策略优化） | Schulman et al. 2017 | RLEnv(状态, 动作, 奖励) → 策略 | L3 AI 建议层的策略学习 | **默认**（训练时） |
| 上下文赌博机（LinUCB / UCB） | Li et al. 2010 / Auer et al. 2002 | 特征(浮盈−门槛差值向量) → 动作选择 | 轻量在线决策/门槛偏移调制替代件 | 可选 |
| 在线凸优化（OGD/FTL，无遗憾） | Zinkevich, ICML 2003 | 每 Bar 的决策损失 → 门槛参数梯度更新 | 门槛参数自身算法化自校准（调参消失） | 可选增强 |
| Potential-Based Reward Shaping | Ng, Harada & Russell 1999 | 中间态浮盈 → 附加奖励 γΦ(s')−Φ(s) | 复合奖励的唯一理论正确形式 | **默认** |

### 域 E：执行与评估（服务功能⑦执行⑩回测⑪绩效）

| 算法 | 文献 | 输入 → 输出 | 角色 | 状态 |
|---|---|---|---|---|
| 成本模型（半价差+滑点+swap） | 微观结构成本理论（Kyle 1985 等） | 成交、持仓 → 成本/swap 金额 | 奖惩口径统一、防纸面利润幻觉 | **默认** |
| 最优执行轨迹（分批节奏） | Almgren & Chriss, J. Risk 2000 | 冲击系数λ、σ、剩余量 → 分批轨迹 | 分批"何时再分、每次多少"的优化 | 可选增强 |
| Walk-Forward + Purged/Embargoed CV | AFML ch.7（2018） | 历史 tick 分段 → 无泄漏评估/重训节奏 | 防时间重叠泄漏、非平稳适应 | **默认** |
| 峰值兑现率（Capture Ratio）等指标 | MAE/MFE（Sweeney）审计思想 | 订单档案(峰值, 兑现) → 兑现率/偏度/左尾统计 | 评价"退出质量"，反向校准门槛 | **默认** |

---

## 3. 功能 → 算法 映射表

| 功能 | 用到的算法 |
|---|---|
| ① 收益K线实时生成 | 域 A：信息驱动 Bar 采样（dollar-bar 思想 + 时间护栏，无前视） |
| ② 状态空间构建 | 域 B：滚动 z 归一、EWMA σ(t)、HMM 状态、在线分位数（→ 状态向量 39 维） |
| ③ 分批止盈决策 | 域 C：美式期权价值交叉定价、最优停时上边界、固定比率结构、成本门控 |
| ④ 追踪止损（动态止损价格） | 域 C：Chandelier（峰值−k·ATR）+ Volatility Targeting 缩放 |
| ⑤ 断熔/防硬扛 | 域 C：最优停时下边界、EVT-POT 左尾、Kaminski-Lo 开关、MAE 断熔规则 |
| ⑥ 时间止损 | 域 C：最优停时时间预算、机会成本率 r；域 B：HMM 状态（高波动缩短上限） |
| ⑦ 执行落地 | 域 E：成本模型；指令映射（modify_stop / 按手数分批 / 清仓） |
| ⑧ AI 辅助 | 域 D：BC 预训练 + PPO；状态特征=浮盈−门槛差值向量；输出可被否决 |
| ⑨ 门槛自校准 | 域 D：在线凸优化（可选）；或 walk-forward 定期重标定 |
| ⑩ 回测引擎 | 域 E：tick 重放 + 成本模型；与实盘共用同一 生成器/策略/护栏 代码 |
| ⑪ 绩效评估 | 域 E：walk-forward、purged CV、Capture Ratio、收益率/偏度/左尾统计 |
| ⑫ 人工随时介入 | L0 覆盖协议（非算法，但受算法日志驱动：每次介入记录当时的门槛与估计量） |

---

## 4. 算法之间如何搭配（数据管线）

### 4.1 决策时刻 t（收益K线 commit）的完整管线

```
 ① 采样：tick 流 → 收益K线生成器 → Δζ 序列（最近 N 根已提交Bar）
 ② 估计（全部因果）：
      σ(t)   ← EWMA(Δζ)                    （默认；可选 GARCH/HAR-RV）
      μ(t)   ← 滚动均值(Δζ)                 （可选 DLM）
      state(t) ← HMM 后验                    （可选 贝叶斯变点/CUSUM）
      q_up/q_dn ← t-digest 在线分位(Δζ)     （增强：QR 森林条件分位）
      tail_dn    ← EVT-POT(Δζ 左尾)          （断熔左尾）
      ksl_ls     ← Kaminski-Lo(收益自相关)   （止损规则有效性开关）
 ③ 边界（最优决策内核）：
      b_up(t), b_dn(t) ← 停时边界求解器(σ, μ, r, t_remaining)   [离线求解+在线查表]
      tp_price(t)      ← 美式期权二叉树(内在价值 vs 时间价值)     [分批档交叉点]
 ④ 合成七档门槛（全部是估计量/边界的函数，无业务常量）：
      分批1 = w₁·q_up(t) + w₂·b_up(t)          （分布证据 + 最优边界加权）
      分批2 = 分批1 × VT 缩放 × 时间预算因子
      保本   = f(σ(t), 点差) × VT 缩放
      断熔   = min(q_dn(t), tail_dn(t), b_dn(t))，受 ksl_ls 开关调制
      追踪   = 峰值 − k₀·(σ_target/σ(t))·ATR(t)
      时间上限 = f(state(t), μ(t)存续性, r)
      出Bar阈值 = σ 噪声标定（高波动提高阈值，防无效决策）
         —— 全部 clamp 有界；每条记录"输入估计量→输出门槛"（审计）
 ⑤ 决策仲裁：
      L0 人工介入（无条件） > L1 护栏（断熔/时间/点差异常，用④的档位）
                                  > L2 规则决策（当前浮盈 vs ④门槛 + 成本门控）
                                  > L3 AI 建议（PPO，特征=浮盈−④门槛差值向量）
 ⑥ 执行：指令 → 成本模型扣费 → MT5（modify_stop / 按手数分批平仓 / 清仓）
 ⑦ 反馈：收益K线/决策日志 → 绩效指标（Capture Ratio 等）→ walk-forward 重训/重标定
```

### 4.2 数据依赖关系图

```
tick ──► 收益K线生成器 ──► Δζ 序列 ──► ┌─ EWMA ──────────► σ(t) ──┐
  │         (域A)               │      ├─ 滚动均值 ──────► μ(t) ──┼──► 停时边界求解器(域C)
  │                            │      ├─ 在线分位 ──────► q_up/q_dn ─┼──► 美式期权定价(域C)
  │                            │      ├─ EVT-POT ───────► tail_dn ──┤
  │                            │      └─ HMM ───────────► state(t) ─┼─► 时间上限/Kaminski开关
  ▼                            ▼                                    ▼
指标聚合(1min) ──► ATR ──► VT缩放 ──► 门槛合成器(域C) ──► 七档门槛 ──► 决策仲裁(L0-L3) ──► 执行(域E)
                                                                        ▲
                              RL训练(域D): BC预训练 → PPO(RLEnv) ◄──────┘
                              特征=浮盈−门槛差值向量；输出仅建议，可被否决
```

### 4.3 关键搭配逻辑（为什么这么接）

1. **估计层→门槛合成器**：门槛不是算法各自独立输出，而是"证据融合"——分批档=分位数证据与最优边界的最优加权；断熔档取三个来源的保守值（分布/尾部/最优边界），并受 Kaminski-Lo 有效性开关调制（不可预测的行情里断熔才收紧）。**没有哪一档出自单一算法**，单一算法的盲区被其他算法互补。
2. **停止论（停时/期权/SPRT）驱动"时点"，分布估计驱动"水平"**：最优停时和美式期权决定"该不该兑现/结算"（时点逻辑），分位数与 EVT 决定"兑现/断熔的具体距离"（水平逻辑）——两者在合成器里合并，一个管时间、一个管空间。
3. **ARL 层（L3）吃"差值"，不吃原始门槛**：AI 的输入是 `浮盈 − 门槛` 的差向量（高信息量、归一化），既避免学习"门槛自身"，又保证 AI 建议天然在阈值语义的空间里；输出动作仍被 L0/L1 过滤，安全层永远在。
4. **学习层唯一接触"门槛更新"的接口是门槛参数的自校准算法（在线凸优化，区域 D），而不是让 AI 直接改门槛**——保持"规则系统可解释"的底线。
5. **回测与实盘共用同一估计/合成/决策代码**（sim-to-real 一致性），评估用 walk-forward + purged CV 防泄漏——这是"算法能生效"的前提，否则任何估计器的结论都不可信。

### 4.4 默认启用 / 可选增强 / 后续扩展 分层

- **默认启用（核心管线，缺一不可，均已落地）**：信息驱动采样、滚动 z 归一、EWMA(σ)、滚动 μ、**HMM 状态（`EstimatorBundle` 内嵌 `HMMStateEstimator`：分段 EM 重标定 + 在线前向滤波，`state_best/probs()` 供门槛与状态空间消费）**、在线分位 q + EVT 左尾、**停时边界（`BoundaryTable`：隐式有限差分离线数值标定美式线性停时，经 `lookup` 在线表驱动插值 + μ 解析修正；`ThresholdScheduler` 默认使用，未标定回退解析外推）**、**美式期权分批定价（`PartialBarrierPricing`：与停时边界共享同一数值表——二批档 = 初始时点停时目标 `b_p2`；未标定回退时间价值尺度近似）**、VT 缩放、Chandelier 追踪、成本门控、Kaminski-Lo 开关、护栏、**差值特征 + L3 顾问接线（`framework.state_reward.build_decision_features` 输出 11 维差值向量 → `rl_advisor.RuleAdvisor` 高置信建议 → `ExitStrategy.decide` 第 6 步仲裁：仅规则无动作且未被护栏拦截时补足，白名单动作 + 状态机防重入 + 成本门控复用）**、BC+PPO 训练（PPO 保留为后续替换接口，BC 的确定性版本 `RuleAdvisor` 已落地）、**walk-forward / purged CV（`backtest.metrics.purged_cv`：时间切折 + purge 重叠窗 + embargo，防时间重叠泄漏）**、Capture Ratio 评估、成本模型。
- **可选增强（按数据质量/算力取舍）**：GARCH 族/HAR-RV（σ 更准）、DLM（μ 更稳）、贝叶斯在线变点（状态更全）、QR 森林（条件分位）、SPRT（结算判定统计化）、Almgren-Chriss（分批节奏）、在线凸优化（门槛自校准）、CRR 二叉树的完整期权定价（当前为表驱动工程替代）。
- **后续扩展**：更多文献对照与消融实验（每个增强件必须 A/B 出益才保留）、实盘护栏策略版本管理；**表参数（σ 网格 / 时间层 / x_max）在生产环境按品种重新离线标定并持久化**。

---

## 5. 成本与奖励的口径（算法输出统一归一）

- 所有估计量与门槛的**单位统一为初始风险 R 的倍数**（σ 取 Δζ 的波动、μ 取 Δζ 的均值漂移、门槛均为 R 距离，追踪止损为价格但由峰值−k·σ·合约换算）→ 跨订单可比、可聚合；
- 奖励：终局 = 已实现盈亏(R) − 成本(R) − swap(R)；中间态 = potential shaping（γΦ(s')−Φ(s)，Φ=clamp(浮盈)）——算法输出的全部中间量不影响终局目标（Ng 1999 定理保证）。

---

## 6. 文献附录

1. López de Prado, *Advances in Financial Machine Learning*, Wiley 2018（ch.2 采样、ch.3 三屏障/meta-labeling、ch.7 CV、ch.14 回测）
2. J.P. Morgan, *RiskMetrics*, 1996（EWMA λ≈0.94）
3. Bollerslev 1986（GARCH）；Glosten, Jagannathan, Runkle 1993（GJR）；Nelson 1991（EGARCH）
4. Andersen, Bollerslev, Diebold, Labys 2003（已实现波动率）；Corsi 2009（HAR-RV）
5. West & Harrison, *Bayesian Forecasting and Dynamic Models*, 1997（DLM）
6. Hamilton 1989（HMM 切换）；Page 1954（CUSUM）；Adams & MacKay 2007（贝叶斯在线变点）
7. Dunning & Ertl 2019（t-digest 在线分位）；Meinshausen 2006（QR 森林）；McNeil & Frey 2000、Embrechts et al. 1997（EVT/POT）
8. Peskir & Shiryaev, *Optimal Stopping and Free-Boundary Problems*, 2006
9. Cox, Ross, Rubinstein 1979（二叉树/美式期权）
10. Wald, *Sequential Analysis*, 1947（SPRT）
11. Kaminski & Lo, *When Do Stop-Loss Rules Stop Losses?*, J. Financial Markets 17, 2014
12. LeBeau & Lucas 1992（Chandelier Exit）；Wilder 1978（ATR）
13. Moreira & Muir, *Volatility-Managed Portfolios*, J. Finance, 2017
14. Kyle 1985；Glosten-Milgrom 1985（点差分解/成交成本结构）
15. Jones, *The Trading Game*, 1999（固定比率）
16. Schulman et al. 2017（PPO）；Li et al. 2010（LinUCB）；Auer et al. 2002（UCB）；Zinkevich 2003（在线凸优化）
17. Ng, Harada & Russell, ICML 1999（potential-based shaping 定理）
18. Almgren & Chriss, *Optimal Execution of Portfolio Transactions*, J. Risk, 2000
19. Sweeney（MAE/MFE 审计思想，*Maximum Adverse Excursion*, Wiley 2001）

---

## 7. 与代码的对应

| 文档章节 | 代码落点 |
|---|---|
| 域 A 采样 | `framework/profit_kline.py` |
| 域 B 估计 | `framework/indicators.py`（含 IncrementalStats）、未来 `framework/estimators.py` |
| 域 C 门槛 | 未来 `strategy/threshold_scheduler.py`（合成器/边界查表）、`strategy/exit_strategy.py`、`strategy/guards.py` |
| 域 D 学习 | `strategy/rl_advisor.py`、`backtest/replay.py`（RLEnv） |
| 域 E 执行评估 | `framework/cost_model.py`、`backtest/metrics.py`、`integration/mt5_bridge.py` |
| 仲裁与日志 | `backtest/replay.py`（OrderManager 的 L0–L2 流程 + decision_log 审计） |