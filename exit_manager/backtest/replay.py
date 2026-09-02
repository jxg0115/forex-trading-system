# -*- coding: utf-8 -*-
"""
回测层：tick 重放引擎 + 订单管理者（决策协议执行主体）+ RL 训练环境

对应 INTERFACES.md 第 6/7 章。核心设计：
    - 回测与实盘共用 收益K线生成器 + 估计器 + 门槛合成器 + 策略 + 护栏 + 成本模型；
    - 决策协议：逐 tick 护栏（介入快）+ 收益K线 commit 时算动态门槛并策略评估；
    - 执行器契约：SimExecutor（回测）/ MT5Executor（integration/mt5_bridge.py）双实现；
    - OrderManager 是实盘与回测共用的"大脑"，两边只换 Executor 与数据入口。

因果性保证：单次正向扫描 tick 流，任何决策只依赖 ts <= t 的数据
（配 tests/test_causality.py 验证）。
"""

import random as _random
from dataclasses import dataclass, field, replace
from typing import Callable, Dict, List, Optional

from framework.actions import (
    ACT_CLOSE_ALL, ACT_HOLD, ACT_PART_1_3, ACT_PART_1_2, ACT_PART_2_3,
    ACT_TIGHTEN_BE, ACTION_NAMES, ActionContext, OrderCmd, apply_action,
)
from framework.cost_model import (
    CostConfig, close_cost_usd, entry_cost_usd, price_to_usd, swap_usd,
)
from framework.estimators import EstimatorBundle
from framework.indicators import CausalIndicators, IndicatorSnapshot
from framework.order_state import OrderState
from framework.profit_kline import BarConfig, ProfitBar, ProfitKlineGenerator, Snapshot
from framework.state_reward import (
    RewardConfig, build_decision_features, build_state,
)
from strategy.exit_strategy import DecisionSignal, ExitConfig, ExitStrategy
from strategy.guards import GuardConfig, GuardSignal, ManualOverride, check_guards
from strategy.threshold_scheduler import DynamicThresholds, ThresholdScheduler


# ---------------- 数据结构 ----------------

@dataclass(frozen=True)
class Tick:
    ts: float          # epoch 秒
    mid: float         # 中间价（USD/oz）
    spread_pip: float  # 当前点差（pip）


@dataclass
class OrderResult:
    """每单结算档案（含峰值字段，供 Capture Ratio 审计）"""
    ticket: int
    direction: int
    entry_price: float
    exit_price: float
    entry_ts: float
    exit_ts: float
    exit_reason: str
    gross_usd: float
    cost_usd: float
    swap_usd: float
    net_usd: float
    r0_usd: float
    net_r: float
    holding_sec: float
    bars_total: int
    partials: int
    peak_r: float = 0.0      # 订单最高浮盈(R)
    peak_ts: float = 0.0     # 峰值出现时间
    capture_ratio: float = 0.0  # 净兑现(R)/峰值(R)，峰值>0才有意义；由 metrics 计算


@dataclass
class ReplayResult:
    name: str = "策略"
    orders: List[OrderResult] = field(default_factory=list)
    decision_log: List[dict] = field(default_factory=list)   # 审计日志


# ---------------- 指标聚合（tick -> 1分钟K线，供外部指标流） ----------------

class IndicatorAggregator:
    """把 tick 聚合成 1 分钟K线，收盘时喂给 CausalIndicators（因果）。"""

    def __init__(self, ind: CausalIndicators, bar_seconds: float = 60.0):
        self.ind = ind
        self.bar_seconds = bar_seconds
        self._cur_start: Optional[float] = None
        self._h = self._l = self._c = self._o = 0.0

    def on_tick(self, ts: float, mid: float):
        if self._cur_start is None:
            self._cur_start = ts
            self._o = self._h = self._l = self._c = mid
        else:
            self._c = mid
            self._h = max(self._h, mid)
            self._l = min(self._l, mid)
            if ts - self._cur_start >= self.bar_seconds:
                self.ind.on_bar_close(self._h, self._l, self._c, ts)
                self._cur_start = ts
                self._o = self._h = self._l = self._c = mid

    @property
    def ready(self) -> bool:
        return len(self.ind._closes) >= 2


# ---------------- 执行器契约（域 E） ----------------

class Executor:
    """执行器接口：回测（SimExecutor）与实盘（MT5Executor）同一契约。"""

    exchange: str = "unknown"    # sim | mt5：决定成交价来源与成本记账口径

    def open(self, ticket: int, direction: int, lots: float,
             entry_price: float, stop_price: float, ts: float) -> None:
        raise NotImplementedError

    def modify_stop(self, ticket: int, new_stop: float, ts: float,
                    reason: str = "") -> bool:
        raise NotImplementedError

    def close_volume(self, ticket: int, frac: float, ts: float,
                    reason: str = "") -> float:
        """平掉 frac 比例（相对原始手数），返回成交均价的 fill 价。"""
        raise NotImplementedError

    def close_all(self, ticket: int, ts: float, reason: str = "") -> float:
        raise NotImplementedError


