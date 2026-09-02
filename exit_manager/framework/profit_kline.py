# -*- coding: utf-8 -*-
"""
模块一：收益K线实时生成器（事件驱动，非固定时间）

核心理念（理论依据）：
    López de Prado《Advances in Financial Machine Learning》第 2 章：
    传统"时间K线"在信息流量不均匀的市场里信息含量不均（震荡期一堆无效bar，
    单边期信息被压扁）。"美元Bar/成交量Bar"按信息量切分，使每根Bar信息等量。

    本模块把该思想移植到"订单浮盈"上：
    - 每根收益K线累积 |浮盈净变动| 达到阈值（threshold_r，单位：初始风险R的倍数）
      时立即"提交(commit)"并开启新K线 —— 浮盈剧烈波动时出Bar快（介入快），
      浮盈平静时出Bar慢（避免无效决策噪声）。
    - 另设"时间护栏(max_seconds)"：浮盈长时间不动也要强制出Bar，保证
      (a) 监控不会"失明"；(b) 收益K线计数的时间止损/状态特征持续更新。

无前视(No Look-ahead)保证（本模块最重要的不变量）：
    1. on_snapshot(s) 只读取当前快照，从不引用未来价格；
    2. 已 commit 的 Bar 永不再被改写（immutable-by-convention，配单元测试验证）；
    3. 回测与实盘共用同一个生成器实例代码 —— 标准不分裂，杜绝"回测用收盘Bar、
       实盘用进行中Bar"这类重绘/闪烁的根源。

Bar 字段均以 R 倍数归一（对仓位规模不敏感，状态可迁移）。
"""

from dataclasses import dataclass, field
from typing import Callable, List, Optional


@dataclass(frozen=True)
class Snapshot:
    """一次订单状态快照（由上层每 tick 或每收到一个新报价时构造）"""
    ts: float              # 时间戳（epoch 秒）
    price: float           # 当前成交价（mid）
    floating_r: float      # 当前浮盈，单位：初始风险 R 的倍数（未扣成本）
    remaining_frac: float  # 剩余仓位比例 [0, 1]（分批止盈后变小）
    direction: int         # +1 多, -1 空（本引擎对收益K线只关心幅度，方向用于语义注释）


@dataclass
class ProfitBar:
    """一根收益K线。除 idx/committed 外，字段在 commit 前会更新，commit 后冻结。"""
    idx: int = 0
    open_r: float = 0.0     # 开盘浮盈(R)
    high_r: float = 0.0     # 区间最高浮盈(R)
    low_r: float = 0.0      # 区间最低浮盈(R)
    close_r: float = 0.0    # 收盘浮盈(R)
    dd_rate: float = 0.0    # 浮盈最大回撤率 ∈[0,1]：相对区间峰值正浮盈的回撤
    peak_r: float = 0.0     # 区间峰值浮盈(R)，仅取正
    start_ts: float = 0.0   # 开盘时间
    commit_ts: float = 0.0  # 提交时间
    ticks: int = 0          # 区间内快照数量
    volume_frac_close: float = 1.0  # 提交时的剩余仓位比例（近似"成交量"语义）
    committed: bool = False


@dataclass
class BarConfig:
    threshold_r: float = 0.25    # 出Bar阈值：|累计浮盈变动| ≥ 0.25R（事件驱动灵敏度）
    max_seconds: float = 900.0   # 时间护栏：Bar 最长 15 分钟，超时强制提交
    min_seconds: float = 1.0     # 最短Bar时长：防止同一时刻反复提交


