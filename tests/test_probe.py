import threading

import pytest

from netriage.probe import download, download_multi, ping, probe, probe_payload, summarize_ping, upload, watch
from netriage.server import make_server


@pytest.fixture()
def server(tmp_path):
    control = make_server("127.0.0.1", 0, db_path=str(tmp_path / "history.db"))
    thread = threading.Thread(target=control.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    yield "http://127.0.0.1:%d" % control.server_address[1], control
    control.shutdown()
    control.server_close()
    thread.join(timeout=2)


def test_ping_on_loopback_has_no_loss(server):
    base, _ = server
    samples = ping(base, count=5, interval=0.02)
    summary = summarize_ping(samples)
    assert len(samples) == 5
    assert summary["loss_pct"] == 0
    assert summary["avg_ms"] is not None and summary["avg_ms"] > 0
    assert summary["jitter_ms"] >= 0


def test_summarize_counts_failures():
    summary = summarize_ping([1.0, None, 3.0, None])
    assert summary["loss_pct"] == 50.0
    assert summary["avg_ms"] == 2.0


def test_download_returns_bytes_and_speed(server):
    base, _ = server
    result = download(base, 0.5)
    assert result["bytes"] == int(0.5 * 1024 * 1024)
    assert result["mbps"] > 0


def test_download_multi_uses_four_streams(server):
    base, _ = server
    result = download_multi(base, 0.8, streams=4)
    assert result["streams"] == 4
    assert result["bytes"] == int(0.8 * 1024 * 1024) // 4 * 4
    assert result["mbps"] > 0


def test_upload_returns_server_side_number(server):
    base, _ = server
    result = upload(base, 0.25)
    assert result["mbps"] > 0
    assert result["server_mbps"] is not None and result["server_mbps"] > 0


def test_probe_returns_diagnosable_measure(server):
    base, _ = server
    measure = probe(base, download_mb=0.4, upload_mb=0.2, ping_count=4, load_cap_mb=2)
    for field in ("down_mbps", "up_mbps", "latency_idle_ms", "jitter_ms", "loss_pct"):
        assert measure[field] is not None
    assert measure["down_mbps"] > 0
    assert measure["latency_loaded_ms"] is None or measure["latency_loaded_ms"] >= 0

    payload = probe_payload(base, plan={"down": 1000, "up": 100},
                            download_mb=0.4, upload_mb=0.2, ping_count=4, load_cap_mb=2)
    assert payload["plan"]["down"] == 1000
    assert payload["measure"]["target"] == base


def test_watch_stores_each_round(server):
    base, control = server
    rounds = watch(base, interval=0, count=1, plan={"down": 1000, "up": 100},
                   download_mb=0.3, upload_mb=0.15, ping_count=3, load_cap_mb=2, sleep=lambda _: None)
    assert len(rounds) == 1
    assert rounds[0]["id"] is not None
    assert control.store.count() == 1
    assert rounds[0]["result"]["health"] >= 0
