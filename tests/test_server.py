import http.client
import json
import threading

import pytest

from netriage.server import make_server


@pytest.fixture()
def port(tmp_path):
    server = make_server("127.0.0.1", 0, db_path=str(tmp_path / "history.db"))
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def request(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request(method, path, body=body, headers=headers or {})
    response = conn.getresponse()
    data = response.read()
    status = response.status
    content_type = response.getheader("Content-Type") or ""
    conn.close()
    return status, data, content_type


def test_ping_returns_204(port):
    status, data, _ = request(port, "GET", "/api/ping")
    assert status == 204
    assert data == b""


def test_health_and_meta(port):
    status, data, _ = request(port, "GET", "/api/health")
    payload = json.loads(data)
    assert status == 200 and payload["ok"] is True

    status, data, _ = request(port, "GET", "/api/meta")
    payload = json.loads(data)
    assert payload["version"]
    assert payload["max_download_mb"] > 0


def test_download_streams_exact_length(port):
    status, data, content_type = request(port, "GET", "/api/download?mb=0.5")
    assert status == 200
    assert len(data) == 512 * 1024
    assert content_type == "application/octet-stream"


def test_download_caps_absurd_size(port):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", "/api/download?mb=99999")
    response = conn.getresponse()
    assert response.status == 200
    assert response.getheader("X-Netriage-Bytes") == str(512 * 1024 * 1024)  # 被 MAX_DOWNLOAD_MB 截断
    conn.close()

def test_diagnose_saves_history(port):
    from netriage.samples import demo_payload
    body = json.dumps(demo_payload()).encode("utf-8")
    status, data, _ = request(port, "POST", "/api/diagnose", body=body,
                              headers={"Content-Type": "application/json"})
    payload = json.loads(data)
    assert status == 200
    assert isinstance(payload["id"], int)

    status, data, _ = request(port, "GET", "/api/history?limit=5")
    listing = json.loads(data)
    assert listing["count"] == 1
    assert listing["runs"][0]["id"] == payload["id"]
    assert listing["runs"][0]["verdict"] == payload["result"]["verdict"]

    status, data, _ = request(port, "GET", "/api/history/%d" % payload["id"])
    record = json.loads(data)
    assert status == 200
    assert record["run"]["result"]["health"] == payload["result"]["health"]
    assert record["run"]["payload"]["measure"]["down_mbps"] == 318.4


def test_diagnose_can_skip_saving(port):
    from netriage.samples import demo_payload
    body = json.dumps(demo_payload()).encode("utf-8")
    status, data, _ = request(port, "POST", "/api/diagnose?save=0", body=body,
                              headers={"Content-Type": "application/json"})
    assert json.loads(data)["id"] is None
    status, data, _ = request(port, "GET", "/api/history")
    assert json.loads(data)["count"] == 0


def test_history_clear(port):
    from netriage.samples import demo_payload
    body = json.dumps(demo_payload()).encode("utf-8")
    request(port, "POST", "/api/diagnose", body=body, headers={"Content-Type": "application/json"})
    status, data, _ = request(port, "POST", "/api/history/clear", body=b"")
    assert json.loads(data)["removed"] == 1
    status, data, _ = request(port, "GET", "/api/history")
    assert json.loads(data)["count"] == 0


def test_history_missing_id(port):
    status, data, _ = request(port, "GET", "/api/history/9999")
    assert status == 404


def test_report_endpoint_returns_html(port):
    from netriage.samples import demo_payload
    body = json.dumps(demo_payload()).encode("utf-8")
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("POST", "/api/report", body=body, headers={"Content-Type": "application/json"})
    response = conn.getresponse()
    html = response.read().decode("utf-8")
    assert response.status == 200
    assert "text/html" in (response.getheader("Content-Type") or "")
    assert "attachment" in (response.getheader("Content-Disposition") or "")
    assert "宽带质量诊断报告" in html
    conn.close()
