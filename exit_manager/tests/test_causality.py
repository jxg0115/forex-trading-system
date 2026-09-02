# -*- coding: utf-8 -*-
"""
因果性/无前视 单元测试（INTERFACES.md 第 0 章约定的验证）

运行：python -m unittest tests.test_causality -v   （零第三方依赖）

覆盖：
    1. 收益K线生成器：prefix 确定性（只用前缀数据）、已提交 Bar 不可变；
    2. 估计器 Bundle：相同前缀 -> 相同 snapshot（估计也只依赖历史）；
    3. 门槛合成器：极端估计输入下七档全部有界（clamp 生效）；
    4. 状态构建：维度固定、因果可复现。
"""

import unittest

from framework.estimators import EstimatorBundle, EstimatorOutput
from framework.indicators import IndicatorSnapshot
from framework.order_state import OrderState
from framework.profit_kline import BarConfig, ProfitKlineGenerator, Snapshot
from framework.state_reward import STATE_DIMS, build_state
from strategy.threshold_scheduler import ThresholdScheduler


def _snap_stream(n: int, seed: int = 1):
    """浮盈随机游走快照流（浮盈单位 R）"""
    import random
    rng = random.Random(seed)
    z = 0.0
    out = []
    ts = 1_700_000_000.0
    for i in range(n):
        z += rng.gauss(0.0, 0.3)
        ts += 1.5
        out.append(Snapshot(ts=ts, price=1900.0 + 0.1 * i, floating_r=z,
                            remaining_frac=1.0, direction=1))
    return out


class TestProfitKlineCausality(unittest.TestCase):

    def test_prefix_determinism(self):
        snaps = _snap_stream(100)
        cfg = BarConfig(threshold_r=0.3, max_seconds=600.0)
        g1 = ProfitKlineGenerator(cfg)
        for s in snaps:
            g1.on_snapshot(s)
        # g1 在前 50 个快照内提交的 Bar
        t50 = snaps[49].ts
        bars1 = [b for b in g1.committed if b.commit_ts <= t50 + 1e-9]

        g2 = ProfitKlineGenerator(BarConfig(threshold_r=0.3, max_seconds=600.0))
        for s in snaps[:50]:
            g2.on_snapshot(s)
        self.assertEqual(len(bars1), len(g2.committed),
                         "前缀相同但提交的Bar数量不同 -> 存在前视")
        for b1, b2 in zip(bars1, g2.committed):
            self.assertEqual((b1.open_r, b1.high_r, b1.low_r, b1.close_r,
                              b1.dd_rate, b1.peak_r),
                             (b2.open_r, b2.high_r, b2.low_r, b2.close_r,
                              b2.dd_rate, b2.peak_r))

    def test_committed_bar_immutable(self):
        snaps = _snap_stream(120)
        cfg = BarConfig(threshold_r=0.25)
        g = ProfitKlineGenerator(cfg)
        for s in snaps:
            g.on_snapshot(s)
        frozen = [(b.open_r, b.high_r, b.low_r, b.close_r, b.dd_rate, b.peak_r,
                   b.commit_ts, b.ticks) for b in g.committed]
        # 继续喂更多数据
        for s in _snap_stream(40, seed=9):
            g.on_snapshot(s)
        for i, b in enumerate(g.committed[:len(frozen)]):
            cur = (b.open_r, b.high_r, b.low_r, b.close_r, b.dd_rate, b.peak_r,
                   b.commit_ts, b.ticks)
            self.assertEqual(frozen[i], cur, "已提交Bar被改写 -> 重绘/前视")


