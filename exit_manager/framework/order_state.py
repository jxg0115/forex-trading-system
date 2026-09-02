# -*- coding: utf-8 -*-
"""
订单状态（OrderState）：决策与审计共用的订单生命周期数据契约
（INTERFACES.md 1.3 节）。纯数据容器 + 少量派生量，无业务逻辑。

因果性：所有字段由 OrderManager 逐 tick 因果更新，任何时刻只反映 ts <= t。
单位：R 为初始风险倍数；价格字段为 USD/oz。
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class OrderState:
    ticket: int
    direction: int               # +1 多, -1 空
    entry_price: float           # 入场 mid（USD/oz）
    entry_ts: float
    lots0: float                 # 初始手数
    lots_cur: float              # 当前剩余手数
    r_price: float               # 初始风险对应价格距离（USD/oz），入场固定
    r0_usd: float                # 初始风险金额（USD）
    stop_price: Optional[float] = None   # 当前止损（单调收紧）
    peak_r: float = 0.0          # 订单峰值浮盈（R），跨分批保留
    peak_ts: float = 0.0         # 峰值出现时间（峰值档案审计用）
    mae_fired: bool = False
    partial_count: int = 0
    moved_be: bool = False
    gross_usd: float = 0.0       # 已实现毛盈亏
    cost_usd: float = 0.0        # 累计交易成本（点差+滑点）
    swap_usd: float = 0.0        # 累计隔夜利息
    last_ts: Optional[float] = None
    # ---- 动态门槛的输入（由 OrderManager 维护，供 ThresholdScheduler 消费）----
    dd_avg: float = 0.0          # 近 N 根收益K线平均浮盈回撤率 [0,1]
    hold_frac: float = 0.0       # 持仓进度 bars/时间上限 [0,1]

    # ---------------- 派生量 ----------------

    def zeta_at(self, mid: float) -> float:
        """当前浮盈（R 倍数，方向感知）"""
        return (mid - self.entry_price) * self.direction / self.r_price if self.r_price else 0.0

    def usd_pnl_at(self, mid: float) -> float:
        """浮动盈亏（USD）：XAUUSD 按 1 标准手 = 100 oz 换算（合约规模硬编码，
        后续可配置化 contract_size）。"""
        return (mid - self.entry_price) * self.direction * self.lots0 * 100.0

    def pips_at(self, mid: float) -> float:
        """浮动盈亏（pip）：XAUUSD 1 pip = 0.01（美元计价 5 位报价）。"""
        return (mid - self.entry_price) * self.direction * 100.0

    def peak_price(self) -> float:
        """峰值浮盈对应价格（追踪止损计算用）"""
        if self.direction > 0:
            return self.entry_price + self.peak_r * self.r_price
        return self.entry_price - self.peak_r * self.r_price

    def remaining_frac(self) -> float:
        return self.lots_cur / self.lots0 if self.lots0 else 0.0

    # ---------------- 状态机查询 ----------------

    @property
    def done_p1(self) -> bool:
        return self.partial_count >= 1

    @property
    def done_p2(self) -> bool:
        return self.partial_count >= 2