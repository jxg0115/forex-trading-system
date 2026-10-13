"""第 0 期：抓取加密历史 K 线，供「现有因子能否迁移到加密」验证使用。

数据源说明（重要）：
- 本机网络下 OKX 域名 DNS 被污染（`www.okx.com` 解析到不可路由的 169.254.0.2），
  代理亦返回 502，因此**联调阶段无法直连 OKX**。
- 本脚本改用可达的 Binance 现货公开端点 `data-api.binance.vision`（无需鉴权）。
- 用现货数据替代永续做"因子有效性"验证是成立的：永续锚定现货、基差通常 <0.1%，
  M15 级别价格序列差异对形态类因子可忽略。此结论会在报告中标注。

输出：data/crypto/{SYMBOL}_{TF}.json，字段与项目 MT5 bar 结构一致。
"""

from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE = "https://data-api.binance.vision"
KLINES = "/api/v3/klines"
OUT_DIR = Path(__file__).resolve().parents[1] / "data" / "crypto"

# 交易对（Binance 现货命名）→ 项目内别名
SYMBOLS = {
    "BTCUSDT": "BTC-USDT",
    "ETHUSDT": "ETH-USDT",
    "SOLUSDT": "SOL-USDT",
}
# 项目周期 → Binance interval
TIMEFRAMES = {"M15": "15m", "M30": "30m", "H1": "1h"}
# 每个品种/周期抓取的根数
BARS = {"M15": 20000, "M30": 12000, "H1": 8000}
LIMIT = 1000          # Binance 单次上限
SLEEP = 0.15          # 限频保护


def _opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def iso_from_ms(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat()


def fetch_chunk(opener: urllib.request.OpenerDirector, symbol: str, interval: str,
                end_ms: int | None) -> list[list[Any]]:
    url = f"{BASE}{KLINES}?symbol={symbol}&interval={interval}&limit={LIMIT}"
    if end_ms:
        url += f"&endTime={end_ms}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with opener.open(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_symbol(symbol: str, alias: str, interval: str, total: int) -> list[dict[str, Any]]:
    opener = _opener()
    bars: list[dict[str, Any]] = []
    end_ms: int | None = None
    while len(bars) < total:
        rows = fetch_chunk(opener, symbol, interval, end_ms)
        if not rows:
            break
        for r in rows:
            bars.append({
                "time": iso_from_ms(int(r[0])),
                "open": float(r[1]),
                "high": float(r[2]),
                "low": float(r[3]),
                "close": float(r[4]),
                "volume": float(r[5]),
            })
        # 继续往更早翻：endTime = 本批最早 openTime - 1
        earliest = int(rows[0][0])
        if end_ms is not None and earliest >= end_ms:
            break
        end_ms = earliest - 1
        if len(rows) < LIMIT:
            break
        time.sleep(SLEEP)

    bars.sort(key=lambda b: b["time"])       # 保证升序
    bars = bars[-total:] if len(bars) > total else bars
    return bars


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--tfs", default="", help="只抓指定周期，逗号分隔，如 M30；留空=全部")
    args = ap.parse_args()
    tfs = {k: v for k, v in TIMEFRAMES.items()
           if not args.tfs or k in [t.strip() for t in args.tfs.split(",")]}

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    summary: list[dict[str, Any]] = []

    for symbol, alias in SYMBOLS.items():
        for tf, interval in tfs.items():
            t0 = time.time()
            bars = fetch_symbol(symbol, alias, interval, BARS[tf])
            path = OUT_DIR / f"{alias}_{tf}.json"
            path.write_text(json.dumps(bars), encoding="utf-8")
            if bars:
                span_days = (
                    datetime.fromisoformat(bars[-1]["time"]) - datetime.fromisoformat(bars[0]["time"])
                ).days
                print(
                    f"[OK] {alias:<10} {tf:<4} {len(bars):>6} 根  "
                    f"{bars[0]['time'][:10]} → {bars[-1]['time'][:10]} ({span_days} 天)  "
                    f"{time.time()-t0:.1f}s"
                )
                summary.append({
                    "alias": alias, "symbol": symbol, "timeframe": tf,
                    "bars": len(bars), "span_days": span_days,
                    "first": bars[0]["time"], "last": bars[-1]["time"],
                    "last_close": bars[-1]["close"],
                })
            else:
                print(f"[FAIL] {alias} {tf} 无数据")

    meta = OUT_DIR / "_meta.json"
    meta.write_text(json.dumps({
        "source": "Binance 现货公开 API (data-api.binance.vision)",
        "note": "OKX 本机不可达（DNS 污染），现货替代永续做因子有效性验证",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "datasets": summary,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n元数据写入 {meta}")


if __name__ == "__main__":
    main()
