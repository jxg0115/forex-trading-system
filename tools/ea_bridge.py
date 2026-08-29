# -*- coding: utf-8 -*-
"""系统端桥服务（EA 桥测试）：决策+统计全在系统，EA 只做转发/机械执行/成交回报。

职责：
  1. 读 EA 转发的测试上下文（bridge_config.csv：品种/周期/入金/杠杆/起始时间）与
     bar 流（bars.csv）；
  2. 决策（全部在系统）：
     - 信号：candidate_entry（swing100 + 趋势段门控 + 延迟3，与 rolling_recheck 同一序列）；
     - 开仓：信号非 0 且无持仓 -> 发 OPEN（dir / sl / tp / vol 全由系统给定）；
     - 止损止盈价位：2×ATR（1R）止损、6×ATR（3R）止盈（与引擎 sltp 同口径）；
     - 移动止损（默认开启）：盈利达 1R 激活，回撤 1.5ATR 提损（与引擎 hw 语义一致）；
     - 时间停：持仓 120 bar -> CLOSE；
     - 写指令 cmds.csv（EA 机械执行）。
  3. 统计：读 EA 成交回报 trades.csv（in/out，含引擎触发的 SL/TP 平仓）-> 配对算盈亏
     （价格%：与仓位无关，胜率/PF/最大亏损/最高盈利/累计收益）
     -> 输出统计表（UTF-8 stats.txt）与收益曲线（equity.csv，每笔平仓累计）。
订单不保存到系统数据库：成交只进系统内存统计 + 输出文件，明细看 MT5 Tester 报告。

用法：主线 python tools/ea_bridge.py（后台常驻）；MT5 Tester 跑 DSH_Bridge_EA。
"""

from __future__ import annotations

import csv
import os
import sys
import time

import pandas as pd

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 主线根
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from tools.rolling_recheck import candidate_entry  # 复用：与回测同一候选、同一信号序列

# EA 在 Tester 沙箱内只能写 Common 文件（FILE_COMMON）——桥读 Common\Files\dsb
BRIDGE_DIR = r"C:\Users\xg\AppData\Roaming\MetaQuotes\Terminal\Common\Files\dsb"
CONFIG_FILE = os.path.join(BRIDGE_DIR, "bridge_config.csv")
BARS_FILE = os.path.join(BRIDGE_DIR, "bars.csv")
CMDS_FILE = os.path.join(BRIDGE_DIR, "cmds.csv")
TRADES_FILE = os.path.join(BRIDGE_DIR, "trades.csv")
STATS_FILE = os.path.join(BRIDGE_DIR, "stats.txt")
EQUITY_FILE = os.path.join(BRIDGE_DIR, "equity.csv")
POLL_S = 0.5

ATR_PERIOD = 14
SL_ATR_MULT = 2.0          # 止损 = 2×ATR（1R）
TP_R_MULT = 3.0            # 止盈 = 3R = 6×ATR
TIME_STOP_BARS = 120       # 时间停
HW_ACTIVATION_R = 1.0      # 移动止损激活：盈利 >= 1R
HW_RETRACE_ATR = 1.5       # 移动止损回撤：1.5×ATR
VOL_FIXED = 0.01           # 仓位固定（统计用价格%，与仓位无关；明细看 MT5 报告）
MIN_BARS = 110             # swing 100 + 延迟3 预热


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    """与 backtest_store.engine._atr 同一公式（TR.rolling(period).mean()）。"""
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period).mean()


def read_bars() -> pd.DataFrame:
    if not os.path.exists(BARS_FILE):
        return pd.DataFrame()
    try:
        df = pd.read_csv(BARS_FILE)
    except Exception:
        return pd.DataFrame()  # EA 正在写（表头/半行）-> 跳过本轮
    if df.empty or "time" not in df.columns:
        return pd.DataFrame()
    df["time"] = pd.to_datetime(df["time"].astype("int64"), unit="s", utc=True)
    df = df.set_index("time").sort_index()
    for c in ("open", "high", "low", "close"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=["open", "high", "low", "close"])


def read_config() -> dict:
    cfg = {}
    if os.path.exists(CONFIG_FILE):
        try:
            rows = list(csv.reader(open(CONFIG_FILE, encoding="utf-8", errors="replace")))
            if len(rows) >= 2:
                r = rows[1]
                cfg = {"symbol": r[0], "period": r[1], "balance": r[2], "leverage": r[3], "start": r[4]}
        except Exception:
            pass
    return cfg


def append_cmd(seq: int, cmd: str, *args) -> None:
    # 容错：EA 在测试重启（OnInit）会清空 cmds.csv（FileOpen FILE_WRITE 持锁），
    # 与桥的 append 可能冲突（Windows 文件锁 PermissionError）——重试几次，仍失败则跳过
    #（下轮循环再试），绝不让桥崩溃。
    vals = []
    for a in args:
        vals.append(round(float(a), 5) if isinstance(a, (int, float)) else a)
    line_vals = [seq, cmd] + vals
    for _ in range(5):
        try:
            with open(CMDS_FILE, "a", newline="") as f:
                csv.writer(f).writerow(line_vals)
            return
        except PermissionError:
            time.sleep(0.3)
        except Exception:
            return
    print(f"[ea_bridge] append_cmd 写入失败(锁冲突) seq={seq} cmd={cmd}——下轮重试")


