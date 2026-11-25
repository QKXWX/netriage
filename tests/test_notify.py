import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from netriage import notify


RESULT = {
    "health": 42,
    "verdict": "高度怀疑：上联拥塞 / 缓冲膨胀",
    "causes": [
        {"id": "congestion", "name": "上联拥塞 / 缓冲膨胀", "confidence": 0.72},
        {"id": "qos", "name": "路由 QoS / 单流限速", "confidence": 0.41},
    ],
}
MEASURE = {"down_mbps": 505.0, "up_mbps": 96.0, "latency_idle_ms": 18.4,
           "latency_loaded_ms": 322.0, "bufferbloat_ms": 303.6,
           "jitter_ms": 9.2, "loss_pct": 0.8}


def test_summarize_has_numbers_and_top_cause():
    text = notify.summarize(RESULT, MEASURE)
    assert "42 分" in text
    assert "上联拥塞" in text
    assert "505.0 Mbps" in text
    assert "72%" in text


def test_build_data_is_compact():
    data = notify.build_data(RESULT, MEASURE, at="2026-11-25T20:00:00+08:00")
    assert data["source"] == "netriage"
    assert data["event"] == "degradation"
    assert data["created_at"] == "2026-11-25T20:00:00+08:00"
    assert len(data["causes"]) == 2
    assert data["measure"]["down_mbps"] == 505.0


def test_envelope_adapts_chat_bots():
    text, data = "hello", {"health": 42}
    assert notify._envelope("https://open.feishu.cn/open-apis/bot/v2/hook/x", text, data) == {
        "msg_type": "text", "content": {"text": text}}
    assert notify._envelope("https://oapi.dingtalk.com/robot/send?access_token=x", text, data) == {
        "msgtype": "text", "text": {"content": text}}
    assert notify._envelope("https://hooks.slack.com/services/x", text, data) == {"text": text}
    generic = notify._envelope("https://example.com/hook", text, data)
    assert generic["health"] == 42 and generic["text"] == text


def test_send_posts_json_and_reads_response():
    captured = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            captured["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
            captured["type"] = self.headers.get("Content-Type")
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status = notify.send("http://127.0.0.1:%d/hook" % server.server_address[1], RESULT, MEASURE, timeout=5)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert status == 200
    assert "application/json" in captured["type"]
    assert captured["body"]["source"] == "netriage"
    assert captured["body"]["health"] == 42
