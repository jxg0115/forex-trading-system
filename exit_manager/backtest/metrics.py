# -*- coding: utf-8 -*-
"""
绩效评估（域 E，INTERFACES.md 6.3 / TECH_DESIGN.md 域E）

指标集合：
    - 每单档案（含峰值：peak_r / peak_ts / capture_ratio —— 退出质量审计）
    - 聚合：总R、期望/单、胜率、Profit Factor、最大回撤、平均持仓、交易频率
    - 退出原因分布（审计"不硬扛"是否生效：MAE断熔/时间止损/追踪兑现）
    - 与朴素基线对比表
    - walk-forward 分段验证（防时间重叠泄漏的评估入口）
单位：R 倍数（可跨单聚合）；Capture Ratio = 净兑现R / 峰值R ∈[0,1]，衡量
追踪止损把"纸上利润"兑现了多少 —— 这套系统的核心绩效 KPI。
"""

from typing import Dict, List, Optional

from backtest.replay import OrderResult, ReplayResult, Tick


def augment_capture(order: OrderResult) -> None:
    """峰值>0 时计算兑现率；峰值<=0（从未盈利）记 0。"""
    if order.peak_r > 0:
        order.capture_ratio = min(max(order.net_r / order.peak_r, 0.0), 1.0)


def evaluate(res: ReplayResult) -> Dict[str, float]:
    """聚合一笔回测结果 -> 指标字典。"""
    orders = res.orders
    n = len(orders)
    total_net_r = 0.0
    wins = 0
    gross_win = 0.0
    gross_loss = 0.0
    max_rel_dd = 0.0
    cum = 0.0
    peak_cum = 0.0
    hold_total = 0.0
    exit_reasons: Dict[str, int] = {}
    capture_sum = 0.0
    capture_n = 0

    cur_win = cur_loss = max_win = max_loss = 0
    hold_win_total = hold_loss_total = 0.0
    hold_win_n = hold_loss_n = 0
    capture_list: List[float] = []

    for o in orders:
        augment_capture(o)
        total_net_r += o.net_r
        hold_total += o.holding_sec
        if o.net_r > 0:
            wins += 1
            gross_win += o.net_r
            cur_win += 1; cur_loss = 0
            max_win = max(max_win, cur_win)
            hold_win_total += o.holding_sec; hold_win_n += 1
        else:
            gross_loss += -o.net_r
            cur_loss += 1; cur_win = 0
            max_loss = max(max_loss, cur_loss)
            hold_loss_total += o.holding_sec; hold_loss_n += 1
        cum += o.net_r
        peak_cum = max(peak_cum, cum)
        max_rel_dd = max(max_rel_dd, peak_cum - cum)
        exit_reasons[o.exit_reason] = exit_reasons.get(o.exit_reason, 0) + 1
        if o.peak_r > 0:
            capture_sum += o.capture_ratio
            capture_n += 1
            capture_list.append(o.capture_ratio)

    cap_sorted = sorted(capture_list)

    def _q(p: float) -> float:
        if not cap_sorted:
            return 0.0
        k = int((len(cap_sorted) - 1) * p)
        return cap_sorted[k]

    return {
        "订单数": float(n),
        "总R": round(total_net_r, 2),
        "期望R/单": round(total_net_r / n, 3) if n else 0.0,
        "胜率%": round(100.0 * wins / n, 1) if n else 0.0,
        "ProfitFactor": round(gross_win / gross_loss, 2) if gross_loss > 0 else float("inf"),
        "最大回撤R": round(max_rel_dd, 2),
        "平均持仓(分钟)": round(hold_total / n / 60.0, 1) if n else 0.0,
        "峰值兑现率均值%": round(100.0 * capture_sum / capture_n, 1) if capture_n else 0.0,
        "最大连盈次数": float(max_win),
        "最大连亏次数": float(max_loss),
        "盈利单均持仓(分钟)": round(hold_win_total / hold_win_n / 60.0, 1) if hold_win_n else 0.0,
        "亏损单均持仓(分钟)": round(hold_loss_total / hold_loss_n / 60.0, 1) if hold_loss_n else 0.0,
        "兑现率P25/P50/P75%": [round(100.0 * _q(0.25), 1), round(100.0 * _q(0.5), 1),
                              round(100.0 * _q(0.75), 1)],
    }


def print_reason_breakdown(res: ReplayResult) -> None:
    reasons: Dict[str, int] = {}
    for o in res.orders:
        key = o.exit_reason.split(":")[0] if ":" in o.exit_reason else o.exit_reason
        reasons[key] = reasons.get(key, 0) + 1
    print("  退出原因分布:", ", ".join(f"{k}={v}" for k, v in sorted(reasons.items())))


def compare(res_a: ReplayResult, res_b: ReplayResult) -> None:
    """策略 vs 基线 对比表。"""
    a = evaluate(res_a)
    b = evaluate(res_b)
    rows = ["订单数", "总R", "期望R/单", "胜率%", "ProfitFactor",
            "最大回撤R", "平均持仓(分钟)", "峰值兑现率均值%"]
    print(f"\n===== {res_a.name} vs {res_b.name} =====")
    print(f"{'指标':<16}{res_a.name:<18}{res_b.name}")
    for r in rows:
        va = a.get(r, "-")
        vb = b.get(r, "-")
        va = f"{va:.2f}" if isinstance(va, float) else str(va)
        vb = f"{vb:.2f}" if isinstance(vb, float) else str(vb)
        print(f"{r:<16}{va:<18}{vb}")


