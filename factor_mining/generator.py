"""候选因子生成器：信号模板（入场/出场/结构位）+ 参数网格 + 变体 → 候选因子（含可执行代码）。

候选代码与现有因子库同格式：`def calculate(df, params)`，仅用 pandas/numpy 内联实现
（无 indicators 依赖，可过 AST 沙盒并在回测/实盘直接执行）。每个候选不自动入库，
先进入候选池由用户审阅后保存（见 factor_mining/miner.py 与 /api/mining）。

变体扩充候选规模：base（基础信号）/ trend（叠加 50 均线方向趋势滤波）/ delay（延迟 1 根确认）/
trend_delay（两者叠加）；出场模板仅 base / delay（离场不依赖方向过滤）。
"""

from __future__ import annotations

from typing import Any

# ---------- 内联实现片段（注入每个候选代码） ----------

_HELPERS = """\
def _ema(s, n):
    return s.ewm(span=n, adjust=False).mean()

def _rsi(close, period=14):
    diff = close.diff()
    up = diff.clip(lower=0).ewm(alpha=1.0 / period, adjust=False).mean()
    dn = (-diff).clip(lower=0).ewm(alpha=1.0 / period, adjust=False).mean()
    rs = up / dn.replace(0, float('nan'))
    return 100 - 100 / (1 + rs)

def _macd(close, fast, slow, sig):
    line = _ema(close, fast) - _ema(close, slow)
    signal = line.ewm(span=sig, adjust=False).mean()
    return line, signal, line - signal

def _atr(high, low, close, period=14):
    prev_close = close.shift(1)
    tr = (high - low).where((high - low) >= ((high - prev_close).abs()), (high - prev_close).abs())
    tr = tr.where(tr >= (low - prev_close).abs(), (low - prev_close).abs())
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()
"""

def _indent(text: str) -> str:
    """将 helper 片段整体缩进 4 空格（保持空行），注入到 calculate 函数体内。"""
    return "\n".join(ln if not ln.strip() else "    " + ln for ln in text.splitlines()) + "\n"


# ---------- 模板定义 ----------

TEMPLATES: list[dict[str, Any]] = []


def _entry_code(expr: str) -> str:
    """包装入场因子代码：expr 为生成 entry Series 的表达式（多头为正、空头为负）。"""
    return (
        "def calculate(df, params):\n"
        "    import numpy as np\n"
        "    close = df['close'].astype(float)\n"
        "    high = df['high'].astype(float)\n"
        "    low = df['low'].astype(float)\n"
        "    open_ = df['open'].astype(float)\n"
        "    volume = df['volume'].astype(float)\n"
        + _indent(_HELPERS)
        + "    entry = "
        + expr
        + "\n    entry = entry.fillna(0.0).clip(-1, 1)\n"
        "    return {'entry': entry}\n"
    )


def _exit_code(expr: str) -> str:
    """包装出场因子代码：expr 生成 1 平仓信号；入场信号置 0（入场由入场因子负责）。"""
    return (
        "def calculate(df, params):\n"
        "    import numpy as np\n"
        "    close = df['close'].astype(float)\n"
        "    high = df['high'].astype(float)\n"
        "    low = df['low'].astype(float)\n"
        "    open_ = df['open'].astype(float)\n"
        "    volume = df['volume'].astype(float)\n"
        + _indent(_HELPERS)
        + "    sig = "
        + expr
        + "\n    sig = sig.fillna(0.0).clip(0, 1)\n"
        "    return {'entry': sig * 0.0, 'exit': sig}\n"
    )


def _reg(key: str, name: str, kind: str, grids: list[dict[str, Any]]) -> None:
    TEMPLATES.append({"key": key, "name": name, "kind": kind, "grids": grids})


# —— 入场：动量/均值回归 ——
_reg(
    "rsi_reversal",
    "RSI 超买超卖反转",
    "entry",
    [
        {"rsi_period": p, "oversold": o, "overbought": b, "confirm": c}
        for p in (5, 7, 9, 14, 21)
        for o, b in ((25, 75), (20, 80), (30, 70), (15, 85))
        for c in (1, 2, 3)
    ],
)
_reg(
    "macd_cross",
    "MACD 金叉死叉",
    "entry",
    [
        {"fast": f, "slow": s, "signal": g}
        for f, s, g in ((8, 17, 5), (12, 26, 9), (20, 40, 10), (5, 35, 5), (16, 40, 9), (25, 50, 12))
    ],
)
_reg(
    "sma_cross",
    "均线金叉死叉",
    "entry",
    [
        {"fast": f, "slow": s}
        for f, s in (
            (5, 20), (10, 30), (20, 60), (5, 60), (15, 50), (30, 120),
            (10, 60), (20, 120), (3, 15), (8, 24), (12, 36), (40, 100), (5, 100), (20, 200),
        )
    ],
)
_reg(
    "momentum",
    "动量突破",
    "entry",
    [
        {"n": n, "k": k}
        for n in (3, 5, 8, 10, 15, 20, 30, 40, 60)
        for k in (1.0, 1.5, 2.0, 2.5, 3.0)
    ],
)

