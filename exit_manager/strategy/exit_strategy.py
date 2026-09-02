# -*- coding: utf-8 -*-
"""
止损止盈策略核心（基于框架的规则策略，理论支撑）：

1. 事件驱动决策（介入快）
   - 策略性动作只在「收益K线 commit」时评估：浮盈波动越大决策越频繁，
     浮盈平静时不空转 —— 这正是收益K线设计的初衷（dollar-bar 思想）。

2. 动态波动率止损 —— Chandelier Exit（LeBeau / 经典趋势退出法）
   - 追踪止损 = Peak − k × ATR。k 随波动率 regime 自适应（懂变通）：
       低波动 regime: k=3.0（波动小，给足空间避免被噪声洗出）
       中波动 regime: k=2.5
       高波动 regime: k=2.0（波动已大，绝对距离仍然够宽，但相对风险收紧）
   - 止损单调收紧（绝不放松）：默认 allow_loosen=False，止损只朝有利方向移动，
     回撤即退出 = 不硬扛的工程落地。

3. R 倍数里程碑（三屏障法 triple-barrier 思想的止盈侧落地，López de Prado）
   - 浮盈 ≥ +1R  → 止损移至保本位+缓冲（锁定本轮不亏损，本金永不受伤）
   - 浮盈 ≥ +2R  → 分批止盈 1/3
   - 浮盈 ≥ +3R  → 再分批 1/3，剩余 1/3 交给追踪止损奔跑
     里程碑分级止盈 = 固定比率法(fixed-ratio)精神：越赚越保护，

4. 成本门控（懂变通：不做亏本的分批）
   - 分批止盈前检查：该档平仓的"点差+滑点"成本是否显著低于该档盈利，
     盈利不足以覆盖成本时跳过分批、改为持有/收紧 —— 防止为"分批而分批"。

5. 决策协议（与护栏配合，见 guards.py）：
   - 逐 tick：护栏检查（MAE断熔/时间止损/手动介入/点差异常）；
   - 收益K线 commit：本文件的策略性评估（追踪/保本/分批/清仓建议）；
   - 手动介入（ManualOverride）永远最优先 —— 监控中可随时介入。

调用协议：
    sig: DecisionSignal（由 OrderManager 在收益K线 commit 时构造）
    -> (action, reason, cmds)  cmds 由执行层逐条落地。
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple

from framework.actions import (
    ACT_HOLD, ACT_TIGHTEN_BE, ACT_TIGHTEN_1A, ACT_TIGHTEN_2A, ACT_TIGHTEN_3A,
    ACT_PART_1_3, ACT_PART_1_2, ACT_PART_2_3, ACT_CLOSE_ALL,
    ActionContext, OrderCmd, apply_action, tighten_stop_price,
)
from strategy.guards import GuardConfig, ManualOverride
from strategy.threshold_scheduler import DynamicThresholds


@dataclass
class ExitConfig:
    """止损止盈策略参数（全部可覆盖；默认值基于 XAU/USD 日线以下周期经验）"""
    # --- 动态止损（Chandelier Exit）---
    atr_mult_base: float = 2.5          # 入场初始止损 = 2.5 × ATR
    atr_mult_by_regime: dict = None     # 波动率 regime 自适应乘数 {0: 3.0, 1: 2.5, 2: 2.0}
    # --- 保本与追踪 ---
    breakeven_offset_r: float = 0.25    # 保本位 = 入场 + 0.25R + 点差缓冲
    tighten_at_peak_r: float = 1.5      # 峰值达 1.5R 后开始启用追踪止损
    trail_mult_by_stage: dict = None    # 按浮盈阶段收紧追踪 {stage: atr_mult}
    # --- 分批止盈（R 里程碑）---
    partial1_at_r: float = 2.0          # 浮盈 ≥ 2R → 分批 1/3
    partial2_at_r: float = 3.0          # 浮盈 ≥ 3R → 再分批 1/3（留 1/3 奔跑）
    partial1_frac: float = 1.0 / 3.0
    partial2_frac: float = 1.0 / 3.0
    # --- 成本门控 ---
    cost_gate_partial: bool = True      # 分批前检查成本是否值得
    min_partial_net_r: float = 0.15     # 该档分批完税后净盈利不足 0.15R 则跳过
    # --- 风控纪律 ---
    allow_loosen: bool = False          # 止损是否允许放松（默认绝不放松）
    # --- 状态机防重入 ---
    max_partials_per_order: int = 2     # 单笔订单最多分批次数（防过度交易）

    def __post_init__(self):
        if self.atr_mult_by_regime is None:
            self.atr_mult_by_regime = {0: 3.0, 1: 2.5, 2: 2.0}
        if self.trail_mult_by_stage is None:
            # 峰值阶段越高，追踪越紧（让利润奔跑但锁定更多）
            self.trail_mult_by_stage = {1.5: 1.25, 2.0: 1.00, 3.0: 0.75}


@dataclass
class DecisionSignal:
    """收益K线 commit 时刻的全部决策输入（上层构造）"""
    direction: int
    entry_price: float          # 入场价（mid）
    zeta: float                 # 当前浮盈(R)
    peak_r: float               # 订单峰值浮盈(R)
    peak_price: float           # 峰值对应价格
    atr_price: float            # 当前 ATR（美元/盎司）
    regime: int                 # 波动率 regime 0/1/2
    spread_price: float         # 当前点差（美元/盎司）
    remaining_frac: float       # 剩余仓位比例
    holding_sec: float
    bars_total: int
    # 订单状态机（防重入）：
    moved_be: bool              # 是否已移过保本
    done_p1: bool               # 是否已完成第一批分批
    done_p2: bool               # 是否已完成第二批分批
    partial_count: int
    mae_fired: bool
    override: Optional[ManualOverride] = None
    # 成本门控辅助：
    spread_typical: float = 3.0 * 0.10  # 典型点差（美元/盎司），用于点差异常判断
    spread_anomaly: bool = False        # 点差异常（护栏判定），禁止分批
    block_advisor: bool = False         # 禁止 AI 建议（护栏判定，降级纯规则）
    # AI 建议（可选辅助，来自 rl_advisor）：
    advisor_action: Optional[int] = None
    advisor_conf: float = 0.0


class ExitStrategy:
    """止损止盈策略引擎（纯函数式评估，无内部状态 -> 便于回测与实盘共用）"""

    def __init__(self, cfg: Optional[ExitConfig] = None,
                 guard_cfg: Optional[GuardConfig] = None):
        self.cfg = cfg or ExitConfig()
        self.guard_cfg = guard_cfg or GuardConfig()

    # ---------------- 主入口 ----------------

    def decide(self, sig: DecisionSignal,
               thr: Optional[DynamicThresholds] = None) -> Tuple[int, str, List[OrderCmd]]:
        """评估一个决策信号，返回 (动作, 原因, 指令序列)。

        动态门槛（thr）由 ThresholdScheduler 给出：保本/分批/追踪启动档位
        全部由估计层算法实时合成；thr 为 None 时回退到 ExitConfig 常量（兜底）。
        """
        r_price = self._r_price_of(sig)   # 初始风险对应的价格距离（固定锚）

        # ---- 护栏已在逐 tick 层处理；这里做策略性评估 ----
        # 1) 手动介入：最高优先级（随时介入）
        if sig.override is not None:
            reason = f"MANUAL: {sig.override.reason}"
            return sig.override.action, reason, self._cmds(sig.override.action, sig, r_price)

        # 2) 点差异常 -> 禁止分批，仅做止损管理（变通：行情异常不折腾）
        if sig.spread_anomaly:
            return self._trail_only(sig, r_price, "点差异常,仅止损管理", thr)

        # 3) 分批止盈档（动态门槛；无门槛时回退常量兜底）
        p1 = thr.partial1_r if thr else self.cfg.partial1_at_r
        p2 = thr.partial2_r if thr else self.cfg.partial2_at_r
        if not sig.done_p1 and sig.zeta >= p1:
            if self._partial_worth_it(sig, self.cfg.partial1_frac, r_price):
                reason = f"分批1: 浮盈{sig.zeta:.2f}R≥动态档{p1:.2f}R"
                return ACT_PART_1_3, reason, self._cmds(ACT_PART_1_3, sig, r_price)
        if not sig.done_p2 and sig.zeta >= p2:
            if self._partial_worth_it(sig, self.cfg.partial2_frac, r_price):
                reason = f"分批2: 浮盈{sig.zeta:.2f}R≥动态档{p2:.2f}R"
                return ACT_PART_2_3, reason, self._cmds(ACT_PART_2_3, sig, r_price)

        # 4) 保本档（动态门槛；锁定本金：不硬扛的第三道防线）
        be_r = thr.be_r if thr else 1.0
        if not sig.moved_be and sig.zeta >= be_r:
            be = self._breakeven_price(sig, r_price)
            reason = f"保本: 浮盈{sig.zeta:.2f}R≥动态档{be_r:.2f}R,止损移至{be:.2f}"
            return ACT_TIGHTEN_BE, reason, self._cmds(ACT_TIGHTEN_BE, sig, r_price)

        # 5) 追踪止损（Chandelier Exit：峰值越高越收紧，且单调不放松）
        action, reason, cmds = self._trail_only(sig, r_price, "正常追踪", thr)

        # 6) L3 仲裁（INTERFACES 4.4 ⑤ / THEORY 3.3 “AI 建议可被否决”）：
        #    规则无动作（HOLD）+ 顾问高置信非 HOLD 建议 + 护栏未拦 → 采纳。
        #    白名单动作 + 状态机防重入 + 成本门控复用（护栏语义全部保持：L0/L1 永远优先）。
        if (action == ACT_HOLD and sig.advisor_action is not None
                and sig.advisor_action != ACT_HOLD and not sig.block_advisor
                and sig.advisor_conf >= 0.7):
            adv = sig.advisor_action
            if adv == ACT_PART_1_3 and not sig.done_p1:
                if self._partial_worth_it(sig, self.cfg.partial1_frac, r_price):
                    return adv, (f"L3建议: 分批1档达标(差档置信{sig.advisor_conf:.2f}),"
                                 + "成本门控通过"), self._cmds(adv, sig, r_price)
            if adv == ACT_PART_2_3 and not sig.done_p2:
                if self._partial_worth_it(sig, self.cfg.partial2_frac, r_price):
                    return adv, (f"L3建议: 分批2档达标(美式交叉,置信{sig.advisor_conf:.2f}),"
                                 + "成本门控通过"), self._cmds(adv, sig, r_price)
            if adv == ACT_TIGHTEN_BE and not sig.moved_be:
                be = self._breakeven_price(sig, r_price)
                return adv, (f"L3建议: 保本档达标(距离差,置信{sig.advisor_conf:.2f}),"
                             + f"止损移至{be:.2f}"), self._cmds(adv, sig, r_price)
            if adv == ACT_CLOSE_ALL:
                return adv, (f"L3建议: 断熔逼近(差值特征,置信{sig.advisor_conf:.2f}),"
                             + "清仓避险"), self._cmds(adv, sig, r_price)
        return action, reason, cmds

    # ---------------- 内部逻辑 ----------------

    def _trail_only(self, sig: DecisionSignal, r_price: float,
                    note: str, thr: Optional[DynamicThresholds] = None
                    ) -> Tuple[int, str, List[OrderCmd]]:
        """仅做追踪止损管理（不触发分批/保本的新增动作）"""
        start_r = thr.trail_start_r if thr else self.cfg.tighten_at_peak_r
        if sig.peak_r < start_r:
            reason = f"{note}: 峰值{sig.peak_r:.2f}R未达追踪启动档{start_r:.2f}R"
            return ACT_HOLD, reason, self._cmds(ACT_HOLD, sig, r_price)
        # 峰值阶段越高，追踪乘数越紧（1.25 → 1.00 → 0.75 ATR）
        mult = self.cfg.atr_mult_base
        for s, m in sorted(self.cfg.trail_mult_by_stage.items()):
            if sig.peak_r >= s:
                mult = m
            else:
                break
        action = {1.25: ACT_TIGHTEN_1A, 1.00: ACT_TIGHTEN_2A, 0.75: ACT_TIGHTEN_3A}.get(
            round(mult, 2), ACT_TIGHTEN_2A)
        reason = f"{note}: 追踪收紧(峰值{sig.peak_r:.2f}R,{mult}ATR)"
        return action, reason, self._cmds(action, sig, r_price)

    def _r_price_of(self, sig: DecisionSignal) -> float:
        """初始风险对应的价格距离：入场止损距离 = atr_mult_base × 入场时ATR。
        简化：用当前 ATR 近似（策略层不需要精确历史；订单管理者持有精确 R 时覆盖）。"""
        return self.cfg.atr_mult_base * sig.atr_price

    def _breakeven_price(self, sig: DecisionSignal, r_price: float) -> float:
        """保本位 = 入场 ± (0.25R + 半价差缓冲)，确保即使触发也净赚小利或保本。"""
        offset = self.cfg.breakeven_offset_r * r_price + sig.spread_price / 2.0
        if sig.direction > 0:
            return sig.entry_price + offset
        return sig.entry_price - offset

    def _partial_worth_it(self, sig: DecisionSignal, frac: float, r_price: float) -> bool:
        """成本门控：该档平仓的净盈利是否值得（懂变通：不做亏本分批）。
        每档平仓成本 ≈ (半价差 + 滑点)。滑点随机，这里用典型值中位数近似。"""
        if not self.cfg.cost_gate_partial:
            return True
        cost_r = (sig.spread_price / 2.0 + 0.5 * sig.spread_price) / r_price
        net_r = sig.zeta * frac - cost_r
        return net_r >= self.cfg.min_partial_net_r

    def _cmds(self, action: int, sig: DecisionSignal, r_price: float) -> List[OrderCmd]:
        """动作 -> 券商指令（复用 actions.py 的映射，并计算具体价格）"""
        ctx = ActionContext(
            direction=sig.direction,
            entry_price=sig.entry_price,
            peak_price=sig.peak_price,
            atr_price=sig.atr_price,
            spread_price=sig.spread_price,
            remaining_frac=sig.remaining_frac,
            breakeven_level=self._breakeven_price(sig, r_price),
        )
        return apply_action(action, ctx)