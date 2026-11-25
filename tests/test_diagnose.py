import copy

from netriage import diagnose as dg
from netriage.samples import demo_payload


def _clean():
    return {
        "plan": {"down": 300, "up": 100},
        "measure": {
            "down_mbps": 298,
            "up_mbps": 97,
            "down_single_mbps": 280,
            "down_multi_mbps": 298,
            "latency_idle_ms": 8,
            "jitter_ms": 2,
            "loss_pct": 0,
            "bufferbloat_ms": 10,
        },
    }


def test_healthy_line_has_no_high_confidence_cause():
    result = dg.diagnose(_clean())
    assert result["causes"] == []
    assert result["health"] == 100
    assert "未发现明确异常" == result["verdict"]


def test_demo_case_ranks_provisioning_first():
    result = dg.diagnose(demo_payload())
    assert result["causes"][0]["id"] == "provisioning"
    assert result["causes"][0]["confidence"] >= 0.5
    assert any("32%" in ev for ev in result["causes"][0]["evidence"])


def test_single_stream_limit_points_to_qos():
    payload = _clean()
    payload["plan"] = {"down": 500, "up": 100}
    payload["measure"].update({"down_mbps": 480, "down_single_mbps": 100, "down_multi_mbps": 480})
    result = dg.diagnose(payload)
    assert result["causes"][0]["id"] == "qos"
    assert any("倍" in ev for ev in result["causes"][0]["evidence"])


def test_bufferbloat_points_to_congestion():
    payload = _clean()
    payload["measure"].update({"latency_idle_ms": 12, "latency_loaded_ms": 480, "bufferbloat_ms": 468})
    result = dg.diagnose(payload)
    assert result["causes"][0]["id"] == "congestion"


def test_weak_wifi_points_to_wifi():
    payload = _clean()
    payload["measure"].update({"down_mbps": 120, "down_single_mbps": 60, "down_multi_mbps": 120})
    payload["wifi"] = {"rssi": -82, "snr": 12, "retry_pct": 30}
    result = dg.diagnose(payload)
    assert result["causes"][0]["id"] == "wifi"


def test_optical_power_points_to_optical():
    payload = _clean()
    payload["lqc"] = "rx_power=-29.4 band=5G"
    result = dg.diagnose(payload)
    assert result["causes"][0]["id"] == "optical"
    assert any("-29.4" in ev for ev in result["causes"][0]["evidence"])


def test_dial_error_691_points_to_dialing():
    payload = _clean()
    payload["dial_log"] = "pppd[1]: CHAP authentication failed\npppd[1]: error 691"
    result = dg.diagnose(payload)
    assert result["causes"][0]["id"] == "dialing"


def test_missing_plan_adds_note_and_skips_ratio():
    payload = _clean()
    payload["plan"] = {}
    result = dg.diagnose(payload)
    assert any("套餐" in note for note in result["notes"])
    keys = {item["key"]: item for item in result["checks"]}
    assert keys["down_ratio"]["status"] == "unknown"


def test_checks_and_metrics_shape():
    result = dg.diagnose(demo_payload())
    assert len(result["checks"]) >= 8
    assert result["metrics"]["stream_ratio"] == round(318.4 / 88.6, 2)
    assert 0 <= result["health"] <= 100
    assert result["dial"]["error_codes"] == []


def test_ticket_contains_measurements_and_diagnosis():
    payload = demo_payload()
    result = dg.diagnose(payload)
    ticket = dg.ticket_for(payload, result)
    assert "宽带故障报障单" in ticket
    assert "318.4" in ticket
    assert "高度怀疑" in ticket
    assert "未做的条目请删除" in ticket


def test_diagnose_is_deterministic():
    payload = demo_payload()
    first = dg.diagnose(copy.deepcopy(payload))
    second = dg.diagnose(copy.deepcopy(payload))
    first.pop("generated_at")
    second.pop("generated_at")
    assert first == second