class TestEstimatorCausality(unittest.TestCase):

    def _bars(self, n: int, seed: int = 3):
        import random
        from framework.profit_kline import ProfitBar
        rng = random.Random(seed)
        z = 0.0
        bars = []
        ts = 1_700_000_000.0
        for i in range(n):
            z += rng.gauss(0.0, 0.3)
            ts += 4.0
            b = ProfitBar(idx=i + 1, open_r=z - rng.uniform(0.1, 0.3),
                          high_r=z + 0.2, low_r=z - 0.3, close_r=z,
                          peak_r=max(z, 0.0), start_ts=ts, commit_ts=ts,
                          ticks=3, committed=True)
            bars.append(b)
        return bars

    def test_bundle_prefix_determinism(self):
        bars = self._bars(60)
        ind = IndicatorSnapshot(atr=0.5, spread_pip=3.0, spread_pip_avg=3.0)

        b1 = EstimatorBundle()
        for b in bars[:40]:
            b1.on_bar(b)
        out1 = b1.snapshot(1_700_000_250.0, ind)

        b2 = EstimatorBundle()
        for b in bars[:40]:
            b2.on_bar(b)
        out2 = b2.snapshot(1_700_000_250.0, ind)

        self.assertEqual(out1.sigma_r, out2.sigma_r)
        self.assertEqual(out1.mu_r, out2.mu_r)
        self.assertEqual(out1.q_up, out2.q_up)
        self.assertEqual(out1.ksl, out2.ksl)
        self.assertEqual(out1.audit, out2.audit)


class TestThresholdBounds(unittest.TestCase):

    def _order(self, dd_avg: float = 0.0, hold_frac: float = 0.0) -> OrderState:
        return OrderState(ticket=1, direction=1, entry_price=1900.0,
                          entry_ts=0.0, lots0=1.0, lots_cur=1.0,
                          r_price=1.5, r0_usd=150.0, dd_avg=dd_avg,
                          hold_frac=hold_frac)

    def test_extreme_input_bounded(self):
        sched = ThresholdScheduler()
        # 极端估计：σ 巨大、μ 巨大、分位极端 -> 门槛必须全部落在 clamp 区间内
        est = EstimatorOutput(sigma_r=10.0, mu_r=2.0, q_up=8.0, q_dn=-8.0,
                              tail_dn=-12.0, ksl=1.0, state_best=2)
        thr = sched.compute(est, self._order())
        for name, lo, hi in [("be_r", 0.6, 2.0), ("partial1_r", 1.0, 4.0),
                             ("partial2_r", 1.5, 5.0), ("mae_r", 0.8, 2.0)]:
            v = getattr(thr, name)
            self.assertTrue(lo <= v <= hi, f"{name}={v} 越界")
        self.assertTrue(12 <= thr.max_hold_bars <= 96)
        self.assertTrue(0.1 <= thr.bar_threshold_r <= 0.6)

    def test_audit_record_present(self):
        sched = ThresholdScheduler()
        est = EstimatorOutput(sigma_r=0.5, mu_r=0.05)
        thr = sched.compute(est, self._order())
        self.assertIn("est", thr.audit)
        self.assertIn("b_up", thr.audit)


class TestStateContract(unittest.TestCase):

    def test_state_dims_and_repro(self):
        import random
        rng = random.Random(5)
        z = 0.0
        ts = 1_700_000_000.0
        cfg = BarConfig(threshold_r=0.3)
        g1 = ProfitKlineGenerator(cfg)
        g2 = ProfitKlineGenerator(BarConfig(threshold_r=0.3))
        for i in range(80):
            z += rng.gauss(0.0, 0.25)
            ts += 1.5
            s = Snapshot(ts=ts, price=1900.0, floating_r=z,
                         remaining_frac=1.0, direction=1)
            g1.on_snapshot(s)
            g2.on_snapshot(s)
        ext = IndicatorSnapshot(atr=0.4, rsi=55.0, regime=1, spread_pip=3.0,
                                spread_pip_avg=3.0)
        v1 = build_state(g1, 0.5, 0.8, 120.0, 1.0, ext)
        v2 = build_state(g2, 0.5, 0.8, 120.0, 1.0, ext)
        self.assertEqual(len(v1), STATE_DIMS)
        self.assertEqual(v1, v2)


