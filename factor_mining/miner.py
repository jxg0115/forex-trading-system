"""因子挖掘主流程：候选生成 → 沙盒执行 → 有效性评估 → 回测（sltp R/ATR 口径）→ 候选池。

挖掘出的候选默认停留在候选池（status=pending，不自动入库）；用户审阅后经
/api/mining/accept 保存为正式因子（factors 表，source=mining），或 ignore 忽略。
出场因子（kind=exit）只做有效性评估（出口正确性 = 出场后价格反向），不做独立回测
（回测需与入场因子搭配）。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pandas as pd

from backtest_store.engine import Backtester
from factor_mining.evaluator import (
    deflated_sharpe,
    evaluate_signal,
    harvey_t_required,
    t_ratio,
    walk_forward,
)
from factor_mining.generator import generate_candidates
from sandbox.runner import execute_factor
from oems.sltp_policy import SltpPolicyConfig


def _clean(value: Any, default: float = 0.0) -> float:
    """清洗评估数值：NaN/None/非数值一律回退默认（避免写入 SQLite NOT NULL 列的 NaN 变 NULL）。"""
    try:
        f = float(value)
        return f if f == f else default
    except Exception:
        return default


def _point_size(symbol: str) -> float:
    """按品种推断 1 点的价格单位（成本建模用）：JPY 0.01 / 黄金白银 0.1 / 指数&加密 1.0 / 其余 0.0001。"""
    up = str(symbol or "").upper()
    if "JPY" in up:
        return 0.01
    if "XAU" in up or "XAG" in up:
        return 0.1
    if any(x in up for x in ("US30", "NAS", "SPX", "SP500", "GER", "UK100", "DAX", "BTC", "ETH")):
        return 1.0
    return 0.0001


def _params_similar(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """参数近似判定（同族去重）：键集合一致；数值差 ≤ max(1, 5%)；枚举/布尔/字符串需相等。"""
    if set(a) != set(b):
        return False
    for k in a:
        va, vb = a[k], b[k]
        if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
            if abs(float(va) - float(vb)) > max(1.0, 0.05 * max(abs(float(va)), abs(float(vb)))):
                return False
        elif va != vb:
            return False
    return True


def _dedupe_similar(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """去相关去重：同模板且参数近似（同族）只保留综合评分最高者，减少"族效应"假阳性。"""
    groups: list[list[dict[str, Any]]] = []
    for r in records:
        target = None
        for group in groups:
            if group[0]["template"] == r["template"] and _params_similar(group[0]["params"], r["params"]):
                target = group
                break
        if target is None:
            groups.append([r])
        else:
            target.append(r)
    return [max(g, key=lambda x: float(x.get("score") or 0.0)) for g in groups]


def _composite_score(kind: str, pf: float, ret: float, win: float, t: float, wf_level: str, signal_bars: int) -> float:
    """收益导向综合评分（0-100）：入场 = PF/样本外收益/胜率/t 显著性/一致性；出场 = t/一致性/信号数。"""
    if kind == "entry":
        base = (
            0.35 * min(max((pf - 1.0) / 1.0, 0.0), 1.0)
            + 0.25 * min(max(ret / 10.0, 0.0), 1.0)
            + 0.15 * min(win / 60.0, 1.0)
            + 0.15 * min(max((abs(t) - 2.0) / 2.0, 0.0), 1.0)
            + 0.10 * (1.0 if wf_level == "稳定" else (0.55 if wf_level == "一般" else 0.0))
        )
    else:
        base = (
            0.40 * min(max((abs(t) - 2.0) / 2.0, 0.0), 1.0)
            + 0.30 * (1.0 if wf_level == "稳定" else (0.55 if wf_level == "一般" else 0.0))
            + 0.30 * min(signal_bars / 100.0, 1.0)
        )
    return round(base * 100.0, 2)


def _to_df(bars: list[dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(bars)
    if df.empty:
        return df
    if "time" in df:
        try:
            df = df.set_index(pd.to_datetime(df["time"]))
        except Exception:
            df = df.reset_index(drop=True)
    return df


class MiningJob:
    """一轮因子挖掘任务：进度可查（status），完成后候选落库为 pending。"""

    def __init__(
        self,
        symbol: str,
        timeframe: str,
        repository: Any,
        gateway: Any = None,
        max_candidates: int = 2000,
        include_structures: bool = True,
        sltp_policy: dict[str, Any] | None = None,
        executor: Any = None,
        date_from: Any = None,
        date_to: Any = None,
        keep_ungated: bool = False,   # True=未达门槛候选也入库（gate=ungated 供审阅）；False=只收达门槛候选
        spread_points: float = 10.0,  # 点差成本（价格点，默认 10 点 = 1 pip；0=不计点差）
        point_size: float | None = None,  # 1 点的价格单位；None=按品种自动推断
    ) -> None:
        self.symbol = symbol
        self.timeframe = timeframe
        self.repository = repository
        self.gateway = gateway
        self.max_candidates = int(max_candidates)
        self.include_structures = include_structures
        self.sltp_policy = sltp_policy or SltpPolicyConfig().to_dict()
        # 执行器可注入：生产默认 execute_factor（沙盒隔离）；测试/特殊环境可传同进程执行器
        self.executor = executor or execute_factor
        # 时间范围可选：给定后按 [date_from, date_to] 取数；默认取最近 3000 根
        self.date_from = date_from
        self.date_to = date_to
        self.keep_ungated = bool(keep_ungated)
        self.spread_points = max(float(spread_points), 0.0)
        self.point_size = float(point_size) if point_size is not None else _point_size(symbol)
        # 停止/暂停控制：由 /api/mining/stop（/pause、/resume）置位，run() 循环在候选边界响应
        self.cancelled = False
        self.paused = False
        self.progress: dict[str, Any] = {
            "running": False,
            "symbol": symbol,
            "timeframe": timeframe,
            "date_from": date_from,
            "date_to": date_to,
            "total": 0,
            "done": 0,
            "ok": 0,
            "gated": 0,
            "ungated": 0,
            "paused": False,
            "cancelled": False,
            "message": "未开始",
            "started_at": None,
            "finished_at": None,
        }

    @property
    def status(self) -> dict[str, Any]:
        status = dict(self.progress)
        total = status.get("total") or 0
        done = status.get("done") or 0
        status["percent"] = round(done * 100.0 / total, 1) if total else 0.0
        # 暂停标志以属性为准（/pause、/resume 直接改属性；循环边界的字典是辅助展示）
        status["paused"] = self.paused
        return status

    async def run(self) -> dict[str, Any]:
        self.progress.update({"running": True, "message": "拉取行情数据…", "started_at": time.strftime("%Y-%m-%d %H:%M:%S")})
        try:
            # 取数：指定了时间范围 → 按 [date_from, date_to] 取（策略测试器式回测窗口）；
            # 未指定 → 取最近 3000 根
            if self.gateway:
                if self.date_from is not None and hasattr(self.gateway, "get_rates_range"):
                    bars = self.gateway.get_rates_range(self.symbol, self.timeframe, self.date_from, self.date_to)
                else:
                    bars = self.gateway.get_rates(self.symbol, self.timeframe, 3000)
            else:
                bars = []
            if not bars:
                self.progress.update({"running": False, "message": "无行情数据，终止（检查时间范围或网关连接）", "finished_at": time.strftime("%Y-%m-%d %H:%M:%S")})
                return self.status
            df = _to_df(bars)
            if len(df) < 200:
                self.progress.update({"running": False, "message": f"数据不足（{len(df)} 根 < 200），终止", "finished_at": time.strftime("%Y-%m-%d %H:%M:%S")})
                return self.status
            # 样本外切分：前段评估（IC/命中率/行情分组），后段（≥30%，最多 500 根）做样本外回测
            total_n = len(df)
            bt_n = min(max(int(total_n * 0.3), 100), 500)
            split = total_n - bt_n
            eval_df = df.iloc[:split]
            bt_df = df.iloc[split:]

            candidates = generate_candidates(max_candidates=self.max_candidates, include_structures=self.include_structures)
            total = len(candidates)
            self.progress.update({"total": total, "message": f"评估 {total} 个候选…"})

            records: list[dict[str, Any]] = []
            bt = Backtester()
            loop = asyncio.get_running_loop()
            for idx, cand in enumerate(candidates):
                if self.cancelled:
                    break
                self.progress["done"] = idx + 1
                self.progress["current"] = f"{cand['name']}（{cand['variant']}）"
                # 暂停：在候选边界挂起；暂停期间点了停止则退出
                while self.paused and not self.cancelled:
                    await asyncio.sleep(0.5)
                if self.cancelled:
                    break
                try:
                    execution = await loop.run_in_executor(
                        None, self.executor, cand["code"], bars, cand["params"]
                    )
                except Exception:
                    continue
                if not getattr(execution, "ok", False):
                    continue

                if cand["kind"] == "entry":
                    entry = pd.Series(execution.entry_values, index=df.index).fillna(0.0)
                    eval_out = evaluate_signal(entry.iloc[:split], eval_df, forward_bars=5, kind="entry")
                    wf = walk_forward(entry.iloc[:split], eval_df, forward_bars=5, kind="entry", segments=4)
                    backtest_stats: dict[str, Any] | None = None
                    bt_params = {
                        "sltp": self.sltp_policy,
                        "spread_points": self.spread_points,
                        "point_size": self.point_size,
                    }
                    try:
                        result = await loop.run_in_executor(
                            None, bt.run, bt_df, entry.iloc[split:], None, bt_params
                        )
                        if getattr(result, "ok", False):
                            m = result.metrics
                            # 样本外分段收益（后 30% 段再分 2 段，看样本外内是否稳健）
                            out_segments: list[float] = []
                            if len(bt_df) >= 200:
                                half = len(bt_df) // 2
                                for seg_df in (bt_df.iloc[:half], bt_df.iloc[half:]):
                                    try:
                                        seg_res = await loop.run_in_executor(
                                            None, bt.run, seg_df, entry.iloc[split:].reindex(seg_df.index),
                                            None, bt_params,
                                        )
                                        if getattr(seg_res, "ok", False) and seg_res.metrics is not None:
                                            out_segments.append(round(float(seg_res.metrics.total_return_pct), 2))
                                        else:
                                            out_segments.append(0.0)
                                    except Exception:
                                        out_segments.append(0.0)
                            backtest_stats = {
                                "total_return_pct": m.total_return_pct,
                                "annual_return_pct": m.annual_return_pct,
                                "sharpe": m.sharpe,
                                "max_drawdown_pct": m.max_drawdown_pct,
                                "win_rate_pct": m.win_rate_pct,
                                "profit_factor": m.profit_factor,
                                "num_trades": m.num_trades,
                                "avg_trade_pct": m.avg_trade_pct,
                                "out_segments": out_segments,  # 样本外 2 段收益
                                # 逐笔明细（样本外回测段），用于查看回测数据
                                "trades": [t.model_dump(mode="json") for t in (result.trades or [])],
                            }
                    except Exception:
                        backtest_stats = None
                else:  # 出场因子：只评估出口正确性（与入场因子搭配使用，不做独立回测）
                    exit_sig = pd.Series(execution.exit_values, index=df.index).fillna(0.0)
                    eval_out = evaluate_signal(exit_sig.iloc[:split], eval_df, forward_bars=3, kind="exit")
                    wf = walk_forward(exit_sig.iloc[:split], eval_df, forward_bars=3, kind="exit", segments=4)
                    backtest_stats = None

                self.progress["ok"] += 1

                # ---- 收益导向硬门槛（gate）：入场看 样本外收益/PF/笔数/一致性/t；出场看 信号数/一致性/t ----
                gate, gate_reason = "ungated", ""
                wf_level = str((wf or {}).get("level") or "")
                ic_t = abs(float(eval_out.get("ic_tstat") or 0.0))
                sample_note = ""
                if cand["kind"] == "entry":
                    bt0 = backtest_stats or {}
                    pf = float(bt0.get("profit_factor") or 0.0)
                    ret = float(bt0.get("total_return_pct") or 0.0)
                    nt = int(bt0.get("num_trades") or 0)
                    reasons = []
                    if pf < 1.2:
                        reasons.append(f"PF={pf:.2f}<1.2")
                    if ret <= 0:
                        reasons.append("样本外收益≤0")
                    if nt < 30:
                        reasons.append(f"仅{nt}笔<30")
                    if wf_level not in ("稳定", "一般"):
                        reasons.append(f"一致性{wf_level}")
                    if ic_t < 2.0:
                        reasons.append(f"|t|={ic_t:.2f}<2")
                    gate_reason = "，".join(reasons)
                    if not gate_reason:
                        gate = "passed"
                    if nt and nt < 30:
                        sample_note = f"样本外仅 {nt} 笔（<30），胜率/盈亏比统计不可靠——建议加大时间范围或换更大周期再验证"
                else:
                    sb = int(eval_out.get("signal_bars") or 0)
                    reasons = []
                    if sb < 50:
                        reasons.append(f"信号仅{sb}次<50")
                    if wf_level not in ("稳定", "一般"):
                        reasons.append(f"一致性{wf_level}")
                    if ic_t < 2.0:
                        reasons.append(f"|t|={ic_t:.2f}<2")
                    gate_reason = "，".join(reasons)
                    if not gate_reason:
                        gate = "passed"
                    if sb and sb < 50:
                        sample_note = f"信号仅 {sb} 次（<50），统计不可靠"

                if gate == "passed":
                    self.progress["gated"] += 1
                else:
                    self.progress["ungated"] += 1
                    if not self.keep_ungated:
                        continue  # 未勾选「保留未达门槛候选」→ 拦截，不入池（不再清一色 PF<1 堆积）

                # ---- 多重检验修正标注：Harvey 修正 t 门槛 / t 比值 / DSR（过拟合概率） ----
                t_required = harvey_t_required(total)
                t_adj = t_ratio(float(eval_out.get("ic_tstat") or 0.0), total)
                dsr = 0.5
                if cand["kind"] == "entry" and backtest_stats:
                    pnls = [float(td.get("pnl_pct") or 0.0) for td in (backtest_stats.get("trades") or [])]
                    dsr = deflated_sharpe(pnls, total)

                # ---- 收益导向综合评分（0-100）：score 改复合分，原 IC 分保留在 score_ic ----
                if cand["kind"] == "entry":
                    bt0 = backtest_stats or {}
                    comp = _composite_score(
                        "entry",
                        float(bt0.get("profit_factor") or 0.0),
                        float(bt0.get("total_return_pct") or 0.0),
                        float(bt0.get("win_rate_pct") or 0.0),
                        float(eval_out.get("ic_tstat") or 0.0),
                        wf_level,
                        0,
                    )
                else:
                    comp = _composite_score(
                        "exit",
                        0.0, 0.0, 0.0,
                        float(eval_out.get("ic_tstat") or 0.0),
                        wf_level,
                        int(eval_out.get("signal_bars") or 0),
                    )

                records.append(
                    {
                        "signature": cand["signature"],
                        "template": cand["template"],
                        "name": cand["name"],
                        "kind": cand["kind"],
                        "variant": cand["variant"],
                        "params": cand["params"],
                        "code": cand["code"],
                        "symbol": self.symbol,
                        "timeframe": self.timeframe,
                        "ic": _clean(eval_out.get("ic")),
                        "icir": _clean(eval_out.get("icir")),
                        "ic_tstat": _clean(eval_out.get("ic_tstat")),
                        "n_pairs": int(eval_out.get("n_pairs") or 0),
                        "hit_rate": _clean(eval_out.get("hit_rate")),
                        "score": comp,                               # 收益导向综合分（排序用）
                        "score_ic": _clean(eval_out.get("score")),    # 原 IC 分（参考）
                        "signal_bars": int(eval_out.get("signal_bars") or 0),
                        "wf": wf,
                        "sample_note": sample_note,
                        "groups": eval_out.get("groups") or {},
                        "gate": gate,
                        "gate_reason": gate_reason,
                        "dsr": round(dsr, 4),
                        "t_adj": round(t_adj, 4),
                        "t_required": round(t_required, 3),
                        "eval_bars": split,
                        "bt_bars": bt_n,
                        "backtest": backtest_stats,
                        "status": "pending",
                    }
                )

            # 去相关去重：同模板且参数近似（同族）只保留综合评分最高者，减少族效应假阳性
            records = _dedupe_similar(records)

            if self.cancelled:
                # 停止：保留已评估成果（已攒的候选落库），进度标注为已停止
                if records:
                    self.repository.save_mining_records(records)
                self.progress.update(
                    {
                        "running": False,
                        "cancelled": False,
                        "paused": False,
                        "message": f"已停止：{len(records)}/{total} 个候选入库（达门槛 {self.progress['gated']} · 未达 {self.progress['ungated']}）",
                        "current": "",
                        "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    }
                )
                return self.status
            if records:
                self.repository.save_mining_records(records)
            self.progress.update(
                {
                    "running": False,
                    "message": f"完成：{len(records)}/{total} 个候选入库（达门槛 {self.progress['gated']} · 未达 {self.progress['ungated']}）：前 {split} 根评估 + 后 {bt_n} 根样本外回测（含成本），评分=收益导向复合分（PF≥1.2 · 笔数≥30 · 一致性 · |t|≥2）",
                    "current": "",
                    "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
            )
        except Exception as exc:
            self.progress.update(
                {"running": False, "message": f"挖掘失败：{exc}", "finished_at": time.strftime("%Y-%m-%d %H:%M:%S")}
            )
        return self.status


async def incremental_review(repository: Any, gateway: Any = None) -> dict[str, Any]:
    """每日增量重估：对已入库的挖掘因子重新评估 IC，明显衰减的标记 stale（保留）——第一版只做标记。"""
    return {"reviews": 0, "message": "增量重估接口就绪（首版仅做占位，供 /api/mining/review 调用）"}