"""网络诊断第三轮：为第 0 期确定可用数据源。

两件事：
1. 找可用于「BTC 因子回测」的替代行情源（Binance 现货/永续、Stooq CSV）
2. 试 DoH(DoH over HTTPS) 解析 OKX 真实 IP，再用 server_hostname 直连绕过 DNS 拦截
"""

from __future__ import annotations

import http.client
import json
import ssl
import time
import urllib.error
import urllib.request

TIMEOUT = 12
UA = {"User-Agent": "Mozilla/5.0 (probe)"}


def get(url: str, use_proxy: bool = False, headers: dict | None = None) -> tuple[bool, str, int]:
    opener = urllib.request.build_opener() if use_proxy else urllib.request.build_opener(
        urllib.request.ProxyHandler({})
    )
    h = dict(UA)
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    t0 = time.time()
    try:
        with opener.open(req, timeout=TIMEOUT) as resp:
            body = resp.read(300).decode("utf-8", "replace")
        return True, " ".join(body.split())[:110], int((time.time() - t0) * 1000)
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}", int((time.time() - t0) * 1000)
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {str(exc)[:70]}", int((time.time() - t0) * 1000)


def section(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def probe_sources() -> None:
    section("替代行情源（BTC 永续 / 现货）")
    targets = [
        ("Binance 现货 klines", "https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT&interval=15m&limit=2"),
        ("Binance 永续 fapi", "https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=15m&limit=2"),
        ("Binance 永续 fdata", "https://fdata.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=15m&limit=2"),
        ("Binance vision fapi", "https://data-api.binance.vision/fapi/v1/klines?symbol=BTCUSDT&interval=15m&limit=2"),
        ("Stooq BTCUSD 日线", "https://stooq.com/q/d/l/?s=btcusd&i=d"),
    ]
    for name, url in targets:
        ok, info, ms = get(url, use_proxy=False)
        print(f"  [{'OK  ' if ok else 'FAIL'}] {name:<22} {ms:>6}ms  {info}")


def probe_doh() -> None:
    section("DoH 解析 OKX 真实 IP（绕过本机 DNS）")
    doh_endpoints = [
        ("Cloudflare", "https://cloudflare-dns.com/dns-query?name=www.okx.com&type=A",
         {"Accept": "application/dns-json"}),
        ("Google", "https://dns.google/resolve?name=www.okx.com&type=A", None),
        ("阿里", "https://dns.alidns.com/resolve?name=www.okx.com&type=A", None),
    ]
    resolved: list[str] = []
    for name, url, hdr in doh_endpoints:
        ok, info, ms = get(url, use_proxy=False, headers=hdr)
        print(f"  [{'OK  ' if ok else 'FAIL'}] {name:<12} {ms:>6}ms  {info}")
        if ok:
            try:
                data = json.loads(urllib.request.urlopen(
                    urllib.request.Request(url, headers=hdr or UA), timeout=TIMEOUT
                ).read().decode())
                answers = data.get("Answer") or data.get("answer") or []
                for a in answers:
                    if a.get("type") in (1, "A") or a.get("type") is None:
                        ip = a.get("data") or a.get("value")
                        if ip and ip.count(".") == 3:
                            resolved.append(ip)
                            print(f"          -> A 记录 {ip}")
            except Exception as exc:  # noqa: BLE001
                print(f"          解析响应失败: {type(exc).__name__}")

    if not resolved:
        print("\n  未拿到 OKX 的 A 记录，IP 直连方案不可用。")
        return

    section("用 IP + server_hostname 直连 OKX（绕过 DNS）")
    ctx = ssl.create_default_context()
    for ip in resolved[:3]:
        t0 = time.time()
        try:
            conn = http.client.HTTPSConnection(ip, 443, context=ctx, timeout=TIMEOUT)
            conn.request("GET", "/api/v5/public/time", headers={
                "Host": "www.okx.com",
                "User-Agent": "Mozilla/5.0 (probe)",
            })
            resp = conn.getresponse()
            body = resp.read(200).decode("utf-8", "replace")
            print(f"  [OK  ] {ip:<18} {int((time.time()-t0)*1000):>6}ms  status={resp.status}  {body[:80]}")
            conn.close()
        except Exception as exc:  # noqa: BLE001
            print(f"  [FAIL] {ip:<18} {int((time.time()-t0)*1000):>6}ms  {type(exc).__name__}: {str(exc)[:70]}")


if __name__ == "__main__":
    probe_sources()
    probe_doh()
