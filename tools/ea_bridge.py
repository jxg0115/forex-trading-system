# -*- coding: utf-8 -*-
"""系统端桥服务（EA 桥测试）——Tester 决策调度器（v2：调系统各模块，整体功能参与）。

职责：
  1. 读 EA 转发的测试上下文（bridge_config.csv）与 bar 流（bars.csv）；
  2. 决策（全部在系统，逐模块调用系统功能）：
     - 因子匹配：候选 swing（candidate_entry，与 rolling_recheck 同序列）+
       系统因子库 active 因子（/api/factors，exec 因子 code 真实执行）——多因子融合；
     - 事件过滤：/api/events 近事件窗口内不开仓；
     - SLTP：读触发因子的 sl_tp_strategy（stop_atr_mult / take_atr_mult / max_hold_bars）；
     - 智能止损（移动止损）：读因子 trailing 参数（activation_atr / stop_atr），默认 1R/1.5ATR；
     - 风控：仓位 = 1% 风险 / (止损距离占价比例)（balance 来自 Tester 注入）；
     - 时间停：因子 max_hold_bars（默认 120 根）；
     - 写指令 cmds.csv（EA 机械执行）。
  3. 统计：in/out 配对（含引擎 SL/TP 平仓）-> 价格%盈亏 -> stats.txt / equity.csv。
订单不保存到系统数据库：明细看 MT5 Tester 报告。

用法：主线 python tools/ea_bridge.py（后台常驻）；MT5 Tester 跑 DSH_Bridge_EA。
"""

from __future__ import annotations

import csv
import json
import os
import sys
import time
import urllib.request
from datetime import datetime

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
SL_ATR_MULT = 2.0          # 默认止损 = 2×ATR（1R）
TP_R_MULT = 3.0            # 默认止盈 = 3R = 6×ATR
TIME_STOP_BARS = 120       # 默认时间停
HW_ACTIVATION_R = 1.0      # 默认移动止损激活：盈利 >= 1R（=2×ATR）
HW_RETRACE_ATR = 1.5       # 默认移动止损回撤：1.5×ATR
VOL_FIXED = 0.01           # 风控失败时的回退仓位
MIN_BARS = 110             # swing 100 + 延迟3 预热

_API = "http://127.0.0.1:8002"  # 系统正式端口（启动脚本 $env:PORT=8002）
_FACTOR_CACHE = {"t": 0.0, "items": []}


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    """与 backtest_store.engine._atr 同一公式（TR.rolling(period).mean()）。"""
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period).mean()


def _read_raw(path: str) -> str:
    """读文件原文（归档快照用）；不存在/读失败返回空串。"""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception:
        return ""


ARCHIVE_DIR = os.path.join(BRIDGE_DIR, "archive")


def _archive_segment(bars_raw: str, cmds_raw: str, trades_raw: str, stats_raw: str) -> str:
    """把上一段测试数据归档到 archive/<时间戳>/（每次测试独立可回溯，不混在活跃文件）。"""
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        d = os.path.join(ARCHIVE_DIR, ts)
        os.makedirs(d, exist_ok=True)
        if bars_raw:  open(os.path.join(d, "bars.csv"), "w", encoding="utf-8").write(bars_raw)
        if cmds_raw:  open(os.path.join(d, "cmds.csv"), "w", encoding="utf-8").write(cmds_raw)
        if trades_raw: open(os.path.join(d, "trades.csv"), "w", encoding="utf-8").write(trades_raw)
        if stats_raw: open(os.path.join(d, "stats.txt"), "w", encoding="utf-8").write(stats_raw)
        print(f"[ea_bridge] 已归档上一测试段 -> {d}")
        return d
    except Exception as e:
        print(f"[ea_bridge] 归档失败: {e}")
        return ""


# 混段防呆（MIXED-SEGMENT）：
# EA "同段重启保留"误判（旧 config start==缓存最老根恒定）时，新测试轮不清空 bars，
# 而是把重放数据追加到旧段尾部 -> 原始文件出现"时间倒退"（回归点）。
# 桥 detect 回归点后：归档当前段、跳过回归点前的旧段行、重置段状态（自愈，无需手动清理）。
_MIX_SKIP = 0    # 跳过的旧段行数（read_bars 丢弃）
_MIX_MARK = -1   # 已处理的回归点数据行号（-1=无；变化才触发归档/重建，防止重复归档）


