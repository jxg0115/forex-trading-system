# 接口契约文档（INTERFACES.md）

> 本文件是 `TECH_DESIGN.md` 第 4 章“管线”的接口级细化：为每个算法模块定义
> **输入 / 输出签名**、**估计器之间的接口**、**数据流时序**、**因果性约束**，
> 是恢复代码时的直接对照。所有签名用 Python typing 风格，与现有骨架
> （`framework/`、`strategy/`、`backtest/`）一一对应。
>
> 姊妹文档：`THEORY.md`（为什么）、`TECH_DESIGN.md`（用什么、怎么搭）、本文件（接口怎么签）。

---

## 0. 全局约定（所有接口必须遵守）

1. **因果性**：任何接口只能在输入满足 `ts <= t` 的前提下计算；禁止引用未来。
   - 收益K线：已 commit 的 Bar 永不改写；进行中 Bar 只增；
   - 估计器：只 push 已发生样本，snapshot 只读；
   - 指标：只用已收盘的 1 分钟K线；S/R 水平在入场时固定（pin）。
2. **单位**：除价格类（止损价、ATR、点差用 USD/oz）外，门槛与估计量一律用
   **初始风险 R 的倍数**；美元换算只在执行层（cost_model）发生。
3. **可审计**：任何动态门槛输出必须同时产出 `audit` 记录（输入估计量 → 输出值），
   决策日志持久化；不允许“无来源的门槛值”。
4. **有界**：所有估计量与门槛 clamp 到文档给定区间；极端输入不得让算法发散。
5. **可重置**：所有带状态的模块必须提供 `reset()`，在开仓 / 回测周期边界调用，
   保证因果起点干净（防跨订单泄漏）。

---

## 1. 跨层共享数据结构（Data Contracts）

### 1.1 已存在（骨架中已定义，契约固化）

```python
# framework/profit_kline.py
@dataclass(frozen=True)
class Snapshot:
    ts: float               # epoch 秒
    price: float            # mid（USD/oz）
    floating_r: float       # 当前浮盈（R 倍数，未扣成本）
    remaining_frac: float   # 剩余仓位比例 [0,1]
    direction: int          # +1 / -1

@dataclass
class ProfitBar:
    idx: int; open_r: float; high_r: float; low_r: float; close_r: float
    dd_rate: float          # 浮盈最大回撤率 [0,1]
    peak_r: float; start_ts: float; commit_ts: float
    ticks: int; volume_frac_close: float; committed: bool

@dataclass
class BarConfig:
    threshold_r: float = 0.25   # 出Bar阈值（可以动态更新，见 4.2 门槛调度器）
    max_seconds: float = 900.0  # 时间护栏
    min_seconds: float = 1.0

class ProfitKlineGenerator:
    def on_snapshot(self, s: Snapshot) -> Optional[ProfitBar]   # None=未触发提交
    def bars_back(self, n: int) -> List[ProfitBar]
    def reset(self) -> None
    @property def bar_count(self) -> int
    # 不变量：on_snapshot 后，self.committed 中已有的 bar 数值不变
```

```python
# framework/indicators.py（外部指标流，域 B 的“日历时间”输入）
@dataclass
class IndicatorSnapshot:
    atr: float; atr_z: float; regime: int          # 0低/1中/2高
    rsi: float; support: float; resistance: float
    dist_support_pct: float; dist_resist_pct: float
    spread_pip: float; spread_pip_avg: float

class CausalIndicators:
    def __init__(self, cfg: IndicatorConfig | None = None) -> None
    def on_bar_close(self, high: float, low: float, close: float, ts: float) -> None
    def on_spread(self, spread_pip: float) -> None
    def snapshot(self, mid_price: float, spread_pip: float,
                 pin_levels: bool = True) -> IndicatorSnapshot
    def reset(self) -> None
```

```python
# framework/cost_model.py（域 E 成本口径，奖惩共用）
@dataclass
class CostConfig:
    spread_pip: float = 3.0; slipping_ratio: float = 0.25
    swap_per_lot_day: dict   # {1: -4.5, -1: 2.0} $/手/天
    rng_seed: int = 7
def price_to_usd(price_delta, lots) -> float
def entry_cost_usd(cfg, lots) -> float
def close_cost_usd(cfg, lots, rng) -> float
def swap_usd(cfg, direction, lots, seconds) -> float
```

```python
# framework/actions.py（域 C→E 的动作映射）
ACT_HOLD=0; ACT_TIGHTEN_BE=1; ACT_TIGHTEN_1A=2; ACT_TIGHTEN_2A=3; ACT_TIGHTEN_3A=4
ACT_PART_1_3=5; ACT_PART_1_2=6; ACT_PART_2_3=7; ACT_CLOSE_ALL=8
@dataclass(frozen=True)
class OrderCmd:
    kind: str              # modify_stop / close_volume / close_all / none
    stop_price: Optional[float]; volume_frac: float; reason: str; action_id: int
def apply_action(action: int, ctx: ActionContext, reason: str = "") -> List[OrderCmd]
# ActionContext: direction, entry_price, peak_price, atr_price, spread_price,
#                remaining_frac, breakeven_level
```

