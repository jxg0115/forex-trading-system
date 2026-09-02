# -*- coding: utf-8 -*-
"""
域 C：边界求解与动态门槛合成（strategy/threshold_scheduler.py）

对应 INTERFACES.md 第 4 章。全部门槛是「估计量 + 边界」的函数，无业务档位常量。

模块组成：
    1. StoppingBoundaryTable  最优停时边界（Peskir & Shiryaev 2006）：
       离线有限差分解障碍 PDE 生成表、在线查表插值；表未标定时用
       工程近似外推（c1/c2/c3 为标定超参，非业务档位）。
    2. PartialBarrierPricing  美式期权分批定价（CRR 1979 语义落地）：
       二批档 b_p2 = 初始时点（t_frac=0 层）的停时边界 —— 整个持有期的理论兑现目标
       （σ 越大目标越高，时间推进 → 现时边界 b_up 收敛向它）；与停时边界共享同一
       数值表实例（一处标定、两处消费）；未标定时回退时间价值尺度近似
       （κ·σ·sqrt(t_frac)）。
    3. ThresholdScheduler    七档合成器：估计捆 → DynamicThresholds（含审计）。

合成逻辑（全部由 est/order 的函数给出）：
    - 回撤调制 dd_scale = 1 - dd_slope*max(dd_avg-0.3, 0)   clamp [0.8, 1.0]
    - 时间衰减 t_scale   = 1 - t_slope*hold_frac             clamp [0.85, 1.0]
    - 波动目标 VT_scale  = clamp(k_base*sigma_target/max(σ_r,eps), 0.8, 1.4)
    - 分批1 = clamp(w_q*q_up + w_b*b_up, ...) × dd × t × VT
    - 分批2 = clamp(w_q*q_up + w_b*b_p2, ...) × dd × t × VT（b_p2=初始停时目标，表驱动）
    - 保本   = clamp(0.5*分批1, ...) × dd × t
    - 断熔   = clamp(min(q_dn, tail_dn, b_dn)取反 × (1-0.25*clip(ksl)), ...)
    - 追踪启动 = clamp(1.0 × VT, ...)
    - 时间上限 = round(48 × 状态因子 × 漂移存续因子)   clamp [12, 96]
    - 出Bar阈值 = clamp(0.15 + (σ_r-0.25)*0.5, ...)     clamp [0.1, 0.6]
"""

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from framework.estimators import EstimatorOutput
from framework.order_state import OrderState

# ---------------- 七档动态门槛（INTERFACES.md 1.2） ----------------

@dataclass
class DynamicThresholds:
    be_r: float = 1.0            # 保本档（浮盈达此值移保本）
    partial1_r: float = 2.0      # 分批1档
    partial2_r: float = 3.0      # 分批2档
    trail_start_r: float = 1.5   # 追踪启动档（峰值达此值开始追踪）
    mae_r: float = 1.2           # 断熔档（浮亏达 -mae_r 清仓）
    max_hold_bars: int = 48      # 时间止损（收益K线条数上限）
    bar_threshold_r: float = 0.25  # 收益K线出Bar阈值（动态）
    audit: dict = field(default_factory=dict)   # {档位: (来源, 计算过程)} 审计

    def to_audit_line(self) -> str:
        """一行可读审计（监控面板/日志用）"""
        return (f"be={self.be_r:.2f} p1={self.partial1_r:.2f} p2={self.partial2_r:.2f} "
                f"trail={self.trail_start_r:.2f} mae={self.mae_r:.2f} "
                f"hold={self.max_hold_bars} bar={self.bar_threshold_r:.2f}R")


# ---------------- 合成器配置（算法超参，非业务档位） ----------------

@dataclass
class SchedulerConfig:
    # 分位数证据 与 停时边界 的加权（证据融合）
    w_q: float = 0.5
    w_b: float = 0.5
    # 回撤/时间调制
    dd_slope: float = 0.2        # 回撤斜率（dd_avg>0.3 提前兑现）
    dd_floor: float = 0.30
    dd_clamp: Tuple[float, float] = (0.8, 1.0)
    t_slope: float = 0.12        # 时间衰减斜率
    t_clamp: Tuple[float, float] = (0.85, 1.0)
    # 波动目标（Moreira & Muir 2017，止损宽度/门槛距离的 VT 缩放）
    k_base: float = 2.5
    sigma_target: float = 1.0
    vt_clamp: Tuple[float, float] = (0.8, 1.4)
    # Kaminski-Lo 对断熔的调制
    ksl_slope: float = 0.25
    # 各档 clamp（防护界，防极端输入发散；非业务档位）
    clamp_be: Tuple[float, float] = (0.6, 2.0)
    clamp_p1: Tuple[float, float] = (1.0, 4.0)
    clamp_p2: Tuple[float, float] = (1.5, 5.0)
    clamp_mae: Tuple[float, float] = (0.8, 2.0)
    clamp_hold: Tuple[int, int] = (12, 96)
    clamp_bar: Tuple[float, float] = (0.1, 0.6)
    r_oc: float = 0.02           # 机会成本率（停时/期权共用）
    p2_scale: float = 1.5        # 分批2档相对分批1档的放大
    audit_enabled: bool = True


