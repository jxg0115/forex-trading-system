"""tick 存档服务（阶段二：数据补缺）。

- 历史回填：MT5 copy_ticks_range 近 N 天 → 按日写 JSONL（data/ticks/<符号>_<YYYYMMDD>.jsonl）；
- 实时追加：后台录制任务定时 get_tick 追加当日文件；
- 回放：按符号+日期读回列表。

记录统一字段：time_ms（毫秒，回放排序基准）/ time（秒浮点）/ bid / ask / last / volume / flags（可选）。
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from config import BASE_DIR

TICK_DIR = os.path.join(BASE_DIR, "data", "ticks")

_recorder_task: asyncio.Task | None = None
_recorder_state: dict[str, Any] = {"running": False, "symbol": "", "interval": 0.0}


def _day_file(symbol: str, day) -> str:
    return os.path.join(TICK_DIR, f"{symbol}_{day.strftime('%Y%m%d')}.jsonl")


def _fmt(tick: dict[str, Any]) -> str:
    return json.dumps(tick, ensure_ascii=False)


def _day_of(time_ms: int) -> Any:
    return datetime.fromtimestamp(time_ms / 1000.0, tz=timezone.utc).date()


def backfill(gateway: Any, symbol: str, days: int = 30) -> int:
    """回填最近 N 天历史 tick（按日去重追加：已存在文件仍追加尾部，简单策略）。"""
    if days <= 0:
        return 0
    now = datetime.now()
    date_from = now - timedelta(days=days)
    ticks = gateway.copy_ticks_range(symbol, date_from, now)
    if not ticks:
        return 0
    os.makedirs(TICK_DIR, exist_ok=True)
    by_day: dict[Any, list[dict[str, Any]]] = {}
    for t in ticks:
        by_day.setdefault(_day_of(t["time_ms"]), []).append(t)
    total = 0
    for day, items in by_day.items():
        with open(_day_file(symbol, day), "a", encoding="utf-8") as f:
            for t in items:
                f.write(_fmt(t) + "\n")
        total += len(items)
    return total


def append_tick(symbol: str, tick: dict[str, Any]) -> None:
    """实时追加一条 tick（get_tick 的 time 为秒 → 转 time_ms 统一；缺 time 用当前时间）。"""
    os.makedirs(TICK_DIR, exist_ok=True)
    time_sec = tick.get("time")
    if time_sec is None:
        time_sec = datetime.now(timezone.utc).timestamp()
    row = {"time_ms": int(float(time_sec) * 1000), "time": float(time_sec)}
    for k in ("bid", "ask", "last", "volume", "spread", "spread_points", "flags", "symbol"):
        if k in tick:
            row[k] = tick[k]
    with open(_day_file(symbol, _day_of(row["time_ms"])), "a", encoding="utf-8") as f:
        f.write(_fmt(row) + "\n")


def replay(symbol: str, date_str: str) -> list[dict[str, Any]]:
    """按日期回放（date_str 格式 YYYY-MM-DD，UTC 日）。"""
    try:
        day = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        day = datetime.strptime(date_str, "%Y%m%d").date()  # 兼容 YYYYMMDD
    path = _day_file(symbol, day)
    if not os.path.exists(path):
        return []
    out: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
    out.sort(key=lambda x: x.get("time_ms", 0))
    return out


def list_days(symbol: str) -> list[str]:
    if not os.path.isdir(TICK_DIR):
        return []
    names = [n for n in os.listdir(TICK_DIR) if n.startswith(symbol + "_") and n.endswith(".jsonl")]
    return sorted(names)


def start_recorder(gateway: Any, symbol: str = "GOLD", interval: float = 5.0) -> bool:
    """启动实时录制后台任务（定时 get_tick → 当日文件追加）。"""
    global _recorder_task
    if _recorder_task is not None and not _recorder_task.done():
        return False

    async def _loop():
        while True:
            try:
                tick = await asyncio.to_thread(gateway.get_tick, symbol)
                if tick:
                    append_tick(symbol, tick)
            except Exception:
                pass
            await asyncio.sleep(max(interval, 1.0))

    _recorder_task = asyncio.create_task(_loop())
    _recorder_state.update({"running": True, "symbol": symbol, "interval": float(interval)})
    return True


def stop_recorder() -> bool:
    global _recorder_task
    if _recorder_task is not None:
        _recorder_task.cancel()
        _recorder_task = None
    _recorder_state.update({"running": False})
    return True


def recorder_status() -> dict[str, Any]:
    running = _recorder_task is not None and not _recorder_task.done()
    _recorder_state["running"] = running
    total_files = 0
    total_lines = 0
    if os.path.isdir(TICK_DIR):
        for n in os.listdir(TICK_DIR):
            if n.endswith(".jsonl"):
                total_files += 1
                try:
                    with open(os.path.join(TICK_DIR, n), encoding="utf-8", errors="replace") as f:
                        total_lines += sum(1 for _ in f)
                except Exception:
                    pass
    return {**_recorder_state, "files": total_files, "lines": total_lines}