# —— 入场：通道/波动 ——
_reg(
    "boll_break",
    "布林带突破",
    "entry",
    [
        {"period": p, "k": k}
        for p in (10, 15, 20, 30, 40, 50)
        for k in (1.5, 2.0, 2.5, 3.0)
    ],
)
_reg(
    "atr_channel",
    "ATR 通道突破",
    "entry",
    [
        {"ema": e, "k": k}
        for e in (5, 10, 15, 20, 30, 40, 50)
        for k in (1.0, 1.5, 2.0, 2.5)
    ],
)
_reg(
    "volume_break",
    "放量突破",
    "entry",
    [
        {"n": n, "vol_k": vk}
        for n in (5, 10, 15, 20, 30, 40, 60)
        for vk in (1.0, 1.2, 1.5, 2.0, 2.5)
    ],
)

# —— 入场：结构位（支撑/压力） ——
_reg(
    "swing_break",
    "突破前高/跌破前低",
    "entry",
    [{"n": n} for n in (3, 5, 8, 10, 15, 20, 30, 40, 60, 80, 100, 120, 150)],
)
_reg(
    "swing_pullback",
    "回踩支撑/压力反弹",
    "entry",
    [
        {"n": n, "ema": e}
        for n in (5, 8, 10, 15, 20, 30, 40, 60)
        for e in (10, 20)
    ],
)

# —— 出场 ——
_reg(
    "rsi_extreme_exit",
    "RSI 超买超卖离场",
    "exit",
    [
        {"rsi_period": p, "overbought": b, "oversold": o}
        for p in (5, 7, 9, 14, 21)
        for b, o in ((70, 30), (75, 25), (80, 20), (85, 15))
    ],
)
_reg(
    "macd_reverse_exit",
    "MACD 柱转向离场",
    "exit",
    [
        {"fast": f, "slow": s, "signal": g}
        for f, s, g in ((8, 17, 5), (12, 26, 9), (20, 40, 10), (5, 35, 5), (16, 40, 9), (25, 50, 12))
    ],
)
_reg(
    "boll_touch_exit",
    "布林触轨离场",
    "exit",
    [
        {"period": p, "k": k}
        for p in (10, 15, 20, 30, 40)
        for k in (2.0, 2.5, 3.0)
    ],
)
_reg(
    "support_resistance_exit",
    "触压力位/破支撑离场",
    "exit",
    [{"n": n} for n in (3, 5, 8, 10, 15, 20, 30, 40, 60, 80, 100, 120, 150)],
)


# ---------- 代码生成 ----------

