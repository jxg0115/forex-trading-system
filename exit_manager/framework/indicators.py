# -*- coding: utf-8 -*-
"""
外部市场指标流（数据双流的第二流）—— 全部因果计算，无前视

内容：
    1. ATR(14)（经典 Chandelier Exit 的波动率口径）+ 因果 z-score + 波动率 regime；
    2. RSI(14)（仅使用已收盘的 1 分钟K线）；
    3. 入场时固定的支撑/阻力水平（过去 N 根已收盘K线的 min/max），
       以及"距最近支撑/阻力的百分比"（水平位在入场时刻用过去数据固定，绝不未来函数）；
    4. 点差滚动均值（用于点差异常检测）。

因果性保证：
    - 所有指标只在"1 分钟K线收盘"时更新一次；
    - 任意时刻 t 的特征值只依赖 ts ≤ t 的 tick（配单元测试验证）。
    - 与算法交易文献惯例一致：指标永远基于已收盘Bar，进行中Bar不参与。

实盘落地：MT5/OANDA 等数据源把 1 分钟K线喂给本类（接口见 to_* 注释），
回测时把 tick 聚合为 1 分钟K线后喂入 —— 同一份代码，保证 sim-to-real 一致。
"""

from dataclasses import dataclass
from typing import List, Optional

from .cost_model import PIP


@dataclass
class IndicatorConfig:
    atr_period: int = 14            # ATR 周期（1分钟bar）
    rsi_period: int = 14            # RSI 周期
    stats_window: int = 200         # 因果 z-score 的滚动窗口
    regime_low_qt: float = 0.30     # 波动率 regime 分位阈值（低/中/高）
    regime_high_qt: float = 0.70
    sr_lookback_bars: int = 60      # 支撑/阻力回看：过去 60 根已收盘1分钟bar
    bar_seconds: float = 60.0       # 聚合K线周期（1分钟）


class IncrementalStats:
    """定长窗口的因果 mean/std/z-score。
    只使用已 push 的历史样本，绝不引用未来 —— 这是状态归一化的因果保证。"""

    def __init__(self, window: int = 100):
        self.buf: List[float] = []
        self.window = window

    def push(self, x: float) -> None:
        self.buf.append(x)
        if len(self.buf) > self.window:
            self.buf.pop(0)

    def mean_std(self) -> tuple:
        n = len(self.buf)
        if n == 0:
            return 0.0, 1.0
        m = sum(self.buf) / n
        v = sum((x - m) ** 2 for x in self.buf) / n
        s = v ** 0.5
        if s < 1e-9:
            s = 1.0
        return m, s

    def z(self, x: float) -> float:
        m, s = self.mean_std()
        return (x - m) / s


@dataclass
class IndicatorSnapshot:
    """任意时刻 t 从 CausalIndicators 读出的外部特征（只含 ts ≤ t 的信息）"""
    atr: float = 0.0            # 当前 ATR（美元/盎司的1分钟波动）
    atr_z: float = 0.0          # ATR 相对自身历史的因果 z-score
    regime: int = 1             # 波动率 regime: 0 低 / 1 中 / 2 高
    rsi: float = 50.0           # RSI(14)，基于已收盘K线
    support: float = 0.0        # 入场时固定的支撑位（过去已收盘K线最低点）
    resistance: float = 0.0     # 入场时固定的阻力位
    dist_support_pct: float = 0.0   # 距支撑的百分比 = |price-support|/price*100
    dist_resist_pct: float = 0.0    # 距阻力的百分比
    spread_pip: float = 3.0     # 当前点差(pip)
    spread_pip_avg: float = 3.0 # 点差滚动均值(pip)