class SimExecutor(Executor):
    """模拟执行器：内联成本模型（半价差+滑点+swap），与实盘同口径。"""

    exchange: str = "sim"

    def __init__(self, cost_cfg: CostConfig, rng=None, stop_fill_at_stop: bool = True):
        self.cost_cfg = cost_cfg
        self.rng = rng or _random.Random(0)
        self.stop_fill_at_stop = stop_fill_at_stop

    def _fill_price(self, direction: int, mid: float) -> float:
        """市价成交填价：多单卖在 bid（mid-半价差），空单买在 ask（mid+半价差），再扣滑点。"""
        slip = self.cost_cfg.slippage_price(self.rng)
        if direction > 0:
            return mid - self.cost_cfg.spread_price / 2.0 - slip
        return mid + self.cost_cfg.spread_price / 2.0 + slip

    # Sim 模式成交在 OrderManager 内建模（自算 fill 与成本）；以下仅占位保证契约完整。
    def open(self, ticket: int, direction: int, lots: float,
             entry_price: float, stop_price: float, ts: float) -> None:
        pass

    def modify_stop(self, ticket: int, new_stop: float, ts: float,
                    reason: str = "") -> bool:
        return True

    def close_volume(self, ticket: int, frac: float, ts: float,
                    reason: str = "") -> float:
        raise NotImplementedError("Sim 模式分批成交在 OrderManager 内建模，不调用 executor")

    def close_all(self, ticket: int, ts: float, reason: str = "") -> float:
        raise NotImplementedError("Sim 模式清仓成交在 OrderManager 内建模，不调用 executor")


# ---------------- 订单管理器（决策协议执行主体，实盘/回测共用） ----------------

@dataclass
class OrderBundle:
    """一笔被监控订单的完整上下文（多仓隔离核心）。
    每笔托管单持有独立的：收益K线生成器(gen) + 估计器(estimators) + 动态门槛(thr)
    + 护栏档位快照(guard)，互不串扰 —— 收益K线按订单各自生成（用户需求）。
    """
    order: OrderState
    gen: ProfitKlineGenerator
    estimators: EstimatorBundle
    thr: Optional[DynamicThresholds] = None
    guard: Optional[GuardConfig] = None     # 逐单护栏档位快照（含 mae/hold 动态值）
    last_bar_n: int = 0                     # 服务层已推送 bar 数（BAR_COMMIT 用）


