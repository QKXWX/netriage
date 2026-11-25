#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""历史测量存档：SQLite 单文件，零第三方依赖。

每次 `/api/diagnose` 都会把「输入载荷 + 诊断结果」一起落盘，
用来回答一个单次测速回答不了的问题：**是一直这么慢，还是只有现在慢**。
"""

import json
import os
import sqlite3
import threading
import time

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    health INTEGER,
    verdict TEXT,
    cause_id TEXT,
    cause_name TEXT,
    plan_down REAL,
    plan_up REAL,
    down_mbps REAL,
    up_mbps REAL,
    down_single_mbps REAL,
    latency_idle_ms REAL,
    latency_loaded_ms REAL,
    bufferbloat_ms REAL,
    jitter_ms REAL,
    loss_pct REAL,
    payload TEXT NOT NULL,
    result TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_created_at ON runs(created_at);
"""


def default_db_path():
    """默认存档位置：环境变量优先，其次项目目录下的 data/。"""
    env = os.environ.get("NETRIAGE_DB")
    if env:
        return env
    return os.path.join(os.getcwd(), "data", "netriage.db")


def _float(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


class HistoryStore:
    """线程安全的测量存档。每个进程一个实例，连接跨线程复用（加锁串行化）。"""

    def __init__(self, path=None):
        self.path = path or default_db_path()
        directory = os.path.dirname(os.path.abspath(self.path))
        if directory and not os.path.isdir(directory):
            os.makedirs(directory, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # ---------------- 写 ----------------
    def save(self, payload, result):
        """保存一次诊断，返回记录 id。"""
        metrics = result.get("metrics") or {}
        causes = result.get("causes") or []
        top = causes[0] if causes else {}
        created = result.get("generated_at") or time.strftime("%Y-%m-%dT%H:%M:%S")
        row = (
            created,
            result.get("health"),
            result.get("verdict"),
            top.get("id"),
            top.get("name"),
            _float(metrics.get("plan_down")),
            _float(metrics.get("plan_up")),
            _float(metrics.get("down_mbps")),
            _float(metrics.get("up_mbps")),
            _float(metrics.get("down_single_mbps")),
            _float(metrics.get("latency_idle_ms")),
            _float(metrics.get("latency_loaded_ms")),
            _float(metrics.get("bufferbloat_ms")),
            _float(metrics.get("jitter_ms")),
            _float(metrics.get("loss_pct")),
            json.dumps(payload, ensure_ascii=False),
            json.dumps(result, ensure_ascii=False),
        )
        with self._lock:
            cursor = self._conn.execute(
                "INSERT INTO runs (created_at, health, verdict, cause_id, cause_name,"
                " plan_down, plan_up, down_mbps, up_mbps, down_single_mbps,"
                " latency_idle_ms, latency_loaded_ms, bufferbloat_ms, jitter_ms, loss_pct,"
                " payload, result) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                row,
            )
            self._conn.commit()
            return int(cursor.lastrowid)

    def clear(self):
        with self._lock:
            cursor = self._conn.execute("DELETE FROM runs")
            self._conn.commit()
            return cursor.rowcount

    # ---------------- 读 ----------------
    def count(self):
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0])

    def list(self, limit=20, offset=0):
        """按时间倒序返回摘要列表（不含完整 result，页面列表够用）。"""
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, created_at, health, verdict, cause_id, cause_name,"
                " plan_down, plan_up, down_mbps, up_mbps, down_single_mbps,"
                " latency_idle_ms, latency_loaded_ms, bufferbloat_ms, jitter_ms, loss_pct"
                " FROM runs ORDER BY id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [dict(row) for row in rows]

    def get(self, run_id):
        with self._lock:
            row = self._conn.execute("SELECT * FROM runs WHERE id = ?", (int(run_id),)).fetchone()
        if row is None:
            return None
        data = dict(row)
        try:
            data["payload"] = json.loads(data.get("payload") or "{}")
        except ValueError:
            data["payload"] = {}
        try:
            data["result"] = json.loads(data.get("result") or "{}")
        except ValueError:
            data["result"] = {}
        return data

    def close(self):
        with self._lock:
            self._conn.close()
