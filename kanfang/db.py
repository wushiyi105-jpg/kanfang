"""SQLite 持久层:存看过的房源与生成的报告,方便横向对比。

Web 是 ThreadingHTTPServer,每请求一线程,所以用 check_same_thread=False
加一把锁,把对同一连接的访问串行化。
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading

DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "kanfang.db")


class Database:
    def __init__(self, path: str | None = None):
        self.path = path or os.environ.get("KANFANG_DB") or DEFAULT_DB_PATH
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self):
        with self._lock:
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS scans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    city TEXT,
                    rent REAL,
                    area REAL,
                    layout TEXT,
                    monthly_cost REAL,
                    first_payment REAL,
                    source TEXT,
                    report TEXT,
                    created_at TEXT DEFAULT (datetime('now', 'localtime'))
                )
                """
            )
            self.conn.commit()

    def save(self, item: dict, report: dict, source: str = "cli") -> int:
        layout = ""
        if item.get("rooms"):
            layout = f"{item['rooms']}室{item.get('halls') or 0}厅"
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO scans (city, rent, area, layout, monthly_cost, first_payment, source, report) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (item.get("city"), item.get("rent"), item.get("area"), layout,
                 item.get("monthly_cost"), item.get("first_payment"), source,
                 json.dumps(report, ensure_ascii=False)),
            )
            self.conn.commit()
            return int(cur.lastrowid)

    def latest(self, limit: int = 10) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT id, city, rent, area, layout, monthly_cost, first_payment, created_at "
                "FROM scans ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def get(self, scan_id: int) -> dict | None:
        with self._lock:
            row = self.conn.execute("SELECT * FROM scans WHERE id = ?", (scan_id,)).fetchone()
        if not row:
            return None
        data = dict(row)
        data["report"] = json.loads(data["report"]) if data.get("report") else {}
        return data

    def clear(self):
        with self._lock:
            self.conn.execute("DELETE FROM scans")
            self.conn.commit()

    def close(self):
        self.conn.close()