class OrderManager:
    """订单生命周期管理者：逐 tick 驱动 生成器/估计器/门槛/护栏/策略，并落地指令。

    决策协议（INTERFACES.md 6.2）：
        1. 逐 tick：浮盈/峰值/swap 更新 -> 收益K线快照 -> 护栏检查（介入快）；
        2. 收益K线 commit：估计器更新 -> 动态门槛合成 -> 策略评估 -> 指令执行；
        3. 止损触发：bid/ask 穿越 stop 即以止损价成交（含滑点）；
        4. 人工介入（set_manual_override）在护栏层无条件优先，单次有效。
    """

    def __init__(self,
                 strategy: ExitStrategy,
                 bar_cfg: BarConfig,
                 cost_cfg: CostConfig,
                 guard_cfg: GuardConfig,
                 reward_cfg: Optional[RewardConfig] = None,
                 estimators: Optional[EstimatorBundle] = None,   # 兼容参数（多仓下每单独立，不再共享）
                 scheduler: Optional[ThresholdScheduler] = None,
                 executor: Optional[Executor] = None,
                 typical_spread_pip: float = 3.0,
                 rng=None,
                 indicators: Optional[CausalIndicators] = None,
                 advisor: Optional["RLAdvisor"] = None,   # L3 顾问（差值特征输入；None=纯规则）
                 on_decision: Optional[Callable[[dict], None]] = None,
                 on_close: Optional[Callable[[OrderResult], None]] = None):
        self.strategy = strategy
        self.bar_cfg = bar_cfg
        self.cost_cfg = cost_cfg
        self.guard_cfg = guard_cfg
        self.reward_cfg = reward_cfg or RewardConfig()
        self.scheduler = scheduler or ThresholdScheduler()
        self.executor = executor or SimExecutor(cost_cfg, rng)
        self.typical_spread_pip = typical_spread_pip
        self.rng = rng or _random.Random(0)
        self.indicators = indicators
        self.advisor = advisor          # L3 顾问接线（None → 决策管线保持纯规则）
        self.on_decision = on_decision
        self.on_close = on_close
        self._blocks: Dict[int, OrderBundle] = {}      # 多仓表：ticket -> 该单完整上下文
        self._main_ticket: Optional[int] = None        # 主单（最近托管的一笔；兼容单例读取）
        self.overrides: Dict[int, ManualOverride] = {} # 人工介入（L0）按单隔离，单次有效
        self._ticket = 0

    # ---------------- 对外接口 ----------------

    @property
    def in_position(self) -> bool:
        """有任一托管单即视为持仓（多仓：多笔同时托管）。"""
        return len(self._blocks) > 0

    @property
    def order(self) -> Optional[OrderState]:
        """主单（最近托管一笔）订单状态；无托管则 None（兼容旧单仓消费者）。"""
        b = self.block(self._main_ticket)
        return b.order if b is not None else None

    @property
    def gen(self) -> Optional[ProfitKlineGenerator]:
        """主单收益K线生成器（兼容旧消费者；engine_service 已改逐单访问 b.gen）。"""
        b = self.block(self._main_ticket)
        return b.gen if b is not None else None

    @property
    def _thr(self) -> Optional[DynamicThresholds]:
        """主单动态门槛（兼容旧消费者）。"""
        b = self.block(self._main_ticket)
        return b.thr if b is not None else None

    def blocks(self) -> List[OrderBundle]:
        """全部托管单 bundle（多仓遍历用）。"""
        return list(self._blocks.values())

    def block(self, ticket: Optional[int]) -> Optional[OrderBundle]:
        """按券商 ticket 取单；ticket=None 时取主单。"""
        if ticket is None:
            ticket = self._main_ticket
        return self._blocks.get(ticket)

    def set_manual_override(self, action: int, reason: str,
                            ticket: Optional[int] = None) -> None:
        """人工介入（L0）按单注入；ticket=None 时作用于主单（最近托管的一笔）。
        单次有效：该单决策执行后自动清除。"""
        tk = ticket if ticket is not None else self._main_ticket
        if tk is None or tk not in self._blocks:
            return
        self.overrides[tk] = ManualOverride(action=action, reason=reason)

    def open_order(self, t: Tick, direction: int, lots: float = 1.0) -> None:
        """开仓（sim 演示用途）：固定 R 锚（初始止损距离 = k_base×ATR，入场锁定）。
        每单独立建块（独立收益K线/估计器/门槛/护栏），与其他托管单互不串扰。"""
        self._ticket += 1
        ticket = self._ticket
        ind_snap = self._ind_snapshot(t, pin_levels=True)
        atr = ind_snap.atr if ind_snap.atr > 0 else t.mid * 0.001
        r_price = self.strategy.cfg.atr_mult_base * atr        # R 锚（价格距离）
        r_price = max(r_price, t.mid * 0.0005)                 # 兜底最小波动
        r0_usd = price_to_usd(r_price, lots)

        stop = t.mid - r_price if direction > 0 else t.mid + r_price
        o = OrderState(
            ticket=ticket, direction=direction,
            entry_price=t.mid, entry_ts=t.ts, lots0=lots, lots_cur=lots,
            r_price=r_price, r0_usd=r0_usd, stop_price=stop,
        )
        o.cost_usd += entry_cost_usd(self.cost_cfg, lots)   # 入场成本：半个点差
        self._blocks[ticket] = OrderBundle(
            order=o, gen=ProfitKlineGenerator(self.bar_cfg),
            estimators=EstimatorBundle())
        self._main_ticket = ticket
        self.executor.open(ticket, direction, lots, t.mid, stop, t.ts)
        self._notify({"event": "OPEN", "ts": t.ts, "dir": direction,
                      "entry": round(t.mid, 3), "r_price": round(r_price, 3),
                      "stop": round(stop, 2), "ticket": ticket})

    def adopt(self, t: Tick, direction: int, entry_price: float, lots0: float,
              stop_price: Optional[float], mt_ticket: int) -> None:
        """接管券商端持仓（实盘用途：引擎不开仓，只管理外部已开的仓）。
        与 open_order 同初始化，但入场价/手数/R 锚以券商持仓为准：
            - 若外部已带 SL，则 R 锚 = |entry - stop|（以用户设定为准）；
            - 若无 SL，按策略 k_base×ATR 兜底设初始止损。
        多仓：每笔外部仓独立建块（独立收益K线/估计器/门槛/护栏）——全部接管，
        不再"只托管其一"；本单收益K线独立生成。
        本地不再计入场成本（成本已在券商侧发生），结算以券商为准。"""
        ind_snap = self._ind_snapshot(t, pin_levels=True)
        atr = ind_snap.atr if ind_snap.atr > 0 else entry_price * 0.001
        if stop_price is not None:
            r_price = abs(entry_price - stop_price)
        else:
            r_price = self.strategy.cfg.atr_base * atr
            r_price = max(r_price, entry_price * 0.0005)
            stop_price = entry_price - r_price if direction > 0 else entry_price + r_price
        r0_usd = price_to_usd(r_price, lots0)
        o = OrderState(
            ticket=mt_ticket, direction=direction,
            entry_price=entry_price, entry_ts=t.ts, lots0=lots0, lots_cur=lots0,
            r_price=r_price, r0_usd=r0_usd, stop_price=stop_price,
        )
        self._blocks[mt_ticket] = OrderBundle(
            order=o, gen=ProfitKlineGenerator(self.bar_cfg),
            estimators=EstimatorBundle())
        self._main_ticket = mt_ticket
        self._notify({"event": "OPEN", "ts": t.ts, "dir": direction,
                      "entry": round(entry_price, 3), "r_price": round(r_price, 3),
                      "stop": round(stop_price, 2), "source": "external",
                      "ticket": mt_ticket})

    def on_tick(self, t: Tick) -> None:
        if not self._blocks:
            return
        ext = self._ind_snapshot(t, pin_levels=False)

        # 每笔被监控订单独立驱动（多仓：各单独立收益K线/估计器/门槛/护栏）
        for ticket, b in list(self._blocks.items()):
            if b.order.lots_cur <= 0:
                continue
            self._drive_order(b, t, ext)

    def _drive_order(self, b: OrderBundle, t: Tick, ext: IndicatorSnapshot) -> None:
        """单笔订单的逐 tick 决策驱动（收益K线按单独立生成/独立门槛/独立护栏）。"""
        o, gen, est = b.order, b.gen, b.estimators

        # 2) 浮盈与峰值（方向感知）
        zeta = o.zeta_at(t.mid)
        if zeta > o.peak_r:
            o.peak_r = zeta
            o.peak_ts = t.ts

        # 3) swap 累积（只对当前剩余仓位计息）
        if o.last_ts is not None:
            o.swap_usd += swap_usd(self.cost_cfg, o.direction, o.lots_cur,
                                   t.ts - o.last_ts)
        o.last_ts = t.ts

        # 4) 收益K线快照（永远先喂，保证 bar 连续性与因果）
        committed: Optional[ProfitBar] = None
        if gen is not None:
            committed = gen.on_snapshot(Snapshot(
                ts=t.ts, price=t.mid, floating_r=zeta,
                remaining_frac=o.remaining_frac(), direction=o.direction))

        # 5) commit -> 估计器更新 + 动态门槛合成 + 护栏档位刷新（该单上下文）
        if committed is not None and o.lots_cur > 0:
            est.on_bar(committed)
            self._refresh_dd_avg(b)
            est_snap = est.snapshot(t.ts, ext)
            b.thr = self.scheduler.compute(
                est_snap, o, ts=t.ts,
                budget_bars=self.guard_cfg.max_hold_bars, budget_sec=60.0)
            # 动态档位注入该单护栏快照与生成器（只影响后续，不回写历史；多仓不互踩）
            b.guard = replace(self.guard_cfg,
                              max_adverse_r=b.thr.mae_r,
                              max_hold_bars=b.thr.max_hold_bars)
            gen.cfg.threshold_r = b.thr.bar_threshold_r

        # 6) 护栏（逐 tick，介入快）
        gsig = GuardSignal(
            zeta=zeta,
            holding_sec=t.ts - o.entry_ts,
            bars_total=gen.bar_count if gen else 0,
            mae_fired=o.mae_fired,
            override=self.overrides.get(o.ticket),
            spread_pip=ext.spread_pip,
            spread_pip_avg=ext.spread_pip_avg,
        )
        gres = check_guards(gsig, b.guard or self.guard_cfg)
        if gres.forced:
            self._apply_action(gres.action, gres.reason, zeta, t, b)
            self.overrides.pop(o.ticket, None)
            return

        # 7) commit -> 策略评估（事件驱动决策）
        anomaly = gres.block_partial or gres.block_advisor
        if committed is not None and o.lots_cur > 0 and not anomaly:
            sig = self._decision_signal(zeta, ext, anomaly, b)
            # ---- L3 接线（THEORY 3.3 / INTERFACES 4.4）：差值特征 → 顾问建议 → 入 sig ----
            adv_action, adv_conf = None, 0.0
            if (self.advisor is not None and not gres.block_advisor
                    and b.thr is not None):
                feats = build_decision_features(
                    zeta, b.thr, o.peak_r, t.ts - o.entry_ts,
                    o.remaining_frac(),
                    bars_total=gen.bar_count if gen else 0,
                    budget_bars=(b.guard.max_hold_bars if b.guard
                                 else self.guard_cfg.max_hold_bars),
                    bar_sec=60.0)
                adv_action, adv_conf = self.advisor.suggest(feats)
                sig.advisor_action = adv_action
                sig.advisor_conf = adv_conf
            action, reason, cmds = self.strategy.decide(sig, b.thr)
            self._notify({"event": "DECIDE", "ts": t.ts, "ticket": o.ticket,
                          "action": ACTION_NAMES.get(action, action), "reason": reason,
                          "zeta": round(zeta, 2), "peak_r": round(o.peak_r, 2),
                          "stop": round(o.stop_price or 0, 2),
                          "bars": gen.bar_count if gen else 0,
                          "thr": b.thr.to_audit_line() if b.thr else "",
                          "adv": (ACTION_NAMES.get(adv_action, "HOLD") if adv_action
                                  else "HOLD"),
                          "adv_conf": round(adv_conf, 2)})
            self._apply_cmds(cmds, zeta, t, b)

        # 8) 止损触发检查（券商端止损兜底，含滑点；多头看 bid，空头看 ask）
        if self._stop_hit(t, b):
            self._close_remaining("止损触发", t, True, b)

    # ---------------- 内部 ----------------

    def _ind_snapshot(self, t: Tick, pin_levels: bool) -> IndicatorSnapshot:
        if self.indicators is None:
            from framework.indicators import IndicatorSnapshot as IS
            return IS(atr=0.0, spread_pip=t.spread_pip, spread_pip_avg=t.spread_pip)
        self.indicators.on_spread(t.spread_pip)
        return self.indicators.snapshot(t.mid, t.spread_pip, pin_levels=pin_levels)

    def _refresh_dd_avg(self, b: OrderBundle, n_bars: int = 8) -> None:
        bars = b.gen.bars_back(n_bars) if b.gen else []
        if bars:
            b.order.dd_avg = sum(x.dd_rate for x in bars) / len(bars)
        b.order.hold_frac = (b.gen.bar_count
                             / max((b.guard.max_hold_bars if b.guard
                                    else self.guard_cfg.max_hold_bars), 1))

    def _decision_signal(self, zeta: float, ext: IndicatorSnapshot,
                         anomaly: bool, b: OrderBundle) -> DecisionSignal:
        o = b.order
        spread_price = self.cost_cfg.spread_price * (ext.spread_pip / max(self.typical_spread_pip, 1e-9))
        moved_be = o.moved_be
        if o.stop_price is not None:
            moved_be = (o.stop_price >= o.entry_price) if o.direction > 0 \
                else (o.stop_price <= o.entry_price)
        return DecisionSignal(
            direction=o.direction, entry_price=o.entry_price,
            zeta=zeta, peak_r=o.peak_r, peak_price=o.peak_price(),
            atr_price=ext.atr if ext.atr > 0 else o.r_price / self.strategy.cfg.atr_mult_base,
            regime=ext.regime,
            spread_price=spread_price,
            remaining_frac=o.remaining_frac(),
            holding_sec=t_last_sec(o),
            bars_total=b.gen.bar_count if b.gen else 0,
            moved_be=moved_be,
            done_p1=o.done_p1, done_p2=o.done_p2,
            partial_count=o.partial_count, mae_fired=o.mae_fired,
            override=self.overrides.get(o.ticket),
            spread_anomaly=anomaly, block_advisor=anomaly,
        )

    def _action_ctx(self, b: OrderBundle) -> ActionContext:
        o = b.order
        return ActionContext(
            direction=o.direction, entry_price=o.entry_price, peak_price=o.peak_price(),
            atr_price=o.r_price / self.strategy.cfg.atr_mult_base,
            spread_price=self.cost_cfg.spread_price, remaining_frac=o.remaining_frac(),
            breakeven_level=self._breakeven_price(b),
        )

    def _breakeven_price(self, b: OrderBundle) -> float:
        o = b.order
        offset = (self.strategy.cfg.breakeven_offset_r * o.r_price +
                  self.cost_cfg.spread_price / 2.0)
        return o.entry_price + offset if o.direction > 0 else o.entry_price - offset

    def _apply_action(self, action: int, reason: str, zeta: float, t: Tick,
                      b: OrderBundle) -> None:
        cmds = apply_action(action, self._action_ctx(b), reason=reason)
        self._notify({"event": "GUARD", "ts": t.ts, "ticket": b.order.ticket,
                      "action": ACTION_NAMES.get(action, action), "reason": reason,
                      "zeta": round(zeta, 2),
                      "bars": b.gen.bar_count if b.gen else 0})
        self._apply_cmds(cmds, zeta, t, b)

    def _apply_cmds(self, cmds: List[OrderCmd], zeta: float, t: Tick,
                    b: OrderBundle) -> None:
        o = b.order
        for c in cmds:
            if o.lots_cur <= 0:
                return
            if c.kind == "modify_stop" and c.stop_price is not None:
                self._modify_stop(c.stop_price, c.reason, b)
            elif c.kind == "close_volume" and c.volume_frac > 0:
                self._partial(c.volume_frac, c.reason, c.action_id, t, b)
            elif c.kind == "close_all":
                self._close_remaining(c.reason, t, False, b)

    def _modify_stop(self, new_stop: float, reason: str, b: OrderBundle) -> None:
        """止损单调收紧（默认绝不放松）：多头只看更高、空头只看更低。"""
        o = b.order
        if o is None:
            return
        better = (o.direction > 0 and (o.stop_price is None or new_stop > o.stop_price)) or \
                 (o.direction < 0 and (o.stop_price is None or new_stop < o.stop_price))
        if better or self.strategy.cfg.allow_loosen:
            if self.executor.modify_stop(o.ticket, new_stop, o.last_ts or 0.0, reason):
                o.stop_price = new_stop
                self._notify({"event": "STOP", "ts": o.last_ts, "ticket": o.ticket,
                              "stop": round(new_stop, 2), "reason": reason})
            else:
                self._notify({"event": "STOP_FAILED", "ts": o.last_ts,
                              "ticket": o.ticket, "stop": round(new_stop, 2),
                              "reason": reason})

    def _partial(self, frac_of_original: float, reason: str, action_id: int,
                 t: Tick, b: OrderBundle) -> None:
        """分批止盈：按原始手数比例平仓，每次独立付一次点差+滑点。"""
        o = b.order
        seg = min(o.lots0 * frac_of_original, o.lots_cur)
        if seg <= 0:
            return
        if self.executor.exchange == "mt5":
            fill = self.executor.close_volume(o.ticket, seg / o.lots_cur, t.ts, reason)
            if fill <= 0:
                self._notify({"event": "PARTIAL_FAILED", "ts": t.ts,
                              "ticket": o.ticket,
                              "frac": round(frac_of_original, 2), "reason": reason})
                return
            # 实盘：成交价为券商实际 deal 价（已含点差/佣金），本地不再重复计成本
            cost_extra = 0.0
        else:
            slip = self.cost_cfg.slippage_price(self.rng)
            if o.direction > 0:
                fill = t.mid - self.cost_cfg.spread_price / 2.0 - slip
            else:
                fill = t.mid + self.cost_cfg.spread_price / 2.0 + slip
            cost_extra = close_cost_usd(self.cost_cfg, seg, self.rng)
        gross = price_to_usd((fill - o.entry_price) * o.direction, seg)
        o.gross_usd += gross
        o.cost_usd += cost_extra
        o.lots_cur -= seg
        o.partial_count += 1
        self._notify({"event": "PARTIAL", "ts": t.ts, "ticket": o.ticket,
                      "frac": round(frac_of_original, 2), "fill": round(fill, 2),
                      "gross_usd": round(gross, 2), "reason": reason})
        if o.lots_cur <= 0:
            self._finalize(reason, t.ts, o.zeta_at(t.mid), b)

    def _stop_hit(self, t: Tick, b: OrderBundle) -> bool:
        o = b.order
        if o is None or o.stop_price is None or o.lots_cur <= 0:
            return False
        if o.direction > 0:
            return t.mid - self.cost_cfg.spread_price / 2.0 <= o.stop_price
        return t.mid + self.cost_cfg.spread_price / 2.0 >= o.stop_price

    def _close_remaining(self, reason: str, t: Tick, fill_at: bool,
                         b: OrderBundle) -> None:
        """清仓剩余仓位。fill_at=True 时按止损价成交（停损触发语义）。"""
        o = b.order
        if o is None or o.lots_cur <= 0:
            return
        seg = o.lots_cur
        if self.executor.exchange == "mt5":
            fill = self.executor.close_all(o.ticket, t.ts, reason)
            if fill <= 0:
                # 券商已无仓位（可能已按 SL 成交）：本地按止损价估算结算（实盘账目以券商为准）
                fill = o.stop_price if o.stop_price is not None else t.mid
            cost_extra = 0.0
        else:
            slip = self.cost_cfg.slippage_price(self.rng)
            if fill_at and o.stop_price is not None:
                fill = o.stop_price - slip if o.direction > 0 else o.stop_price + slip
            else:
                if o.direction > 0:
                    fill = t.mid - self.cost_cfg.spread_price / 2.0 - slip
                else:
                    fill = t.mid + self.cost_cfg.spread_price / 2.0 + slip
            cost_extra = close_cost_usd(self.cost_cfg, seg, self.rng)
        gross = price_to_usd((fill - o.entry_price) * o.direction, seg)
        o.gross_usd += gross
        o.cost_usd += cost_extra
        o.lots_cur = 0.0
        self._finalize(reason, t.ts, o.zeta_at(t.mid), b)

    def _finalize(self, reason: str, exit_ts: float, zeta_at: float,
                  b: OrderBundle) -> None:
        o = b.order
        net_usd = o.gross_usd - o.cost_usd + o.swap_usd
        res = OrderResult(
            ticket=o.ticket, direction=o.direction,
            entry_price=o.entry_price, exit_price=zeta2price(o, zeta_at),
            entry_ts=o.entry_ts, exit_ts=exit_ts, exit_reason=reason,
            gross_usd=o.gross_usd, cost_usd=o.cost_usd, swap_usd=o.swap_usd,
            net_usd=net_usd, r0_usd=o.r0_usd,
            net_r=net_usd / o.r0_usd if o.r0_usd else 0.0,
            holding_sec=exit_ts - o.entry_ts,
            bars_total=b.gen.bar_count if b.gen else 0,
            partials=o.partial_count,
            peak_r=o.peak_r, peak_ts=o.peak_ts,
        )
        if res.peak_r > 0:
            res.capture_ratio = min(max(res.net_r / res.peak_r, 0.0), 1.0)
        # 该单上下文销毁（收益K线/估计器/门槛/护栏 随单释放）——多仓互不影响
        self.overrides.pop(o.ticket, None)
        del self._blocks[o.ticket]
        if self._main_ticket == o.ticket:
            self._main_ticket = min(self._blocks) if self._blocks else None
        self._notify({"event": "CLOSE", "ts": exit_ts, "ticket": o.ticket,
                      "reason": reason, "net_r": round(res.net_r, 2),
                      "peak_r": round(res.peak_r, 2)})
        if self.on_close is not None:
            self.on_close(res)

    def _notify(self, ev: dict) -> None:
        if self.on_decision is not None:
            self.on_decision(ev)


