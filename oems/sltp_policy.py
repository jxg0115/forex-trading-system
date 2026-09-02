"""统一止损止盈政策配置：机制矩阵 + 状态门控（R/ATR 单位）。

设计对应 docs/止损止盈重构设计方案.md（已定稿）：
- 六个机制单元（移动止损止盈 / 阶梯止盈 / 分批落袋 / 高水位离场 / 时间止损 / 保本）各自独立开关与 Gate；
- 全部档位与距离统一为 R（风险倍数，R = risk_mult × ATR）或 ATR 单位；
- 美元档位仅用于前端显示换算（由引擎按 volume×contract 折算，不存于本配置）。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class SltpPolicyConfig:
    """统一止损止盈政策（R/ATR 单位，六单元矩阵）。"""

    # ---- 全局 ----
    enabled: bool = False
    symbol: str = "EURUSD"          # 参考品种（用于波动自适应频率与行情维度兜底）
    timeframe: str = "M15"
    manage_manual: bool = True
    scan_interval_seconds: int = 5  # 基础心跳
    scan_adaptive: bool = True      # 波动自适应：高 5s / 中 10s / 低 20s
    modify_cooldown_seconds: int = 60  # SL/TP 修改冷却（扫描可密、动作节流）
    risk_mult: float = 1.5          # R = risk_mult × ATR
    take_r_mult: float = 2.0        # L1 初始止盈 = R × take_r_mult（上限）
    hard_stop_atr: float = 1.5      # 硬止损距离（风控维度底线）

    # ---- L1 初始止盈来源 ----
    initial_tp_source: str = "r"    # r（R 上限）| structure（结构目标）| pattern（形态上限参考）
    pattern_max_take_pct: float = 3.0  # 形态建议仅作上限参考（%），超过按此封顶

    # ---- 保本单元 ----
    breakeven_enabled: bool = True
    breakeven_trigger_r: float = 0.5
    breakeven_buffer_atr: float = 0.1

    # ---- 移动止损止盈单元 ----
    trailing_enabled: bool = True
    trailing_activation_r: float = 0.5
    trailing_stop_atr: float = 1.0
    trailing_take_atr: float = 0.5
    trailing_take_buffer_atr: float = 0.5

    # ---- 阶梯止盈单元 ----
    ladder_enabled: bool = True
    ladder_tiers: list[dict[str, Any]] = field(
        default_factory=lambda: [
            {"trigger_r": 1.0, "raise_to_r": 0.0, "gate": {}},
            {"trigger_r": 2.0, "raise_to_r": 1.0, "gate": {}},
            {"trigger_r": 3.0, "raise_to_r": 2.0, "gate": {}},
        ]
    )
    ladder_advance: dict[str, Any] = field(
        default_factory=lambda: {
            "time_force_bars": 60,       # 超时未进阶强制上一档
            "rsi_extreme_force": True,   # 超买超卖强制锁利
            "macd_reverse_force": True,  # MACD 柱转向强制上档
            "boll_touch_force": True,    # 布林外轨触碰强制锁利
        }
    )
    ladder_retrace_atr: float = 1.0     # 回落容忍（底线 = 峰值 −/− N×ATR，取更有利）

    # ---- 分批落袋单元 ----
    partial_close_enabled: bool = True
    partial_close_tiers: list[dict[str, Any]] = field(
        default_factory=lambda: [
            {"trigger_r": 1.0, "close_pct": 30, "gate": {}},
            {"trigger_r": 2.0, "close_pct": 30, "gate": {}},
            {"trigger_r": 3.0, "close_pct": 40, "gate": {}},
        ]
    )
    partial_adaptive: dict[str, Any] = field(
        default_factory=lambda: {
            "regime_accelerate": True,    # 震荡/高波动提前分批
            "rsi_extreme_force": True,    # 超买超卖强制触发当前档
            "time_increase_pct": True,    # 超时未进阶加大分批比例
        }
    )

    # ---- 高水位离场单元 ----
    high_watermark_enabled: bool = True
    hw_activation_r: float = 1.0
    hw_retrace_atr: float = 1.5         # 峰值回落超过 N×ATR 全平

    # ---- 时间止损单元 ----
    time_stop_enabled: bool = True
    time_stop_bars: int = 120
    time_stop_min_profit_r: float = 0.5
    time_stop_action: str = "close"     # close（离场）| tighten（收紧到保本）

    # ---- 指标阈值（Gate 参数）----
    rsi_ob: float = 75.0
    rsi_os: float = 25.0
    adx_strong: float = 25.0
    boll_touch_enabled: bool = True
    swing_bars: int = 10

    # ---- 外部供给层增强（整合版：收益K线事件驱动 / 动态档位 / 审计细节）----
    event_driven: bool = False          # 收益K线事件驱动采样（介入快标记，审计可见）
    dynamic_thresholds: bool = False    # 七档动态合成供给六单元档位（覆盖固定 R 档）
    audit_detail: bool = False          # 决策审计细节（估计量/档位来源，diagnose 查看）

    # ---- AI 辅助层（L6，默认关闭）----
    ai_enabled: bool = False
    ai_take_profit_enabled: bool = True
    ai_min_confidence: float = 0.6
    ai_min_interval_seconds: int = 60
    ai_triggers: list[str] = field(
        default_factory=lambda: ["time_stop", "near_hard", "profit_lock"]
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "SltpPolicyConfig":
        if not data:
            return cls()
        keys = set(cls.__dataclass_fields__.keys())
        return cls(**{k: v for k, v in data.items() if k in keys})