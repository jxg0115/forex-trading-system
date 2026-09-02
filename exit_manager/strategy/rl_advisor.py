# -*- coding: utf-8 -*-
"""
AI 辅助层（可选）：RL 顾问（RL Advisor）

设计（呼应 README 的落地原则）：
    - 规则策略是基线与护栏，RL/PPO 顾问只提供"建议"，不直接下指令；
    - 任何时刻护栏（策略层 guards + 执行层单调止损）优先于顾问建议；
    - 顾问输出 (action_id, confidence)，由 ExitStrategy 在收益K线 commit 时
      决定是否采纳（默认：顾问高置信建议作为 tie-breaker / 可 A/B 对比）；
    - 训练管线（PPO, stable-baselines3）是可选的：历史 tick 重放环境
      （backtest/replay.py 的 ReplayEnv）+ 状态/奖励接口
      （framework/state_reward.py）已经就绪，直接对接即可。

理论依据：
    PPO (Schulman et al. 2017)；potential-based reward shaping (Ng et al. 1999)；
    建议先行为克隆(BC)预训练（用三屏障法打标的历史订单），再 PPO 微调；
    训练与推理共用同一状态向量（framework/state_reward.build_state）。
"""

from typing import List, Optional

from framework.actions import (
    ACT_HOLD, ACT_TIGHTEN_BE, ACT_PART_1_3, ACT_PART_2_3, ACT_CLOSE_ALL,
    ACTION_NAMES, ACTION_LIST, NUM_ACTIONS,
)


class RLAdvisor:
    """AI 顾问接口：输入状态向量，输出建议动作。"""

    def suggest(self, state_vec: List[float]) -> tuple:
        """返回 (action_id:int, confidence:float in [0,1])。"""
        raise NotImplementedError

    def state_dims(self) -> int:
        return len(ADVISOR_STATE_SPEC)  # 与 framework 状态维度对接时由上层注入


# 与 framework/state_reward.STATE_DIMS 的对接点
ADVISOR_STATE_SPEC: List[str] = [
    # 由 framework.state_reward.build_state 生成；此处仅声明契约
    # 实际维度以 build_state 的 STATE_DIMS 为准，建议注入
]


class RuleAdvisor(RLAdvisor):
    """差值规则顾问 —— L3 的可解释确定性基线（BC 预训练的规则化版本）。

    输入：build_decision_features 的差值特征向量（顺序 = DECISION_FEATURE_NAMES：
          d_be, d_p1, d_p2, d_trail, d_mae, d_time, d_bar, ...；见
          framework/state_reward.build_decision_features）。
    语义（THEORY 3.3：“差值越深 → 置信越高”；护栏永远可否决）：
        d_mae ≤ -mae_advance     → CLOSE_ALL（断熔逼近，高置信）
        d_p2 ≥ 0                 → PART_2_3（美式分批交叉档到达）
        d_p1 ≥ 0                 → PART_1_3（分批1档到达）
        d_be ≥ 0                 → TIGHTEN_BE（保本档到达）
        其余                     → HOLD（conf=0）
    conf：差值深度 0→conf_lo、≥1R→conf_hi，线性。
    """

    def __init__(self, mae_advance: float = 0.3, conf_lo: float = 0.7,
                 conf_hi: float = 0.95):
        self.mae_advance = mae_advance
        self.conf_lo = conf_lo
        self.conf_hi = conf_hi

    def suggest(self, state_vec):
        if state_vec is None or len(state_vec) < 7:
            return ACT_HOLD, 0.0
        d_be, d_p1, d_p2, _d_trail, d_mae, _d_time, _d_bar = state_vec[:7]

        def _conf(depth: float) -> float:
            return self.conf_lo + (self.conf_hi - self.conf_lo) \
                * min(max(depth, 0.0), 1.0)

        if d_mae <= -self.mae_advance:
            return ACT_CLOSE_ALL, _conf(-d_mae)
        if d_p2 >= 0.0:
            return ACT_PART_2_3, _conf(d_p2)
        if d_p1 >= 0.0:
            return ACT_PART_1_3, _conf(d_p1)
        if d_be >= 0.0:
            return ACT_TIGHTEN_BE, _conf(d_be)
        return ACT_HOLD, 0.0


class DummyRLAdvisor(RLAdvisor):
    """演示用顾问：永远建议 HOLD（占位，便于 demo 端到端可运行）。"""

    def suggest(self, state_vec):
        return ACT_HOLD, 0.5


class PPOAdvisor(RLAdvisor):
    """PPO 顾问（可选依赖 stable-baselines3；未安装时降级为 HOLD）。

    训练对接（README 有完整说明）：
        1) 用 backtest/replay.py 的 ReplayEnv 跑历史 tick，产出 (state, action, reward)；
        2) from stable_baselines3 import PPO; ppo = PPO('MlpPolicy', env, ...)；
        3) ppo.load(path).predict(state_vec, deterministic=True) -> action；
        4) 采纳逻辑：confidence>阈值 && 护栏未拦截 时采纳建议。
    """

    def __init__(self, model_path: Optional[str] = None, accept_thresh: float = 0.7):
        self._model = None
        self._thresh = accept_thresh
        if model_path:
            try:
                from stable_baselines3 import PPO  # 可选依赖
                self._model = PPO.load(model_path)
            except Exception as e:  # 未装/加载失败 -> 降级
                self._model = None
                print(f"[advisor] PPO 加载失败，降级为 HOLD: {e}")

    def suggest(self, state_vec):
        if self._model is None:
            return ACT_HOLD, 0.0
        try:
            import numpy as np
            action, _ = self._model.predict(np.array(state_vec, dtype=np.float32),
                                            deterministic=True)
            return int(action), 1.0
        except Exception:
            return ACT_HOLD, 0.0


# ---------------- 采纳协议（供策略层使用） ----------------

def advisor_acceptable(action: int, conf: float, thresh: float = 0.7) -> bool:
    """顾问建议是否可采纳（高置信 + 合法动作区间）。
    实际采纳与否还须通过护栏与状态机（ExitStrategy 内部处理）。"""
    return conf >= thresh and action in ACTION_LIST and action != ACT_HOLD