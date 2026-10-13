"""用真实 OKX 公开数据验证 OkxGateway（无需 API Key）。

覆盖：代理探测 → 合约规格 → K 线（校验升序/周期映射）→ ticker → 资金费率 → 手数换算。
K 线顺序是本轮最重要的校验项：OKX 返回降序，网关必须反转，否则指标与因子全部反向。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from oems.okx_gateway import OkxGateway, detect_system_proxy  # noqa: E402
from risk_sizing.crypto_sizing import size_from_symbol_info    # noqa: E402

CASES = [
    ("BTC-USDT-SWAP", "M15"),   # 永续
    ("ETH-USDT-SWAP", "M30"),   # 永续
    ("BTC-USDT", "M15"),        # 现货
]


def main() -> None:
    proxy = detect_system_proxy()
    print("=" * 84)
    print(f"自动探测到的系统代理：{proxy or '（无，将直连）'}")
    print("=" * 84)

    gw = OkxGateway(flag="1")           # 公开端点无需鉴权
    gw.is_connected = True              # 公开数据不需要 connect()

    for inst_id, tf in CASES:
        print(f"\n--- {inst_id}  {tf} ---")
        try:
            info = gw.get_symbol_info(inst_id)
        except Exception as exc:  # noqa: BLE001
            print(f"  [FAIL] 合约规格：{type(exc).__name__}: {str(exc)[:80]}")
            continue
        if not info:
            print("  [FAIL] 合约规格为空")
            continue
        unit = "张" if info["inst_type"] != "SPOT" else "币"
        print(f"  类型 {info['inst_type']:<8} 1{unit}={info['ct_val']} {info['ct_val_ccy'] or '币'} "
              f"({info['ct_type'] or '-'})")
        print(f"  步进 {info['lot_step']}  最小 {info['min_lot']}  最大 {info['max_lot']} "
              f"价格精度 {info['tick_sz']}  最大杠杆 {info['max_leverage']}")
        print(f"  bid {info['bid']}  ask {info['ask']}  点差 {info['spread_points']}")

        try:
            bars = gw.get_rates(inst_id, tf, 500)
        except Exception as exc:  # noqa: BLE001
            print(f"  [FAIL] K 线：{type(exc).__name__}: {str(exc)[:80]}")
            continue
        if not bars:
            print("  [FAIL] K 线为空")
            continue
        first, last = bars[0], bars[-1]
        ascending = first["time"] < last["time"]
        print(f"  K 线 {len(bars)} 根  {first['time'][:19]} → {last['time'][:19]}")
        print(f"  升序校验：{'✅ 正确（旧→新）' if ascending else '❌ 错误（顺序反了）'}")
        gap_min = None
        if len(bars) >= 2:
            from datetime import datetime

            t0 = datetime.fromisoformat(bars[0]["time"])
            t1 = datetime.fromisoformat(bars[1]["time"])
            gap_min = (t1 - t0).total_seconds() / 60.0
        print(f"  相邻间隔：{gap_min} 分钟（{tf} 期望 "
              f"{ {'M15': 15, 'M30': 30, 'H1': 60}.get(tf, '?') }）")
        print(f"  末根收盘 {last['close']}  量 {last['volume']}")

        fr = gw.funding_rate(inst_id)
        if fr:
            print(f"  资金费率 {fr['funding_rate'] * 100:.4f}%  下次结算 {fr['settle'][:19]}")
        else:
            print("  资金费率：现货无")

        sizing = size_from_symbol_info(10_000.0, 1.0, last["close"],
                                       last["close"] * 0.99, info)
        print(f"  手数换算：{sizing['message']}")

    print("\n" + "=" * 84)
    print("完成。以上均为 OKX 真实公开数据（未使用任何 API Key）。")
    print("=" * 84)


if __name__ == "__main__":
    main()
