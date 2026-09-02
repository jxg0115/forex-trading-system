# -*- coding: utf-8 -*-
"""
域 B：状态估计算法库（framework/estimators.py）

对应 INTERFACES.md 第 3 章。五个估计器 + EstimatorBundle 聚合器。
每个估计器遵守全局约定：因果（只读已 push 样本）、可重置、有界、可审计。

默认启用（零依赖，纯 Python）：
    1. EWMAVolEstimator        σ_r(t)：浮盈变动波动率
       —— RiskMetrics EWMA（λ=0.94），输入是收益K线相邻收盘浮盈差 Δζ；
    2. DriftEstimator          μ_r(t)：浮盈漂移（滚动均值）；
    3. StateEstimator          状态 0/1/2（σ 历史分位 + μ 符号）—— 轻量在线实现，
       HMM（Hamilton 1989）作为增强件（需 hmmlearn），接口保留；
    4. DistributionEstimator   q_up/q_dn 在线分位 + tail_dn 左尾
       —— 滑窗经验分位（EVT-POT 广义帕累托为增强点，标注见注释）；
    5. KaminskiLoEstimator     ksl(t)：Δζ 一阶自相关的负值 [-1,1]
       —— 对应 Kaminski & Lo (2014)：收益负自相关（可预测反转）时止损有效，
       ksl>0 收紧断熔档、<0 放宽。

单位约定：除 atr/spread 价格口径来自 CausalIndicators 外，其余全部为 R 倍数。
"""

from collections import deque
from dataclasses import dataclass, field
from typing import List, Optional

from framework.indicators import IndicatorSnapshot
from framework.profit_kline import ProfitBar


# ---------------- 估计捆输出（INTERFACES.md 1.2） ----------------

@dataclass
class EstimatorOutput:
    ts: float = 0.0
    sigma_r: float = 1.0          # 浮盈变动波动率（R/bar）
    mu_r: float = 0.0             # 浮盈漂移（R/bar）
    state_probs: List[float] = field(default_factory=lambda: [1.0, 0.0, 0.0])
    state_best: int = 1           # 0 低波 / 1 中 / 2 高波（或趋势）
    q_up: float = 0.25            # Δζ 分布上分位（默认 0.75）
    q_dn: float = -0.25           # Δζ 分布下分位（默认 0.05）
    tail_dn: float = -0.3         # 左尾估计（EVT-POT 增强前的经验 2% 分位）
    ksl: float = 0.0              # Kaminski-Lo 有效性 [-1,1]
    atr_price: float = 0.0        # 来自 CausalIndicators（USD/oz）
    spread_pip: float = 3.0
    spread_pip_avg: float = 3.0
    audit: dict = field(default_factory=dict)   # {算法: 输入关键值}


# ---------------- 估计器 1：EWMA 波动率 ----------------

class EWMAVolEstimator:
    """σ_r(t) = sqrt(λ·σ²_{t-1} + (1-λ)·Δζ²_t)，λ=0.94（RiskMetrics）。
    因果：sigma() 只依赖已 update 样本；样本 < min_n 时返回兜底 1.0。"""

    def __init__(self, lam: float = 0.94, min_n: int = 20):
        self.lam = lam
        self.min_n = min_n
        self.s2 = 0.0
        self.n = 0

    def update(self, delta_zeta: float) -> float:
        self.n += 1
        if self.n == 1:
            self.s2 = delta_zeta * delta_zeta
        else:
            self.s2 = self.lam * self.s2 + (1.0 - self.lam) * delta_zeta * delta_zeta
        return self.sigma()

    def sigma(self) -> float:
        if self.n < self.min_n or self.s2 <= 0.0:
            return 1.0
        return self.s2 ** 0.5

    def reset(self) -> None:
        self.s2 = 0.0
        self.n = 0


# ---------------- 估计器 2：漂移（滚动均值） ----------------

class DriftEstimator:
    """μ_r(t) = 最近 window 根 Δζ 的均值。可升级为 DLM（West & Harrison）。"""

    def __init__(self, window: int = 50):
        self.window = window
        self.buf: deque = deque(maxlen=window)

    def update(self, delta_zeta: float) -> float:
        self.buf.append(delta_zeta)
        return self.mu()

    def mu(self) -> float:
        if not self.buf:
            return 0.0
        return sum(self.buf) / len(self.buf)

    def reset(self) -> None:
        self.buf.clear()


