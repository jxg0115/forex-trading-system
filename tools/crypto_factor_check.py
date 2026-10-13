"""第 0 期验证：把现有因子库搬到加密数据上跑回测，回答「能不能直接用」。

做法：
1. 从因子库读出所有 active 因子（原生成品种多为 XAUUSD/M15）；
2. 在 Binance 现货 BTC/ETH/SOL 的同周期数据上用同一套沙盒执行，得到 entry/exit；
3. 用项目自己的 Backtester 结算（加密成本口径：taker 0.1% 单边，point_size=1.0）；
4. 与因子库里保存的原品种回测成绩对照。

判定：若多数因子在加密数据上 num_trades 极少或 profit_factor < 1，
说明「因子过拟合到黄金」，接入后必须重新挖掘，不能平移。
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backtest_store.engine import Backtester          # noqa: E402
from backtest_store.repository import get_repository   # noqa: E402
from sandbox.runner import execute_factor              # noqa: E402

DATA_DIR = ROOT / "data" / "crypto"
OUT_CSV = ROOT / "tools" / "crypto_factor_check.csv"

# 加密成本口径（Binance/OKX 现货标准档 taker 0.1% 单边）
CRYPTO_COST: dict[str, Any] = {
    "commission_pct": 0.001,
    "point_size": 1.0,
    "spread_points": 0.0,
    "slippage_price": 0.0,
    "leverage": 10,
    "round_trip_cost": True,
}
# 黄金对照口径（与 costs.py 的 GOLD 默认一致：点差 10 点、滑点 0.02、佣金 0.01%）
GOLD_COST: dict[str, Any] = {
    "commission_pct": 0.0001,
    "point_size": 0.1,
    "spread_points": 10.0,
    "slippage_price": 0.02,
    "leverage": 30,
    "round_trip_cost": True,
}
GOLD_ALIASES = {"XAUUSD", "GOLD", "XAUUSDm"}
# 外汇 24/5（一年约 250 个交易日），加密 24/7
BARS_PER_YEAR_FX = {"M15": 24_000, "M30": 12_000, "H1": 6_000}
BARS_PER_YEAR_CRYPTO = {"M15": 35_040, "M30": 17_520, "H1": 8_760}


def load_dataset(alias: str, tf: str, max_bars: int, offset: int = 0) -> list[dict[str, Any]]:
    """offset = 从末尾往前跳过的根数，用于选取更早的时间窗口（如币价下跌段）。"""
    path = DATA_DIR / f"{alias}_{tf}.json"
    if not path.exists():
        return []
    bars = json.loads(path.read_text(encoding="utf-8"))
    if offset:
        bars = bars[:-offset] if offset < len(bars) else []
    return bars[-max_bars:] if max_bars and len(bars) > max_bars else bars


def to_frame(bars: list[dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(bars)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return df.set_index("time")


def _series(values: list[Any] | None, index: pd.Index) -> pd.Series:
    """因子常常只产出入场信号、不产出出场信号（沙盒返回空列表），这里补齐为全 0。"""
    if not values or len(values) != len(index):
        return pd.Series(0.0, index=index)
    return pd.Series(values, index=index).fillna(0.0)


def baseline_stats(f: Any) -> dict[str, Any]:
    """因子库里保存的原品种回测成绩（metrics 可能嵌在 ok/metrics 下）。"""
    stats = getattr(f, "backtest_stats", None) or {}
    if not isinstance(stats, dict):
        return {}
    return stats.get("metrics") or stats


def fmt(v: Any, nd: int = 2, dash: str = "-") -> str:
    if v is None:
        return dash
    try:
        return f"{float(v):.{nd}f}"
    except (TypeError, ValueError):
        return dash


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-bars", type=int, default=6000, help="每个数据集使用的根数（取最近）")
    ap.add_argument("--aliases", default="BTC-USDT,ETH-USDT,SOL-USDT")
    ap.add_argument("--tfs", default="M15,H1")
    ap.add_argument("--offset", type=int, default=0, help="从末尾跳过 N 根，用于选取更早窗口")
    args = ap.parse_args()

    repo = get_repository()
    factors = repo.list_factors(status="active")
    print(f"因子库 active 因子：{len(factors)} 个")
    for f in factors:
        print(f"  - {f.name[:34]:<34} {f.symbol:<8} {f.timeframe}")

    aliases = [a.strip() for a in args.aliases.split(",") if a.strip()]
    tfs = [t.strip() for t in args.tfs.split(",") if t.strip()]

    rows: list[dict[str, Any]] = []
    t_start = time.time()

    for f in factors:
        base = baseline_stats(f)
        for tf in tfs:
            if str(f.timeframe).upper() != tf:
                continue
            for alias in aliases:
                bars = load_dataset(alias, tf, args.max_bars, args.offset)
                if not bars:
                    continue
                t0 = time.time()
                exc = execute_factor(f.code, bars, dict(f.params or {}))
                if not exc.ok:
                    print(f"  [执行失败] {f.name[:26]} @ {alias} {tf}: {exc.message[:60]}")
                    continue

                df = to_frame(bars)
                entry = _series(exc.entry_values, df.index)
                exit_sig = _series(exc.exit_values, df.index)

                bt = Backtester()
                bt.symbol = alias
                bt.timeframe = tf
                is_gold = alias.upper() in GOLD_ALIASES or alias.upper().startswith("XAU")
                cost = GOLD_COST if is_gold else CRYPTO_COST
                per_year = (BARS_PER_YEAR_FX if is_gold else BARS_PER_YEAR_CRYPTO).get(tf, 24_000)
                params = {
                    **dict(f.params or {}),
                    **cost,
                    "bars_per_year": per_year,
                    "symbol": "GOLD" if is_gold else alias,
                }
                try:
                    res = bt.run(df, entry, exit_sig, params)
                except Exception as e:  # noqa: BLE001
                    print(f"  [回测异常] {f.name[:26]} @ {alias} {tf}: {type(e).__name__}: {str(e)[:60]}")
                    continue
                m = res.metrics
                buy_hold = (float(df["close"].iloc[-1]) / float(df["close"].iloc[0]) - 1.0) * 100.0

                row = {
                    "factor_name": f.name,
                    "origin_symbol": f.symbol,
                    "crypto_symbol": alias,
                    "timeframe": tf,
                    "bars": len(bars),
                    "entry_signals": exc.entry_count,
                    "exit_signals": exc.exit_count,
                    "num_trades": getattr(m, "num_trades", 0) if m else 0,
                    "total_return_pct": getattr(m, "total_return_pct", None) if m else None,
                    "win_rate_pct": getattr(m, "win_rate_pct", None) if m else None,
                    "profit_factor": getattr(m, "profit_factor", None) if m else None,
                    "max_drawdown_pct": getattr(m, "max_drawdown_pct", None) if m else None,
                    "sharpe": getattr(m, "sharpe", None) if m else None,
                    "buy_hold_pct": round(buy_hold, 2),
                    "origin_return_pct": base.get("total_return_pct"),
                    "origin_win_rate_pct": base.get("win_rate_pct"),
                    "origin_profit_factor": base.get("profit_factor"),
                    "origin_num_trades": base.get("num_trades"),
                    "exec_ms": exc.execution_ms,
                }
                rows.append(row)
                print(
                    f"  {f.name[:24]:<24} {alias:<9} {tf:<4} "
                    f"交易 {row['num_trades']:>4} 笔  收益 {fmt(row['total_return_pct']):>8}%  "
                    f"胜率 {fmt(row['win_rate_pct']):>6}%  PF {fmt(row['profit_factor']):>5}  "
                    f"回撤 {fmt(row['max_drawdown_pct']):>6}%  "
                    f"(持有 {fmt(row['buy_hold_pct']):>7}%)  [{time.time()-t0:.1f}s]"
                )

    if rows:
        with OUT_CSV.open("w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"\n明细写入 {OUT_CSV}")

    # 汇总
    print("\n" + "=" * 92)
    print("汇总：现有因子迁移到加密数据后的表现")
    print("=" * 92)
    total = len(rows)
    tradable = [r for r in rows if (r["num_trades"] or 0) >= 10]
    profitable = [r for r in tradable if (r["profit_factor"] or 0) >= 1.0]
    beat_hold = [r for r in tradable if (r["total_return_pct"] or -999) > (r["buy_hold_pct"] or 0)]
    no_signal = [r for r in rows if (r["entry_signals"] or 0) == 0]

    print(f"  组合总数              : {total}")
    print(f"  有 >=10 笔交易        : {len(tradable)}")
    print(f"  其中 PF >= 1.0        : {len(profitable)}")
    print(f"  其中跑赢买入持有      : {len(beat_hold)}")
    print(f"  完全无入场信号的组合  : {len(no_signal)}")

    if tradable:
        avg_pf = sum(r["profit_factor"] or 0 for r in tradable) / len(tradable)
        avg_ret = sum(r["total_return_pct"] or 0 for r in tradable) / len(tradable)
        avg_wr = sum(r["win_rate_pct"] or 0 for r in tradable) / len(tradable)
        print(f"\n  可交易组合均值：PF {avg_pf:.2f} ｜ 收益 {avg_ret:.2f}% ｜ 胜率 {avg_wr:.1f}%")
    print(f"\n耗时 {time.time()-t_start:.0f}s")


if __name__ == "__main__":
    main()
