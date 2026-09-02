# -*- coding: utf-8 -*-
"""
模块二：多因子状态空间（State Space）与复合奖励函数（Reward）计算器

状态设计原则：
    - 全部特征因果（只依赖已提交Bar + 已收盘指标K线 + 当前时刻信息）；
    - 全部归一化（R倍数、z-score、百分比、分数），对仓位规模与价格水平不敏感；
    - 维度与顺序固定（STATE_DIMS / FEATURE_NAMES），实盘与回测用同一份代码。

奖励设计原则（理论依据，学术上最重要的一条）：
    - 交易频率/过度交易不应靠"拍脑袋惩罚项"压制 —— 点差+滑点已进环境成本，
      频繁分批/频繁开关仓在目标函数里自然被惩罚（见 cost_model 注释）。
    - 若确需中间态奖励加速训练，必须用 potential-based reward shaping
      （Ng, Harada & Russell 1999: "Policy Invariance Under Reward Transformations"）：
          r'(s,a,s') = r(s,a,s') + γ·Φ(s') − Φ(s)
      它不改变最优策略；此处取势能函数 Φ = 归一化当前浮盈(R)。
    - 终局奖励 = 已实现盈亏(R) − 全部交易成本(R) − 累计swap(R)。
      单位统一为初始风险 R 的倍数，不同订单可比较、可聚合。
"""

from dataclasses import dataclass
from typing import List, Optional

from .indicators import IndicatorSnapshot, IncrementalStats
from .profit_kline import ProfitKlineGenerator, BarConfig

# ---------------- 状态向量规格 ----------------

# 状态维度与特征名（顺序固定；修改时须同步更新两处）
FEATURE_NAMES: List[str] = []
for i in range(1, 6):  # 最近 5 根已提交收益K线的 OHLC + 最大回撤
    FEATURE_NAMES += [f"bar{i}_open", f"bar{i}_high", f"bar{i}_low", f"bar{i}_close", f"bar{i}_dd"]
FEATURE_NAMES += [
    "close_z20",       # 最近收盘浮盈相对20根收益K线的z-score（因果滚动）
    "close_mean20",    # 20根收益K线收盘浮盈均值
    "close_std20",     # 20根收益K线收盘浮盈标准差
    "zeta",            # 当前浮盈(R)
    "peak_r",          # 订单峰值浮盈(R)
    "hold_sec_day",    # 持仓时长归一化（秒/86400）
    "remaining_frac",  # 剩余仓位比例
    "bars_total",      # 收益K线总数（log1p 压缩）
    "atr_z",           # 外部: ATR 因果 z-score
    "regime",          # 外部: 波动率 regime 0/1/2
    "rsi_norm",        # 外部: (RSI-50)/50
    "dist_support_pct",  # 外部: 距支撑百分比
    "dist_resist_pct",   # 外部: 距阻力百分比
    "spread_ratio",      # 外部: 当前点差/典型点差
]
STATE_DIMS: int = len(FEATURE_NAMES)
N_BARS_FEAT: int = 5


@dataclass
class RewardConfig:
    gamma: float = 0.99          # 折扣率（potential shaping 与 MDP 共用）
    zeta_clamp_lo: float = -3.0  # Φ 的截断下界
    zeta_clamp_hi: float = 10.0  # Φ 的截断上界
    use_potential_shaping: bool = True  # 默认开（理论依据见模块docstring）


# ---------------- 状态构建 ----------------

def build_state(
    gen: ProfitKlineGenerator,
    zeta: float,
    peak_r: float,
    holding_sec: float,
    remaining_frac: float,
    ext: IndicatorSnapshot,
    typical_spread_pip: float = 3.0,
    close_stats: Optional[IncrementalStats] = None,
) -> List[float]:
    """构建定长状态向量（因果、归一化）。

    close_stats：收益K线收盘浮盈的因果滚动统计器（由上层维护推入），
    供 close_z20 使用；为 None 时退化为窗口内即时统计（仍只用已提交Bar）。
    """
    vec: List[float] = []

    bars = gen.bars_back(N_BARS_FEAT)
    if len(bars) < N_BARS_FEAT:
        # 不足时用 0 补齐：新订单的收益K线天然以 0（入场点浮盈）为基准
        bars = (N_BARS_FEAT - len(bars)) * [None] + bars
    for b in bars:
        if b is None:
            vec += [0.0, 0.0, 0.0, 0.0, 0.0]
        else:
            vec += [b.open_r, b.high_r, b.low_r, b.close_r, b.dd_rate]

    # 20根收益K线聚合（因果）
    cs = [b.close_r for b in gen.committed[-20:]]
    if len(cs) >= 2:
        m = sum(cs) / len(cs)
        v = sum((x - m) ** 2 for x in cs) / len(cs)
        s = v ** 0.5 or 1.0
        vec += [(cs[-1] - m) / s, m, s]
    else:
        vec += [0.0, 0.0, 1.0]

    # 快照特征
    vec += [
        zeta,
        peak_r,
        holding_sec / 86400.0,
        remaining_frac,
        _log1p(max(gen.bar_count, 0)),
    ]

    # 外部特征
    vec += [
        ext.atr_z,
        float(ext.regime),
        (ext.rsi - 50.0) / 50.0,
        ext.dist_support_pct,
        ext.dist_resist_pct,
        ext.spread_pip / max(typical_spread_pip, 1e-9),
    ]

    assert len(vec) == STATE_DIMS, f"状态维度不一致: {len(vec)} != {STATE_DIMS}"
    return vec


