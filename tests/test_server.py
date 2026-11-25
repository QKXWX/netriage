import http.client
import json
import threading

import pytest

from netriage.server import make_server


@pytest.fixture()
def port():
    server = make_server("127.0.0.1", 0)
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