class CausalIndicators:
    """外部指标工厂：喂入"已收盘的1分钟K线"（和当前点差），产出因果特征。

    使用方式：
        ind = CausalIndicators(cfg)
        ind.on_bar_close(high, low, close, ts)   # 每根1分钟bar收盘时调用
        任意时刻：snap = ind.snapshot(mid_price, spread_pip, pin_levels=True)
        pin_levels=True 表示支撑/阻力在首次调用后固定（入场时刻），不再漂移。

    注意：支撑/阻力默认在"首次 snapshot 时固定"（即入场决策时刻），
    之后 snapshot 一直返回固定水平 —— 这是防止未来函数的标准做法。
    """

    def __init__(self, cfg: Optional[IndicatorConfig] = None):
        self.cfg = cfg or IndicatorConfig()
        self._highs: List[float] = []
        self._lows: List[float] = []
        self._closes: List[float] = []
        self._atr_series: List[float] = []
        self._atr_stats = IncrementalStats(self.cfg.stats_window)
        self._spread_stats = IncrementalStats(self.cfg.stats_window)
        self._spread_boot_first = True   # 首个点差观测预填充窗口（冷启动均值不被默认 3.0 误导）
        self._pinned = False              # 支撑/阻力是否已固定（入场时刻固定）
        self._support: float = 0.0
        self._resistance: float = 0.0
        self._rsi: float = 50.0
        self._atr: float = 0.0

    # ---------------- 数据入口 ----------------

    def on_bar_close(self, high: float, low: float, close: float, ts: float) -> None:
        """每根1分钟K线收盘时调用（因果：只用当天及之前的数据）"""
        self._highs.append(high)
        self._lows.append(low)
        self._closes.append(close)

        # ATR(14)：采用 Wilder 简化递归（rma）；样本不足时用简单均值
        if len(self._closes) >= 2:
            tr = max(high - low,
                     abs(high - self._closes[-2]),
                     abs(low - self._closes[-2]))
            if len(self._closes) == 2:
                self._atr = tr
            elif len(self._closes) > self.cfg.atr_period:
                self._atr = (self._atr * (self.cfg.atr_period - 1) + tr) / self.cfg.atr_period
            else:
                self._atr = (self._atr * (len(self._closes) - 2) + tr) / (len(self._closes) - 1)
        self._atr_series.append(self._atr)
        self._atr_stats.push(self._atr)

        # RSI(14)：基于已收盘价（简化平滑）
        self._update_rsi()

    def on_spread(self, spread_pip: float) -> None:
        """记录一次点差观测（也可在 snapshot 时顺带更新）。
        首个观测用其值预填充均值窗口，避免冷启动窗口内以默认 3.0
        充当均值而让护栏的'点差极端'(均值×3)被真实点差即刻误触发。"""
        if self._spread_boot_first:
            self._spread_boot_first = False
            fill = min(self.cfg.stats_window, 24)
            for _ in range(fill):
                self._spread_stats.push(spread_pip)
        else:
            self._spread_stats.push(spread_pip)

    # ---------------- 特征读取 ----------------

    def snapshot(self, mid_price: float, spread_pip: float, pin_levels: bool = True) -> IndicatorSnapshot:
        """读取当前时刻的外部特征。pin_levels=True 时首次调用固定支撑/阻力（入场时刻）。"""
        self.on_spread(spread_pip)

        # 支撑/阻力：入场时刻固定（过去 sr_lookback_bars 根已收盘K线的 min/max）
        if pin_levels and not self._pinned and len(self._closes) >= 2:
            n = min(self.cfg.sr_lookback_bars, len(self._closes))
            self._support = min(self._lows[-n:])
            self._resistance = max(self._highs[-n:])
            self._pinned = True

        # ATR z-score 与 regime（均因果：只含历史）
        atr_z = 0.0
        regime = 1
        if len(self._atr_series) > 10:
            atr_z = self._atr_stats.z(self._atr)
            sorted_atr = sorted(self._atr_series)
            rank = sorted_atr.index(self._atr) / max(len(sorted_atr) - 1, 1)
            if rank < self.cfg.regime_low_qt:
                regime = 0
            elif rank > self.cfg.regime_high_qt:
                regime = 2

        snap = IndicatorSnapshot(
            atr=self._atr,
            atr_z=atr_z,
            regime=regime,
            rsi=self._rsi,
            support=self._support,
            resistance=self._resistance,
        )
        if self._support > 0:
            snap.dist_support_pct = abs(mid_price - self._support) / mid_price * 100.0
        if self._resistance > 0:
            snap.dist_resist_pct = abs(self._resistance - mid_price) / mid_price * 100.0
        snap.spread_pip = spread_pip
        if len(self._spread_stats.buf) >= 10:
            snap.spread_pip_avg, _ = self._spread_stats.mean_std()
        return snap

    def reset(self) -> None:
        """重置（每笔订单/每个回测周期开始时调用，保持因果起点干净）"""
        self.__init__(self.cfg)

    # ---------------- 内部 ----------------

    def _update_rsi(self) -> None:
        n = self.cfg.rsi_period
        if len(self._closes) < n + 1:
            return
        gains = []
        losses = []
        for i in range(-n, 0):
            d = self._closes[i] - self._closes[i - 1]
            gains.append(max(d, 0.0))
            losses.append(max(-d, 0.0))
        ag = sum(gains) / n
        al = sum(losses) / n
        if al == 0:
            self._rsi = 100.0
        else:
            rs = ag / al
            self._rsi = 100.0 - 100.0 / (1.0 + rs)