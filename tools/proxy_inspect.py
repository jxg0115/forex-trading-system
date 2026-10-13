"""对比两个代理端口，找出能访问 OKX 的那一个。

已知：
- Windows 系统代理（浏览器用的）: 127.0.0.1:3067
- 环境变量 HTTP_PROXY（Python 默认用的）: 127.0.0.1:59004
此前用 59004 访问 okx.com 得到 502，但那只代表"那个代理不转发 okx"。
浏览器走的是 3067，用户反映浏览器可能能开 okx，因此优先验证 3067。
"""

from __future__ import annotations

import json
import socket
import ssl
import urllib.error
import urllib.request

PROXIES = {
    "系统代理 3067（浏览器用）": "http://127.0.0.1:3067",
    "环境变量 59004": "http://127.0.0.1:59004",
    "直连": "",
}
TARGETS = [
    ("OKX time", "https://www.okx.com/api/v5/public/time"),
    ("OKX candles", "https://www.okx.com/api/v5/market/candles?instId=BTC-USDT-SWAP&bar=15m&limit=2"),
    ("OKX instruments", "https://www.okx.com/api/v5/public/instruments?instType=SWAP&instId=BTC-USDT-SWAP"),
    ("Binance 对照", "https://data-api.binance.vision/api/v3/time"),
]
TIMEOUT = 12


def build(proxy_url: str) -> urllib.request.OpenerDirector:
    if not proxy_url:
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
    )


def main() -> None:
    print("=" * 84)
    print("端口存活")
    for label, url in PROXIES.items():
        if not url:
            continue
        port = int(url.rsplit(":", 1)[1])
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.6):
                print(f"  [OPEN ] {label}")
        except OSError as exc:
            print(f"  [DOWN ] {label}  {exc}")

    print("\n" + "=" * 84)
    print("连通性矩阵")
    print("=" * 84)
    winner: str | None = None
    for label, purl in PROXIES.items():
        opener = build(purl)
        print(f"\n--- {label} ---")
        for name, url in TARGETS:
            try:
                with opener.open(urllib.request.Request(
                        url, headers={"User-Agent": "Mozilla/5.0"}), timeout=TIMEOUT) as r:
                    body = r.read(220).decode("utf-8", "replace")
                print(f"  [OK  ] {name:<18} {r.status}  {' '.join(body.split())[:110]}")
                if name == "OKX time" and winner is None:
                    winner = purl
            except urllib.error.HTTPError as e:
                print(f"  [HTTP] {name:<18} code={e.code}")
            except Exception as e:  # noqa: BLE001
                print(f"  [FAIL] {name:<18} {type(e).__name__}: {str(e)[:60]}")

    print("\n" + "=" * 84)
    if winner:
        print(f"可用代理：{winner}")
        print(f"  → Python 侧用法：proxies={{'http':'{winner}','https':'{winner}'}}")
        print(f"  → 本项目 OkxGateway 用法：OkxGateway(..., proxy=True) 需配合环境变量，"
              f"或把 HTTP_PROXY/HTTPS_PROXY 设为 {winner}")
    else:
        print("两个代理都无法访问 OKX。")
    print("=" * 84)


if __name__ == "__main__":
    main()