def _find_mix_mark(raw: str) -> int:
    """原始 bars 文本中最后一个"时间倒退行"的数据位置（0-based），无回归返 -1。

    时间倒退 = 混段特征（旧段尾之后追加了更早时间的重放数据）。
    返回的位置即"新段（回归后）起点"，跳过它之前的行即得到干净新段。
    """
    mark = -1
    prev = None
    i = 0
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            t = int(line.split(",")[0])
        except Exception:
            continue  # 表头/半行
        if prev is not None and t < prev:
            mark = i
        prev = t
        i += 1
    return mark


def read_bars() -> pd.DataFrame:
    if not os.path.exists(BARS_FILE):
        return pd.DataFrame()
    try:
        df = pd.read_csv(BARS_FILE)
    except Exception:
        return pd.DataFrame()  # EA 正在写（表头/半行）-> 跳过本轮
    if _MIX_SKIP > 0:
        # 混段：丢弃回归点前的旧段行（文件行序=追加序），仅保留回归后新段再排序
        df = df.iloc[_MIX_SKIP:]
    if df.empty:
        return pd.DataFrame()
    if "time" not in df.columns:
        # 无表头/列名异常：取第一列作时间，其余按顺序（open,high,low,close）
        cols = ["time", "open", "high", "low", "close"]
        df.columns = [cols[i] if i < len(cols) else f"c{i}" for i in range(len(df.columns))]
    df["time"] = pd.to_datetime(df["time"].astype("int64"), unit="s", utc=True)
    df = df.set_index("time").sort_index()
    for c in ("open", "high", "low", "close"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=["open", "high", "low", "close"])


# ================= Tester 决策调度器：调系统模块 =================