def _base_expr(template: dict[str, Any], params: dict[str, Any]) -> str:
    p = params
    key = template["key"]
    if key == "rsi_reversal":
        return (
            f"((_rsi(close, {p['rsi_period']}).shift({p['confirm']}) < {p['oversold']})"
            f" & (_rsi(close, {p['rsi_period']}) > {p['oversold']})).astype(float)"
            f" - ((_rsi(close, {p['rsi_period']}).shift({p['confirm']}) > {p['overbought']})"
            f" & (_rsi(close, {p['rsi_period']}) < {p['overbought']})).astype(float)"
        )
    if key == "macd_cross":
        return (
            f"(_macd(close, {p['fast']}, {p['slow']}, {p['signal']})[0]"
            f" > _macd(close, {p['fast']}, {p['slow']}, {p['signal']})[1]).astype(float)"
            f" - (_macd(close, {p['fast']}, {p['slow']}, {p['signal']})[0]"
            f" < _macd(close, {p['fast']}, {p['slow']}, {p['signal']})[1]).astype(float)"
        )
    if key == "sma_cross":
        return (
            f"(_ema(close, {p['fast']}) > _ema(close, {p['slow']})).astype(float)"
            f" - (_ema(close, {p['fast']}) < _ema(close, {p['slow']})).astype(float)"
        )
    if key == "momentum":
        return (
            f"((close - close.shift({p['n']})) > _atr(high, low, close, 14) * {p['k']}).astype(float)"
            f" - ((close - close.shift({p['n']})) < -_atr(high, low, close, 14) * {p['k']}).astype(float)"
        )
    if key == "boll_break":
        return (
            f"(close > close.rolling({p['period']}).mean() + close.rolling({p['period']}).std() * {p['k']}).astype(float)"
            f" - (close < close.rolling({p['period']}).mean() - close.rolling({p['period']}).std() * {p['k']}).astype(float)"
        )
    if key == "atr_channel":
        return (
            f"(close > _ema(close, {p['ema']}) + _atr(high, low, close, 14) * {p['k']}).astype(float)"
            f" - (close < _ema(close, {p['ema']}) - _atr(high, low, close, 14) * {p['k']}).astype(float)"
        )
    if key == "volume_break":
        return (
            f"((close > high.rolling({p['n']}).max().shift(1))"
            f" & (volume > volume.rolling(20).mean() * {p['vol_k']})).astype(float)"
            f" - ((close < low.rolling({p['n']}).min().shift(1))"
            f" & (volume > volume.rolling(20).mean() * {p['vol_k']})).astype(float)"
        )
    if key == "swing_break":
        return (
            f"(close > high.rolling({p['n']}).max().shift(1)).astype(float)"
            f" - (close < low.rolling({p['n']}).min().shift(1)).astype(float)"
        )
    if key == "swing_pullback":
        return (
            f"((low < low.rolling({p['n']}).min().shift(1) + _atr(high, low, close, 14) * 0.5)"
            f" & (close > _ema(close, {p['ema']}))).astype(float)"
            f" - ((high > high.rolling({p['n']}).max().shift(1) - _atr(high, low, close, 14) * 0.5)"
            f" & (close < _ema(close, {p['ema']}))).astype(float)"
        )
    if key == "rsi_extreme_exit":
        return (
            f"((_rsi(close, {p['rsi_period']}) > {p['overbought']})"
            f" | (_rsi(close, {p['rsi_period']}) < {p['oversold']})).astype(float)"
        )
    if key == "macd_reverse_exit":
        return (
            f"((_macd(close, {p['fast']}, {p['slow']}, {p['signal']})[2].shift(1) > 0)"
            f" & (_macd(close, {p['fast']}, {p['slow']}, {p['signal']})[2] < 0)).astype(float)"
        )
    if key == "boll_touch_exit":
        return (
            f"(close >= close.rolling({p['period']}).mean() + close.rolling({p['period']}).std() * {p['k']})"
            f" | (close <= close.rolling({p['period']}).mean() - close.rolling({p['period']}).std() * {p['k']})"
        ) + ".astype(float)"
    if key == "support_resistance_exit":
        return (
            f"(close >= high.rolling({p['n']}).max().shift(1))"
            f" | (close <= low.rolling({p['n']}).min().shift(1))"
        ) + ".astype(float)"
    raise ValueError(f"未知模板：{key}")


def _apply_variant(base: str, kind: str, variant: str) -> str:
    if variant in ("delay", "trend_delay"):
        base = f"({base}).shift(1)"
    if variant in ("trend", "trend_delay"):
        if kind != "entry":
            raise ValueError("出场模板不支持趋势滤波变体")
        base = (
            f"((({base}) > 0) & (close > _ema(close, 50))).astype(float)"
            f" - ((({base}) < 0) & (close < _ema(close, 50))).astype(float)"
        )
    return base


def _make_code(template: dict[str, Any], params: dict[str, Any], variant: str) -> str:
    base = _base_expr(template, params)
    expr = _apply_variant(base, template["kind"], variant)
    return _exit_code(expr) if template["kind"] == "exit" else _entry_code(expr)


# 入场模板支持的变体
_ENTRY_VARIANTS = ("base", "trend", "delay", "trend_delay")
_EXIT_VARIANTS = ("base", "delay")


def _signature(template_key: str, params: dict[str, Any], variant: str) -> str:
    parts = ";".join(f"{k}={v}" for k, v in sorted(params.items()))
    return f"{template_key}|{parts}|{variant}"


def generate_candidates(max_candidates: int = 2000, include_structures: bool = True) -> list[dict[str, Any]]:
    """按模板参数网格 × 变体生成候选因子列表（上限 max_candidates），每个候选含可执行代码与签名。"""
    out: list[dict[str, Any]] = []
    for template in TEMPLATES:
        if not include_structures and template["key"] in ("swing_break", "swing_pullback", "support_resistance_exit"):
            continue
        variants = _ENTRY_VARIANTS if template["kind"] == "entry" else _EXIT_VARIANTS
        for params in template["grids"]:
            for variant in variants:
                if len(out) >= max_candidates:
                    return out
                code = _make_code(template, params, variant)
                out.append(
                    {
                        "template": template["key"],
                        "name": template["name"],
                        "kind": template["kind"],
                        "variant": variant,
                        "params": params,
                        "code": code,
                        "signature": _signature(template["key"], params, variant),
                    }
                )
    return out