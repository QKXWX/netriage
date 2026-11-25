import json

from netriage import parsers


def test_parse_dial_log_detects_auth_and_drops():
    log = """
Nov 25 19:02:11 pppd[2841]: sent [PADI Host-Uniq 0x0b2a]
Nov 25 19:02:12 pppd[2841]: rcvd [PADO]
Nov 25 19:02:12 pppd[2841]: local  IP address 100.64.18.77
Nov 25 20:41:07 pppd[2841]: Modem hangup
Nov 25 20:41:09 pppd[2841]: LCP: timeout sending Config-Requests
Nov 25 21:02:00 pppd[2841]: CHAP authentication failed
Nov 25 21:02:01 pppd[2841]: error 691
"""
    result = parsers.parse_dial_log(log)
    assert result["lines"] == 7
    assert result["dial_attempts"] == 1
    assert result["got_ip"] == 1
    assert result["drops"] >= 1
    assert result["lcp_timeouts"] >= 1
    assert result["auth_failures"] >= 1
    codes = {item["code"]: item for item in result["error_codes"]}
    assert "691" in codes and codes["691"]["count"] == 1
    assert "认证" in codes["691"]["meaning"]


def test_parse_dial_log_empty_is_safe():
    result = parsers.parse_dial_log("")
    assert result["lines"] == 0
    assert result["error_codes"] == []
    assert result["auth_failures"] == 0


def test_parse_lqc_json_and_nested_wrapper():
    payload = json.dumps({"data": {"RSRP": "-96dBm", "SINR": 17, "band": "5G", "OpticalRxPower": "-24.6 dBm"}})
    result = parsers.parse_lqc(payload)
    assert result["source"] == "json"
    assert result["metrics"]["rsrp"] == -96
    assert result["metrics"]["rx_power"] == -24.6
    assert result["metrics"]["band"] == "5G"


def test_parse_lqc_key_value_text():
    result = parsers.parse_lqc("RSSI: -71, 信噪比=19 dB, retry=12%, 信道占用=58")
    assert result["metrics"]["rssi"] == -71
    assert result["metrics"]["snr"] == 19
    assert result["metrics"]["retry_pct"] == 12
    assert result["metrics"]["channel_util"] == 58


def test_parse_lqc_handles_placeholder_values():
    result = parsers.parse_lqc("RSRP=N/A SINR=- tx_rate=866")
    assert "rsrp" not in result["metrics"]
    assert result["metrics"]["tx_rate"] == 866


def test_parse_wifi_report():
    text = "SSID: home-5g\nRSSI: -68\nSNR: 24\n重传率: 9\n信道占用: 47\n协商速率: 866"
    result = parsers.parse_wifi_report(text)
    assert result["ssid"] == "home-5g"
    assert result["metrics"]["rssi"] == -68
    assert result["metrics"]["retry_pct"] == 9