def read_trades() -> list[dict]:
    rows = []
    if os.path.exists(TRADES_FILE):
        try:
            for r in csv.reader(open(TRADES_FILE, encoding="utf-8", errors="replace")):
                if len(r) >= 4 and r[0] in ("in", "out"):
                    rows.append({"kind": r[0], "time": int(r[1]), "price": float(r[2]),
                                 "dir": int(r[3]), "vol": float(r[4]) if len(r) > 4 else 0.01})
        except Exception:
            pass
    return rows


def pair_trades(rows: list[dict]) -> list[dict]:
    """in/out FIFO 配对（单仓系统：交替）。返回每笔 [in, dir, out, t_in, t_out]。"""
    trades = []
    queue = []
    for r in rows:
        if r["kind"] == "in":
            queue.append(r)
        elif r["kind"] == "out" and queue:
            e = queue.pop(0)
            trades.append({"in": e["price"], "dir": e["dir"], "out": r["price"],
                           "t_in": e["time"], "t_out": r["time"]})
    return trades


def pnl_pct(t: dict) -> float:
    return (t["out"] - t["in"]) / t["in"] * 100.0 if t["dir"] == 0 else (t["in"] - t["out"]) / t["in"] * 100.0


def write_stats(trades: list[dict]) -> dict:
    """统计 + 输出 stats.txt / equity.csv（每笔平仓累计）。"""
    lines = []
    eq = 0.0
    eq_rows = []
    pnls = []
    for i, t in enumerate(trades, 1):
        p = pnl_pct(t)
        pnls.append(p)
        eq += p
        eq_rows.append([t["t_out"], round(eq, 5)])
        d = "LONG" if t["dir"] == 0 else "SHORT"
        lines.append(
            f"#{i} {d:5} in={t['in']:.3f} out={t['out']:.3f} "
            f"t_in={t['t_in']} t_out={t['t_out']} pnl={p:+.3f}%"
        )
    n = len(pnls)
    if n:
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        win_rate = len(wins) / n * 100.0
        pf = sum(wins) / abs(sum(losses)) if losses and sum(losses) != 0 else float("inf")
        lines += [
            "",
            "=== 整体测试统计（价格%，与仓位无关）===",
            f"开仓笔数 n = {n}",
            f"胜率 = {win_rate:.1f}% ({len(wins)}/{n})",
            f"盈亏比 PF = {pf:.3f}" if pf != float("inf") else "盈亏比 PF = inf（无亏损）",
            f"最大亏损 = {min(pnls):+.3f}%" if min(pnls) < 0 else "最大亏损 = 无亏损笔",
            f"最高盈利 = {max(pnls):+.3f}%",
            f"累计收益 = {eq:+.3f}%（价格%）",
        ]
    else:
        lines.append("尚无平仓记录")
    with open(STATS_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    with open(EQUITY_FILE, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(["time_unix", "equity_pct_cum"])
        csv.writer(f).writerows(eq_rows)
    return {"n": n, "eq": eq}


def main() -> None:
    os.makedirs(BRIDGE_DIR, exist_ok=True)
    if not os.path.exists(CMDS_FILE):
        with open(CMDS_FILE, "w", newline="") as f:
            csv.writer(f).writerow(["seq", "cmd", "arg1", "arg2", "arg3", "arg4"])
    if not os.path.exists(TRADES_FILE):
        with open(TRADES_FILE, "w", newline="") as f:
            csv.writer(f).writerow(["kind", "time", "price", "dir", "vol"])

    print("[ea_bridge] system-side ready | decision+stats in system, EA is pure executor")
    cfg = read_config()
    if cfg:
        print(f"[ea_bridge] test context: symbol={cfg.get('symbol')} period={cfg.get('period')} "
              f"balance={cfg.get('balance')} leverage={cfg.get('leverage')} start={cfg.get('start')}")

    seen_bars = 0
    seq = 0
    open_info: dict | None = None
    trades_known = 0

    # 重启恢复：若已有持仓（trades 有 in 未配对），重建 open_info（近似：extreme/时间停从当前起，
    # sl 下轮 holding 会自动重算并发 MODIFY 接管）——重启不丢持仓。
    rows0 = read_trades()
    n_open0 = max(0,
                  sum(1 for r in rows0 if r["kind"] == "in")
                  - sum(1 for r in rows0 if r["kind"] == "out"))
    if n_open0 > 0:
        df0 = read_bars()
        last_in = [r for r in rows0 if r["kind"] == "in"][-1]
        open_info = {
            "idx": max(0, len(df0) - 1),
            "entry": last_in["price"],
            "dir": 1 if last_in["dir"] == 0 else -1,
            "sl": 0.0, "tp": 0.0,
            "extreme": last_in["price"],
            "sl_sent": 0.0, "wait_in": False,
        }
        print(f"[ea_bridge] 重启恢复持仓 entry={last_in['price']:.3f} dir={'LONG' if open_info['dir'] > 0 else 'SHORT'} "
              f"bar#{open_info['idx']}（移动止损/时间停自动接管）")

    while True:
        df = read_bars()
        rows = read_trades()
        n_in = sum(1 for r in rows if r["kind"] == "in")
        n_out = sum(1 for r in rows if r["kind"] == "out")
        n_open = max(0, n_in - n_out)

        if len(df) > seen_bars and len(df) >= MIN_BARS:
            seen_bars = len(df)
            sig = candidate_entry(df)
            latest = float(sig.iloc[-1]) if len(sig) else 0.0
            atr_s = _atr(df, ATR_PERIOD)
            idx = len(df) - 1
            close = float(df["close"].iloc[idx])

            if open_info is None:
                if n_open == 0 and latest != 0:
                    # OPEN：价位由系统算（2ATR 止损 / 3R 止盈），EA 市价执行
                    atr = float(atr_s.iloc[idx]) if len(atr_s) and not pd.isna(atr_s.iloc[idx]) else close * 0.005
                    if atr <= 0:
                        atr = close * 0.005
                    stop_dist = atr * SL_ATR_MULT
                    d = 1 if latest > 0 else -1
                    sl = round(close - d * stop_dist, 5)
                    tp = round(close + d * stop_dist * TP_R_MULT, 5)
                    seq += 1
                    append_cmd(seq, "OPEN", d, sl, tp, VOL_FIXED)
                    open_info = {"idx": idx, "entry": close, "dir": d, "sl": sl, "tp": tp,
                                 "extreme": float(df["high"].iloc[idx]) if d > 0 else float(df["low"].iloc[idx]),
                                 "sl_sent": sl, "wait_in": True}
                    print(f"[ea_bridge] OPEN seq={seq} dir={'LONG' if d > 0 else 'SHORT'} "
                          f"ref={close:.3f} sl={sl:.3f} tp={tp:.3f} bar#{idx} (wait in)")

            elif open_info.get("wait_in"):
                # 已发 OPEN，等 EA 回报 in
                if n_open > 0:
                    open_info["wait_in"] = False
                    for r in reversed(rows):
                        if r["kind"] == "in":
                            open_info["entry"] = r["price"]
                            open_info["idx"] = idx
                            break
                    print(f"[ea_bridge] in confirmed -> holding entry={open_info['entry']:.3f} bar#{idx}")
                elif idx - open_info["idx"] > 6:
                    # OPEN 超时未回报（EA 失败）-> 重置，避免卡死
                    open_info = None
                    print("[ea_bridge] OPEN timeout (no in) -> reset")

            elif n_open == 0:
                # 引擎触发 SL/TP 或系统 CLOSE 已平 -> 回到 idle
                open_info = None

            else:
                # holding：移动止损 + 时间停（决策在系统）
                oi = open_info
                atr = float(atr_s.iloc[idx]) if len(atr_s) and not pd.isna(atr_s.iloc[idx]) else oi["entry"] * 0.005
                if atr <= 0:
                    atr = oi["entry"] * 0.005
                sl = oi["sl"]
                r = 0.0
                if oi["dir"] > 0:
                    extreme = max(oi["extreme"], float(df["high"].iloc[idx]))
                    r = (extreme - oi["entry"]) / (atr * SL_ATR_MULT)
                    if r >= HW_ACTIVATION_R:
                        new_sl = max(oi["sl"], round(extreme - atr * HW_RETRACE_ATR, 5))
                        if new_sl > oi["sl_sent"]:
                            sl = new_sl
                else:
                    extreme = min(oi["extreme"], float(df["low"].iloc[idx]))
                    r = (oi["entry"] - extreme) / (atr * SL_ATR_MULT)
                    if r >= HW_ACTIVATION_R:
                        new_sl = min(oi["sl"], round(extreme + atr * HW_RETRACE_ATR, 5))
                        if new_sl < oi["sl_sent"]:
                            sl = new_sl
                open_info["extreme"] = extreme
                open_info["sl"] = sl

                if sl != oi["sl_sent"]:
                    seq += 1
                    append_cmd(seq, "MODIFY_SL", sl)
                    open_info["sl_sent"] = sl
                    print(f"[ea_bridge] MODIFY_SL seq={seq} sl={sl:.3f} (trailing r={r:.2f}R) bar#{idx}")

                if idx - oi["idx"] >= TIME_STOP_BARS and oi.get("close_sent") is None:
                    seq += 1
                    append_cmd(seq, "CLOSE")
                    open_info["close_sent"] = True
                    print(f"[ea_bridge] CLOSE seq={seq} time_stop {idx - oi['idx']} bars bar#{idx}")

        # 统计（每次循环刷新，平仓后立即更新）
        trades = pair_trades(rows)
        if len(trades) != trades_known:
            trades_known = len(trades)
            st = write_stats(trades)
            if st["n"] > 0:
                print(f"[ea_bridge] stats updated: n={st['n']} cum={st['eq']:+.3f}% "
                      f"(see {STATS_FILE} / {EQUITY_FILE})")

        time.sleep(POLL_S)


if __name__ == "__main__":
    main()