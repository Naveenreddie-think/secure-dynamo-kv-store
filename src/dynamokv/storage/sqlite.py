import json
import sqlite3
from pathlib import Path
from typing import Any, Optional


class SqliteStorage:
    """File-backed storage. Data survives a process restart.

    Opens a fresh connection per call rather than holding one open --
    simpler and avoids sqlite's single-thread-per-connection restriction
    under FastAPI's threaded request handling. Fine at Phase 1 scale.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    def get(self, key: str) -> Optional[Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row is not None else None

    def put(self, key: str, value: Any) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO kv (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, json.dumps(value)),
            )

    def delete(self, key: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM kv WHERE key = ?", (key,))
        return cursor.rowcount > 0

    def exists(self, key: str) -> bool:
        with self._connect() as conn:
            row = conn.execute("SELECT 1 FROM kv WHERE key = ?", (key,)).fetchone()
        return row is not None
