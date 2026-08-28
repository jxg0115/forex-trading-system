"""回测引擎正确性测试。"""

from datetime import datetime, timedelta, timezone

import pandas as pd

from backtest_store.engine import Backtester
from risk_sizing.sizing import calculate_position_size


def _frame(closes, opens=None, highs=None, lows=None):
    n = len(closes)
    opens = opens or closes
    highs = highs or [c * 1.002 for c in closes]
    lows = lows or [c * 0.998 for c in closes]
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    index = [start + timedelta(minutes=15 * i) for i in range(n)]
    return pd.DataFrame(
        {
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": [1000.0] * n,
        },
        index=index,
    )


def test_entry_executes_at_next_bar_open():
    closes = [1.0, 1.0, 1.0, 1.0, 1.0]
    opens = [1.0, 1.0, 1.0, 1.0, 1.0]
    df = _frame(closes, opens)
    entry = pd.Series([0.0, 1.0, 0.0, 0.0, 0.0], index=df.index)
    bt = Backtester().run(df, entry)
    assert len(bt.trades) >= 1
    assert bt.trades[0].entry_time == df.index[2]


def test_stop_loss_triggered():
    closes = [1.0, 1.0, 1.0, 1.0, 1.0]
    opens = [1.0, 1.0, 1.0, 1.0, 1.0]
    # 大幅低开直接击穿止损
    lows = [0.99, 0.99, 0.90, 0.99, 0.99]
    highs = [1.01, 1.01, 1.02, 1.01, 1.01]
    df = _frame(closes, opens, highs, lows)
    entry = pd.Series([0.0, 1.0, 0.0, 0.0, 0.0], index=df.index)
    bt = Backtester().run(df, entry, params={"stop_atr_mult": 1.0, "take_atr_mult": 5.0})
    assert len(bt.trades) == 1
    assert bt.trades[0].exit_reason == "止损"


def test_metrics_shape():
    closes = [1.0, 1.02, 0.99, 1.03, 1.01, 1.05, 1.0]
    df = _frame(closes)
    entry = pd.Series([0.0, 1.0, 0.0, 0.0, -1.0, 0.0, 0.0], index=df.index)
    bt = Backtester().run(df, entry)
    assert bt.ok
    assert bt.metrics.num_trades >= 1
    assert len(bt.equity_curve) == len(df)


def test_calculate_position_size_rounding():
    lot = calculate_position_size(
        equity=10000,
        risk_percent=1.0,
        sl_pips=20,
        pip_value=10,
        min_lot=0.01,
        max_lot=100,
        lot_step=0.01,
    )
    assert lot == 0.5


def test_calculate_position_size_bounds():
    low = calculate_position_size(equity=100, risk_percent=0.1, sl_pips=200, pip_value=10, min_lot=0.01, max_lot=10, lot_step=0.01)
    high = calculate_position_size(equity=1000000, risk_percent=10, sl_pips=1, pip_value=10, min_lot=0.01, max_lot=10, lot_step=0.01)
    assert low == 0.01
    assert high == 10.0


def test_execution_delay_bars():
    closes = [1.0] * 8
    opens = [1.0] * 8
    df = _frame(closes, opens)
    entry = pd.Series([0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0], index=df.index)
    bt = Backtester().run(df, entry, params={"execution_delay_bars": 2, "max_hold_bars": 10})
    assert len(bt.trades) == 1
    assert bt.trades[0].entry_time == df.index[4]


def test_slippage_worsens_stop_loss():
    closes = [1.0, 1.0, 1.0, 1.0, 1.0]
    opens = [1.0, 1.0, 1.0, 1.0, 1.0]
    highs = [1.01, 1.01, 1.01, 1.01, 1.01]
    lows = [0.99, 0.99, 0.99, 0.99, 0.99]
    df = _frame(closes, opens, highs, lows)
    entry = pd.Series([0.0, 1.0, 0.0, 0.0, 0.0], index=df.index)
    plain = Backtester().run(df, entry, params={"stop_atr_mult": 1.0})
    slipped = Backtester().run(df, entry, params={"stop_atr_mult": 1.0, "slippage_price": 0.002})
    assert plain.trades[0].pnl_pct is not None
    assert slipped.trades[0].pnl_pct is not None
    assert slipped.trades[0].pnl_pct < plain.trades[0].pnl_pct


def test_leverage_cap_does_not_crash():
    closes = [1.0, 1.02, 0.99, 1.03, 1.01]
    df = _frame(closes)
    entry = pd.Series([0.0, 1.0, 0.0, 0.0, 0.0], index=df.index)
    bt = Backtester().run(df, entry, params={"leverage": 1})
    assert bt.ok
    assert len(bt.trades) >= 1
