"""阶段四 D3 事件窗口单测：非农规则正确性 + 事件窗口判断（隔离事件文件）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from market_data import events as ev


def test_routine_nfp_first_friday():
    dts = [e["dt"] for e in ev._routine_events(2026, 2026)]
    assert len(dts) == 12
    for dt in dts:
        day = int(dt[8:10])
        assert 1 <= day <= 7  # 第一个周五必然落在 1-7 号


def test_is_high_event_window(tmp_path, monkeypatch):
    monkeypatch.setattr(ev, "EVENTS_FILE", str(tmp_path / "events.json"))
    ev._save(
        {
            "events": [
                {
                    "id": "nfp-2026-09",
                    "dt": "2026-09-04T13:30:00Z",
                    "type": "nfp",
                    "title": "非农就业报告 (NFP)",
                    "importance": "high",
                    "source": "routine",
                }
            ],
            "manual": [],
        }
    )
    base = datetime(2026, 9, 4, 13, 30, tzinfo=timezone.utc)
    # 窗口内（±30 分钟）
    assert ev.is_high_event_window(base, 30)[0] is True
    assert ev.is_high_event_window(base - timedelta(minutes=29), 30)[0] is True
    assert ev.is_high_event_window(base + timedelta(minutes=30), 30)[0] is True
    # 窗口外
    assert ev.is_high_event_window(base - timedelta(minutes=31), 30)[0] is False
    assert ev.is_high_event_window(base + timedelta(minutes=31), 30)[0] is False
    # 低影响事件不进窗口
    ev._save(
        {
            "events": [
                {
                    "id": "low-1",
                    "dt": "2026-09-04T13:30:00Z",
                    "type": "manual",
                    "title": "低影响",
                    "importance": "low",
                    "source": "manual",
                }
            ],
            "manual": [],
        }
    )
    assert ev.is_high_event_window(base, 30)[0] is False