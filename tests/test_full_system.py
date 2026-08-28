"""10 大板块完整系统集成验收（需要真实 MT5 连接，未连接则跳过）。"""

from datetime import datetime, timedelta, timezone

import pytest
from starlette.testclient import TestClient

from api.app import create_app

BREAKOUT_CODE = """\
import pandas as pd
import numpy as np

def calculate(df, params):
    lookback = int(params.get("lookback", 20))
    close = df["close"]
    high = df["high"].rolling(lookback).max().shift(1)
    low = df["low"].rolling(lookback).min().shift(1)
    entry = pd.Series(0.0, index=df.index)
    entry.loc[close > high] = 1.0
    entry.loc[close < low] = -1.0
    return {"entry": entry}
"""


@pytest.fixture()
def client():
    with TestClient(create_app(broker="mt5")) as test_client:
        status = test_client.get("/api/mt5/status")
        if status.status_code != 200 or not status.json().get("connected"):
            pytest.skip("MT5 未连接，跳过完整系统验收")
        yield test_client


def test_full_system_10_boards(client):
    checks: list[str] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        assert condition, f"{name} 失败：{detail}"
        checks.append(name)

    # 板块一：K 线与历史数据
    r = client.get("/api/market/bars", params={"symbol": "BTCUSD", "timeframe": "M15", "limit": 200})
    check("板块一 K线", r.status_code == 200 and len(r.json()["bars"]) >= 100, r.text[:200])
    bars = r.json()["bars"]

    # 板块二：实时 Tick
    r = client.get("/api/mt5/tick", params={"symbol": "BTCUSD"})
    check("板块二 Tick", r.status_code == 200 and r.json()["data"]["bid"] > 0, r.text[:200])

    # 板块三：品种信息与点差
    r = client.get("/api/mt5/symbols")
    check("板块三 品种列表", r.status_code == 200 and len(r.json()["symbols"]) > 100, r.text[:200])
    r = client.get("/api/mt5/symbol_info", params={"symbol": "BTCUSD"})
    check("板块三 品种信息", r.status_code == 200 and r.json()["data"]["pip_value"] > 0, r.text[:200])

    # 板块四：AI 因子生成
    region = {
        "symbol": "BTCUSD",
        "timeframe": "M15",
        "time_start": bars[60]["time"],
        "time_end": bars[110]["time"],
        "price_top": max(b["high"] for b in bars[60:111]),
        "price_bottom": min(b["low"] for b in bars[60:111]),
        "entry_points": [],
        "exit_points": [],
    }
    r = client.post("/api/ai/generate", json={"region": region})
    check("板块四 AI生成", r.status_code == 200 and r.json()["sandbox"]["ok"], r.text[:300])
    draft = r.json()

    # 板块四：ML 模型池训练与预测
    r = client.post("/api/models/logistic_momentum/train", json={"symbol": "BTCUSD", "timeframe": "M15", "count": 300})
    check("板块四 ML训练", r.status_code == 200 and r.json()["metrics"]["samples"] > 30, r.text[:200])
    r = client.post("/api/models/logistic_momentum/predict", json={"symbol": "BTCUSD", "timeframe": "M15", "count": 300})
    check("板块四 ML预测", r.status_code == 200 and r.json()["signal"] in ("long", "short", "none"), r.text[:200])

    # 板块五：自定义回测
    r = client.post(
        "/api/backtest/run",
        json={
            "code": draft["code"],
            "symbol": "BTCUSD",
            "timeframe": "M15",
            "bars": 500,
            "params": draft["params"],
            "initial_equity": 20000,
            "leverage": 20,
            "delay_ms": 60000,
            "slippage_points": 1,
        },
    )
    check("板块五 回测", r.status_code == 200 and r.json()["ok"] is True, r.text[:300])

    r = client.post(
        "/api/backtest/optimize",
        json={
            "code": draft["code"],
            "symbol": "BTCUSD",
            "timeframe": "M15",
            "bars": 200,
            "params": draft["params"],
            "max_iterations": 5,
            "early_stop_rounds": 2,
        },
    )
    check(
        "板块五 参数优化",
        r.status_code == 200 and r.json()["ok"] is True and r.json()["targets_reached"] is True,
        r.text[:300],
    )

    # 板块六：技术指标
    r = client.get("/api/indicators", params={"symbol": "BTCUSD", "timeframe": "M15", "count": 300})
    check("板块六 指标", r.status_code == 200 and "rsi_14" in r.json()["latest"], r.text[:200])

    # 板块七：风险手数计算
    r = client.get("/api/mt5/risk/calc_lot", params={"symbol": "BTCUSD", "risk_percent": 1, "sl_pips": 100})
    check("板块七 手数计算", r.status_code == 200 and r.json()["calculated_lot"] > 0, r.text[:200])

    # 板块八：因子 CRUD + 执行器状态 + 订单日志
    payload = {
        "name": "完整验收因子",
        "description": "10 大板块验收临时因子",
        "code": draft["code"],
        "symbol": "BTCUSD",
        "timeframe": "M15",
        "source": draft["source"],
        "model": draft["model"],
        "params": draft["params"],
        "tags": ["验收"],
        "chart_stats": {},
        "prompt_snapshot": {},
        "generated_region": region,
    }
    created = client.post("/api/factors", json=payload)
    check("板块八 因子新增", created.status_code == 201, created.text[:200])
    factor_id = created.json()["id"]
    payload["name"] = "完整验收因子-已更新"
    updated = client.put(f"/api/factors/{factor_id}", json=payload)
    check("板块八 因子编辑", updated.status_code == 200 and updated.json()["factor"]["name"] == payload["name"], updated.text[:200])
    check("板块八 因子查询", client.get(f"/api/factors/{factor_id}").status_code == 200)

    # 形态学习、保存与匹配
    learn_payload = {
        "symbol": "BTCUSD",
        "timeframe": "M15",
        "time_start": region["time_start"],
        "time_end": region["time_end"],
        "price_top": region["price_top"],
        "price_bottom": region["price_bottom"],
        "entry_points": region["entry_points"],
        "exit_points": region["exit_points"],
    }
    r = client.post("/api/patterns/learn", json=learn_payload)
    check("形态学习", r.status_code == 200 and "fingerprint" in r.json() and "features" in r.json() and "pattern_type" in r.json(), r.text[:200])
    learned = r.json()
    r = client.post("/api/patterns", json={**learned, "factor_id": factor_id, **learn_payload})
    check("形态保存", r.status_code == 201 and r.json()["case_id"], r.text[:200])
    r = client.post("/api/patterns/match", json={"symbol": "BTCUSD", "timeframe": "M15", "window_bars": 60, "min_similarity": 0.0, "top_k": 5})
    check("形态匹配", r.status_code == 200 and isinstance(r.json()["matches"], list), r.text[:200])

    r = client.get("/api/executor/state")
    check("板块八 执行器状态", r.status_code == 200 and "last_executions" in r.json(), r.text[:200])
    r = client.get("/api/orders/log")
    check("板块八 订单日志", r.status_code == 200 and isinstance(r.json()["logs"], list), r.text[:200])

    # 板块四/八：信号扫描（不触发真实下单）
    r = client.post("/api/matcher/scan", json={})
    check("板块四/八 信号扫描", r.status_code == 200 and "candidates" in r.json(), r.text[:200])

    # 板块五：模拟盘启停
    r = client.post("/api/matcher/paper/start", json={"factor_id": factor_id, "symbol": "BTCUSD", "timeframe": "M15", "equity": 20000})
    check("板块五 模拟盘启动", r.status_code == 200, r.text[:200])
    r = client.post("/api/matcher/paper/stop", json={})
    check("板块五 模拟盘停止", r.status_code == 200, r.text[:200])

    # 板块九：告警配置与测试
    r = client.get("/api/observability/config")
    check("板块九 告警配置", r.status_code == 200 and isinstance(r.json()["channels"], list), r.text[:200])
    r = client.post("/api/observability/test", json={})
    check("板块九 告警测试", r.status_code == 200, r.text[:200])

    # 板块十：账户与心跳
    r = client.get("/api/system/state")
    check("板块十 账户状态", r.status_code == 200 and r.json()["account_login"] is not None, r.text[:200])
    r = client.get("/api/health")
    check("板块十 健康心跳", r.status_code == 200 and r.json()["status"] == "ok", r.text[:200])

    # 清理临时因子
    client.delete(f"/api/factors/{factor_id}")
    print("10 大板块验收通过：", ", ".join(checks))