```python
# strategy/guards.py（域 C 护栏）
@dataclass(frozen=True)
class ManualOverride:
    action: int; reason: str; ts: Optional[float] = None
@dataclass
class GuardSignal:
    zeta: float; holding_sec: float; bars_total: int; mae_fired: bool
    override: Optional[ManualOverride]; spread_pip: float; spread_pip_avg: float
@dataclass
class GuardResult:
    forced: bool; action: int; reason: str
    block_partial: bool; block_advisor: bool
def check_guards(sig: GuardSignal, cfg: GuardConfig) -> GuardResult
```

### 1.2 新增（本契约定义，待代码创建）

```python
# 域 B 输出的“估计捆”（EstimatorBundle.snapshot 的返回）
@dataclass
class EstimatorOutput:
    ts: float
    sigma_r: float          # 浮盈变动波动率（R/bar），EWMA 估计
    mu_r: float             # 浮盈漂移（R/bar），滚动均值估计
    state_probs: np.ndarray # HMM 后验（[趋势, 震荡, 高波动]），可选
    state_best: int         # 0/1/2（argmax 或由阈值化得到）
    q_up: float             # Δζ 分布上分位（默认 0.75）
    q_dn: float             # Δζ 分布下分位（默认 0.05）
    tail_dn: float          # EVT-POT 左尾估计（R），仅下尾
    ksl: float              # Kaminski-Lo 有效性 [-1,1]（>0 可收紧，<0 放宽）
    atr_price: float        # 来自 CausalIndicators（价格口径，供追踪止损）
    spread_pip: float; spread_pip_avg: float
    audit: dict             # {算法: 输入关键值} 供审计
```

```python
# 域 C 输出的“七档动态门槛”
@dataclass
class DynamicThresholds:
    be_r: float             # 保本档（浮盈达此值移保本）
    partial1_r: float       # 分批1档
    partial2_r: float       # 分批2档
    trail_start_r: float    # 追踪启动档（峰值达此值开始追踪）
    mae_r: float            # 断熔档（浮亏达 -mae_r 清仓）
    max_hold_bars: int      # 时间止损（收益K线条数上限）
    bar_threshold_r: float  # 收益K线出Bar阈值（动态）
    audit: dict             # {档位: (来源估计量, 计算过程)} 审计记录

# 域 C 决策上下文（估计输出 + 订单状态 + 外部，送进 ExitStrategy）
@dataclass
class DecisionContext:
    est: EstimatorOutput
    thr: DynamicThresholds
    order: OrderState        # 见 1.3
    ext: IndicatorSnapshot
    spread_price: float; typical_spread_pip: float
```

### 1.3 订单状态（OrderState，决策与审计共用）

```python
@dataclass
class OrderState:
    ticket: int; direction: int
    entry_price: float; entry_ts: float
    lots0: float; lots_cur: float
    r_price: float          # 初始风险对应价格距离（USD/oz），入场固定
    r0_usd: float
    stop_price: Optional[float]          # 当前止损（单调收紧）
    peak_r: float
    mae_fired: bool
    partial_count: int
    moved_be: bool
    gross_usd: float; cost_usd: float; swap_usd: float
    dd_avg: float           # 近 N 根收益K线平均回撤率（动态门槛的输入）
    hold_frac: float        # 持仓进度 bars/时间上限（动态门槛的输入）

    def zeta_at(self, mid: float) -> float        # (mid-entry)*dir / r_price
    def peak_price(self) -> float                 # entry ± peak_r*r_price
    def remaining_frac(self) -> float
```

---

## 2. 域 A：采样层接口（不变）

```python
# framework/profit_kline.py（见 1.1，契约已固化）
# 附加约定：BarConfig.threshold_r 允许被门槛调度器在每个 commit 后更新
# （gen.cfg.threshold_r = thr.bar_threshold_r），且只影响后续 Bar，不回写历史。
```

---

## 3. 域 B：状态估计层接口（新模块 framework/estimators.py）

### 3.1 单个估计器接口（每个都要满足 0.1/0.5 约定）

