#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""趋势判定的单元测试：用构造存档验证分时/近期/长期三条线。"""

from netriage.trend import analyze


def run(at, down, health=80, loss=0.0, buffer=None, plan=1000.0, run_id=1):
    return {
        "id": run_id,
        "created_at": at,
        "health": health,
        "down_mbps": down,
        "up_mbps": 100,
        "bufferbloat_ms": buffer,
        "loss_pct": loss,
        "plan_down": plan,
    }


def test_insufficient_data():
    trend = analyze([run("2026-11-24T12:00:00", 900), run("2026-11-24T12:30:00", 880)])
    assert trend["verdict"] == "insufficient"
    assert len(trend["evidence"]) == 0


def test_peak_hour_degradation():
    rows = [run("2026-11-23T14:00:00", 900, run_id=i) for i in range(1, 4)]
    rows += [run("2026-11-23T20:00:00", 480, run_id=i) for i in range(4, 7)]
    trend = analyze(rows, plan_down=1000)
    assert trend["verdict"] == "peak_degradation"
    assert trend["stats"]["evening_day_ratio"] < 0.6
    assert "晚高峰" in trend["headline"]


def test_recent_drop():
    rows = [run("2026-11-23T12:00:00", 900, run_id=i) for i in range(1, 5)]
    rows += [run("2026-11-23T12:%02d:00" % (i * 10), value, run_id=i + 4)
             for i, value in enumerate((620, 560, 500), start=1)]
    trend = analyze(rows, plan_down=1000)
    assert trend["verdict"] == "recent_drop"
    assert trend["stats"]["recent_change_pct"] <= -20


def test_sustained_shortfall():
    rows = [run("2026-11-23T13:%02d:00" % (i * 5), 600, run_id=i) for i in range(1, 7)]
    trend = analyze(rows, plan_down=1000)
    assert trend["verdict"] == "sustained_shortfall"
    assert trend["stats"]["plan_ratio"] < 0.8


def test_bufferbloat():
    rows = [run("2026-11-23T13:%02d:00" % (i * 5), 900, buffer=260, run_id=i) for i in range(1, 7)]
    trend = analyze(rows, plan_down=1000)
    assert trend["verdict"] == "bufferbloat"


def test_packet_loss():
    rows = [run("2026-11-23T13:%02d:00" % (i * 5), 900, loss=3.5, run_id=i) for i in range(1, 7)]
    trend = analyze(rows, plan_down=1000)
    assert trend["verdict"] == "packet_loss"


def test_stable_and_hourly_shape():
    rows = [run("2026-11-23T%02d:00:00" % (8 + i), 950, run_id=i) for i in range(1, 7)]
    trend = analyze(rows, plan_down=1000)
    assert trend["verdict"] == "stable"
    assert len(trend["hourly"]) == 24
    assert trend["hourly"][13]["count"] == 1