def t_last_sec(o: OrderState) -> float:
    return (o.last_ts - o.entry_ts) if o.last_ts else 0.0


def zeta2price(o: OrderState, zeta: float) -> float:
    return o.entry_price + zeta * o.r_price * o.direction


# ---------------- 入场信号（演示用：唐奇安式突破 + 冷却） ----------------

@dataclass
class EntryRuleConfig:
    window: int = 40          # 突破窗口（tick 数）
    cooldown: int = 200       # 平仓后冷却 tick 数（防频繁交易）
    min_ready_bars: int = 2   # 至少多少根 1 分钟K线才允许开仓（指标就绪）


class BreakoutEntry:
    """演示入场规则：mid 突破近 window 根 tick 的最高点开多，跌破最低点开空。
    生产用你自己的入场模型（三屏障标签 + meta-model），本规则只服务演示与基线对比。"""

    def __init__(self, cfg: Optional[EntryRuleConfig] = None):
        self.cfg = cfg or EntryRuleConfig()
        self.buf: List[float] = []
        self._cooldown_left = 0

    def wants(self, mid: float) -> Optional[int]:
        if self._cooldown_left > 0:
            self._cooldown_left -= 1
            return None
        self.buf.append(mid)
        if len(self.buf) > self.cfg.window:
            self.buf.pop(0)
        if len(self.buf) < self.cfg.window // 2:
            return None
        hi = max(self.buf[:-1])
        lo = min(self.buf[:-1])
        if mid > hi and mid - hi > 1e-4:
            self._cooldown_left = self.cfg.cooldown
            return 1
        if mid < lo and lo - mid > 1e-4:
            self._cooldown_left = self.cfg.cooldown
            return -1
        return None


