# -*- coding: utf-8 -*-
"""数据轮盘两层复测（monitor 模式）：候选 = swing_break n=100 + 趋势段门控 + 延迟3。

层1 固定续测：最后 30%（≈1.2 年）独立验证；
层2 数据轮盘：全 3.4 年按信号频率自适应窗口无重叠切段，随机抽全部段独立验证，输出 PF 分布+通过率。
验收：层1 达标（PF>=1.2 且 n>=30）且 层2 通过率 >=70% -> 评估入库；否则继续等数据。
红线：只验证不筛选（候选固定；轮盘只算稳健度，不用于选参数）。
"""

import json
import sys
import urllib.request

import numpy as np
import pandas as pd

sys.path.insert(0, r"C:\Users\xg\Documents\外汇交易系统-dev")

from backtest_store.engine import Backtester  # noqa: E402
from indicators.technical import atr_series  # noqa: E402

BASE = "http://127.0.0.1:8003"


def get(path, params=None):
    url = BASE + path
    if params:
        url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def swing_signal(df, n=100):
    hi = df["high"].shift(1).rolling(n).max()
    lo = df["low"].shift(1).rolling(n).min()
    sig = pd.Series(0.0, index=df.index)
    sig[df["close"] > hi] = 1.0
    sig[df["close"] < lo] = -1.0
    return sig


def trend_gate(df):
    close = df["close"]
    ema20 = close.ewm(span=20, adjust=False).mean()
    ema60 = close.ewm(span=60, adjust=False).mean()
    choppy = ((ema20 - ema60).abs() / close) < 0.005
    return ~choppy


def candidate_entry(df):
    """固定候选：swing n=100 + 趋势段门控 + 延迟3。"""
    base = swing_signal(df, 100).where(trend_gate(df), 0.0)
    return base.shift(3).fillna(0.0)


def run_backtest(df, entry):
    bt = Backtester()
    r = bt.run(df, entry, None, {"symbol": "GOLD", "sltp": {"risk_mult": 2.0, "take_r_mult": 3.0, "time_stop_bars": 120}})
    trades = r.trades
    wins = [t for t in trades if t.pnl > 0]
    m = r.metrics
    return {
        "n": len(trades),
        "win": round(len(wins) / max(len(trades), 1) * 100.0, 1),
        "pf": round(m.profit_factor if m else 0.0, 2),
        "ret": round(m.total_return_pct if m else 0.0, 2),
    }


def main():
    rates = get("/api/mt5/klines", {"symbol": "GOLD", "timeframe": "H1", "count": 20000})
    bars = rates.get("bars") or rates.get("rates") or []
    df = pd.DataFrame(bars)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df = df.set_index("time").sort_index()
    df = df.dropna(subset=["open", "high", "low", "close"])
    print(f"bars: {len(df)} | {df.index[0]} -> {df.index[-1]}")

    entry = candidate_entry(df)

    # 层1：固定续测（最后 30%）
    split = int(len(df) * 0.7)
    df_fix = df.iloc[split:]
    r1 = run_backtest(df_fix, entry.iloc[split:])
    ok1 = r1["pf"] >= 1.2 and r1["n"] >= 30
    print(f"\n[层1 固定续测] 后30%({len(df_fix)}根): n={r1['n']} win={r1['win']}% PF={r1['pf']} ret={r1['ret']}% -> {'达标' if ok1 else '不达标'}")

    # 层2：数据轮盘（窗口按信号频率自适应：每窗口需 >=30 笔信号；窗口过长则降级分年段）
    sig_count = int((entry != 0).sum())
    avg_gap = len(df) / max(sig_count, 1)
    seg_len = int(30 * avg_gap * 1.2)  # 30 笔 x 平均间隔 x 安全系数
    seg_len = max(800, min(seg_len, 12000))
    print(f"信号数={sig_count} 平均间隔={avg_gap:.0f} 根 -> 自适应窗口={seg_len} 根({seg_len/len(df)*len(df)/24/21:.1f} 月)")
    n_seg = len(df) // seg_len
    segs = [df.iloc[i * seg_len : (i + 1) * seg_len] for i in range(n_seg)]
    rng = np.random.default_rng(42)
    picked = rng.choice(n_seg, size=min(20, n_seg), replace=False)
    results = []
    for i in picked:
        seg = segs[i]
        r = run_backtest(seg, entry.reindex(seg.index))
        results.append({"seg": i, "range": f"{seg.index[0].date()}~{seg.index[-1].date()}", "n": r["n"], "pf": r["pf"], "win": r["win"]})
    rd = pd.DataFrame(results)
    passed = rd[(rd["pf"] >= 1.2) & (rd["n"] >= 30)]
    rate = len(passed) / len(rd) * 100 if len(rd) else 0.0
    print(f"\n[层2 数据轮盘] {len(rd)} 个无重叠自适应窗口（随机抽）:")
    for _, row in rd.iterrows():
        star = " ★" if (row["pf"] >= 1.2 and row["n"] >= 30) else ""
        print(f"  {row['range']} | n={row['n']:>3} win={row['win']:5.1f}% PF={row['pf']:5.2f}{star}")
    print(f"\nPF 分布: 中位={rd['pf'].median():.2f} | 25%={rd['pf'].quantile(0.25):.2f} | 75%={rd['pf'].quantile(0.75):.2f}")
    print(f"n 分布: 中位={int(rd['n'].median())} | 最小={int(rd['n'].min())}")
    print(f"通过率(>=1.2 且 n>=30): {rate:.0f}% ({len(passed)}/{len(rd)})")

    ok2 = rate >= 70
    verdict = "评估入库" if (ok1 and ok2) else "继续等数据"
    print(f"\n=== 验收: 层1={'达标' if ok1 else '不达标'} 层2通过率={rate:.0f}%(需>=70%) -> {verdict} ===")


if __name__ == "__main__":
    main()