class ProfitKlineGenerator:
    """收益K线生成器。

    用法：
        gen = ProfitKlineGenerator(cfg, on_commit=handler)
        for snap in snap_stream: gen.on_snapshot(snap)
        # gen.committed 为全部已提交Bar（冻结），gen.live 为进行中Bar
    """

    def __init__(self, cfg: BarConfig, on_commit: Optional[Callable[[ProfitBar], None]] = None):
        self.cfg = cfg
        self.committed: List[ProfitBar] = []  # 已提交Bar：永不改写
        self.live: Optional[ProfitBar] = None # 进行中Bar
        self._on_commit = on_commit
        self._last_float: Optional[float] = None  # 上一快照浮盈
        self._cum_abs: float = 0.0                # 当前Bar累计|浮盈变动|
        self._idx: int = 0
        self._last_ts: Optional[float] = None

    # ---------------- 对外接口 ----------------

    def on_snapshot(self, s: Snapshot) -> Optional[ProfitBar]:
        """喂入一个快照。若触发提交，返回该 Bar（并回调 on_commit），否则返回 None。
        注意：返回值只用于"本事件触发了提交"的同步判断，历史Bar一律读 self.committed。"""
        if self.live is None:
            self._open_new_bar(s)
            self._last_float = s.floating_r
            self._last_ts = s.ts
            return None

        b = self.live
        b.ticks += 1

        # 更新 OHLC 与峰值/回撤（只基于当前快照，不参考未来）
        b.close_r = s.floating_r
        if s.floating_r > b.high_r:
            b.high_r = s.floating_r
        if s.floating_r < b.low_r:
            b.low_r = s.floating_r
        if s.floating_r > b.peak_r:
            b.peak_r = s.floating_r
        b.dd_rate = self._dd_rate(b.peak_r, s.floating_r)
        b.volume_frac_close = s.remaining_frac

        # 事件驱动：累计 |浮盈变动| 达到阈值 -> 提交
        d = s.floating_r - self._last_float
        self._cum_abs += abs(d)

        emit = None
        if (self._cum_abs >= self.cfg.threshold_r or
                (s.ts - b.start_ts >= self.cfg.max_seconds and s.ts - b.start_ts >= self.cfg.min_seconds)):
            emit = self._commit(s.ts)

        self._last_float = s.floating_r
        self._last_ts = s.ts
        return emit

    def reset(self) -> None:
        """重置生成器（新订单 / 新回测周期时调用）"""
        self.committed.clear()
        self.live = None
        self._last_float = None
        self._cum_abs = 0.0
        self._idx = 0
        self._last_ts = None

    # ---------------- 内部 ----------------

    def _open_new_bar(self, s: Snapshot) -> None:
        self._idx += 1
        self.live = ProfitBar(idx=self._idx, start_ts=s.ts, commit_ts=s.ts)
        b = self.live
        b.open_r = b.high_r = b.low_r = b.close_r = s.floating_r
        b.peak_r = max(0.0, s.floating_r)   # 峰值仅计正浮盈，避免未盈利时 dd 除零
        b.dd_rate = self._dd_rate(b.peak_r, s.floating_r)
        b.volume_frac_close = s.remaining_frac
        b.ticks = 1
        self._cum_abs = 0.0

    def _commit(self, ts: float) -> ProfitBar:
        """提交当前Bar：冻结并归档，随后立即以同一快照开启新Bar。"""
        b = self.live
        b.committed = True
        b.commit_ts = ts
        self.committed.append(b)
        self.live = None
        self._cum_abs = 0.0
        if self._on_commit is not None:
            self._on_commit(b)
        return b

    @staticmethod
    def _dd_rate(peak_r: float, cur_r: float) -> float:
        """浮盈最大回撤率：相对区间峰值正浮盈的当前回撤，∈[0,1]。
        定义：peak 为 0（从未盈利）时回撤记 0；当前浮盈 ≤ 0 视为相对峰值回撤 100%。"""
        if peak_r <= 0.0:
            return 0.0
        dd = (peak_r - max(cur_r, 0.0)) / peak_r
        return min(max(dd, 0.0), 1.0)

    # ---------------- 查询 ----------------

    @property
    def bar_count(self) -> int:
        return len(self.committed)

    def live_bar_dict(self) -> Optional[dict]:
        """进行中Bar的只读快照（供服务层/前端实时渲染）。
        不修改任何状态，不违反因果约定。"""
        b = self.live
        if b is None:
            return None
        return {
            "idx": b.idx, "open_r": round(b.open_r, 3),
            "high_r": round(b.high_r, 3), "low_r": round(b.low_r, 3),
            "close_r": round(b.close_r, 3), "dd_rate": round(b.dd_rate, 3),
            "peak_r": round(b.peak_r, 3), "start_ts": b.start_ts,
            "ticks": b.ticks, "committed": False,
        }

    def bars_back(self, n: int) -> List[ProfitBar]:
        """最近 n 根已提交Bar（时间升序，不足 n 返回全部）"""
        return self.committed[-n:] if n > 0 else []