def walk_forward_validate(ticks: List[Tick], folds: int = 4,
                          runner=None, **kwargs) -> List[Dict[str, float]]:
    """简单 walk-forward：按时间切成 folds 段，逐段跑 runner 并评估。
    runner(ticks_segment) -> ReplayResult；默认用 backtest.replay.run_replay。
    完整性说明：生产版应训练段/测试段分离（purged/embargoed CV，AFML ch.7），
    本函数提供"分段评估"入口框架。"""
    from backtest.replay import run_replay
    runner = runner or (lambda seg: run_replay(seg, **kwargs))
    segs = np_split(ticks, folds)
    out: List[Dict[str, float]] = []
    for i, seg in enumerate(segs):
        res = runner(seg)
        out.append(evaluate(res))
        out[-1]["fold"] = float(i)
    return out


def purged_cv(ticks: List[Tick], folds: int = 4, runner=None,
              embargo_frac: float = 0.05, overlap_pad_frac: float = 0.02,
              **kwargs) -> Dict[str, object]:
    """Purged K 折交叉验证（④落地；AFML ch.7 工程化，无 numpy 依赖）。

    原理（防时间重叠泄漏，THEORY 6 ref[1] ch.7 / TECH_DESIGN 域E ⑪）：
        时间切折 → 每折测试窗 [t_lo, t_hi]；
        训练折剔除（两层）：
            - purge：与测试窗重叠的 tick 全部剔除（tick 级剔除 ⇒ 订单级不重叠：
              测试窗内不存在任何训练 tick，则训练段产生的订单区间不可能与测试窗相交）；
            - embargo：测试窗前 embargo 秒（=测试窗长 × embargo_frac）内的 tick 也剔除
              （防“临近测试段的训练样本被测试段信号污染”，AFML 7.6.2）。
        每折 runner(训练段) → evaluate → 指标；末尾给各折均值。
    与 walk_forward_validate 并存：后者=简单分段（不 purge，快）；本函数=防泄漏评估。
    runner(ticks_segment) -> ReplayResult；默认 backtest.replay.run_replay。
    """
    from backtest.replay import run_replay
    runner = runner or (lambda seg: run_replay(seg, **kwargs))
    n = len(ticks)
    if n < folds * 10:
        raise ValueError("tick 数太少，无法可靠做 purged CV")
    t0 = ticks[0].ts
    t1 = ticks[-1].ts
    span = max(t1 - t0, 1e-9)
    edges = [t0 + span * i / folds for i in range(folds + 1)]
    results: Dict[str, object] = {}
    for k in range(folds):
        lo, hi = edges[k], edges[k + 1]
        pad = span * overlap_pad_frac
        emb = (hi - lo) * embargo_frac
        em_lo = lo - pad - emb
        train = [t for t in ticks
                 if not (lo - pad <= t.ts <= hi + pad)   # purge：重叠窗内剔除
                 and not (em_lo <= t.ts < lo - pad)]     # embargo：测试窗前缓冲窗剔除
        res = runner(train)
        results[f"fold_{k}"] = evaluate(res)
        results[f"fold_{k}"]["n_train_ticks"] = float(len(train))
    if folds > 0:
        keys = [kk for kk in results["fold_0"] if kk != "n_train_ticks"]
        mean: Dict[str, object] = {}
        for kk in keys:
            vals = [results[f"fold_{i}"][kk] for i in range(folds)]
            if isinstance(vals[0], list):
                # 分布型字段（如 兑现率P25/P50/P75%）按列元素均值
                mean[kk] = [round(sum(col) / folds, 1) for col in zip(*vals)]
            else:
                mean[kk] = sum(vals) / folds
        results["mean"] = mean
    return results


def np_split(lst: List, k: int) -> List[List]:
    """无 numpy 的均分切片。"""
    n = len(lst)
    size = max(n // k, 1)
    return [lst[i * size:(i + 1) * size] for i in range(k) if i * size < n]


# ---------------- 每单档案打印（峰值审计） ----------------

def print_order_archive(res: ReplayResult, max_rows: int = 20) -> None:
    print(f"\n===== 每单档案（{res.name}）=====")
    print(f"{'#':<4}{'dir':<5}{'原因':<24}{'净R':<8}{'峰值R':<8}{'兑现率%':<8}"
          f"{'持仓分':<8}{'分批':<5}")
    for i, o in enumerate(res.orders[:max_rows]):
        reason = o.exit_reason
        if len(reason) > 22:
            reason = reason[:22] + "..."
        print(f"{i + 1:<4}{'+' if o.direction > 0 else '-'}{'':<4}"
              f"{reason:<24}{o.net_r:<8.2f}{o.peak_r:<8.2f}"
              f"{100 * o.capture_ratio:<8.1f}{o.holding_sec / 60:<8.1f}{o.partials:<5}")
    if len(res.orders) > max_rows:
        print(f"  ... 共 {len(res.orders)} 单，省略 {len(res.orders) - max_rows} 单")