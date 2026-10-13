"""验证第 0 期的关键假设：现有因子是否有系统性方向偏向（偏空/逆势）。

现象：同一批因子在加密「上涨窗口」全亏（PF 0.84 / 收益 -18.8%），
      在加密「下跌窗口」却盈利（PF 1.07 / 收益 +5.5%）。
      黄金对照组同样是「下跌段 PF 1.44/1.41、上涨与横盘段亏损」。

本脚本统计每个因子在多/空两个方向上的交易笔数、盈亏与胜率，
若空头占绝大多数且盈利集中在空头，即可确证「因子偏空」而非「品种过拟合」。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backtest_store.engine import Backtester          # noqa: E402
from backtest_store.repository import get_repository   # noqa: E402
from sandbox.runner import execute_factor              # noqa: E402

DATA_DIR = ROOT / "data" / "crypto"
CRYPTO_COST = {
    "commission_pct": 0.001, "point_size": 1.0, "spread_points": 0.0,
    "slippage_price": 0.0, "leverage": 10, "round_trip_cost": True,
}
WINDOW = 6000

# 窗口定义：(标签, offset)  offset=距末尾跳过的根数
WINDOWS = [("上涨段", 0), ("下跌段", 6000)]
ALIASES = ["BTC-USDT", "ETH-USDT", "SOL-USDT"]


def load(alias: str, tf: str, offset: int) -> list[dict[str, Any]]:
    path = DATA_DIR / f"{alias}_{tf}.json"
    if not path.exists():
        return []
    bars = json.loads(path.read_text(encoding="utf-8"))
    if offset:
        bars = bars[:-offset]
    return bars[-WINDOW:]


def _series(values: list[Any] | None, index: pd.Index) -> pd.Series:
    if not values or len(values) != len(index):
        return pd.Series(0.0, index=index)
    return pd.Series(values, index=index).fillna(0.0)


def main() -> None:
    repo = get_repository()
    factors = [f for f in repo.list_factors(status="active") if str(f.timeframe).upper() == "M30"]
    print(f"分析 M30 因子 {len(factors)} 个，窗口 {WINDOW} 根\n")

    grand: dict[str, dict[str, float]] = {}

    for f in factors:
        for label, offset in WINDOWS:
            agg = {"long_n": 0, "short_n": 0, "long_pnl": 0.0, "short_pnl": 0.0,
                   "long_win": 0, "short_win": 0}
            for alias in ALIASES:
                bars = load(alias, "M30", offset)
                if len(bars) < WINDOW:
                    continue
                exc = execute_factor(f.code, bars, dict(f.params or {}))
                if not exc.ok:
                    continue
                df = pd.DataFrame(bars)
                df["time"] = pd.to_datetime(df["time"], utc=True)
                df = df.set_index("time")
                bt = Backtester()
                bt.symbol = alias
                params = {**dict(f.params or {}), **CRYPTO_COST,
                          "bars_per_year": 17_520, "symbol": alias}
                res = bt.run(df, _series(exc.entry_values, df.index),
                             _series(exc.exit_values, df.index), params)
                for t in res.trades or []:
                    side = getattr(t, "side", "") or ""
                    pnl = float(getattr(t, "pnl", 0.0) or 0.0)
                    if side == "long":
                        agg["long_n"] += 1
                        agg["long_pnl"] += pnl
                        if pnl > 0:
                            agg["long_win"] += 1
                    elif side == "short":
                        agg["short_n"] += 1
                        agg["short_pnl"] += pnl
                        if pnl > 0:
                            agg["short_win"] += 1

            n = agg["long_n"] + agg["short_n"]
            if n == 0:
                print(f"  {f.name[:26]:<26} {label}  无交易")
                continue
            short_ratio = agg["short_n"] / n * 100.0
            print(
                f"  {f.name[:26]:<26} {label}  多 {agg['long_n']:>3}笔 "
                f"({agg['long_pnl']:>+9.0f})  空 {agg['short_n']:>3}笔 "
                f"({agg['short_pnl']:>+9.0f})  空头占比 {short_ratio:>5.1f}%"
            )
            g = grand.setdefault(label, {"long_n": 0, "short_n": 0, "long_pnl": 0.0, "short_pnl": 0.0})
            for k in g:
                g[k] += agg[k]

    print("\n" + "=" * 84)
    print("汇总（三品种合计）")
    print("=" * 84)
    for label, g in grand.items():
        n = g["long_n"] + g["short_n"]
        if not n:
            continue
        print(f"\n  【{label}】总交易 {n} 笔")
        print(f"    多头 {g['long_n']:>4} 笔 ({g['long_n']/n*100:>5.1f}%)  合计盈亏 {g['long_pnl']:>+10.0f}")
        print(f"    空头 {g['short_n']:>4} 笔 ({g['short_n']/n*100:>5.1f}%)  合计盈亏 {g['short_pnl']:>+10.0f}")
        if g["long_n"]:
            print(f"    多头单笔均值 {g['long_pnl']/g['long_n']:>+8.2f}")
        if g["short_n"]:
            print(f"    空头单笔均值 {g['short_pnl']/g['short_n']:>+8.2f}")


if __name__ == "__main__":
    main()
