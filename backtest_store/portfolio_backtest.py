"""组合历史回测：把「启用因子 + 多周期方向硬过滤 + 实盘 sltp 策略」作为整体在历史 K 线上验证。

只读离线任务：
- 不碰实盘状态/持仓/配置，不写业务库，结果仅返回调用方；
- 信号判定只依赖 t 及之前数据（各周期已收盘 K），出场依赖后续 bars（与实盘一致，出场本来就该看未来）；
- 与挖掘的样本外切分不同：这里是全量历史组合验证，回答「当前这组配置在历史上整体赚不赚钱」。

诚实口径：硬过滤只做「方向类」（主层方向 trends / H4 宏方向 macro_directions / D1 方向 mtf_directions.d1）；
波动/量能类过滤无法逐 bar 精确重现，不参与回测判定。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pandas as pd

from backtest_store.engine import Backtester
from factor_mining.miner import _point_size
from indicators.technical import atr_series
from sandbox.runner import execute_factor


def _ema(close: pd.Series, span: int = 50) -> pd.Series:
    return close.ewm(span=span, adjust=False).mean()


def _dir_at(c: float, e: float, atr: float | None) -> str:
    """单 bar 方向：EMA50 ± 0.5×ATR 死区（黄金自适应）；ATR 缺省时 0.5% 保底。"""
    dead = 0.5 * atr if atr and atr > 0 else c * 0.005
    if c > e + dead:
        return "up"
    if c < e - dead:
        return "down"
    return "flat"


def consensus_for(main_dir: str, layers: list[dict[str, Any]]) -> str:
    """浓缩版多周期共识（与多周期引擎同口径）：
    主层 flat → 方向由首个非 flat 大层定义；大层与主层同向 → 明确；
    有方向但无同向 → 矛盾；全无 → 不明。
    layers 按大层升序（M15 → H1 → H4 → D1）。
    """
    if main_dir == "flat":
        for l in layers:
            if l["direction"] in ("up", "down"):
                return f"方向由{l['key']}定义"
        return "不明"
    for l in layers:
        if l["direction"] == "flat":
            continue
        if l["direction"] == main_dir:
            return "明确"
    if any(l["direction"] in ("up", "down") for l in layers):
        return "矛盾"
    return "不明"


class PortfolioBacktestJob:
    """一轮组合历史回测任务：进度可查（status），完成后 result 含全量指标与按共识分组的盈亏。"""

    def __init__(
        self,
        symbol: str,
        timeframe: str,
        repository: Any,
        gateway: Any,
        date_from: Any = None,
        date_to: Any = None,
        market_filter: dict[str, Any] | None = None,
        sltp_policy: dict[str, Any] | None = None,
        max_factors: int = 200,
        executor: Any = None,
    ) -> None:
        self.symbol = symbol
        self.timeframe = timeframe
        self.repository = repository
        self.gateway = gateway
        self.date_from = date_from
        self.date_to = date_to
        self.market_filter = market_filter or {}
        self.sltp_policy = sltp_policy or {}
        self.max_factors = int(max_factors)
        self.executor = executor or execute_factor
        self.progress: dict[str, Any] = {
            "running": False,
            "symbol": symbol,
            "timeframe": timeframe,
            "message": "未开始",
            "started_at": None,
            "finished_at": None,
            "result": None,
        }

    @property
    def status(self) -> dict[str, Any]:
        return dict(self.progress)

    async def run(self) -> dict[str, Any]:
        self.progress.update({"running": True, "message": "拉取历史行情…", "started_at": time.strftime("%Y-%m-%d %H:%M:%S")})
        try:
            tfs = ["M15", "H1", "H4", "D1"]
            tf_data: dict[str, dict[str, Any]] = {}
            anchor_df: pd.DataFrame | None = None
            anchor_key = str(self.timeframe).upper()
            for tf in [anchor_key] + tfs:
                bars: list[dict[str, Any]] = []
                try:
                    if self.date_from is not None and hasattr(self.gateway, "get_rates_range"):
                        bars = self.gateway.get_rates_range(self.symbol, tf, self.date_from, self.date_to) or []
                    if not bars:
                        bars = self.gateway.get_rates(self.symbol, tf, 3000) or []
                except Exception:
                    bars = []
                if not bars:
                    continue
                d = pd.DataFrame(bars)
                for col in ("open", "high", "low", "close", "volume"):
                    d[col] = d[col].astype(float)
                d = d.set_index(pd.to_datetime(d["time"]))
                d = d[~d.index.duplicated(keep="last")].sort_index()
                ema = _ema(d["close"], 50)
                atr = atr_series(d, 14).dropna()
                tf_data[tf] = {"close": d["close"], "ema": ema, "atr": atr}
                if tf == anchor_key:
                    anchor_df = d
            if anchor_df is None or len(anchor_df) < 200:
                self.progress.update(
                    {"running": False, "message": f"数据不足（{0 if anchor_df is None else len(anchor_df)} 根 < 200），终止", "finished_at": time.strftime("%Y-%m-%d %H:%M:%S")}
                )
                return self.status

            idx = anchor_df.index
            # 各周期按锚 index 前向对齐（每 bar 时刻取该层最新已收盘值）
            aligned: dict[str, dict[str, Any]] = {}
            for tf, info in tf_data.items():
                aligned[tf] = {
                    "close": info["close"].reindex(idx, method="ffill"),
                    "ema": info["ema"].reindex(idx, method="ffill"),
                    "atr": info["atr"].reindex(idx, method="ffill"),
                }
            # 方向序列（每层整列）
            direction_seq: dict[str, pd.Series] = {}
            for tf, a in aligned.items():
                direction_seq[tf] = pd.Series(
                    [_dir_at(float(c), float(e), None if pd.isna(at) else float(at)) for c, e, at in zip(a["close"], a["ema"], a["atr"])],
                    index=idx,
                )
            main_dir_seq = direction_seq[anchor_key]

            self.progress["message"] = "逐因子执行信号…"
            loop = asyncio.get_running_loop()
            bars_all: list[dict[str, Any]] = []
            try:
                if self.date_from is not None and hasattr(self.gateway, "get_rates_range"):
                    bars_all = self.gateway.get_rates_range(self.symbol, anchor_key, self.date_from, self.date_to) or []
                if not bars_all:
                    bars_all = self.gateway.get_rates(self.symbol, anchor_key, 3000) or []
            except Exception:
                bars_all = []

            factors = []
            try:
                factors = (self.repository.list_factors(status="active") or [])[: self.max_factors]
            except Exception:
                factors = []

            merged = pd.Series(0.0, index=idx)
            used_factors = 0
            for f in factors:
                try:
                    execution = await loop.run_in_executor(None, self.executor, f.code, bars_all, f.params)
                except Exception:
                    continue
                if not getattr(execution, "ok", False) or not execution.entry_values:
                    continue
                sig = pd.Series([float(v) if v is not None else 0.0 for v in execution.entry_values], index=idx).fillna(0.0)
                stronger = sig.abs() > merged.abs()
                merged = merged.where(~stronger, sig)
                used_factors += 1

            self.progress["message"] = "应用多周期方向过滤…"
            mf = self.market_filter or {}
            allowed_trends = mf.get("trends") or []
            allowed_macro = mf.get("macro_directions") or []
            mtf_d1 = (mf.get("mtf_directions") or {}).get("d1") or []
            keep: list[bool] = []
            for i in range(len(idx)):
                main_dir = str(main_dir_seq.iloc[i])
                h4_dir = str(direction_seq["H4"].iloc[i]) if "H4" in direction_seq else "flat"
                d1_dir = str(direction_seq["D1"].iloc[i]) if "D1" in direction_seq else "flat"
                ok = True
                if allowed_trends and main_dir not in allowed_trends:
                    ok = False
                if allowed_macro and h4_dir not in allowed_macro:
                    ok = False
                if mtf_d1 and d1_dir not in mtf_d1:
                    ok = False
                keep.append(ok)
            pass_mask = pd.Series(keep, index=idx)
            filtered = merged.where(pass_mask, 0.0)

            self.progress["message"] = "组合回测（实盘 sltp 策略口径）…"
            backtest: dict[str, Any] | None = None
            try:
                bt_params = {
                    "sltp": self.sltp_policy or {},
                    "spread_points": 10,
                    "point_size": _point_size(self.symbol),
                }
                result = await loop.run_in_executor(None, Backtester().run, anchor_df, filtered, None, bt_params)
                if getattr(result, "ok", False):
                    m = result.metrics
                    backtest = {
                        "total_return_pct": m.total_return_pct,
                        "annual_return_pct": m.annual_return_pct,
                        "sharpe": m.sharpe,
                        "max_drawdown_pct": m.max_drawdown_pct,
                        "win_rate_pct": m.win_rate_pct,
                        "profit_factor": m.profit_factor,
                        "num_trades": m.num_trades,
                        "avg_trade_pct": m.avg_trade_pct,
                        "trades": [t.model_dump(mode="json") for t in (result.trades or [])],
                    }
            except Exception:
                backtest = None

            self.progress["message"] = "按多周期共识分组统计…"
            by_consensus: dict[str, dict[str, Any]] = {}
            if backtest:
                for t in backtest["trades"]:
                    tt_raw = t.get("entry_time") or t.get("time")
                    cc = "未知"
                    if tt_raw:
                        try:
                            pos = int(idx.get_indexer([pd.Timestamp(tt_raw)], method="ffill")[0])
                            if pos >= 0:
                                main_dir = str(main_dir_seq.iloc[pos])
                                layers = [
                                    {"key": k, "direction": str(direction_seq[k].iloc[pos])}
                                    for k in ("M15", "H1", "H4", "D1")
                                    if k in direction_seq
                                ]
                                cc = consensus_for(main_dir, layers)
                        except Exception:
                            cc = "未知"
                    g = by_consensus.setdefault(cc, {"trades": 0, "return_pct": 0.0})
                    g["trades"] += 1
                    g["return_pct"] = round(g["return_pct"] + float(t.get("pnl_pct") or 0.0), 2)

            self.progress.update(
                {
                    "running": False,
                    "message": f"组合回测完成：{used_factors} 个因子 · {backtest.get('num_trades', 0) if backtest else 0} 笔交易",
                    "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "result": {
                        "symbol": self.symbol,
                        "timeframe": self.timeframe,
                        "bars": len(anchor_df),
                        "factors_used": used_factors,
                        "market_filter": {
                            "trends": list(allowed_trends),
                            "macro_directions": list(allowed_macro),
                            "mtf_d1": list(mtf_d1),
                        },
                        "sltp": {k: self.sltp_policy.get(k) for k in ("risk_mult", "take_r_mult", "time_stop_bars") if k in self.sltp_policy},
                        "summary": backtest,
                        "by_consensus": by_consensus,
                    },
                }
            )
        except Exception as exc:
            self.progress.update(
                {"running": False, "message": f"组合回测失败：{exc}", "finished_at": time.strftime("%Y-%m-%d %H:%M:%S")}
            )
        return self.status