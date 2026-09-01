# -*- coding: utf-8 -*-
"""审计：现版 classify 对几个典型段的判定 + 方向一致性对比（数据驱动定阈值）。"""
import json
import sys
import urllib.request

import pandas as pd

sys.path.insert(0, r"C:\Users\xg\Documents\外汇交易系统")
from signal_matcher.market_regime import classify  # noqa: E402

BASE = "http://127.0.0.1:8002/api/mt5/klines?symbol=XAUUSD&timeframe=M30&count=900"
with urllib.request.urlopen(BASE, timeout=15) as resp:
    bars = json.loads(resp.read().decode("utf-8"))["bars"]

df = pd.DataFrame(bars)
df["time"] = pd.to_datetime(df["time"])
df = df.sort_values("time").reset_index(drop=True)

SEGMENTS = [
    ("08-27 20:30~08-28 22:00 趋势段(赚+1918)", "2026-08-28 22:00"),
    ("08-28 22:30~09-01 02:00 阴跌段(亏-3374)", "2026-09-01 02:00"),
    ("09-01 02:30~18:00 大跌段(赚+3451)", "2026-09-01 18:00"),
]

for label, end_ts in SEGMENTS:
    end = pd.Timestamp(end_ts, tz="UTC")
    mask = df["time"] <= end
    if mask.sum() == 0:
        print(f"{label}: 无数据")
        continue
    win = df.loc[mask].tail(120).copy()
    close = win["close"]
    reg = classify(win)
    # 方向一致性：顺向根占比（方向=close 变化主导方向）
    diff = close.diff().dropna()
    if reg.name.startswith("强趋势") or "下行" in reg.name:
        down_ratio = float((diff < 0).mean())
        up_ratio = float((diff > 0).mean())
        consistency = max(down_ratio, up_ratio)
    else:
        consistency = 0.0
    # 端点斜率（与 classify 内部一致）
    ema_fast = close.ewm(span=10, adjust=False).mean()
    slope = (ema_fast.iloc[-1] - ema_fast.iloc[-20]) / ema_fast.iloc[-20] * 1000.0 if len(ema_fast) >= 20 else 0.0
    print(f"\n[{label}] 终点={end_ts} 窗口根数={len(win)}")
    print(f"  现版 classify -> name={reg.name!r}")
    print(f"  slope={slope:.2f} 顺向根占比={consistency*100:.1f}% (下行根{float((diff<0).mean())*100:.1f}% 上行根{float((diff>0).mean())*100:.1f}%)")