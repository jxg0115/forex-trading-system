"""对照组：拉同期黄金 XAUUSD 数据（MT5），用于区分「品种失效」还是「行情窗口不匹配」。

第 0 期加密回测窗口内 BTC/ETH/SOL 全部大涨（+24%~+43%），而因子库多为逆向/均值回归型。
若同期黄金在这些因子上也亏损，说明是行情窗口问题，跨品种结论需打折；
若同期黄金盈利，则确证「因子过拟合到黄金品种特性」。

依赖 MT5 终端已启动；拉不到就直接退出（不阻塞第 0 期结论，仅在报告中标注缺失）。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / "data" / "crypto"


def main() -> None:
    try:
        from oems.mt5_gateway import MT5Gateway
    except Exception as exc:  # noqa: BLE001
        print(f"[跳过] 无法导入 MT5 网关：{type(exc).__name__}: {exc}")
        return

    gw = MT5Gateway()
    if not gw.connect():
        print("[跳过] MT5 终端未连接，无法获取黄金对照组数据")
        return

    acc = gw.get_account_info()
    print(f"MT5 已连接：{acc['server']} #{acc['login']} {acc['currency']}")

    for symbol, tf, count in (("XAUUSD", "M15", 6000), ("XAUUSD", "M30", 6000)):
        bars = gw.get_rates(symbol, tf, count)
        if not bars:
            print(f"[FAIL] {symbol} {tf} 无数据")
            continue
        path = OUT_DIR / f"{symbol}_{tf}.json"
        path.write_text(json.dumps(bars), encoding="utf-8")
        span = (
            datetime.fromisoformat(bars[-1]["time"]) - datetime.fromisoformat(bars[0]["time"])
        ).days
        first_close, last_close = bars[0]["close"], bars[-1]["close"]
        chg = (last_close / first_close - 1.0) * 100.0
        print(
            f"[OK] {symbol} {tf} {len(bars)} 根  {bars[0]['time'][:10]} → {bars[-1]['time'][:10]} "
            f"({span} 天)  区间涨跌 {chg:+.2f}%  ({first_close:.2f} → {last_close:.2f})"
        )

    print(f"\n对照数据写入 {OUT_DIR}")


if __name__ == "__main__":
    main()