```python
class VolatilityEstimator:
    """σ_r(t)：浮盈变动（R/bar）的波动率估计。默认 EWMA；可选 GARCH 族/HAR-RV。"""
    def __init__(self, lam: float = 0.94, min_n: int = 20) -> None
    def update(self, delta_zeta: float) -> float      # 每根收益K线 commit 时 push
    def sigma(self) -> float                          # 当前 σ_r(t)，未到 min_n 返回兜底 1.0
    def reset(self) -> None
    # 因果：sigma() 只依赖已 update 的样本

class DriftEstimator:
    """μ_r(t)：浮盈漂移估计。默认滚动均值；可选 DLM。"""
    def __init__(self, window: int = 50) -> None
    def update(self, delta_zeta: float) -> float
    def mu(self) -> float
    def reset(self) -> None

class StateEstimator:
    """state(t)：HMM 后验（默认）；可选 CUSUM / 贝叶斯在线变点。"""
    def __init__(self, n_states: int = 3) -> None
    def update(self, obs: float) -> np.ndarray      # 返回后验向量
    def probs(self) -> np.ndarray
    def best_state(self) -> int
    def reset(self) -> None

class DistributionEstimator:
    """q_up/q_dn/tail_dn：Δζ 分布的在线分位 + EVT 左尾。"""
    def __init__(self, q_hi: float = 0.75, q_lo: float = 0.05,
                 window: int = 200) -> None
    def update(self, delta_zeta: float) -> None
    def quantiles(self) -> tuple          # (q_up, q_dn)
    def tail_down(self) -> float          # EVT-POT 左尾（R），样本不足返回 q_dn
    def reset(self) -> None

class KaminskiLoEstimator:
    """ksl(t)：止损规则有效性 [-1,1]，基于收益自相关估计。"""
    def __init__(self, window: int = 100) -> None
    def update(self, delta_zeta: float) -> float
    def value(self) -> float
    def reset(self) -> None
```

### 3.2 聚合器（决策管线实际调用的入口）

```python
class EstimatorBundle:
    """把 5 个估计器聚合成一次 snapshot，供门槛调度器消费。"""
    def __init__(self, vol: VolatilityEstimator | None = None,
                 drift: DriftEstimator | None = None,
                 state: StateEstimator | None = None,
                 dist: DistributionEstimator | None = None,
                 ksl: KaminskiLoEstimator | None = None) -> None
    def on_bar(self, bar: ProfitBar) -> None
        # 内部: dζ = bar.close_r - 上一根 close_r；逐个 update 各估计器
    def snapshot(self, ts: float,
                 ind: IndicatorSnapshot) -> EstimatorOutput
        # 只读组装；audit 记录各估计器当前输入/输出
    def reset(self) -> None
```

### 3.3 接口数据流（谁喂谁）

```text
ProfitKlineGenerator.committed（Δζ 序列）
        │  on_bar(bar)（每根 commit）
        ▼
EstimatorBundle ──► VolatilityEstimator.update(dζ) ──► σ_r(t)
        ├───────── DriftEstimator.update(dζ) ─────────► μ_r(t)
        ├───────── StateEstimator.update(dζ) ─────────► state(t)
        ├───────── DistributionEstimator.update(dζ) ──► q_up/q_dn/tail_dn
        └───────── KaminskiLoEstimator.update(dζ) ─────► ksl(t)
CausalIndicators ──► IndicatorSnapshot ─────────────► atr_price/spread（并入输出）
```

---

## 4. 域 C：边界求解与门槛合成接口（新模块 strategy/threshold_scheduler.py）

### 4.0 数值边界表（② 停时边界 / ③ 分批档 共用内核）

```python
class BoundaryTable:
    """美式线性停时的离线数值解表（②③ 共用同一实例）。
    模型：浮盈过程 X：dX = μdt + σdW，行权收益 = X（平仓立即兑现）。
    数值：隐式有限差分（Thomas 三对角）解 V_t = ½σ²V_xx；每时间层 Bellman
    max(V, X)（美式线性停时，Peskir-Shiryaev 2006 工程化）。
    每层提取：b_up（上侧兑现边界）/ b_dn（下侧止损边界）。
    在线：lookup(sigma_r, t_frac, mu_r) → (b_up, b_dn, b_p2)，
    σ 与 t_frac 双线性插值 + μ 解析修正叠加；lookup 懒标定（首次查询触发 compute）。
    """
    def __init__(self, sigma_grid=None, n_x: int = 121, n_t: int = 24,
                 x_max: float = 4.0, r_oc: float = 0.02) -> None
    def compute(self) -> None          # 离线标定（σ 网格 12 档 × 时间层 n_t）
    def lookup(self, sigma_r: float, t_frac: float,
               mu_r: float = 0.0) -> tuple  # (b_up, b_dn, b_p2)，单位 R
    def ready(self) -> bool
```

### 4.1 最优停时边界（表驱动入口；未标定回退解析外推）

