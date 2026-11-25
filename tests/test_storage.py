
from netriage.diagnose import diagnose
from netriage.samples import demo_payload
from netriage.storage import HistoryStore


def _payload():
    return demo_payload()


def test_save_then_list_and_get(tmp_path):
    store = HistoryStore(str(tmp_path / "history.db"))
    payload = _payload()
    result = diagnose(payload)
    run_id = store.save(payload, result)
    assert run_id >= 1
    assert store.count() == 1

    runs = store.list()
    assert len(runs) == 1
    row = runs[0]
    assert row["id"] == run_id
    assert row["health"] == result["health"]
    assert row["cause_id"] == result["causes"][0]["id"]
    assert row["down_mbps"] == result["metrics"]["down_mbps"]

    record = store.get(run_id)
    assert record["payload"]["plan"]["down"] == 1000
    assert record["result"]["verdict"] == result["verdict"]
    store.close()


def test_newest_first_and_limit(tmp_path):
    store = HistoryStore(str(tmp_path / "history.db"))
    for _ in range(3):
        store.save(_payload(), diagnose(_payload()))
    runs = store.list(limit=2)
    assert len(runs) == 2
    assert runs[0]["id"] == 3
    assert store.count() == 3
    store.close()


def test_clear_removes_all(tmp_path):
    store = HistoryStore(str(tmp_path / "history.db"))
    store.save(_payload(), diagnose(_payload()))
    store.save(_payload(), diagnose(_payload()))
    removed = store.clear()
    assert removed == 2
    assert store.count() == 0
    assert store.list() == []
    store.close()


def test_reopen_keeps_data(tmp_path):
    path = str(tmp_path / "history.db")
    store = HistoryStore(path)
    store.save(_payload(), diagnose(_payload()))
    store.close()

    again = HistoryStore(path)
    assert again.count() == 1
    assert again.get(1)["result"]["health"] == diagnose(_payload())["health"]
    again.close()


def test_missing_record_returns_none(tmp_path):
    store = HistoryStore(str(tmp_path / "history.db"))
    assert store.get(999) is None
    store.close()
