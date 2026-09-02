# -*- coding: utf-8 -*-
"""
模块三：执行动作映射（Action Space 与券商指令映射）

动作定义（离散网格，利于 PPO 采样效率；参数通过"档位"离散化）：

    0  HOLD          保持现状（券商端止损/止盈不动）
    1  TIGHTEN_BE    止损移至成本价 + 缓冲（保本单，锁定零风险）
    2  TIGHTEN_1A    追踪止损 = 峰值 − 1.00 × ATR（让利润奔跑，留正常波动空间）
    3  TIGHTEN_2A    追踪止损 = 峰值 − 0.75 × ATR（趋势明确时收紧）
    4  TIGHTEN_3A    追踪止损 = 峰值 − 0.50 × ATR（大浮盈时大幅收紧）
    5  PART_1_3      分批止盈 1/3 仓位
    6  PART_1_2      分批止盈 1/2 仓位
    7  PART_2_3      分批止盈 2/3 仓位（留 1/3 奔跑仓）
    8  CLOSE_ALL     清仓平今

输出为 OrderCmd 列表，由执行层（回测模拟券商 / MT5 桥）逐条执行：
    - modify_stop: 修改止损价（收紧）
    - close_volume: 平仓指定比例
    - close_all: 清仓
    - none: 无操作（仅记录原因）

注意（风控纪律）：
    - 收紧追踪止损单调性：默认绝不放松止损（allow_loosen=False 由策略层保证），
      这是"不硬扛"的工程落地 —— 止损只朝有利方向移动，回撤即退出；
    - 分批止盈每次独立成交，每次付一次点差+滑点（成本已在环境与奖励中体现）。
"""

from dataclasses import dataclass
from typing import Dict, List, Optional

# ---------------- 动作常量 ----------------
ACT_HOLD = 0
ACT_TIGHTEN_BE = 1
ACT_TIGHTEN_1A = 2
ACT_TIGHTEN_2A = 3
ACT_TIGHTEN_3A = 4
ACT_PART_1_3 = 5
ACT_PART_1_2 = 6
ACT_PART_2_3 = 7
ACT_CLOSE_ALL = 8

ACTION_NAMES: Dict[int, str] = {
    ACT_HOLD: "HOLD",
    ACT_TIGHTEN_BE: "TIGHTEN_BE(保本)",
    ACT_TIGHTEN_1A: "TIGHTEN_1A(追踪1.0ATR)",
    ACT_TIGHTEN_2A: "TIGHTEN_2A(追踪0.75ATR)",
    ACT_TIGHTEN_3A: "TIGHTEN_3A(追踪0.5ATR)",
    ACT_PART_1_3: "PART_1_3(分批1/3)",
    ACT_PART_1_2: "PART_1_2(分批1/2)",
    ACT_PART_2_3: "PART_2_3(分批2/3)",
    ACT_CLOSE_ALL: "CLOSE_ALL(清仓)",
}
ACTION_LIST: List[int] = list(ACTION_NAMES.keys())
NUM_ACTIONS: int = len(ACTION_LIST)


# ---------------- 券商指令 ----------------
@dataclass(frozen=True)
class OrderCmd:
    """一条券商可执行指令"""
    kind: str                # modify_stop / close_volume / close_all / none
    stop_price: Optional[float] = None  # modify_stop 时的新止损价
    volume_frac: float = 0.0            # close_volume 时的平仓比例 [0,1]
    reason: str = ""                    # 动作来源（策略/顾问/人工）
    action_id: int = -1                 # 原始动作编号（回溯用）


def make_stop_cmd(action_id: int, stop_price: float, reason: str) -> OrderCmd:
    return OrderCmd(kind="modify_stop", stop_price=stop_price, reason=reason, action_id=action_id)


def make_partial_cmd(action_id: int, frac: float, reason: str) -> OrderCmd:
    return OrderCmd(kind="close_volume", volume_frac=frac, reason=reason, action_id=action_id)


def make_close_cmd(action_id: int, reason: str) -> OrderCmd:
    return OrderCmd(kind="close_all", reason=reason, action_id=action_id)


def make_noop(action_id: int, reason: str) -> OrderCmd:
    return OrderCmd(kind="none", reason=reason, action_id=action_id)


# ---------------- 动作 -> 指令 映射 ----------------

@dataclass
class ActionContext:
    """把动作映射成具体价格所需的最小上下文（由订单管理者填充）"""
    direction: int          # +1 多, -1 空
    entry_price: float      # 入场价（mid）
    peak_price: float       # 最高浮盈对应的价格（方向已考虑）
    atr_price: float        # 当前 ATR（美元/盎司）
    spread_price: float     # 当前点差（美元/盎司）
    remaining_frac: float   # 剩余仓位比例
    breakeven_level: float  # 保本位（含点差缓冲后的价格）


def tighten_stop_price(ctx: ActionContext, atr_mult: float) -> float:
    """追踪止损价 = 峰值 − mult×ATR（多头）；= 峰值 + mult×ATR（空头）。
    注意：只用"峰值"（已实现过的最大浮盈），因果合法，不引用未来。"""
    if ctx.direction > 0:
        return ctx.peak_price - atr_mult * ctx.atr_price
    return ctx.peak_price + atr_mult * ctx.atr_price


def apply_action(action: int, ctx: ActionContext, reason: str = "") -> List[OrderCmd]:
    """把动作编号转成券商指令序列（策略层/RL 顾问/AI 助手统一入口）。"""
    reason = reason or ACTION_NAMES.get(action, "?")
    if action == ACT_HOLD:
        return [make_noop(action, reason)]
    if action == ACT_TIGHTEN_BE:
        return [make_stop_cmd(action, ctx.breakeven_level, reason)]
    if action == ACT_TIGHTEN_1A:
        return [make_stop_cmd(action, tighten_stop_price(ctx, 1.00), reason)]
    if action == ACT_TIGHTEN_2A:
        return [make_stop_cmd(action, tighten_stop_price(ctx, 0.75), reason)]
    if action == ACT_TIGHTEN_3A:
        return [make_stop_cmd(action, tighten_stop_price(ctx, 0.50), reason)]
    if action == ACT_PART_1_3:
        return [make_partial_cmd(action, 1.0 / 3.0, reason)]
    if action == ACT_PART_1_2:
        return [make_partial_cmd(action, 1.0 / 2.0, reason)]
    if action == ACT_PART_2_3:
        return [make_partial_cmd(action, 2.0 / 3.0, reason)]
    if action == ACT_CLOSE_ALL:
        return [make_close_cmd(action, reason)]
    return [make_noop(ACT_HOLD, f"未知动作{action}")]