# ---------------- 估计器 3：状态（轻量在线；HMM 为增强件） ----------------

class StateEstimator:
    """状态 0/1/2：由 σ 在自身历史中的分位定波动等级。
    增强件：HMM（Hamilton 1989）可用 hmmlearn 替换，接口（update/best）不变。"""

    def __init__(self, window: int = 200, q_lo: float = 0.35, q_hi: float = 0.70):
        self.window = window
        self.q_lo = q_lo
        self.q_hi = q_hi
        self._sigmas: deque = deque(maxlen=window)

    def update(self, sigma_r: float) -> float:
        self._sigmas.append(sigma_r)
        return float(self.best(0.0))

    def best(self, mu_r: float) -> int:
        """波动分位定状态；μ 显著正向且波动中等时视为趋势（2）。"""
        if len(self._sigmas) < 10:
            return 1
        s = sorted(self._sigmas)
        rank = s.index(self._sigmas[-1]) / max(len(s) - 1, 1)
        if rank < self.q_lo:
            return 0
        if rank > self.q_hi or (0.35 <= rank <= 0.70 and mu_r > 0.05):
            return 2
        return 1

    def probs(self) -> List[float]:
        b = self.best(0.0)
        p = [0.0, 0.0, 0.0]
        p[b] = 1.0
        return p

    def reset(self) -> None:
        self._sigmas.clear()


# ---------------- 估计器 3b：HMM 状态（Hamilton 1989；因果前向滤波） ----------------