def _api_get(path: str, timeout: int = 10):
    try:
        with urllib.request.urlopen(_API + path, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def load_factors() -> list[dict]:
    """读系统因子库（/api/factors）active 因子（含 code/sl_tp_strategy），120s 缓存。"""
    now = time.time()
    if now - _FACTOR_CACHE["t"] > 120:
        data = _api_get("/api/factors")
        items = []
        if isinstance(data, dict):
            raw = data.get("items") or data.get("factors") or []
            if isinstance(raw, list):
                for f in raw:
                    if isinstance(f, dict) and f.get("status") == "active" and f.get("code"):
                        items.append(f)
        if items:
            _FACTOR_CACHE.update({"t": now, "items": items})
    return _FACTOR_CACHE["items"]


def run_factor(df: pd.DataFrame, factor: dict):
    """执行因子 code 的 calculate(df, params)，返回归一化 entry 序列（无则 None）。"""
    try:
        ns = {"pd": pd, "np": __import__("numpy")}
        exec(factor["code"], ns)
        calc = ns.get("calculate")
        if not calc:
            return None
        dfv = df.copy()
        if "volume" not in dfv.columns:
            dfv["volume"] = 1.0  # Tester 无成交量——volume 因子退化为纯价格（诚实标注）
        out = calc(dfv, factor.get("params") or {})
        ent = out.get("entry") if isinstance(out, dict) else out
        if ent is None or not hasattr(ent, "iloc"):
            return None
        return pd.Series(ent.values, index=df.index).astype(float)
    except Exception as e:
        print(f"[ea_bridge] 因子执行失败 {factor.get('name', '?')}: {e}")
        return None


def matching_factors(cfg: dict) -> list[dict]:
    sym = (cfg or {}).get("symbol", "")
    fs = load_factors()
    if not sym:
        return fs
    return [f for f in fs if (f.get("symbol") or "").upper() == sym.upper()]


def factor_decision(df: pd.DataFrame, cfg: dict, idx: int):
    """多因子融合信号：候选 swing（基准）+ 匹配因子（OR）。返回 (dir, source, factor)。"""
    sig = candidate_entry(df)
    d0 = float(sig.iloc[idx]) if len(sig) else 0.0
    src = "candidate"
    fact = None
    if d0 == 0.0:
        for f in matching_factors(cfg):
            es = run_factor(df, f)
            if es is None:
                continue
            v = float(es.iloc[idx])
            if v != 0.0:
                d0 = 1.0 if v > 0 else -1.0
                src = f.get("name", "factor")
                fact = f
                break
    return (d0, src, fact)


def sltp_from_factor(fact: dict | None) -> dict:
    """读因子 sl_tp_strategy（ATR 倍数/移动止损/持仓上限），默认系统语义。"""
    cfg = (fact or {}).get("sl_tp_strategy") or {}
    def num(k, d):
        v = cfg.get(k)
        return float(v) if isinstance(v, (int, float)) else d
    stop_atr = num("stop_atr_mult", SL_ATR_MULT)
    if stop_atr <= 0:
        stop_atr = SL_ATR_MULT
    return {
        "stop_atr": stop_atr,
        "take_atr": stop_atr * num("take_atr_mult", TP_R_MULT) / num("stop_atr_mult", SL_ATR_MULT)
                    if num("take_atr_mult", TP_R_MULT) > 0 else stop_atr * TP_R_MULT,
        "tr_act_atr": num("trailing_activation_atr", HW_ACTIVATION_R * SL_ATR_MULT),
        "tr_stop_atr": num("trailing_stop_atr", HW_RETRACE_ATR),
        "max_hold": int(num("max_hold_bars", TIME_STOP_BARS)),
    }


def event_blocked(df: pd.DataFrame, idx: int) -> bool:
    """事件过滤：当前 bar 在近事件 ±3 根（M30）内 -> 不开仓。"""
    evs = _api_get("/api/events")
    if not isinstance(evs, dict):
        return False
    items = evs.get("items") or evs.get("events") or []
    cur = df.index[idx]
    for e in items:
        t = e.get("time") or e.get("datetime")
        if not t:
            continue
        try:
            et = pd.Timestamp(t)
            if abs((cur - et).total_seconds()) <= 3 * 1800:
                return True
        except Exception:
            pass
    return False


def risk_lot(balance: float, sl_dist: float, cur_price: float) -> float:
    """风控板块：仓位 = 1% 风险 / (止损距离占价比例)（近似 1 标准手=10 万单位）。失败回退 0.01。"""
    try:
        if sl_dist > 0 and cur_price > 0 and balance > 0:
            risk = balance * 0.01
            pct = sl_dist / cur_price
            lot = round(risk / (pct * 100000), 2)
            if lot > 0:
                return max(0.01, min(lot, 1.0))
    except Exception:
        pass
    return VOL_FIXED


# ================= 桥状态 / 功能开关（系统板面控制） =================

SETTINGS_FILE = os.path.join(BRIDGE_DIR, "bridge_settings.json")
STATUS_FILE = os.path.join(BRIDGE_DIR, "bridge_status.json")

# 功能参与默认（系统板面可勾选）：factors/sltp/smart_stop/events/risk 参与；patterns/ai 默认关
DEFAULT_SETTINGS = {
    "factors": True, "sltp": True, "smart_stop": True,
    "events": True, "risk": True, "patterns": False, "ai": False,
}
SETTINGS = dict(DEFAULT_SETTINGS)
PROGRESS_FALLBACK_DAYS = 30  # 无 end 时进度兜底（诚实标注近似）


def load_settings() -> None:
    """读系统板面保存的功能勾选（bridge_settings.json）；无文件用默认。"""
    global SETTINGS
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for k in DEFAULT_SETTINGS:
                    if isinstance(data.get(k), bool):
                        SETTINGS[k] = data[k]
    except Exception:
        pass


def _progress_pct(cfg: dict, df) -> float:
    """进度 = 当前测试到的时间 / 整体时间。起点用 bars 首根（真实回放起点，比 bridge_config 可靠）；
    end 用 EA 写入的 end（新 EA 有），无则兜底近似 start+30 天（诚实标注）。"""
    try:
        if len(df) == 0:
            return 0.0
        start = int(df.index[0].timestamp())
        end = int(cfg.get("end") or 0)
        if end <= start:
            end = start + PROGRESS_FALLBACK_DAYS * 86400  # EA 未写 end -> 兜底（诚实近似）
        cur = int(df.index[-1].timestamp())
        pct = max(0.0, min(100.0, (cur - start) / (end - start) * 100.0))
        return round(pct, 1)
    except Exception:
        return 0.0


def _period_label(df) -> str:
    """从 bars 前两根间隔推断周期（EA 的 period 字段写 0 是已知小瑕疵，不影响决策）。"""
    try:
        if len(df) >= 2:
            sec = int(df.index[1].timestamp() - df.index[0].timestamp())
            return {60: "M1", 300: "M5", 900: "M15", 1800: "M30", 3600: "H1",
                    14400: "H4", 86400: "D1"}.get(sec, f"{sec}s")
    except Exception:
        pass
    return ""


def write_status(cfg: dict, df, seq: int, open_info, n_trades: int, cum_pct: float, source: str) -> None:
    """写 bridge_status.json（系统板面实时显示：运行/品种/周期/进度/功能参与/最近动作）。"""
    try:
        status = {
            "running": True,
            "ts": int(time.time()),
            "pid": os.getpid(),
            "symbol": cfg.get("symbol", ""),
            "period": _period_label(df) or cfg.get("period", ""),
            "bars": int(len(df)) if len(df) else 0,
            "progress_pct": _progress_pct(cfg, df),
            "current_time": df.index[-1].strftime("%Y-%m-%d %H:%M") + " UTC" if len(df) else "",
            "modules": dict(SETTINGS),
            "n_trades": n_trades,
            "cum_pct": round(cum_pct, 3),
            "source": source,
            "holding": bool(open_info),
        }
        with open(STATUS_FILE, "w", encoding="utf-8") as f:
            json.dump(status, f, ensure_ascii=False)
    except Exception:
        pass


# ================= 桥通道 =================

def read_config() -> dict:
    cfg = {}
    if os.path.exists(CONFIG_FILE):
        try:
            rows = list(csv.reader(open(CONFIG_FILE, encoding="utf-8", errors="replace")))
            if len(rows) >= 1:
                r = rows[-1]  # 兼容：EA 单行数据（无表头）或 表头+数据 两行
                if r and r[0] and r[0] != "symbol":
                    cfg = {"symbol": r[0], "period": r[1], "balance": r[2], "leverage": r[3],
                           "start": r[4], "end": r[5] if len(r) > 5 else ""}
        except Exception:
            pass
    return cfg


def append_cmd(seq: int, cmd: str, *args) -> None:
    # 容错：EA 在测试重启（OnInit）会清空 cmds.csv（FileOpen FILE_WRITE 持锁），
    # 与桥的 append 可能冲突（Windows 文件锁 PermissionError）——重试几次，仍失败则跳过（下轮再试）。
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
    global _MIX_SKIP, _MIX_MARK  # 混段防呆状态（模块级，主循环内写入）
    os.makedirs(BRIDGE_DIR, exist_ok=True)
    if not os.path.exists(CMDS_FILE):
        with open(CMDS_FILE, "w", newline="") as f:
            csv.writer(f).writerow(["seq", "cmd", "arg1", "arg2", "arg3", "arg4"])
    if not os.path.exists(TRADES_FILE):
        with open(TRADES_FILE, "w", newline="") as f:
            csv.writer(f).writerow(["kind", "time", "price", "dir", "vol"])

    load_settings()
    on = [k for k, v in SETTINGS.items() if v]
    print("[ea_bridge] system-side ready | Tester 决策调度器 v2（功能参与：" + ", ".join(on) + "）")
    cfg = read_config()
    if cfg:
        print(f"[ea_bridge] test context: symbol={cfg.get('symbol')} period={cfg.get('period')} "
              f"balance={cfg.get('balance')} leverage={cfg.get('leverage')} start={cfg.get('start')}")
    facs = load_factors()
    print(f"[ea_bridge] 因子库 active 因子 {len(facs)} 个参与匹配"
          + (f"，本测试匹配 {len(matching_factors(cfg))} 个" if cfg else ""))

    seen_bars = 0
    last_time = 0  # 时间单调去重：同段重播时旧 bar 重复 append，只处理时间递增的新根
    # 归档留底：每轮缓存四文件原文，检测到文件被清空（新测试段）时归档上一段
    prev_raw = {"bars": "", "cmds": "", "trades": "", "stats": ""}
    # seq 重启续接：从 cmds 现有最大 seq + 1（避免同段重启后 EA 侧 seq 续接错乱/重复编号）
    seq = 0
    try:
        rows0 = list(csv.reader(open(CMDS_FILE, encoding="utf-8", errors="replace")))
        for r in reversed(rows0):
            if r and r[0].isdigit():
                seq = int(r[0])
                break
    except Exception:
        pass
    open_info: dict | None = None
    trades_known = 0
    settings_t = time.time()

    # 重启恢复：若已有持仓（trades 有 in 未配对），重建 open_info（近似）——重启不丢持仓。
    rows0 = read_trades()
    n_open0 = max(0,
                  sum(1 for r in rows0 if r["kind"] == "in")
                  - sum(1 for r in rows0 if r["kind"] == "out"))
    if n_open0 > 0:
        df0 = read_bars()
        last_in = [r for r in rows0 if r["kind"] == "in"][-1]
        sc = sltp_from_factor(None)
        open_info = {
            "idx": max(0, len(df0) - 1),
            "entry": last_in["price"],
            "dir": 1 if last_in["dir"] == 0 else -1,
            "sl": 0.0, "tp": 0.0,
            "extreme": last_in["price"],
            "sl_sent": 0.0, "wait_in": False,
            "stop_atr": sc["stop_atr"], "tr_act_atr": sc["tr_act_atr"],
            "tr_stop_atr": sc["tr_stop_atr"], "max_hold": sc["max_hold"],
            "src": "restored",
        }
        print(f"[ea_bridge] 重启恢复持仓 entry={last_in['price']:.3f} dir={'LONG' if open_info['dir'] > 0 else 'SHORT'} "
              f"bar#{open_info['idx']}（移动止损/时间停自动接管）")

    while True:
        # 每轮缓存四个文件原文（新测试段清空文件前的完整段快照，归档用）
        cur_raw = {
            "bars": _read_raw(BARS_FILE),
            "cmds": _read_raw(CMDS_FILE),
            "trades": _read_raw(TRADES_FILE),
            "stats": _read_raw(STATS_FILE),
        }
        df = read_bars()
        rows = read_trades()
        n_in = sum(1 for r in rows if r["kind"] == "in")
        n_out = sum(1 for r in rows if r["kind"] == "out")
        n_open = max(0, n_in - n_out)

        # 混段防呆：检测 bars 原始序的时间倒退（EA 同段误判保留旧文件 + 重放追加）。
        # 回归点变化才动作（归档 + 跳过旧段 + 重置段状态）；回归点不变则静默继续（用回归后数据）。
        mix = _find_mix_mark(cur_raw["bars"])
        if mix != _MIX_MARK:
            _MIX_MARK = mix
            if mix >= 0:
                if seen_bars > 0 and prev_raw["bars"]:
                    _archive_segment(prev_raw["bars"], prev_raw["cmds"],
                                     prev_raw["trades"], prev_raw["stats"])
                _MIX_SKIP = mix
                prev_raw = cur_raw
                seen_bars = 0
                last_time = 0
                print(f"[ea_bridge] 混段检测：跳过旧段 {mix} 行，段状态已重置，从回归点后继续")
            else:
                # 回归消失（文件被清空/新段干净）：复位（下一轮以新段正常处理）
                _MIX_SKIP = 0
            df = pd.DataFrame()  # 变化轮以旧数据构造的 df 弃用：本轮跳过决策，下一轮用新 skip 重读

        if len(df) < seen_bars:  # 文件被清空（新测试段）——归档上一段，再重置进度与时间去重
            if seen_bars > 0 and prev_raw["bars"]:
                _archive_segment(prev_raw["bars"], prev_raw["cmds"],
                                 prev_raw["trades"], prev_raw["stats"])
            prev_raw = cur_raw
            seen_bars = 0
            last_time = 0
        else:
            prev_raw = cur_raw  # 同段内每轮滚动留底
        cur_t = int(df.index[-1].timestamp()) if len(df) else 0  # time 已 set_index，取索引
        if len(df) > seen_bars and len(df) >= MIN_BARS and cur_t > last_time:
            seen_bars = len(df)
            last_time = cur_t
            idx = len(df) - 1
            close = float(df["close"].iloc[idx])
            atr_s = _atr(df, ATR_PERIOD)
            atr = float(atr_s.iloc[idx]) if len(atr_s) and not pd.isna(atr_s.iloc[idx]) else close * 0.005
            if atr <= 0:
                atr = close * 0.005

            if open_info is None:
                if n_open == 0:
                    if SETTINGS["factors"]:
                        d0, src, fact = factor_decision(df, cfg, idx)
                    else:
                        sig = candidate_entry(df)
                        d0 = float(sig.iloc[idx]) if len(sig) else 0.0
                        src = "candidate"
                        fact = None
                    if d0 != 0.0:
                        if SETTINGS["events"] and event_blocked(df, idx):
                            print(f"[ea_bridge] 事件窗口，跳过信号（{src}）bar#{idx}")
                        else:
                            sc = sltp_from_factor(fact) if SETTINGS["sltp"] else sltp_from_factor(None)
                            stop = atr * sc["stop_atr"]
                            d = 1 if d0 > 0 else -1
                            sl = round(close - d * stop, 5)
                            tp = round(close + d * stop * sc["take_atr"] / sc["stop_atr"], 5)
                            vol = (risk_lot(float(cfg.get("balance") or 10000), stop, close)
                                   if SETTINGS["risk"] else VOL_FIXED)
                            seq += 1
                            append_cmd(seq, "OPEN", d, sl, tp, vol)
                            open_info = {
                                "idx": idx, "entry": close, "dir": d, "sl": sl, "tp": tp,
                                "extreme": float(df["high"].iloc[idx]) if d > 0 else float(df["low"].iloc[idx]),
                                "sl_sent": sl, "wait_in": True,
                                "stop_atr": sc["stop_atr"], "tr_act_atr": sc["tr_act_atr"],
                                "tr_stop_atr": sc["tr_stop_atr"], "max_hold": sc["max_hold"],
                                "src": src,
                            }
                            print(f"[ea_bridge] OPEN seq={seq} dir={'LONG' if d > 0 else 'SHORT'} "
                                  f"ref={close:.3f} sl={sl:.3f} tp={tp:.3f} vol={vol} "
                                  f"source={src} bar#{idx} (wait in)")

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
                elif idx - open_info["idx"] > 20:  # OPEN 超时放宽（EA 同段重启/慢执行时不误判 reset，避免链路反复打断）
                    # OPEN 超时未回报（EA 失败）-> 重置，避免卡死
                    open_info = None
                    print("[ea_bridge] OPEN timeout (no in) -> reset")

            elif n_open == 0:
                # 引擎触发 SL/TP 或系统 CLOSE 已平 -> 回到 idle
                open_info = None

            else:
                # holding：移动止损（因子 trailing 参数，smart_stop 勾选时开启）+ 时间停（决策在系统）
                oi = open_info
                sl = oi["sl"]
                r_atr = 0.0
                if SETTINGS["smart_stop"]:
                    if oi["dir"] > 0:
                        extreme = max(oi["extreme"], float(df["high"].iloc[idx]))
                        r_atr = (extreme - oi["entry"]) / atr
                        if r_atr >= oi["tr_act_atr"]:
                            new_sl = max(oi["sl"], round(extreme - atr * oi["tr_stop_atr"], 5))
                            if new_sl > oi["sl_sent"]:
                                sl = new_sl
                    else:
                        extreme = min(oi["extreme"], float(df["low"].iloc[idx]))
                        r_atr = (oi["entry"] - extreme) / atr
                        if r_atr >= oi["tr_act_atr"]:
                            new_sl = min(oi["sl"], round(extreme + atr * oi["tr_stop_atr"], 5))
                            if new_sl < oi["sl_sent"]:
                                sl = new_sl
                    open_info["extreme"] = extreme
                    open_info["sl"] = sl

                if sl != oi["sl_sent"]:
                    seq += 1
                    append_cmd(seq, "MODIFY_SL", sl)
                    open_info["sl_sent"] = sl
                    print(f"[ea_bridge] MODIFY_SL seq={seq} sl={sl:.3f} (trailing {r_atr:.2f}ATR/{oi['tr_act_atr']:.2f}) bar#{idx}")

                if idx - oi["idx"] >= oi["max_hold"] and oi.get("close_sent") is None:
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

        # 状态上报（系统板面实时显示：运行/品种/周期/进度/功能参与/最近来源）
        write_status(cfg, df, seq, open_info, len(trades),
                     sum(pnl_pct(t) for t in trades),
                     open_info.get("src", "idle") if open_info else "idle")

        time.sleep(POLL_S)


if __name__ == "__main__":
    main()