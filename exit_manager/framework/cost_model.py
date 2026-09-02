# -*- coding: utf-8 -*-
"""
成本模型：点差(Spread)、滑点(Slippage)、隔夜利息(Swap)

设计原则（理论依据）：
    1. 成本必须真实进入"模拟环境/回测"，策略才不会把"纸面浮盈"当成可兑现利润，
       否则在线决策会被系统性高估 —— 这是 sim-to-real 差距最主要的来源之一。
    2. 本模块把一切成本统一折算为「初始风险 R 的倍数」或「美元金额」两种口径，
       供状态、奖励、回测三处共用，保证口径一致。
    3. XAU/USD 合约习惯：
       - 1 标准手 = 100 盎司；报价通常 5 位小数（如 1900.01），
         "pip" = 0.10 美元，"point" = 0.01 美元。
       - 黄金零售点差通常 0.2 ~ 0.5 美元（2 ~ 5 pip），ECN 账户另收佣金。
       - 隔夜持仓有 swap，且长、短方向利率不同（通常做多黄金付利息）。
"""

from dataclasses import dataclass, field

# ---- 合约常量（XAU/USD）----
PIP: float = 0.10          # 1 pip = 0.1 美元
POINT: float = 0.01        # 1 point = 0.01 美元
CONTRACT_SIZE_OZ: int = 100  # 1 标准手 = 100 盎司

# 每移动 1 美元价格，1 手对应的美元盈亏 = 合约乘数
USD_PER_LOT_PER_USD_MOVE: float = float(CONTRACT_SIZE_OZ)


@dataclass
class CostConfig:
    """成本配置（默认值是黄金零售经纪商的典型量级，可随账户情况覆盖）"""
    spread_pip: float = 3.0          # 典型点差：3 pip = 0.30 美元（单边价差）
    slipping_ratio: float = 0.25     # 滑点上限 = 该比例 × 点差（均匀分布，市价单/止损单）
    swap_per_lot_day: dict = field(default_factory=lambda: {1: -4.5, -1: 2.0})
    # swap: 单位 $/手/天。做多黄金通常为负（付利息），做空可能为正或负，按账户实际覆盖
    # 注意：不同券商、不同隔夜时段差异大；实盘必须用 MT5 的 symbol_info 实际值回填。
    rng_seed: int = 7                # 滑点随机种子（回测可复现）

    @property
    def spread_price(self) -> float:
        """点差的报价价差（美元/盎司），即 bid/ask 之差"""
        return self.spread_pip * PIP

    def slippage_price(self, rng) -> float:
        """一次市价成交的滑点（美元/盎司），统一为不利方向"""
        return rng.uniform(0.0, self.slipping_ratio) * self.spread_price


# ---------------- 工具函数 ----------------

def price_to_usd(price_delta: float, lots: float) -> float:
    """价格变动(美元/盎司) × 手数 → 美元盈亏"""
    return price_delta * USD_PER_LOT_PER_USD_MOVE * lots


def usd_to_r(usd: float, r0_usd: float) -> float:
    """美元金额 → 初始风险 R 的倍数（r0_usd 为入场时定义的单笔风险）"""
    return usd / r0_usd if r0_usd != 0.0 else 0.0


def entry_cost_usd(cfg: CostConfig, lots: float) -> float:
    """入场成本：市价单进场付出半个点差（ask/bid）
    说明：真实成交以 ask/bid 计，这里把半价差折算为一次成本项。"""
    return (cfg.spread_price / 2.0) * USD_PER_LOT_PER_USD_MOVE * lots


def close_cost_usd(cfg: CostConfig, lots: float, rng) -> float:
    """一次平仓成本：半个点差 + 滑点，均为不利方向"""
    slip = cfg.slippage_price(rng)
    return (cfg.spread_price / 2.0 + slip) * USD_PER_LOT_PER_USD_MOVE * lots


def swap_usd(cfg: CostConfig, direction: int, lots: float, seconds: float) -> float:
    """持仓期间累积的隔夜利息（美元）。seconds 为累计持仓秒数。
    每天按固定利率计，实际券商按 3 倍隔夜（周三）计息，此处简化为日均。"""
    days = seconds / 86400.0
    return cfg.swap_per_lot_day.get(direction, 0.0) * lots * days


# 演示/测试用：验证成本口径一致
if __name__ == "__main__":
    import random
    c = CostConfig()
    rng = random.Random(7)
    print("典型点差(美元):", c.spread_price)
    print("入场成本($/手):", round(entry_cost_usd(c, 1.0), 2))
    print("平仓成本($/手):", round(close_cost_usd(c, 1.0, rng), 2))
    print("隔夜swap($/手): 多仓持1天 =", round(swap_usd(c, 1, 1.0, 86400), 2),
          " 空仓持1天 =", round(swap_usd(c, -1, 1.0, 86400), 2))