# ---------------- 回测重放器 ----------------

def run_replay(ticks: List[Tick],
               strategy: ExitStrategy,
               cost_cfg: CostConfig,
               bar_cfg: BarConfig,
               guard_cfg: GuardConfig,
               estimators: Optional[EstimatorBundle] = None,
               scheduler: Optional[ThresholdScheduler] = None,
               executor: Optional[Executor] = None,
               entry_rule: Optional[BreakoutEntry] = None,
               advisor: Optional["RLAdvisor"] = None,   # L3 顾问（None=纯规则基线；A/B 对比时传 RuleAdvisor）
               lots: float = 1.0,
               seed: int = 0,
               max_orders: int = 100,
               inject_override: Optional[Callable[[int, OrderState], Optional[ManualOverride]]] = None,
               name: str = "策略") -> ReplayResult:
    """整段 tick 重放：入场信号 -> OrderManager 逐 tick 驱动。

    inject_override：可选回调 (tick序号, 当前订单) -> ManualOverride|None，
    用于演示"随时介入"（L0）。
    """
    rng = _random.Random(seed)
    indicators = CausalIndicators()
    agg = IndicatorAggregator(indicators)
    mgr = OrderManager(strategy=strategy, bar_cfg=bar_cfg, cost_cfg=cost_cfg,
                       guard_cfg=guard_cfg, rng=rng, indicators=indicators,
                       typical_spread_pip=cost_cfg.spread_pip,
                       estimators=estimators, scheduler=scheduler,
                       executor=executor, advisor=advisor)
    result = ReplayResult(name=name)
    mgr.on_close = result.orders.append
    mgr.on_decision = result.decision_log.append
    entry = entry_rule or BreakoutEntry()
    orders = 0

    for i, t in enumerate(ticks):
        agg.on_tick(t.ts, t.mid)
        if not mgr.in_position:
            if orders >= max_orders:
                continue
            if len(indicators._closes) < BreakoutEntry().cfg.min_ready_bars:
                continue
            d = entry.wants(t.mid)
            if d is not None:
                mgr.open_order(t, direction=d, lots=lots)
                orders += 1
        else:
            if inject_override is not None:
                ov = inject_override(i, mgr.order)
                if ov is not None:
                    mgr.set_manual_override(ov.action, ov.reason)
            mgr.on_tick(t)

    if mgr.in_position:  # 尾巴：仍未平仓按强制结算
        mgr.set_manual_override(ACT_CLOSE_ALL, "回测结束强制平仓")
        mgr.on_tick(Tick(ts=ticks[-1].ts + 1, mid=ticks[-1].mid,
                         spread_pip=ticks[-1].spread_pip))
    return result