# ---------------- 离线数值标定（② 停时边界 / ③ 美式分批档） ----------------

def _tridiag_solve(a, b, c, d):
    """Thomas 求解三对角 a[i]·x[i-1] + b[i]·x[i] + c[i]·x[i+1] = d[i]。"""
    n = len(d)
    cp = [0.0] * n
    dp = [0.0] * n
    x = [0.0] * n
    m0 = b[0] if b[0] else 1e-12
    cp[0] = (c[0] / m0) if n > 1 else 0.0
    dp[0] = d[0] / m0
    for i in range(1, n):
        m = b[i] - a[i] * cp[i - 1]
        if abs(m) < 1e-12:
            m = 1e-12
        cp[i] = (c[i] / m) if i < n - 1 else 0.0
        dp[i] = (d[i] - a[i] * dp[i - 1]) / m
    x[n - 1] = dp[n - 1]
    for i in range(n - 2, -1, -1):
        x[i] = dp[i] - cp[i] * x[i + 1]
    return x


def _axis_idx(axis, v):
    for i in range(len(axis) - 1):
        if axis[i + 1] >= v:
            return i
    return len(axis) - 2


def _bilinear(grid, sgrid, tgrid, s, t):
    """双线性插值：grid[i_s][i_t]。"""
    si = _axis_idx(sgrid, s)
    ti = _axis_idx(tgrid, t)
    s0, s1 = sgrid[si], sgrid[min(si + 1, len(sgrid) - 1)]
    t0, t1 = tgrid[ti], tgrid[min(ti + 1, len(tgrid) - 1)]
    ws = (s - s0) / (s1 - s0) if s1 > s0 else 0.0
    wt = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
    i1 = min(si + 1, len(sgrid) - 1)
    j1 = min(ti + 1, len(tgrid) - 1)
    v00, v01 = grid[si][ti], grid[si][j1]
    v10, v11 = grid[i1][ti], grid[i1][j1]
    return (v00 * (1 - ws) * (1 - wt) + v01 * (1 - ws) * wt
            + v10 * ws * (1 - wt) + v11 * ws * wt)


def _bilinear_opt(grid, sgrid, tgrid, s, t):
    """带 None 的插值：若 4 邻点全有值返回插值；否则 None（调用方回退）。"""
    si = _axis_idx(sgrid, s)
    ti = _axis_idx(tgrid, t)
    i1 = min(si + 1, len(sgrid) - 1)
    j1 = min(ti + 1, len(tgrid) - 1)
    vals = [grid[si][ti], grid[si][j1], grid[i1][ti], grid[i1][j1]]
    if any(v is None for v in vals):
        return None
    return _bilinear(grid, sgrid, tgrid, s, t)