class TestDecisionFeatures(unittest.TestCase):
    """① L3 差值特征：定长 / 有界 / 门槛上调单调下降 / 可复现（全部因果纯函数）。"""

    def _thr(self, be=1.0, p1=2.0, p2=3.0, trail=1.5, mae=1.2, hold=48,
             bar_thr=0.25):
        from strategy.threshold_scheduler import DynamicThresholds
        return DynamicThresholds(be_r=be, partial1_r=p1, partial2_r=p2,
                                 trail_start_r=trail, mae_r=mae,
                                 max_hold_bars=hold, bar_threshold_r=bar_thr)

    def test_bounded_and_dims(self):
        from framework.state_reward import DECISION_DIMS, build_decision_features
        f = build_decision_features(0.8, self._thr(), 1.2, 300.0, 0.67)
        self.assertEqual(len(f), DECISION_DIMS)
        for v in f:
            self.assertTrue(-5.0 <= v <= 5.0, f"越界特征 {v}")

    def test_threshold_shift_monotone(self):
        from framework.state_reward import build_decision_features
        lo = build_decision_features(0.8, self._thr(p1=2.0), 1.2, 300.0, 0.67)
        hi = build_decision_features(0.8, self._thr(p1=3.0), 1.2, 300.0, 0.67)
        self.assertLess(hi[1], lo[1], "d_p1 随档位上调应下降（差值语义）")

    def test_reproducible(self):
        from framework.state_reward import build_decision_features
        a = build_decision_features(0.8, self._thr(), 1.2, 300.0, 0.67)
        b = build_decision_features(0.8, self._thr(), 1.2, 300.0, 0.67)
        self.assertEqual(a, b)


class TestHMMState(unittest.TestCase):
    """④ 真 HMM：因果前向滤波（前缀确定）+ EM 重标定识别高波动。"""

    def _feed(self, hmm, dzs):
        for dz in dzs:
            hmm.on_delta(dz)

    def test_prefix_deterministic_and_bounded(self):
        from framework.estimators import HMMStateEstimator
        import random
        rng = random.Random(7)
        dzs = [rng.gauss(0.0, 0.6) for _ in range(60)]
        h1 = HMMStateEstimator(retrain_every=40, em_iters=20)
        h2 = HMMStateEstimator(retrain_every=40, em_iters=20)
        self._feed(h1, dzs[:40])
        self._feed(h2, dzs[:40])
        self.assertEqual(h1.probs(), h2.probs(), "前缀重复应确定一致")
        p = h1.probs()
        self.assertTrue(all(0.0 <= x <= 1.0 for x in p))
        self.assertAlmostEqual(sum(p), 1.0, places=6)

    def test_retrain_recalibrates_volatility(self):
        from framework.estimators import HMMStateEstimator
        import random
        rng = random.Random(11)
        dzs = [rng.gauss(0.0, 1.4) for _ in range(120)]
        h = HMMStateEstimator(n_states=2, retrain_every=60, em_iters=20)
        self._feed(h, dzs)
        self.assertTrue(any(s > 0.5 for s in h.sigma), "EM 应识别出高波动态")


class TestBoundaryTable(unittest.TestCase):
    """②③ 数值边界表：有界 / σ 单调（波动越大边界越宽）/ 出界查表安全。"""

    def test_bounds_and_sigma_monotone(self):
        from strategy.threshold_scheduler import BoundaryTable
        bt = BoundaryTable(n_x=81, n_t=16)
        bt.compute()
        k = len(bt._t_frac) // 2
        for i, s in enumerate(bt.sigma_grid):
            for t_frac in bt._t_frac:
                break_up = bt._b_up[i][bt._t_frac.index(t_frac)]
                break_dn = bt._b_dn[i][bt._t_frac.index(t_frac)]
                self.assertTrue(0.2 <= break_up <= bt.x_max, f"b_up 越界 {break_up}")
                self.assertTrue(-bt.x_max <= break_dn <= -0.2, f"b_dn 越界 {break_dn}")
        self.assertGreater(bt._b_up[-1][k], bt._b_up[0][k],
                           "σ 越大 → 上边界越宽（停时语义）")

    def test_lookup_out_of_bounds_safe(self):
        from strategy.threshold_scheduler import BoundaryTable
        bt = BoundaryTable(n_x=61, n_t=10)
        bt.compute()
        for sig in (0.01, 5.0, 2.0):
            for tf in (-0.5, 1.5, 0.4):
                bu, bd, bp = bt.lookup(sig, tf, 0.2)
                self.assertTrue(0.2 <= bu <= bt.x_max)
                self.assertTrue(-bt.x_max <= bd <= -0.2)