```python
class StoppingBoundaryTable:
    """停时边界入口：查 BoundaryTable（与分段定价共享实例）。"""
    def __init__(self, c1: float = 0.20, c2: float = 1.2, c3: float = 0.08,
                 b_up_max: float = 2.5, b_dn_max: float = 1.6,
                 table: BoundaryTable | None = None) -> None
    def load_table(self, path: str) -> None
        # 占位：文件持久化为后续扩展（当前标定在内存 BoundaryTable，不落盘；
        #       compute_offline 后 lookup 自动切表驱动）
    def lookup(self, sigma_r: float, mu_r: float,
               t_frac: float, r: float) -> tuple
        # 返回 (b_up, b_dn) 单位 R。
        # t_frac = 已用预算进度 [0,1]（0=刚入场，对应美式“剩余时间最多”→ 停时目标
        #          最宽；1=预算耗尽 → 边界最窄并收敛向终端层）；μ 解析修正在线叠加。
        # 表未标定回退解析外推：b_up ≈ c1·σ²/max(r,ε)+c2·μ；b_dn ≈ -c3·σ²/max(r,ε)
        # （c1/c2/c3 标定超参，非业务档位）
    def compute_offline(self, sigma_grid=None, n_x: int = 121, n_t: int = 24,
                        x_max: float = 4.0, r: float | None = None) -> None
        # 隐式有限差分求解（Thomas）→ 生成表，之后 lookup 自动表驱动
        # μ 不进网格（在线解析修正）；时间方向用等距层 n_t（t_frac 0..1）
```

### 4.2 美式期权分批定价（③ 表驱动：二批档 = 初始停时目标）

```python
class PartialBarrierPricing:
    """分批档（CRR/美式语义落地）：二批档 = 初始时点停时目标。"""
    def __init__(self, kappa: float = 1.5, t_ref: float = 3600.0,
                 table: BoundaryTable | None = None) -> None
        # 与停时边界共享同一 BoundaryTable 实例（一处标定、两处消费）
    def barrier(self, zeta: float, sigma_r: float,
                t_frac: float) -> float
        # 返回“二批档”R 值：b_p2 = BoundaryTable 初始层（t_frac=0）的停时边界
        # （σ 越大目标越高；时间推进 → 现时 b_up 收敛向它；μ∈[0.2,4.0]）
        # 未标定回退时间价值尺度：max(1.0, κ·σ·sqrt(t_frac))
    # 说明：完整 CRR 二叉树为可选增强；当前表驱动为工程替代（等价美式线性停时解）
```

### 4.3 门槛调度器（七档合成器，唯一的“门槛出口”）

```python
@dataclass
class SchedulerConfig:
    w_q: float = 0.5            # 分位数证据权重（与停时边界加权合成）
    w_b: float = 0.5            # 停时边界权重
    dd_slope: float = 0.2       # 回撤调制斜率（dd_avg>0.3 提前兑现）
    t_slope: float = 0.12       # 时间衰减斜率（hold_frac 上升收缩）
    clamp_be: tuple[float, float] = (0.6, 2.0)       # 各档 clamp 下限/上限（维护安全的防护界，非业务档位）
    clamp_p1: tuple[float, float] = (1.0, 4.0)
    clamp_p2: tuple[float, float] = (1.5, 5.0)
    clamp_mae: tuple[float, float] = (0.8, 2.0)
    clamp_bar: tuple[float, float] = (0.1, 0.6)
    clamp_hold: tuple[int, int] = (12, 96)      # 时间止损档 clamp（防极端发散）
    k_base: float = 2.5         # 追踪止损基准乘数（Chandelier）
    sigma_target: float = 1.0   # 波动率目标（VT 缩放基准）
    r_oc: float = 0.02          # 机会成本率（停时/期权共用）
    audit_enabled: bool = True

class ThresholdScheduler:
    def __init__(self, cfg: SchedulerConfig,
                 table: StoppingBoundaryTable | None = None,
                 pricing: PartialBarrierPricing | None = None) -> None
    def compute(self, est: EstimatorOutput, order: OrderState,
                ts: float = 0.0, budget_bars: int | None = None,
                budget_sec: float = 60.0) -> DynamicThresholds
        # 合成逻辑（全部由 est/order/时间预算 的函数给出，无业务常量）：
        #   时间预算：budget = budget_bars or thr.max_hold_bars（护栏上期值，因果）
        #   t_frac = clamp((ts - order.entry_ts) / (budget × budget_sec), 0, 1)
        #   b_up, b_dn = table.lookup(σ_r, μ_r, t_frac, r_oc)   # ② 数值停时边界
        #   b_p2      = pricing.barrier(ζ, σ_r, t_frac)          # ③ 二批档（初始停时目标）
        #   be        = clamp( 0.5 × (w_q·q_up + w_b·b_up) × dd × t )
        #   partial1  = clamp( (w_q·q_up + w_b·b_up) × dd × t × VT )
        #   partial2  = clamp( (w_q·q_up + w_b·b_p2) × dd × t × VT )
        #   trail_start = clamp( 1.0 × VT )
        #   mae       = clamp( min(q_dn, tail_dn, b_dn) 取反 × (1-0.25·clip(ksl)) )
        #   max_hold  = round(48 × state因子 × μ存续因子)  clamp [12, 96]
        #   bar_threshold = clamp( 0.15 + (σ_r - 0.25) × 0.5 )
        # audit 记录每个档位的输入估计量与计算过程（含 b_up_num/b_dn_num/b_p2_num/t_frac/budget）
    def reset(self) -> None
```

