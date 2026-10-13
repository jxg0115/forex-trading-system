"""通过 OkxGateway 拉取永续合约历史，落盘供因子验证复用（避免每次重新分页）。

注意：OKX 的 /market/candles 只覆盖最近约 1440 根，更长的历史要走
/market/history-candles（单次上限 100 根），因此深度越大耗时越长，
务必落盘缓存，不要每次重跑都重新拉。
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from oems.okx_gateway import OkxGateway  # noqa: E402

OUT_DIR = ROOT / "data" / "crypto"
TARGETS = [
    ("BTC-USDT-SWAP", "M30", 3000),
    ("ETH-USDT-SWAP", "M30", 3000),
]


def main() -> None:
    gw = OkxGateway(flag="1")
    print(f"使用代理：{gw.proxy_url or '（直连）'}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    summary = []

    for inst_id, tf, count in TARGETS:
        t0 = time.time()
        bars = gw.get_rates(inst_id, tf, count)
        if not bars:
            print(f"[FAIL] {inst_id} {tf} 无数据")
            continue
        path = OUT_DIR / f"{inst_id}_{tf}.json"
        path.write_text(json.dumps(bars), encoding="utf-8")
        span = (datetime.fromisoformat(bars[-1]["time"]) - datetime.fromisoformat(bars[0]["time"])).days
        chg = (bars[-1]["close"] / bars[0]["close"] - 1.0) * 100.0
        print(f"[OK] {inst_id:<16} {tf} {len(bars):>5} 根  "
              f"{bars[0]['time'][:10]} → {bars[-1]['time'][:10]} ({span} 天)  "
              f"区间涨跌 {chg:+.2f}%  ({time.time()-t0:.0f}s)")
        summary.append({"inst_id": inst_id, "tf": tf, "bars": len(bars),
                        "span_days": span, "change_pct": round(chg, 2)})

    (OUT_DIR / "_okx_meta.json").write_text(json.dumps({
        "source": "OKX v5 永续（本机经系统代理访问）",
        "fetched_at": datetime.now().isoformat(),
        "datasets": summary,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n完成。可用 --aliases BTC-USDT-SWAP,ETH-USDT-SWAP --tfs M30 复跑因子验证。")


if __name__ == "__main__":
    main()