class HMMStateEstimator:
    """隐马尔可夫状态估计（浮盈变动 Δζ 的离散隐状态，高斯发射）。

    与 StateEstimator（轻量分位启发式）接口兼容，作为【真 HMM】替代件：
        - 训练：Baum-Welch（EM）批量拟合历史 Δζ（分段重标定，只看已观测段落，因果）；
        - 在线：标准前向滤波（α 递推）只依赖 ≤t 的 Δζ 前缀 —— 前缀不变性天然成立
          （tests/test_causality 用 prefix 不变性验证）；
        - 输出：state_best()/probs()，与 StateEstimator 同契约（EstimatorBundle 鸭子分支）。

    理论依据：Hamilton 1989 体制转换模型（TECH_DESIGN 域B ②，默认启用清单）；
    THEORY 支柱：状态 0/1/2（低/中/高波动体制），高波动体制收紧时间上限。
    """

    def __init__(self, n_states: int = 3, max_hist: int = 600,
                 retrain_every: int = 300, em_iters: int = 40,
                 min_n: int = 30):
        self.n_states = n_states
        self.max_hist = max_hist
        self.retrain_every = retrain_every
        self.em_iters = em_iters
        self.min_n = min_n
        self.hist: deque = deque(maxlen=max_hist)
        self._n = 0
        # 参数（初始：平分发射均值；对角占优转移）
        self.mu: List[float] = [0.0] * n_states      # 各状态 Δζ 均值
        self.sigma: List[float] = [1.0] * n_states   # 各状态 Δζ 标准差
        self.A: List[List[float]] = _stochastic_matrix(n_states)  # 转移矩阵（行随机）
        self.pi: List[float] = [1.0 / n_states] * n_states        # 初始分布
        self._alpha: List[float] = None              # 在线前向概率
        self._valid: bool = False                    # 参数是否有效（足够样本后置 True）

    # ---- 因果喂入：Δζ（每根收益K线 commit 后调用）----
    def on_delta(self, dz: float) -> None:
        self._n += 1
        self.hist.append(dz)
        if self._n % self.retrain_every == 0 and len(self.hist) >= self.min_n:
            self._fit_em(list(self.hist))
        self._filter(dz)

    def best(self) -> int:
        """当前状态后验 argmax（未有效 → 中态 1 兜底）。"""
        if not self._valid or self._alpha is None:
            return 1
        return max(range(self.n_states), key=lambda s: self._alpha[s])

    def probs(self) -> List[float]:
        if not self._valid or self._alpha is None:
            return [0.0, 1.0, 0.0][:self.n_states]
        total = sum(self._alpha)
        if total <= 0.0:
            return [1.0 / self.n_states] * self.n_states
        return [a / total for a in self._alpha]

    # ---- 在线前向滤波（只依赖前缀；因果核心）----
    def _filter(self, dz: float) -> None:
        if not self._valid:
            return
        alpha = self._alpha
        if alpha is None:
            alpha = [self.pi[s] * _gauss(dz, self.mu[s], self.sigma[s])
                     for s in range(self.n_states)]
        else:
            alpha = [sum(alpha[j] * self.A[j][s] for j in range(self.n_states))
                     * _gauss(dz, self.mu[s], self.sigma[s])
                     for s in range(self.n_states)]
        z = sum(alpha)
        if z > 0.0:
            alpha = [a / z for a in alpha]
        self._alpha = alpha

    # ---- 批量 EM(Baum-Welch) 重标定（只看已观测历史；因果段内拟合）----
    def _fit_em(self, xs: List[float]) -> None:
        n = len(xs)
        ns = self.n_states
        # 初始化：按分位点给状态均值，其余参数沿用/粗估
        self.mu = _quantile_means(xs, ns)
        sd = _std(xs) or 1.0
        self.sigma = [sd / (1.0 + 0.5 * i) for i in range(ns)]  # 低态略紧、高态略松
        self.pi = [1.0 / ns] * ns
        self.A = _stochastic_matrix(ns, diag=0.85)
        log_prev = -1e30
        for _ in range(self.em_iters):
            ga, xi_pi = _forward_backward(xs, self.mu, self.sigma, self.A, self.pi)
            self._m_step(xs, ga, xi_pi)
            log_lik = _loglike(xs, self.mu, self.sigma, self.A, self.pi)
            if abs(log_lik - log_prev) < 1e-4:
                break
            log_prev = log_lik
        self._valid = True
        # 拟合后重新从起点滤波（全部前缀因果，输出状态校准到最新观测）
        self._alpha = None
        for dz in xs:
            self._filter(dz)

    def _m_step(self, xs: List[float], ga, xi_pi) -> None:
        ns = self.n_states
        for s in range(ns):
            tot = sum(ga[t][s] for t in range(len(xs))) + 1e-9
            self.mu[s] = sum(ga[t][s] * xs[t] for t in range(len(xs))) / tot
            var = sum(ga[t][s] * (xs[t] - self.mu[s]) ** 2 for t in range(len(xs))) / tot
            self.sigma[s] = max(var ** 0.5, 1e-4)
            self.pi[s] = max(ga[0][s], 0.0)
        psum = sum(self.pi) or 1.0
        self.pi = [p / psum for p in self.pi]
        # A：利用 xi_pi（期望转移计数）更新（简化：对角主导 + 行归一）
        for i in range(ns):
            row = list(self.A[i])
            for j in range(ns):
                if i != j:
                    row[j] = max(xi_pi.get((i, j), 0.0), 1e-4)
            srow = sum(row)
            self.A[i] = [v / srow for v in row]

    def reset(self) -> None:
        self.hist.clear()
        self._n = 0
        self._alpha = None
        self._valid = False


# ---------------- HMM 数值辅助（纯 Python，零依赖） ----------------

def _gauss(x: float, mu: float, sigma: float) -> float:
    import math
    if sigma <= 0.0:
        sigma = 1.0
    d = (x - mu) / sigma
    return math.exp(-0.5 * d * d) / (sigma * math.sqrt(2.0 * math.pi))