### 4.4 决策层接口（strategy/exit_strategy.py 升级点）

```python
class ExitStrategy:
    """decide 消费 DecisionSignal（含动态门槛）；① L3 仲裁为第 7 步。"""
    def __init__(self, cfg: ExitConfig | None = None,
                 guard_cfg: GuardConfig | None = None) -> None
    def decide(self, sig: DecisionSignal, thr: DynamicThresholds) -> tuple
        # -> (action_id:int, reason:str, cmds:List[OrderCmd])
        # 决策顺序（仲裁；与 OrderManager.on_tick 接线一一对应）：
        #   1) ManualOverride（L0）无条件优先；
        #   2) 点差异常（护栏拦截）-> 仅止损管理（禁分批/AI，用 thr）；
        #   3) ζ ≥ thr.partial1_r 且未分批1 -> PART_1_3（成本门控净额≥0.15R）；
        #   4) ζ ≥ thr.partial2_r 且未分批2 -> PART_2_3（同上）；
        #   5) ζ ≥ thr.be_r 且未移保本 -> TIGHTEN_BE；
        #   6) 峰值≥thr.trail_start_r -> 追踪档（Chandelier: stop≈peak−k·ATR，k 由 VT 缩放）；
        #   7) L3 仲裁：规则结果 HOLD + sig.advisor_action 高置信(≥0.7)非 HOLD
        #      + sig.block_advisor=False（护栏未拦）-> 白名单采纳
        #      （PART_1_3 / PART_2_3 / TIGHTEN_BE / CLOSE_ALL），
        #      状态机防重入（done_p1/done_p2/moved_be）+ 成本门控复用；
        #   8) 兜底 HOLD。
        # 护栏（L1）仍逐 tick 由 guards.check_guards 执行；断熔/时间/异常档位
        #   来自 thr（mae_r / max_hold_bars），由 OrderManager 传入。
```

### 4.5 DecisionSignal 契约（decide 输入；L3 预留字段已接线）

```python
@dataclass
class DecisionSignal:
    direction: int; entry_price: float; zeta: float        # 浮盈(R)
    peak_r: float; peak_price: float; atr_price: float
    regime: int; spread_price: float; remaining_frac: float
    holding_sec: float; bars_total: int
    moved_be: bool; done_p1: bool; done_p2: bool; partial_count: int
    mae_fired: bool; override: Optional[ManualOverride]
    spread_typical: float; spread_anomaly: bool; block_advisor: bool
    advisor_action: Optional[int] = None   # L3 建议（OrderManager 步骤7 填入）
    advisor_conf: float = 0.0              # L3 置信（advisor.suggest 输出原样）
```

---

## 5. 域 D：学习层接口（strategy/rl_advisor.py + backtest/replay.py 升级点）

```python
class RLAdvisor:
    def suggest(self, state_vec: List[float]) -> tuple    # (action_id, confidence)
    # state_vec = build_decision_features(...) 11 维差值向量（① 已落地，见下）
    #   （RLEnv 的观测仍是 build_state(...) 39 维——两条路径：39 维给 RL 学
    #     价值/动作；11 维差值是 L3 建议输入，规则化、可解释、可审计）

def build_decision_features(zeta: float, thr: DynamicThresholds,
                            peak_r: float, holding_sec: float,
                            remaining_frac: float,
                            bars_total: int = 0, bar_sec: float = 60.0,
                            budget_bars: int | None = None) -> List[float]:
    """AI 特征 = 浮盈−门槛 差值向量（① 已落地）。维度 = 11（DECISION_FEATURE_NAMES）：
    d_be, d_p1, d_p2, d_trail, d_mae, d_time, d_bar,
    peak_r, drawdown_peak, remaining_frac, hold_frac。
    语义：d_be/d_p1/d_p2/d_trail/d_bar = ζ − 对应档位；d_mae = ζ + mae_r（断熔余量）；
    d_time = hold_frac − 1（预算耗尽=0，安全=负）；hold_frac = 已用时间预算比例
    （bars_total/budget_bars 优先，holding_sec/(budget·bar_sec) 兜底；thr=None → 全 0）。
    全部 clamp ±5（_clamp_d）。AI 只学“离门槛多远”，不学门槛本身。"""

class RLEnv:
    """订单生命周期 MDP（一单=一局），直接对接 stable-baselines3。"""
    observation_space: int = 39          # build_state 维度
    action_space: int = 9                # ACTION_LIST
    def reset(self) -> List[float]       # 推进到下一入场信号并开仓
    def step(self, action: int) -> tuple # (state, reward, done, info)
    # 奖励契约：
    #   决策间隙: shaped_reward(Δζ, z_old, z_new, RewardConfig)  # potential shaping
    #   终结     : terminal_reward(zeta_final, cost_r, swap_r)   # 已实现盈亏−成本−swap
    # 接线：OrderManager.on_close -> RLEnv._last_result -> terminal_reward
    def seed(self, s: int) -> None
```