# ---------------- 基线对比：固定 SL/TP（无分批、无动态追踪） ----------------

def run_naive_baseline(ticks: List[Tick],
                       cost_cfg: CostConfig,
                       sl_r: float = 1.5,
                       tp_r: float = 3.0,
                       max_hold_sec: float = 8 * 3600.0,
                       lots: float = 1.0,
                       seed: int = 0,
                       entry_rule: Optional[BreakoutEntry] = None,
                       max_orders: int = 100) -> ReplayResult:
    """朴素基线：同一入场信号 + 固定止损止盈（三屏障固定屏障版）。"""
    rng = _random.Random(seed)
    indicators = CausalIndicators()
    agg = IndicatorAggregator(indicators)
    entry = entry_rule or BreakoutEntry()
    result = ReplayResult(name="朴素固定SL/TP基线")
    order = None
    orders = 0
    min_ready = BreakoutEntry().cfg.min_ready_bars

    for t in ticks:
        agg.on_tick(t.ts, t.mid)
        if order is None:
            if orders >= max_orders:
                continue
            if len(indicators._closes) < min_ready:
                continue
            d = entry.wants(t.mid)
            if d is not None:
                atr = indicators._atr if indicators._atr > 0 else t.mid * 0.001
                r_price = 2.5 * atr
                order = {"dir": d, "entry": t.mid, "ts": t.ts, "lots": lots,
                         "r_price": r_price, "r0": price_to_usd(r_price, lots),
                         "last": t.ts, "gross": 0.0,
                         "cost": entry_cost_usd(cost_cfg, lots), "swap": 0.0,
                         "peak_r": 0.0}
                orders += 1
        else:
            o = order
            o["swap"] += swap_usd(cost_cfg, o["dir"], o["lots"], t.ts - o["last"])
            o["last"] = t.ts
            zeta = ((t.mid - o["entry"]) * o["dir"]) / o["r_price"]
            if zeta > o["peak_r"]:
                o["peak_r"] = zeta
            hit = None
            if o["dir"] > 0:
                bid = t.mid - cost_cfg.spread_price / 2.0
                if bid <= o["entry"] - sl_r * o["r_price"]:
                    hit = ("止损", o["entry"] - sl_r * o["r_price"], True)
                elif t.mid + cost_cfg.spread_price / 2.0 >= o["entry"] + tp_r * o["r_price"]:
                    hit = ("止盈", o["entry"] + tp_r * o["r_price"], True)
            else:
                ask = t.mid + cost_cfg.spread_price / 2.0
                if ask >= o["entry"] + sl_r * o["r_price"]:
                    hit = ("止损", o["entry"] + sl_r * o["r_price"], True)
                elif t.mid - cost_cfg.spread_price / 2.0 <= o["entry"] - tp_r * o["r_price"]:
                    hit = ("止盈", o["entry"] - tp_r * o["r_price"], True)
            if t.ts - o["ts"] >= max_hold_sec:
                hit = ("时间止损", t.mid, False)
            if hit:
                reason, ref, at_stop = hit
                slip = cost_cfg.slippage_price(rng)
                if at_stop:
                    fill = ref - slip if o["dir"] > 0 else ref + slip
                else:
                    fill = (t.mid - cost_cfg.spread_price / 2.0 - slip
                            if o["dir"] > 0 else t.mid + cost_cfg.spread_price / 2.0 + slip)
                gross = price_to_usd((fill - o["entry"]) * o["dir"], o["lots"])
                o["gross"] += gross
                o["cost"] += close_cost_usd(cost_cfg, o["lots"], rng)
                net = o["gross"] - o["cost"] + o["swap"]
                result.orders.append(OrderResult(
                    ticket=orders, direction=o["dir"], entry_price=o["entry"],
                    exit_price=fill, entry_ts=o["ts"], exit_ts=t.ts,
                    exit_reason=reason, gross_usd=o["gross"], cost_usd=o["cost"],
                    swap_usd=o["swap"], net_usd=net, r0_usd=o["r0"],
                    net_r=net / o["r0"] if o["r0"] else 0.0,
                    holding_sec=t.ts - o["ts"], bars_total=0, partials=0,
                    peak_r=o["peak_r"]))
                order = None
    return result


