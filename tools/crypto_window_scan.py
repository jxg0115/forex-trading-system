"""扫描加密数据的分段涨跌，找出「下跌窗口」用于二次验证。

第 0 期第一次测试窗口内 BTC/ETH/SOL 全线上涨，而黄金对照组显示因子在下跌段明显更好
（PF 1.44 / 1.41）。为分离「品种过拟合」与「行情方向不匹配」，需要找一段币价下跌的样本。

输出：每个数据集按 6000 根窗口滑动，列出各窗口的涨跌幅，标出下跌窗口。
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "crypto"
WINDOW = 6000
STEP = 1500


def main() -> None:
    files = sorted(DATA_DIR.glob("*_M*.json"))
    files = [f for f in files if not f.name.startswith("_") and "XAUUSD" not in f.name]

    for path in files:
        bars = json.loads(path.read_text(encoding="utf-8"))
        if len(bars) < WINDOW:
            continue
        print(f"\n{path.stem}  ({len(bars)} 根)")
        print(f"  {'窗口起点':<12} {'窗口终点':<12} {'涨跌%':>9}")
        best_down: tuple[float, int] | None = None
        for start in range(0, len(bars) - WINDOW + 1, STEP):
            seg = bars[start:start + WINDOW]
            chg = (seg[-1]["close"] / seg[0]["close"] - 1.0) * 100.0
            mark = "  ← 下跌" if chg < 0 else ""
            print(f"  {seg[0]['time'][:10]:<12} {seg[-1]['time'][:10]:<12} {chg:>+8.2f}%{mark}")
            if chg < 0 and (best_down is None or chg < best_down[0]):
                best_down = (chg, start)
        if best_down:
            idx = len(bars) - (best_down[1] + WINDOW)
            print(f"  => 最深下跌窗口：{best_down[0]:+.2f}%，"
                  f"用 --offset {idx} 可选中它（offset = 距末尾的根数）")
        else:
            print("  => 本数据集在该窗口长度下没有下跌段")


if __name__ == "__main__":
    main()