class BoundaryTable:
    """最优停时边界 + 美式分批档 的离线数值标定表（②③ 落地）。

    模型：浮盈过程 X（R 空间）：dX = μdt + σdW，行权收益 = X（平仓立即兑现）。
    数值：隐式有限差分（Thomas）解 V_t = ½σ²V_xx（μ=0 基表）+ 每层 Bellman
    max(V, X)（美式线性停时；Peskir & Shiryaev 2006 的工程化落地）。
    每层（时间预算分数 t_frac ∈[0,1]）提取：
        b_up      —— 上侧兑现边界（V 与 X 交叉；越过即兑现最优）
        b_dn      —— 下侧止损边界（断熔档的数值内核）
        b_p2 —— 二批兑现目标 = 初始时点（t_frac=0 层）的停时边界（“持有期理论顶点”档）
    在线查表：σ 方向 + 时间预算方向双线性插值；μ 作解析修正叠加（保持旧
    外推同语义）；未标定/出界回退解析外推（向后兼容）。"""

    def __init__(self, sigma_grid=None, n_x: int = 121, n_t: int = 24,
                 x_max: float = 4.0, r_oc: float = 0.02):
        self.sigma_grid = sigma_grid or [round(0.1 + 0.35 * i, 3) for i in range(12)]
        self.n_x = n_x
        self.n_t = n_t
        self.x_max = x_max
        self.r_oc = r_oc
        self._t_frac = [i / max(n_t - 1, 1) for i in range(n_t)]
        self._b_up = None
        self._b_dn = None

    def ready(self) -> bool:
        return self._b_up is not None

    def ensure(self) -> None:
        if not self.ready():
            self.compute()

    # ---------------- 数值求解 ----------------

    def compute(self) -> None:
        bu, bd = [], []
        for s in self.sigma_grid:
            u, d = self._solve_fd(s)
            bu.append(u)
            bd.append(d)
        self._b_up, self._b_dn = bu, bd

    def _solve_fd(self, sigma) -> Tuple[List[float], List[float]]:
        n_x, n_t = self.n_x, self.n_t
        x_max = self.x_max
        dx = 2.0 * x_max / max(n_x - 1, 1)
        dt = 1.0 / max(n_t - 1, 1)
        x = [-x_max + i * dx for i in range(n_x)]
        V = list(x)                      # 终端 payoff（线性行权收益）
        a = 0.5 * sigma * sigma * dt / (dx * dx)
        bu_l = [0.0] * n_t
        bd_l = [0.0] * n_t
        for k in range(n_t - 1, -1, -1):
            diag = [1.0 + 2.0 * a] * n_x
            off = [-a] * n_x
            d = list(V)
            d[0] = x[0]
            d[-1] = x[-1]
            V = _tridiag_solve(off, diag, off, d)
            V[0] = x[0]
            V[-1] = x[-1]
            # Bellman max：美式线性停时（立即兑现 vs 继续持有）
            V = [max(v, x[i]) for i, v in enumerate(V)]
            bu_l[k], bd_l[k] = self._extract(x, V)
        return bu_l, bd_l

    @staticmethod
    def _extract(x, V) -> Tuple[float, float]:
        n = len(x)
        bu = None
        for i in range(n // 2, n - 1):          # 上侧行权区起点
            if V[i] <= x[i] and V[i + 1] > x[i + 1]:
                bu = x[i + 1]
                break
        if bu is None:
            bu = x[-1]
        else:
            bu = max(bu, 0.2)      # 网格粗时的最低护栏：兑现边界不取到 <0.2R
        bd = None
        for i in range(n // 2, 0, -1):          # 下侧止损区起点
            if V[i] >= x[i] and V[i - 1] < x[i - 1]:
                bd = x[i - 1]
                break
        if bd is None:
            bd = x[0]
        else:
            bd = min(bd, -0.2)     # 对称最低护栏
        return bu, bd

    # ---------------- 在线查表 ----------------

    def lookup(self, sigma_r: float, t_frac: float,
               mu_r: float = 0.0) -> Tuple[float, float, float]:
        """返回 (b_up, b_dn, b_p2)，单位 R。
        b_p2 = 初始时点（t_frac=0 层）的停时边界 —— 整个持有期的理论兑现目标
        （美式/CRR 定价语义：σ 越大目标越高；时间推进 → 现时边界 b_up 收敛向它）。
        懒标定：首次查询触发离线数值求解（毫秒级）；计算异常时回退解析外推。"""
        self.ensure()
        if not self.ready():
            s2 = sigma_r * sigma_r
            r = self.r_oc
            b_up = 0.20 * s2 / max(r, 1e-9) + 1.2 * mu_r
            b_dn = -0.08 * s2 / max(r, 1e-9)
            return (min(max(b_up, 0.2), self.x_max),
                    max(b_dn, -self.x_max),
                    min(max(b_up, 1.5), self.x_max))
        s = min(max(sigma_r, self.sigma_grid[0]), self.sigma_grid[-1])
        t = min(max(t_frac, 0.0), 1.0)
        b_up = _bilinear(self._b_up, self.sigma_grid, self._t_frac, s, t)
        b_dn = _bilinear(self._b_dn, self.sigma_grid, self._t_frac, s, t)
        # b_p2：σ 方向插值“初始层 [0]”的 b_up（不随当前时间变）
        for i in range(len(self.sigma_grid) - 1):
            if self.sigma_grid[i + 1] >= s:
                s0, s1 = self.sigma_grid[i], self.sigma_grid[i + 1]
                w = (s - s0) / (s1 - s0) if s1 > s0 else 0.0
                b_p2 = self._b_up[i][0] * (1 - w) + self._b_up[i + 1][0] * w
                break
        if b_p2 is None:
            b_p2 = self._b_up[-1][0]
        b_up += 1.2 * mu_r            # μ 解析修正（跨表后叠加，保持旧外推语义）
        b_dn += 1.2 * mu_r
        b_p2 += 1.2 * mu_r
        b_up = min(max(b_up, 0.2), self.x_max)
        b_dn = max(b_dn, -self.x_max)
        b_p2 = min(max(b_p2, 1.5), self.x_max)
        return b_up, b_dn, b_p2


# ---------------- 最优停时边界（停时边界表 入口；未标定退工程外推） ----------------

class StoppingBoundaryTable:
    """最优停时上/下边界。表由 compute_offline()（有限差分，② 已实现落地）标定；
    未标定时用工程近似外推（INTERFACES.md 4.1 兜底公式）。"""

    def __init__(self, c1: float = 0.20, c2: float = 1.2, c3: float = 0.08,
                 b_up_max: float = 2.5, b_dn_max: float = 1.6,
                 table: Optional[BoundaryTable] = None):
        self.c1, self.c2, self.c3 = c1, c2, c3
        self.b_up_max, self.b_dn_max = b_up_max, b_dn_max
        self.table = table or BoundaryTable(r_oc=0.02)

    def load_table(self, path: str) -> None:
        raise NotImplementedError("离线表为内存标定（compute_offline）；文件持久化为后续扩展")

    def lookup(self, sigma_r: float, mu_r: float, t_frac: float,
               r: float) -> Tuple[float, float]:
        """返回 (b_up, b_dn)，单位 R。
        表驱动（②）：σ 与时间预算双线性插值；μ 解析修正。
        回退（未标定）：b_up = c1·σ²/r + c2·μ；b_dn = -c3·σ²/r。"""
        bu, bd, _bp = self.table.lookup(sigma_r, t_frac, mu_r)
        return (min(max(bu, 0.2), self.b_up_max), max(bd, -self.b_dn_max))

    def compute_offline(self, sigma_grid=None, n_x: int = 121, n_t: int = 24,
                        x_max: float = 4.0, r: Optional[float] = None) -> None:
        """离线标定（有限差分，②）：填数值表；之后 lookup 自动切表驱动。
        理论来源：Peskir & Shiryaev 2006 最优停时障碍解。"""
        self.table = BoundaryTable(sigma_grid=sigma_grid, n_x=n_x, n_t=n_t,
                                   x_max=x_max,
                                   r_oc=r if r is not None else self.table.r_oc)
        self.table.compute()


# ---------------- 美式期权分批定价（②③ 表驱动；未标定用时间价值近似） ----------------

class PartialBarrierPricing:
    """二批兑现档 = 初始时点停时目标（“该兑现”档，美式/CRR 语义落地）。
    ③ 表驱动 —— b_p2 取 BoundaryTable 初始层（t_frac=0）的停时边界，与停时边界
    共享同一数值表实例；未标定时回退时间价值尺度近似：barrier = max(1.0, κ·σ·sqrt(t_frac))。"""

    def __init__(self, kappa: float = 1.5, t_ref: float = 3600.0,
                 table: Optional[BoundaryTable] = None):
        self.kappa = kappa
        self.t_ref = t_ref
        self.table = table or BoundaryTable(r_oc=0.02)

    def time_value(self, sigma_r: float, t_frac: float) -> float:
        return self.kappa * sigma_r * (max(t_frac, 1e-6)) ** 0.5

    def barrier(self, zeta: float, sigma_r: float, t_frac: float) -> float:
        if self.table is not None and self.table.ready():
            _bu, _bd, bp2 = self.table.lookup(sigma_r, t_frac, 0.0)
            if bp2 is not None:
                return max(1.5, bp2)
        return max(1.0, self.time_value(sigma_r, t_frac))


# ---------------- 七档合成器 ----------------

def _clamp_f(x: float, lo: float, hi: float) -> float:
    return min(max(x, lo), hi)


class ThresholdScheduler:
    """估计捆 + 订单状态 → 七档动态门槛（含审计）。决策管线唯一门槛出口。"""

    def __init__(self, cfg: Optional[SchedulerConfig] = None,
                 table: Optional[StoppingBoundaryTable] = None,
                 pricing: Optional[PartialBarrierPricing] = None):
        self.cfg = cfg or SchedulerConfig()
        self.table = table or StoppingBoundaryTable()
        # ③ 与 ② 共享同一数值表实例：一次离线标定/懒标定，两处消费（表驱动一致）
        self.pricing = pricing or PartialBarrierPricing(table=self.table.table)

    def compute(self, est: EstimatorOutput, order: OrderState,
                ts: float = 0.0, budget_bars: Optional[int] = None,
                budget_sec: float = 60.0) -> DynamicThresholds:
        c = self.cfg
        thr = DynamicThresholds()

        # ---- 时间预算（因果：只用已注入护栏/上一期预算；无则常量兜底）----
        budget = int(budget_bars) if budget_bars and budget_bars > 0 else 48
        t_frac = 0.0
        if ts > 0.0 and order.entry_ts > 0.0:
            t_frac = min(max((ts - order.entry_ts) / (budget * budget_sec), 0.0), 1.0)

        # ---- 估计量 / 边界准备 ----
        sig = max(est.sigma_r, 1e-6)
        b_up, b_dn = self.table.lookup(sig, est.mu_r, t_frac, c.r_oc)   # ② 数值停时边界
        b_p2 = self.pricing.barrier(0.0, sig, t_frac)                   # ③ 美式二批档（初始停时目标）
        dd_scale = _clamp_f(1.0 - c.dd_slope * max(order.dd_avg - c.dd_floor, 0.0),
                            *c.dd_clamp)
        t_scale = _clamp_f(1.0 - c.t_slope * max(order.hold_frac, 0.0), *c.t_clamp)
        vt_scale = _clamp_f(c.k_base * c.sigma_target / sig, *c.vt_clamp)

        # ---- 分批档（分位数证据 × 停时边界 加权；分批2 用美式交叉档） ----
        p1_raw = c.w_q * est.q_up + c.w_b * b_up
        thr.partial1_r = _clamp_f(p1_raw * dd_scale * t_scale * vt_scale, *c.clamp_p1)
        p2_raw = c.w_q * est.q_up + c.w_b * b_p2
        thr.partial2_r = _clamp_f(p2_raw * dd_scale * t_scale * vt_scale, *c.clamp_p2)

        # ---- 保本档（约半个分批1档，乘调制） ----
        thr.be_r = _clamp_f(0.5 * p1_raw * dd_scale * t_scale, *c.clamp_be)

        # ---- 断熔档（保守取三来源最小值 → 取负为正；Kaminski-Lo 调制） ----
        mae_raw = -min(est.q_dn, est.tail_dn, b_dn)
        ksl_mod = 1.0 - c.ksl_slope * _clamp_f(est.ksl, -1.0, 1.0)
        thr.mae_r = _clamp_f(mae_raw * ksl_mod, *c.clamp_mae)

        # ---- 追踪启动档 ----
        thr.trail_start_r = _clamp_f(1.0 * vt_scale, 0.6, 2.5)

        # ---- 时间上限（状态因子 × 漂移存续因子；HMM 增强后 state_best 来自在线滤波） ----
        state_factor = {0: 1.25, 1: 1.0, 2: 0.75}.get(est.state_best, 1.0)
        mu_factor = 0.8 if est.mu_r < -0.02 else 1.0
        thr.max_hold_bars = int(_clamp_f(48.0 * state_factor * mu_factor,
                                         float(c.clamp_hold[0]), float(c.clamp_hold[1])))

        # ---- 出Bar阈值（σ 噪声标定：波动大阈值提高，防无效出Bar） ----
        thr.bar_threshold_r = _clamp_f(0.15 + (sig - 0.25) * 0.5, *c.clamp_bar)

        # ---- 审计 ----
        if c.audit_enabled:
            thr.audit = {
                "est": dict(est.audit),
                "b_up": round(b_up, 3), "b_dn": round(b_dn, 3),       # 旧契约键（INTERFACES 4.1）
                "b_up_num": round(b_up, 3), "b_dn_num": round(b_dn, 3),
                "b_p2_num": round(b_p2, 3),
                "t_frac": round(t_frac, 3), "budget_bars": budget,
                "dd_scale": round(dd_scale, 3), "t_scale": round(t_scale, 3),
                "vt_scale": round(vt_scale, 3),
                "p1_raw": round(p1_raw, 3), "p2_raw": round(p2_raw, 3),
                "mae_raw": round(mae_raw, 3),
                "state_factor": state_factor, "mu_factor": mu_factor,
            }
        return thr

    def reset(self) -> None:
        """合成器无内部订单级状态（纯函数），reset 仅占位保证接口一致性。"""
        pass