---

## 6. 域 E：执行与回测接口（backtest/replay.py 升级点）

### 6.1 执行器接口（回测模拟 / MT5 实盘 两实现，同一契约）

```python
class Executor:
    def open(self, ticket: int, direction: int, lots: float,
             entry_price: float, stop_price: float, ts: float) -> None
    def modify_stop(self, ticket: int, new_stop: float, ts: float) -> bool   # False=券商拒绝
    def close_volume(self, ticket: int, frac: float, ts: float) -> float     # 返回成交均(价-成本后fill)
    def close_all(self, ticket: int, ts: float) -> float
    def quote(self, ts: float) -> tuple     # (mid, spread_pip) 数据入口抽象
    # SimExecutor：内部实现成本模型（cost_model）与滑点（确定/随机种子）
    # MT5Executor：integration/mt5_bridge.py 的落地实现（Windows、终端在线）
```

### 6.2 OrderManager 决策管线（契约化的主循环）

```python
class OrderManager:
    """多仓托管：每笔被监控订单 = 独立 OrderBundle（order/gen/estimators/thr/guard 快照），
    可同时托管多笔订单；收益K线/估计器/动态门槛/护栏**逐单隔离**。
    sim 自动开仓保持单仓纪律（in_position 时不开新单）；外部持仓**全部接管**（每笔记为独立单）。

    内部：self._blocks: Dict[ticket, OrderBundle]；self._main_ticket = 最近托管一笔（主单）；
    单仓消费者兼容：mgr.order / mgr.gen / mgr._thr = 主单属性。"""
    def __init__(self, strategy, bar_cfg, cost_cfg, guard_cfg,
                 estimators: EstimatorBundle,
                 scheduler: ThresholdScheduler,
                 executor: Executor,
                 typical_spread_pip: float = 3.0,
                 indicators: CausalIndicators | None = None,
                 advisor: RLAdvisor | None = None,   # ① L3 顾问（None=纯规则基线）
                 on_decision: Callable[[dict], None] | None = None,
                 on_close: Callable[[OrderResult], None] | None = None) -> None
    @property def in_position(self) -> bool            # len(_blocks) > 0
    @property def order(self) -> OrderState | None     # 主单状态（兼容旧单仓读取）
    @property def gen(self) -> ProfitKlineGenerator | None   # 主单收益K线生成器（兼容）
    @property def _thr(self) -> DynamicThresholds | None     # 主单门槛（兼容）
    def blocks(self) -> list[OrderBundle]              # 全部托管单（多仓遍历）
    def block(self, ticket=None) -> OrderBundle | None # 按券商 ticket 取单；None=主单
    def on_tick(self, t: Tick) -> None:
        # 对每笔托管单逐单执行 _drive_order(b, t, ext)（多仓独立运行）：
        # 1. b.est / b.order 更新浮盈、峰值、swap（逐 tick）
        # 2. b.gen.on_snapshot(...) -> committed_bar?（该单收益K线，先喂保持连续性）
        # 3. b.estimators.on_bar(committed_bar)（Bar 级估计更新，逐单独立）
        # 4. est_snap = b.estimators.snapshot(ts, ind_snapshot)（只读）
        # 5. 护栏（逐 tick）：guards.check_guards(GuardSignal, b.thr)
        #    * 断熔档/时间上限 = b.guard（开仓/每根 commit 后 dataclasses.replace 逐单快照）
        # 6. if committed_bar: b.thr = scheduler.compute(est_snap, b.order)
        #                     sig(+b.thr) -> strategy.decide -> executor.apply(cmds)
        # 7. 止损触发检查（bid/ask 穿越 b.order.stop，含滑点）-> executor.close_all
    def set_manual_override(self, action: int, reason: str,
                            ticket: Optional[int] = None) -> None   # L0，单次有效；None=主单
    def open_order(self, t: Tick, direction: int, lots: float = 1.0) -> None
        # sim 自动开仓（仅无持仓时）：建独立 OrderBundle + 更新 _main_ticket
        # 开仓时: 固定 R 锚（初始止损距离= k_base×ATR，入场时锁定）、固定 S/R（pin）
    def adopt(self, t: Tick, direction: int, entry_price: float, lots0: float,
              ticket: int, stop_price: float | None = None) -> None
        # 接管外部持仓（MT5 多仓：对 account 全量每笔 adopt）：建独立 bundle（含该单收益K线起点）
```