def _std(xs: List[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    m = sum(xs) / n
    return (sum((x - m) ** 2 for x in xs) / max(n - 1, 1)) ** 0.5


def _stochastic_matrix(ns: int, diag: float = 0.9) -> List[List[float]]:
    A = [[0.0] * ns for _ in range(ns)]
    for i in range(ns):
        off = (1.0 - diag) / max(ns - 1, 1)
        for j in range(ns):
            A[i][j] = diag if i == j else off
    return A


def _quantile_means(xs: List[float], ns: int) -> List[float]:
    s = sorted(xs)
    n = len(s)
    if n == 0:
        return [0.0] * ns
    out = []
    for k in range(ns):
        idx = min(n - 1, int((k + 0.5) * n / ns))
        out.append(s[idx])
    return out


def _loglike(xs: List[float], mu, sigma, A, pi) -> float:
    import math
    ns = len(mu)
    alpha = [pi[s] * _gauss(xs[0], mu[s], sigma[s]) for s in range(ns)]
    ll = math.log(sum(alpha) + 1e-12)
    for x in xs[1:]:
        alpha = [sum(alpha[j] * A[j][s] for j in range(ns))
                 * _gauss(x, mu[s], sigma[s]) for s in range(ns)]
        ll += math.log(sum(alpha) + 1e-12)
    return ll


def _forward_backward(xs, mu, sigma, A, pi):
    """E 步：前向 α 与后向 β；返回 (gamma, xi_count)（纯 Python，O(T·S²)）。"""
    ns = len(mu)
    T = len(xs)
    alpha = [[0.0] * ns for _ in range(T)]
    for s in range(ns):
        alpha[0][s] = pi[s] * _gauss(xs[0], mu[s], sigma[s])
    z0 = sum(alpha[0]) or 1.0
    alpha[0] = [a / z0 for a in alpha[0]]
    for t in range(1, T):
        for s in range(ns):
            alpha[t][s] = sum(alpha[t - 1][j] * A[j][s] for j in range(ns)) \
                * _gauss(xs[t], mu[s], sigma[s])
        z = sum(alpha[t]) or 1.0
        alpha[t] = [a / z for a in alpha[t]]
    beta = [[0.0] * ns for _ in range(T)]
    for s in range(ns):
        beta[T - 1][s] = 1.0
    for t in range(T - 2, -1, -1):
        for i in range(ns):
            beta[t][i] = sum(A[i][j] * _gauss(xs[t + 1], mu[j], sigma[j])
                             * beta[t + 1][j] for j in range(ns))
        z = sum(beta[t]) or 1.0
        beta[t] = [b / z for b in beta[t]]
    gamma = [[alpha[t][s] * beta[t][s] for s in range(ns)]
             for t in range(T)]
    xi = {}
    for t in range(T - 1):
        norm = 0.0
        for i in range(ns):
            for j in range(ns):
                norm += alpha[t][i] * A[i][j] * _gauss(xs[t + 1], mu[j], sigma[j]) \
                    * beta[t + 1][j]
        if norm <= 0.0:
            continue
        for i in range(ns):
            for j in range(ns):
                xi[(i, j)] = xi.get((i, j), 0.0) + \
                    alpha[t][i] * A[i][j] * _gauss(xs[t + 1], mu[j], sigma[j]) \
                    * beta[t + 1][j] / norm
    return gamma, xi


# ---------------- 估计器 4：分布分位 + 左尾 ----------------

class DistributionEstimator:
    """q_up/q_dn：滑窗经验分位（75%/5%）；tail_down：经验 2% 分位。
    因果：只使用已 update 样本；样本不足返回兜底值。
    增强点：EVT-POT（McNeil & Frey 2000）可用 scipy.stats.genpareto 拟合左尾替换
    tail_down()；分位数回归森林（Meinshausen 2006）可条件化 q_up/q_dn。"""

    def __init__(self, q_hi: float = 0.75, q_lo: float = 0.05,
                 q_tail: float = 0.02, window: int = 200):
        self.q_hi = q_hi
        self.q_lo = q_lo
        self.q_tail = q_tail
        self.window = window
        self.buf: deque = deque(maxlen=window)

    def update(self, delta_zeta: float) -> None:
        self.buf.append(delta_zeta)

    def _pct(self, q: float) -> float:
        n = len(self.buf)
        if n < 10:
            return 0.25 if q >= 0.5 else -0.25
        s = sorted(self.buf)
        idx = int(round(q * (n - 1)))
        return s[idx]

    def quantiles(self) -> tuple:
        return self._pct(self.q_hi), self._pct(self.q_lo)

    def tail_down(self) -> float:
        return self._pct(self.q_tail)

    def reset(self) -> None:
        self.buf.clear()


# ---------------- 估计器 5：Kaminski-Lo 有效性 ----------------

class KaminskiLoEstimator:
    """ksl(t) = -ρ1，ρ1 为 Δζ 一阶自相关（滚动）。
    含义：ρ<0（可预测反转）→ ksl>0 → 止损/断熔有效、应收紧；
          ρ>0（趋势延续）→ ksl<0 → 止损有害、应放宽（Kaminski & Lo 2014）。"""

    def __init__(self, window: int = 100):
        self.window = window
        self.buf: deque = deque(maxlen=window)
        self._v = 0.0

    def update(self, delta_zeta: float) -> float:
        self.buf.append(delta_zeta)
        self._v = self._compute()
        return self._v

    def _compute(self) -> float:
        xs = list(self.buf)
        n = len(xs)
        if n < 10:
            return 0.0
        m = sum(xs) / n
        var = sum((x - m) ** 2 for x in xs) / n
        if var < 1e-12:
            return 0.0
        cov = sum((xs[i] - m) * (xs[i + 1] - m) for i in range(n - 1)) / (n - 1)
        rho = cov / var
        return min(max(-rho, -1.0), 1.0)

    def value(self) -> float:
        return min(max(self._v, -1.0), 1.0)

    def reset(self) -> None:
        self.buf.clear()
        self._v = 0.0


# ---------------- 聚合器：决策管线唯一入口 ----------------

class EstimatorBundle:
    """把 5 个估计器聚合成一次 snapshot，供 ThresholdScheduler 消费（3.2 节契约）。
    使用方式：
        bundle = EstimatorBundle()
        ... 每根收益K线 commit 后：bundle.on_bar(bar)
        ... 任意决策时刻：out = bundle.snapshot(ts, ind_snapshot)
    """

    def __init__(self,
                 vol: Optional[EWMAVolEstimator] = None,
                 drift: Optional[DriftEstimator] = None,
                 state: Optional[StateEstimator] = None,
                 dist: Optional[DistributionEstimator] = None,
                 ksl: Optional[KaminskiLoEstimator] = None):
        self.vol = vol or EWMAVolEstimator()
        self.drift = drift or DriftEstimator()
        self.state = state or StateEstimator()
        self.dist = dist or DistributionEstimator()
        self.ksl = ksl or KaminskiLoEstimator()
        self._prev_close: Optional[float] = None
        self._last_dzeta = 0.0

    def on_bar(self, bar: ProfitBar) -> None:
        """每根收益K线 commit 时调用：Δζ = 相邻收盘浮盈差，逐个更新估计器。"""
        if self._prev_close is None:
            self._prev_close = bar.close_r
            return
        dz = bar.close_r - self._prev_close
        self._prev_close = bar.close_r
        self._last_dzeta = dz

        self.vol.update(dz)
        mu = self.drift.update(dz)
        sig = self.vol.sigma()
        if hasattr(self.state, "on_delta"):   # HMM 类：吃 Δζ 本身（因果前向滤波）
            self.state.on_delta(dz)
        else:                                 # 轻量分位启发式：吃 σ
            self.state.update(sig)
        self.dist.update(dz)
        self.ksl.update(dz)

    def snapshot(self, ts: float, ind: IndicatorSnapshot) -> EstimatorOutput:
        """只读组装当前估计捆；audit 记录关键输入输出。"""
        sig = self.vol.sigma()
        mu = self.drift.mu()
        q_up, q_dn = self.dist.quantiles()
        tail_dn = self.dist.tail_down()
        ksl_v = self.ksl.value()
        state_best = (self.state.best() if hasattr(self.state, "on_delta")
                      else self.state.best(mu))

        out = EstimatorOutput(
            ts=ts, sigma_r=sig, mu_r=mu,
            state_probs=self.state.probs(), state_best=state_best,
            q_up=q_up, q_dn=q_dn, tail_dn=tail_dn, ksl=ksl_v,
            atr_price=ind.atr,
            spread_pip=ind.spread_pip, spread_pip_avg=ind.spread_pip_avg,
        )
        out.audit = {
            "dz": round(self._last_dzeta, 3),
            "sigma_r": round(sig, 3),
            "mu_r": round(mu, 3),
            "q_up": round(q_up, 3),
            "q_dn": round(q_dn, 3),
            "tail_dn": round(tail_dn, 3),
            "ksl": round(ksl_v, 3),
            "state_best": state_best,
            "atr_price": round(ind.atr, 3),
        }
        return out

    def reset(self) -> None:
        """订单级估计起点（开仓时调用，保证因果干净）。"""
        for e in (self.vol, self.drift, self.state, self.dist, self.ksl):
            e.reset()
        self._prev_close = None
        self._last_dzeta = 0.0