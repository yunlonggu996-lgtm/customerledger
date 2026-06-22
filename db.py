"""本地 SQLite 存储：将远端数据缓存到本地并支持本地编辑。"""

import json
import sqlite3
import threading
from datetime import datetime

from config import DB_PATH


# 用于唯一标识一条记录的字段
PRIMARY_KEY = "组织UUID"


class Database:
    def __init__(self, path=DB_PATH):
        self.path = path
        self._lock = threading.RLock()
        self._init_schema()

    def _connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self):
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS records (
                    id TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    synced_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );
                """
            )
            conn.commit()

    # ---------- 同步相关 ----------
    def replace_all(self, records, columns):
        """整批替换远端数据；保留本地编辑。"""
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock, self._connect() as conn:
            existing = {
                row["id"]: json.loads(row["data"])
                for row in conn.execute("SELECT id, data FROM records")
            }
            conn.execute("BEGIN")
            try:
                conn.execute("DELETE FROM records")
                for rec in records:
                    rid = rec.get(PRIMARY_KEY) or rec.get("id")
                    if not rid:
                        continue
                    merged = dict(rec)
                    if rid in existing:
                        # 保留本地编辑过的字段（编辑时间晚于上次同步时间）
                        local = existing[rid]
                        local_updated = local.get("__local_updated_fields__") or {}
                        for k, v in local_updated.items():
                            merged[k] = v
                    conn.execute(
                        "INSERT OR REPLACE INTO records(id, data, updated_at, synced_at) "
                        "VALUES (?, ?, ?, ?)",
                        (rid, json.dumps(merged, ensure_ascii=False), now, now),
                    )
                conn.execute(
                    "INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)",
                    ("columns", json.dumps(columns, ensure_ascii=False)),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)",
                    ("last_sync", now),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def update_field(self, record_id, field, value):
        """更新本地字段，记录编辑时间。"""
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT data FROM records WHERE id=?", (record_id,)
            ).fetchone()
            if not row:
                return False
            data = json.loads(row["data"])
            data[field] = value
            edited = dict(data.get("__local_updated_fields__") or {})
            edited[field] = value
            data["__local_updated_fields__"] = edited
            conn.execute(
                "UPDATE records SET data=?, updated_at=? WHERE id=?",
                (json.dumps(data, ensure_ascii=False), now, record_id),
            )
            conn.commit()
            return True

    def clear_local_edits(self, record_id):
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT data, synced_at FROM records WHERE id=?", (record_id,)
            ).fetchone()
            if not row:
                return False
            data = json.loads(row["data"])
            data.pop("__local_updated_fields__", None)
            conn.execute(
                "UPDATE records SET data=?, updated_at=? WHERE id=?",
                (json.dumps(data, ensure_ascii=False), row["synced_at"],
                 record_id),
            )
            conn.commit()
            return True

    # ---------- 查询 ----------
    def get_columns(self):
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM meta WHERE key='columns'"
            ).fetchone()
            if row and row["value"]:
                return json.loads(row["value"])
            return []

    def get_last_sync(self):
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM meta WHERE key='last_sync'"
            ).fetchone()
            return row["value"] if row else None

    def count(self):
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM records").fetchone()
            return row["c"]

    def search(self, keyword="", fields=None, limit=1000):
        """本地搜索：keyword 命中 records.data 中的任意文本字段。"""
        fields = fields or []
        with self._lock, self._connect() as conn:
            if not keyword:
                rows = conn.execute(
                    "SELECT id, data FROM records LIMIT ?", (limit,)
                ).fetchall()
            else:
                kw = f"%{keyword}%"
                if fields:
                    # 限定字段
                    clauses = " OR ".join([f"data LIKE ?"] * len(fields))
                    params = [kw] * len(fields)
                else:
                    clauses = "data LIKE ?"
                    params = [kw]
                rows = conn.execute(
                    f"SELECT id, data FROM records WHERE {clauses} LIMIT ?",
                    (*params, limit),
                ).fetchall()
            return [
                {"__id__": r["id"], **json.loads(r["data"])} for r in rows
            ]

    def get(self, record_id):
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT data FROM records WHERE id=?", (record_id,)
            ).fetchone()
            return json.loads(row["data"]) if row else None