### 6.3 回测与评估接口

```python
def run_replay(ticks, strategy, cost_cfg, bar_cfg, guard_cfg,
               estimators, scheduler, executor_factory, entry_rule,
               advisor: RLAdvisor | None = None,   # ① L3 顾问（None=纯规则基线；A/B 对比传 RuleAdvisor）
               lots=1.0, seed=0, max_orders=100) -> ReplayResult
    # order_level 因果保证：单次正向扫描，任何决策只依赖 ts ≤ t
def run_naive_baseline(ticks, cost_cfg, sl_r=1.5, tp_r=3.0, ...) -> ReplayResult
def evaluate(replay_result) -> dict
    # 输出: 每单档案（含峰值/兑现率 Capture Ratio）、聚合 R、胜率、PF、期望、
    #       最大回撤、平均持仓、退出原因分布、交易频率/日
    # 注意: 每单档案 = OrderResult + 峰值字段（peak_r/peak_ts/capture_ratio）
def walk_forward_validate(ticks, folds, retrain_fn) -> dict   # 分段重训/重标定评估

def purged_cv(ticks, folds=4, runner=None,
              embargo_frac=0.05, overlap_pad_frac=0.02) -> dict
    # ④ 防泄漏交叉验证（TECH_DESIGN 域E ⑪ 已落地）：
    #   时间切折 → 每折测试窗 [t_lo, t_hi]；训练折剔除两层：
    #     purge   ：tick 级剔除与测试窗重叠的 tick（tick 级剔除 ⇒ 订单级不重叠：
    #               测试窗内无训练 tick ⇒ 训练订单区间不可能与测试窗相交）；
    #     embargo ：测试窗前 embargo 秒（=测试窗长 × embargo_frac）内的 tick 也剔除
    #               （防临近测试段的训练样本被测试段信号污染，AFML 7.6.2）。
    #   每折 runner(训练段) → evaluate → 指标；末尾给各折均值。
    #   返回: {"fold_0": {指标+fold+n_train_ticks}, ..., "mean": {...}}
    #   runner(ticks_segment) -> ReplayResult；默认 backtest.replay.run_replay。
    #   tick 太少（n < folds×10）抛 ValueError。
```

---

## 7. 接口数据流总图（一次完整的决策事件）

```text
Tick(t)
  ├─► IndicatorAggregator ─► CausalIndicators（1min 收盘时）──► IndicatorSnapshot
  ├─► OrderManager.on_tick
  │     ├─► OrderState 更新（浮盈/峰值/swap）
  │     ├─► ProfitKlineGenerator.on_snapshot ──► committed ProfitBar?
  │     │      └─► EstimatorBundle.on_bar(bar) ──► σ/μ/state/q/tail/ksl（域B）
  │     ├─► guards.check_guards（逐 tick；档位来自最近一次 thr）──► forced?
  │     └─► if committed:
  │            est_snap = bundle.snapshot(ts, ind_snap)          # 域B输出
  │            thr      = scheduler.compute(est_snap, order)     # 域C门槛
  │            dec      = strategy.decide(sig(+thr), thr)        # 域C决策
  │            executor.apply(dec.cmds)                          # 域E执行
  │            audit: decision_log(est, thr, dec, ζ, peak)       # 审计
  └─► 绩效: run_replay → ReplayResult → evaluate → Capture Ratio 等 → walk-forward 重标定
```

---

## 8. 功能 → 接口 → 文件 对应表