# ---------------- RL 训练环境（对接 stable-baselines3 PPO） ----------------

class RLEnv:
    """订单生命周期 MDP 环境：一单 = 一局 episode。

    观察: build_state 的 STATE_DIMS 维（因果归一化）
    动作: 0..8（framework.actions 的 9 档网格）
    奖励: 决策间隙 potential shaping（Ng et al. 1999）+ 终局结算 terminal_reward
    终止: 清仓（护栏/止损/策略/时间）即 episode 结束
    """

    def __init__(self, ticks: List[Tick],
                 bar_cfg: BarConfig = BarConfig(),
                 cost_cfg: CostConfig = CostConfig(),
                 guard_cfg: GuardConfig = GuardConfig(),
                 exit_cfg: Optional[ExitConfig] = None,
                 reward_cfg: Optional[RewardConfig] = None,
                 seed: int = 0,
                 entry_rule: Optional[BreakoutEntry] = None):
        self.ticks = ticks
        self.bar_cfg = bar_cfg
        self.cost_cfg = cost_cfg
        self.guard_cfg = guard_cfg
        self.exit_cfg = exit_cfg or ExitConfig()
        self.reward_cfg = reward_cfg or RewardConfig()
        self.rng = _random.Random(seed)
        self.entry_rule = entry_rule or BreakoutEntry()
        self._i = 0
        self._ind = CausalIndicators()
        self._agg = IndicatorAggregator(self._ind)
        self._strategy = ExitStrategy(self.exit_cfg, self.guard_cfg)
        self._mgr: Optional[OrderManager] = None
        self._last_result: Optional[OrderResult] = None
        self._prev_clamp = 0.0

    # gym 风格接口
    def reset(self) -> List[float]:
        self._i = 0
        self._ind = CausalIndicators()
        self._agg = IndicatorAggregator(self._ind)
        self._mgr = None
        self._last_result = None
        self._prev_clamp = 0.0
        n = len(self.ticks)
        while self._i < n:
            t = self.ticks[self._i]
            self._i += 1
            self._agg.on_tick(t.ts, t.mid)
            if len(self._ind._closes) < BreakoutEntry().cfg.min_ready_bars:
                continue
            d = self.entry_rule.wants(t.mid)
            if d is not None:
                self._mgr = OrderManager(
                    strategy=self._strategy, bar_cfg=self.bar_cfg,
                    cost_cfg=self.cost_cfg, guard_cfg=self.guard_cfg,
                    reward_cfg=self.reward_cfg, rng=self.rng,
                    indicators=self._ind,
                    typical_spread_pip=self.cost_cfg.spread_pip)
                self._mgr.on_close = self._on_close
                self._mgr.open_order(t, d, lots=1.0)
                return self._state(t)
        return [0.0] * _state_dims()

    def step(self, action: int) -> tuple:
        n = len(self.ticks)
        done = False
        reward = 0.0
        info = {}
        if action != ACT_HOLD and self._mgr is not None:
            self._mgr.set_manual_override(action, f"RL_action_{action}")
        while self._i < n and self._mgr is not None and self._mgr.in_position:
            t = self.ticks[self._i]
            self._i += 1
            self._agg.on_tick(t.ts, t.mid)
            o = self._mgr.order
            before_zeta = o.zeta_at(t.mid) if o else 0.0
            self._mgr.on_tick(t)
            if not self._mgr.in_position:
                done = True
                if self._last_result is not None:
                    reward += self._last_result.net_r   # 终局结算（R 口径）
                break
            after_zeta = self._mgr.order.zeta_at(t.mid)
            reward += self._shaped(before_zeta, after_zeta)
        state = self._state(self.ticks[min(self._i - 1, n - 1)]) \
            if self._mgr is not None else [0.0] * _state_dims()
        return state, reward, done, info

    def _on_close(self, res: OrderResult) -> None:
        self._last_result = res

    def _state(self, t: Tick) -> List[float]:
        if self._mgr is None or self._mgr.order is None:
            return [0.0] * _state_dims()
        g = self._mgr.gen
        o = self._mgr.order
        zeta = o.zeta_at(t.mid)
        ext = self._ind.snapshot(t.mid, t.spread_pip, pin_levels=False)
        return build_state(g, zeta, o.peak_r, t.ts - o.entry_ts,
                           o.remaining_frac(), ext)

    def _shaped(self, z_old: float, z_new: float) -> float:
        from framework.state_reward import clamp_zeta, shaped_reward
        dz = z_new - z_old
        r = shaped_reward(dz, z_old, z_new, self.reward_cfg)
        self._prev_clamp = clamp_zeta(z_new, self.reward_cfg)
        return r

    def seed(self, s: int) -> None:
        self.rng = _random.Random(s)


def _state_dims() -> int:
    from framework.state_reward import STATE_DIMS
    return STATE_DIMS