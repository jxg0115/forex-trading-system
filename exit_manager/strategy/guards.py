# -*- coding: utf-8 -*-
"""
护栏层（Guards）：把"不硬扛、介入快、懂变通"落地的兜底机制。

设计要点（与 MDP 的接口约定一致）：
    - 护栏<b>逐 tick</b> 检查（不等待收益K线出Bar），实现"介入快"：
      紧急事件（浮亏超限、超时）发生时即刻处置，而不是拖到下一根收益K线；
    - 策略性动作（分档止盈、追踪收紧）在收益K线 commit 时检查
      （见 exit_strategy.py 的调用协议）；
    - 护栏优先级从高到低：手动介入 > MAE断熔 > 时间止损 > 点差异常降级。
"""

from dataclasses import dataclass
from typing import Optional

from framework.actions import (
    ACT_CLOSE_ALL, ACT_HOLD, ACT_PART_1_3, ACT_PART_1_2, ACT_PART_2_3,
    ACTION_NAMES,
)


@dataclass(frozen=True)
class ManualOverride:
    """人工介入指令：监控会话中随时由操作员注入（"随时介入"需求的实体）。"""
    action: int
    reason: str          # 例如 "操作员判断非农前必须降杠杆"
    ts: Optional[float] = None  # 注入时间（可选）


@dataclass
class GuardConfig:
    max_adverse_r: float = 1.2        # MAE 断熔：浮亏达 1.2R 立即清仓（不硬扛兜底）
    max_hold_bars: int = 48           # 收益K线时间止损（条数）：超限强制结算
    max_hold_sec: float = 8 * 3600.0  # 日历时间止损（秒）：超限强制结算
    spread_anomaly_x: float = 2.0     # 点差 > 均值×该倍数 -> 判定异常（禁止新增分批动作）
    spread_extreme_x: float = 3.0     # 点差 > 均值×该倍数 -> 判定极端（事件数据/行情异常）


@dataclass
class GuardSignal:
    """护栏判定所需的订单实时信息（逐 tick 由 OrderManager 构造）"""
    zeta: float                 # 当前浮盈(R)
    holding_sec: float          # 持仓秒数
    bars_total: int             # 已提交收益K线条数
    mae_fired: bool             # MAE 断熔是否已触发（防反复触发抖动）
    override: Optional[ManualOverride] = None
    spread_pip: float = 3.0
    spread_pip_avg: float = 3.0
    # 在收益K线决策时还要判断：是否值得继续分批（由 exit_strategy 做成本门控）


@dataclass
class GuardResult:
    forced: bool               # True = 必须立即执行动作（忽略策略建议）
    action: int
    reason: str
    block_partial: bool = False  # True = 该时刻禁止分批止盈（点差异常降级）
    block_advisor: bool = False  # True = 禁止 AI 建议（降级为纯规则护栏）


def check_guards(sig: GuardSignal, cfg: GuardConfig) -> GuardResult:
    """逐 tick 护栏检查。返回最高优先级触发的处置（未触发则返回 HOLD 占位）。"""

    # 1) 手动介入：操作员指令永远最高优先级（可以 TIGHTEN / PART / CLOSE 任意介入）
    if sig.override is not None:
        return GuardResult(forced=True, action=sig.override.action,
                           reason=f"MANUAL_OVERRIDE: {sig.override.reason}")

    # 2) MAE 断熔（不硬扛的核心兜底）：浮亏超过 1.2R 直接清仓，
    #    不等券商止损触发 —— 深水区的止损触发往往伴随更大滑点与心理压力。
    if sig.zeta <= -cfg.max_adverse_r and not sig.mae_fired:
        return GuardResult(forced=True, action=ACT_CLOSE_ALL,
                           reason=f"MAE断路器: 浮亏{sig.zeta:.2f}R ≤ -{cfg.max_adverse_r}R")

    # 3) 时间止损：无论是收益K线计数还是日历时间超限，都强制结算。
    #    双重目的：(a) 不硬扛 —— 久拖不决的订单一律了结；
    #            (b) MDP 保证 episode 必然终止（RL 训练的前提条件）。
    if sig.bars_total >= cfg.max_hold_bars:
        return GuardResult(forced=True, action=ACT_CLOSE_ALL,
                           reason=f"时间止损(收益K线): 已达{cfg.max_hold_bars}根")
    if sig.holding_sec >= cfg.max_hold_sec:
        return GuardResult(forced=True, action=ACT_CLOSE_ALL,
                           reason=f"时间止损(日历): 持仓{int(sig.holding_sec / 60)}分钟")

    # 4) 点差异常降级（懂变通）：行情异常（大事件/数据）期间
    #    - 禁止新增分批止盈（每次分批都要多付一次点差，异常点时差成本翻倍）；
    #    - 禁止 AI 建议（降级为纯规则护栏模式，杜绝模型在异常行情的误判）。
    block_partial = block_advisor = False
    reason = ""
    if sig.spread_pip > sig.spread_pip_avg * cfg.spread_anomaly_x:
        block_partial = True
        reason = f"点差异常(spread {sig.spread_pip:.1f} pip > {sig.spread_pip_avg:.1f} pip均值), 禁止分批/AI"
        block_advisor = True
    if sig.spread_pip > sig.spread_pip_avg * cfg.spread_extreme_x:
        return GuardResult(forced=True, action=ACT_CLOSE_ALL,
                           reason=f"点差极端({sig.spread_pip:.1f}pip), 立即降杠杆清仓",
                           block_partial=True, block_advisor=True)

    return GuardResult(forced=False, action=ACT_HOLD, reason=reason,
                       block_partial=block_partial, block_advisor=block_advisor)