"""形态指纹、时机学习与案例库测试。"""

from datetime import datetime, timezone
from types import SimpleNamespace

from ai_engine.pattern import (
    classify_pattern,
    compute_fingerprint,
    compute_fingerprints,
    edge_weights,
    extract_features,
    learn_stop_take,
    learn_timing,
    match_cases,
    similarity,
    weighted_similarity,
)
from backtest_store.repository import PatternCaseRecord, get_repository
from signal_matcher.matcher import MatcherService


def _bars(n=40, drift=0.001):
    price = 100.0
    bars = []
    for i in range(n):
        price *= 1.0 + drift
        bars.append(
            {
                "time": f"2026-01-01T00:{i % 60:02d}:00Z",
                "open": round(price, 2),
                "high": round(price * 1.002, 2),
                "low": round(price * 0.998, 2),
                "close": round(price, 2),
                "volume": 1000.0,
            }
        )
    return bars


def test_fingerprint_and_similarity():
    fp = compute_fingerprint(_bars())
    assert len(fp["closes_z"]) == 40
    assert similarity(fp, fp) == 1.0
    other = compute_fingerprint(_bars(drift=-0.001))
    assert similarity(fp, other) < 0.5


def test_learn_timing_and_stop_take():
    bars = _bars()
    entry = [{"time": bars[10]["time"], "price": bars[10]["close"], "label": "入场点"}]
    exit_ = [{"time": bars[25]["time"], "price": bars[25]["close"], "label": "出场点"}]
    region = {
        "time_start": bars[0]["time"],
        "time_end": bars[-1]["time"],
        "timeframe": "M15",
        "price_top": max(b["high"] for b in bars),
        "price_bottom": min(b["low"] for b in bars),
    }
    timing = learn_timing(region, entry, exit_, bars)
    assert timing["avg_entry_time_fraction"] > 0
    assert timing["avg_hold_bars"] > 0
    stop_take = learn_stop_take(entry, exit_, bars)
    assert stop_take["suggested_stop_pct"] > 0
    assert stop_take["suggested_take_pct"] > 0


def test_pattern_case_crud():
    repo = get_repository()
    record = PatternCaseRecord(
        id="pattern-test-1",
        factor_id="factor-test",
        symbol="BTCUSD",
        timeframe="M15",
        time_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        time_end=datetime(2026, 1, 1, 3, 0, tzinfo=timezone.utc),
        price_top=100.0,
        price_bottom=99.0,
        bar_count=12,
        fingerprint={"closes_z": [0.0, 1.0]},
        region_stats={},
        entry_points=[],
        exit_points=[],
        learned={},
    )
    repo.save_pattern_case(record)
    assert repo.get_pattern_case(record.id) is not None
    assert any(c.id == record.id for c in repo.list_pattern_cases(symbol="BTCUSD"))
    assert repo.update_pattern_statistics(record.id, {"samples": 5, "wins": 4})
    updated = repo.get_pattern_case(record.id)
    assert updated is not None and updated.statistics["samples"] == 5
    assert repo.delete_pattern_case(record.id)
    repo.close()


def test_match_cases_threshold_and_min_samples():
    fp = compute_fingerprint(_bars())
    now = datetime.now(timezone.utc)
    case_low_samples = SimpleNamespace(
        id="c1",
        factor_id="",
        fingerprint=fp,
        statistics={"samples": 1},
        learned={},
        region_stats={},
        created_at=now,
    )
    case_enough_samples = SimpleNamespace(
        id="c2",
        factor_id="",
        fingerprint=fp,
        statistics={"samples": 5, "wins": 4},
        learned={},
        region_stats={},
        created_at=now,
    )
    matches = match_cases(fp, [case_low_samples, case_enough_samples], min_similarity=0.9, min_samples=3)
    assert all(m["sample_count"] >= 3 for m in matches)
    assert any(m["case_id"] == "c2" for m in matches)
    assert not any(m["case_id"] == "c1" for m in matches)


def test_extract_features_categories():
    bars = _bars()
    region = {"time_start": bars[0]["time"], "time_end": bars[-1]["time"], "timeframe": "M15", "price_top": 101.0, "price_bottom": 99.0}
    features = extract_features(bars, region, [], [], [{"price": 99.5, "touched": 2}], [{"price": 101.0, "touched": 3}])
    for key in ("structure", "levels", "candles", "indicators", "timing", "market", "execution", "fingerprints", "normalized"):
        assert key in features
    assert len(compute_fingerprints(bars)) == 4


def test_weighted_similarity_symmetric_and_classify():
    bars = _bars()
    region = {"time_start": bars[0]["time"], "time_end": bars[-1]["time"], "timeframe": "M15", "price_top": 101.0, "price_bottom": 99.0}
    fa = extract_features(bars, region, [], [], [], [])
    fb = extract_features(bars, region, [], [], [], [])
    assert abs(weighted_similarity(fa, fb) - weighted_similarity(fb, fa)) < 1e-6
    result = classify_pattern(fa)
    assert "pattern_type" in result and "confidence" in result


def test_edge_weights_favor_recent_bars():
    w = edge_weights(60)
    assert abs(float(w.sum()) - 1.0) < 1e-9
    assert w[-1] > w[0]
    recent_share = float(w[-6:].sum())
    assert 0.2 <= recent_share <= 0.5
    x = {"closes_z": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]}
    assert similarity(x, x, edge_weights(10)) == 1.0


def test_macro_trend_detection():
    class FakeMarket:
        def get_bars(self, symbol, timeframe, limit=300):
            if timeframe == "H4":
                return [
                    {
                        "time": f"2026-01-01T{i:02d}:00Z",
                        "open": 1.0,
                        "high": 1.0 + i * 0.001,
                        "low": 0.99 + i * 0.001,
                        "close": 1.0 + i * 0.001,
                        "volume": 1000.0,
                    }
                    for i in range(250)
                ]
            return []

    service = MatcherService(FakeMarket(), None)
    assert service._macro_trend("EURUSD") == "up"