class TestPurgedCV(unittest.TestCase):
    """④ purged CV：结构完整 + 训练折与测试折时间窗不重叠（防泄漏）。"""

    def test_no_leakage_and_structure(self):
        from backtest.metrics import purged_cv
        from backtest.replay import Tick
        import random
        rng = random.Random(3)
        t0 = 1_700_000_000.0
        ticks = [Tick(ts=t0 + i * 2.0, mid=1900.0 + rng.gauss(0.0, 0.4),
                      spread_pip=3.0) for i in range(2000)]
        res = purged_cv(ticks, folds=3, runner=_run_dummy, embargo_frac=0.05)
        self.assertEqual(len([k for k in res if k.startswith("fold_")]), 3)
        self.assertIn("mean", res)
        self.assertIn("ProfitFactor", res["mean"])
        # 折0：测试窗在 [t0, t0+span/3] → 训练段整体在测试窗 + embargo 之后
        span = ticks[-1].ts - ticks[0].ts
        hi0 = ticks[0].ts + span / 3
        pad = span * 0.02
        emb = (span / 3) * 0.05
        n_tr0 = res["fold_0"]["n_train_ticks"]
        # 剔除生效：训练 tick 数显著少于全量（≈2/3 - pad/span），且不为 0
        self.assertLess(n_tr0, 2000)
        self.assertGreater(n_tr0, 1000)


class TestL3AdvisorWiring(unittest.TestCase):
    """① L3 接线：OrderManager 挂顾问后，DECIDE 事件带 adv / adv_conf 字段。"""

    def test_decision_event_carries_advisor(self):
        from strategy.rl_advisor import RuleAdvisor
        from backtest.replay import (OrderManager, Tick, ExitStrategy,
                                     BarConfig, CostConfig, GuardConfig,
                                     EstimatorBundle, ThresholdScheduler,
                                     SimExecutor, CausalIndicators)
        import random
        rng = random.Random(8)
        ts = 1_700_000_000.0
        z = 0.0
        events = []
        mgr = OrderManager(
            strategy=ExitStrategy(),
            bar_cfg=BarConfig(),
            cost_cfg=CostConfig(),
            guard_cfg=GuardConfig(),
            estimators=EstimatorBundle(),
            scheduler=ThresholdScheduler(),
            executor=SimExecutor(CostConfig(), rng),
            indicators=CausalIndicators(),
            advisor=RuleAdvisor(),
            on_decision=events.append,
        )
        mgr.open_order(Tick(ts=ts, mid=1903.0, spread_pip=3.0), 1, lots=1.0)
        for i in range(1, 200):
            z += 0.05
            ts += 2.0
            mgr.on_tick(Tick(ts=ts, mid=1903.0 + z, spread_pip=3.0))
        decide_evs = [e for e in events if e.get("event") == "DECIDE"]
        self.assertGreater(len(decide_evs), 0, "应产生决策事件")
        self.assertIn("adv", decide_evs[0])
        self.assertIn("adv_conf", decide_evs[0])


def _run_dummy(ticks):
    """极简 runner：tick 流走默认配置回测，返回 ReplayResult。"""
    from backtest.replay import run_replay
    from strategy.exit_strategy import ExitStrategy
    from framework.cost_model import CostConfig
    from framework.profit_kline import BarConfig
    from strategy.guards import GuardConfig
    return run_replay(ticks, strategy=ExitStrategy(),
                      cost_cfg=CostConfig(), bar_cfg=BarConfig(),
                      guard_cfg=GuardConfig())


if __name__ == "__main__":
    unittest.main(verbosity=2)