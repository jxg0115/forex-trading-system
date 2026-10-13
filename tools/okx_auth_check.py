"""欧易 OKX 密钥连通性自检（读取项目根目录 .env 的配置）。

用法：
    python tools/okx_auth_check.py

检查项：
1. 公开端点：服务器时间（验证代理/网络是否通）
2. 私有端点：账户余额（验证 API Key / Secret / Passphrase 三件套）
3. 账户配置：持仓模式（双向 both / 单向 net）是否和 .env 的 OKX_POS_SIDE_MODE 一致
4. 可选合约类型：SWAP / SPOT / FUTURES 各取一个品种，验证可读

不修改任何配置，不发单，只读。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings  # noqa: E402
from oems.okx_gateway import OkxError, OkxGateway  # noqa: E402

PROBE = {"SWAP": "BTC-USDT-SWAP", "SPOT": "BTC-USDT", "FUTURES": "BTC-USD-260925"}


def main() -> int:
    print("=" * 84)
    print("OKX 密钥自检")
    print("=" * 84)

    missing = [k for k, v in (
        ("OKX_API_KEY", settings.okx_api_key),
        ("OKX_SECRET_KEY", settings.okx_secret_key),
        ("OKX_PASSPHRASE", settings.okx_passphrase),
    ) if not v]
    if missing:
        print(f"❌ .env 中缺少：{', '.join(missing)}")
        print("   请在项目根目录 .env 里填好这三行（OKX_FLAG=1 表示模拟盘）。")
        return 1

    print(f"环境：{'模拟盘(沙盒)' if settings.okx_flag == '1' else '⚠️ 实盘'}  "
          f"保证金={settings.okx_td_mode}  持仓模式={settings.okx_pos_side_mode}")

    gw = OkxGateway(
        api_key=settings.okx_api_key,
        secret=settings.okx_secret_key,
        passphrase=settings.okx_passphrase,
        flag=settings.okx_flag,
        td_mode=settings.okx_td_mode,
        pos_side_mode=settings.okx_pos_side_mode,
        proxy_url=(settings.okx_proxy_url or None)
        if settings.okx_proxy_url != "direct" else "",
    )
    print(f"代理：{gw.proxy_url or '（无，直连）'}")

    # 1) 公开端点（验证网络/代理）
    try:
        t = gw._public("/api/v5/public/time")
        ts = int(t[0].get("ts", 0)) / 1000 if t else 0
        from datetime import datetime, timezone
        print(f"✅ 公开端点连通，服务器时间 "
              f"{datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else '?'}")
    except OkxError as exc:
        print(f"❌ 公开端点失败（网络/代理问题）：{exc}")
        return 1

    # 2) 私有端点：余额（验证三件套）
    acc = gw.get_account_info()
    if acc is None:
        try:
            gw._private("GET", "/api/v5/account/balance")   # 再调一次，拿到真实错误码
        except OkxError as exc:
            print(f"❌ 鉴权失败：{exc}")
        else:
            print("❌ 鉴权失败：返回空账户数据")
        print("   常见原因：Key 在实盘创建却用 OKX_FLAG=1 访问模拟盘（或反过来）；"
              "Secret / Passphrase 抄错；IP 不在白名单；权限没勾 Trade。")
        return 1
    print(f"✅ 鉴权成功（{acc['server']}，Key {acc['login']}）")
    print(f"   总权益 {acc['equity']} USDT   可用 {acc['free_margin']}   "
          f"USDT 可用 {acc['usdt']['avail']}")
    for d in acc.get("details", [])[:6]:
        av = float(d.get("availBal") or d.get("availEq") or 0)
        if av:
            print(f"      - {d.get('ccy')} 可用 {av}")

    # 3) 持仓模式
    try:
        cfg = gw._private("GET", "/api/v5/account/config")
        pos_mode = cfg[0].get("posMode") if cfg else "?"
        ok = (pos_mode == "long_short_mode") == (settings.okx_pos_side_mode == "both")
        print(f"{'✅' if ok else '⚠️'} 账户持仓模式：{pos_mode} "
              f"（.env 配置：{settings.okx_pos_side_mode}）")
        if not ok:
            print("   → 不一致时下单会被拒，请在 OKX 交易设置里改成双向持仓，"
                  "或把 OKX_POS_SIDE_MODE 改成 net。")
    except OkxError as exc:
        print(f"（账户配置读取失败：{exc}）")

    # 4) 合约类型
    for inst_type, inst_id in PROBE.items():
        try:
            info = gw.get_symbol_info(inst_id)
        except OkxError as exc:
            print(f"   {inst_type:<8} {inst_id:<16} ❌ {str(exc)[:60]}")
            continue
        if not info:
            print(f"   {inst_type:<8} {inst_id:<16} ❌ 无此合约（可能已到期）")
            continue
        unit = "张" if info["inst_type"] != "SPOT" else "币"
        print(f"   {inst_type:<8} {inst_id:<16} ✅ 1{unit}={info['ct_val']} "
              f"{info['ct_val_ccy'] or '币'}  步进 {info['lot_step']}  "
              f"最小 {info['min_lot']}")

    print("=" * 84)
    print("全部为只读检查，未下任何单。")
    print("=" * 84)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