def _log1p(x: float) -> float:
    import math
    return math.log1p(x)


# ---------------- 特征索引（诊断/可视化用） ----------------

def feature_index(name: str) -> int:
    return FEATURE_NAMES.index(name)


# ---------------- 奖励计算 ----------------

def clamp_zeta(z: float, cfg: RewardConfig) -> float:
    return min(max(z, cfg.zeta_clamp_lo), cfg.zeta_clamp_hi)


def shaped_reward(delta_zeta: float, z_old: float, z_new: float, cfg: RewardConfig) -> float:
    """中间态奖励：浮盈增量 + 可选 potential-based shaping（Ng et al. 1999）。

    r' = r + γ·Φ(s') − Φ(s)，Φ = clamp(zeta)。
    理论保证：不改变最优策略；实践中能显著加速收敛并减少策略抖动。
    """
    if not cfg.use_potential_shaping:
        return delta_zeta
    return delta_zeta + cfg.gamma * clamp_zeta(z_new, cfg) - clamp_zeta(z_old, cfg)


def terminal_reward(zeta_final: float, cost_r: float, swap_r: float) -> float:
    """终局奖励（订单结算时）：已实现盈亏 − 成本 − swap，单位 R。"""
    return zeta_final - cost_r - swap_r


# ---------------- 决策差值特征（供 L3 AI/规则顾问，INTERFACES.md 1.2 ⑤） ----------------

# 特征 = 当前浮盈 − 各动态档 的差值（AI 吃"差值"不吃门槛本身：高信息量、归一、
# 天然活在阈值语义空间里；THEORY 3.3 / TECH_DESIGN 4.3-3）。
# 全部因果（t 时刻仅由 ≤t 的浮盈/订单状态/已合成门槛构成），全部有界。
_DECISION_CLAMP: float = 5.0

DECISION_FEATURE_NAMES: List[str] = [
    "d_be",      # 浮盈 − 保本档       （≥0 → 移保本）
    "d_p1",      # 浮盈 − 分批1档
    "d_p2",      # 浮盈 − 分批2档
    "d_trail",   # 峰值 − 追踪启动档  （≥0 → 开始追踪）
    "d_mae",     # 浮盈 + 断熔档       （<0 → 逼近断熔，越负越危险）
    "d_time",    # 已用时间预算比 − 1  （<0 → 预算内；越接近 0 越临近时间止损）
    "d_bar",     # 浮盈 − 出Bar阈值    （决策信息密度）
    "peak_r",    # 订单峰值浮盈
    "drawdown_peak",  # 峰值回撤深度 = peak−ζ
    "remaining_frac", # 剩余仓位比例
    "hold_frac",      # 已用时间预算比例 [0,1]
]
DECISION_DIMS: int = len(DECISION_FEATURE_NAMES)


def _clamp_d(x: float, lim: float = _DECISION_CLAMP) -> float:
    return max(-lim, min(lim, x))


def build_decision_features(
    zeta: float,
    thr,                       # DynamicThresholds（阈值合成器输出；None → 全 0 兜底）
    peak_r: float,
    holding_sec: float,
    remaining_frac: float,
    bars_total: int = 0,
    bar_sec: float = 60.0,
    budget_bars: Optional[int] = None,   # 时间预算（bar 数；None 用 thr.max_hold_bars）
) -> List[float]:
    """构造 L3 差值特征向量（定长 DECISION_DIMS，全部因果、全部有界）。

    语义约定：d_time = 已用预算比 − 1（负=预算内安全，0=预算耗尽）；
    d_mae = ζ + mae_r（浮亏逼近断熔档的余量）。"""
    if thr is None:
        return [0.0] * DECISION_DIMS
    bb = thr.max_hold_bars if budget_bars is None else budget_bars
    bb = max(bb, 1)
    budget_sec = float(bb) * bar_sec
    hold_frac = min(max(holding_sec / budget_sec, 0.0), 1.0)
    d_mae = zeta + thr.mae_r
    raw = [
        zeta - thr.be_r,
        zeta - thr.partial1_r,
        zeta - thr.partial2_r,
        peak_r - thr.trail_start_r,
        d_mae,
        hold_frac - 1.0,
        zeta - thr.bar_threshold_r,
        peak_r,
        peak_r - zeta,
        remaining_frac,
        hold_frac,
    ]
    return [_clamp_d(x) for x in raw]


def decision_feature_index(name: str) -> int:
    return DECISION_FEATURE_NAMES.index(name)