| 功能 | 接口 | 文件 |
|---|---|---|
| ① 收益K线 | ProfitKlineGenerator / Snapshot / ProfitBar | `framework/profit_kline.py`（现有） |
| ② 状态空间 | build_state / EstimatorOutput / IndicatorSnapshot | `framework/state_reward.py`（现有）、`framework/estimators.py`（新增） |
| ③ 分批止盈 | PartialBarrierPricing / ThresholdScheduler.partial1/2 | `strategy/threshold_scheduler.py`（新增） |
| ④ 追踪止损 | ThresholdScheduler.trail + Chandelier（VT 缩放） | 同上 + `strategy/exit_strategy.py`（升级） |
| ⑤ 断熔 | ThresholdScheduler.mae_r + guards.check_guards | `strategy/threshold_scheduler.py`（新增）+ `strategy/guards.py`（现有） |
| ⑥ 时间止损 | ThresholdScheduler.max_hold_bars + guards | 同上 |
| ⑦ 执行 | Executor（Sim/MT5 两实现）/ OrderCmd / cost_model | `framework/cost_model.py`（现有）、`integration/mt5_bridge.py`（新增） |
| ⑧ AI 辅助 | RLAdvisor / build_decision_features / RLEnv | ✅ `framework/state_reward.py::build_decision_features`（11 维差值向量）、`strategy/rl_advisor.py::RuleAdvisor`（差值规则顾问，BC 确定性版）、`backtest/replay.py`（OrderManager 接线：on_tick 步骤7 差值→建议→入 sig，DECIDE 事件带 adv/adv_conf） |
| ⑨ 门槛自校准 | SchedulerConfig 超参 + walk-forward / purged CV（可选在线凸优化） | ✅ `backtest/metrics.py::purged_cv`（时间切折 + purge 重叠窗 + embargo）+ 既有 `walk_forward_validate` |
| ⑩ 回测 | run_replay / run_naive_baseline | `backtest/replay.py`（升级） |
| ⑪ 绩效 | evaluate / OrderResult+峰值档案 | `backtest/metrics.py`（新增） |
| ⑫ 人工介入 | ManualOverride / set_manual_override（L0 无条件） | `strategy/guards.py`（现有）、OrderManager（现有） |

---

## 9. 与现有代码的差异清单（恢复代码时的改动点）

| 现状 | 需改动 |
|---|---|
| 文件 | 实现状态（四增强件 ①差值特征+L3 / ②停时边界数值表 / ③CRR 分批定价 / ④HMM+purged CV 均已落地） |
|---|---|
| `framework/indicators.py` | ✅ 保持；`framework/estimators.py` 新增：`EstimatorBundle`（五估计器）+ `HMMStateEstimator`（④：分段 EM 重标定 + 在线前向滤波；`on_bar` 分支 `hasattr(state,"on_delta")`，`snapshot.best()` 无参调用） |
| `framework/state_reward.py` | ✅ 保持 `build_state`；新增 `build_decision_features`（①：11 维差值向量 `DECISION_FEATURE_NAMES`，全部差分语义、`_clamp_d(±5)` 有界、thr=None 全 0 兜底；时间预算 = bar 数进度） |
| `strategy/threshold_scheduler.py` | ✅ `BoundaryTable`（②③：隐式有限差分 Thomas 解美式线性停时；σ×t 双线性插值 + μ 解析修正；lookup 懒标定、未标定回退解析外推）；`StoppingBoundaryTable.lookup` 表驱动（b_up/b_dn）+ `compute_offline` 真标定；`PartialBarrierPricing` 与停时边界共享同一表实例（③：二批档 = 初始时点停时目标 b_p2）；`ThresholdScheduler.compute` 新增 `ts/budget_bars` 接线 + 审计字段（b_up_num/b_dn_num/b_p2_num/t_frac/budget_bars） |
| `strategy/exit_strategy.py` | ✅ `decide` 第 6 步 L3 仲裁：规则 HOLD + 顾问高置信非 HOLD + 护栏未拦 → 采纳（白名单 PART_1_3/PART_2_3/TIGHTEN_BE/CLOSE_ALL；状态机防重入 + `_partial_worth_it` 成本门控复用；L0/L1 永远优先） |
| `strategy/guards.py` | ✅ 保持结构；断熔/时间档位来源 = thr（OrderManager.commit 后刷新） |
| `strategy/rl_advisor.py` | ✅ `RuleAdvisor` 真差值顾问：按 d_mae≤−0.3 → CLOSE_ALL、d_p2≥0 → PART_2_3、d_p1≥0 → PART_1_3、d_be≥0 → TIGHTEN_BE；差值深度 0→0.7、≥1R→0.95 线性置信 |
| `backtest/replay.py` | ✅ `build_decision_features` 导入；OrderManager 增加 `advisor` 参数；on_tick 步骤5 compute 传 `ts/budget_bars`；步骤7 L3：差值特征 → `advisor.suggest` → 入 `sig.advisor_action/advisor_conf`，DECIDE 事件带 `adv/adv_conf` 字段 |
| `framework/cost_model.py` | ✅ 保持，不动 |
| `integration/mt5_bridge.py` | ✅ MT5Executor 按 Executor 契约实现（阶段 2 已接线，回归 step5 验证） |
| `backtest/metrics.py` | ✅ `evaluate`/`augment_capture` + 新增 `purged_cv`（④：时间切折 + purge 重叠窗（tick 级剔除 ⇒ 订单级不重叠）+ embargo；每折指标 + mean；tick 太少抛 ValueError） |
| 测试 | ✅ `tests/test_causality.py` 15 项：原有 10 项 + 新增 5 项（差值特征有界/单调/可复现、HMM 前缀确定+EM 重标定、数值表有界+σ 单调+越界安全、purged CV 无泄漏+结构、L3 DECIDE 事件携带 adv） |