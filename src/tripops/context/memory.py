import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any


class SQLiteMemoryStore:
    """Small durable store for cross-thread user preferences and decisions."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def put(self, namespace: str, key: str, value: Any) -> None:
        self._validate_key(namespace, "namespace")
        self._validate_key(key, "key")
        serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO memories(namespace, key, value_json, updated_at)
                VALUES (?, ?, ?, unixepoch())
                ON CONFLICT(namespace, key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_at = excluded.updated_at
                """,
                (namespace, key, serialized),
            )

    def get(self, namespace: str, key: str) -> Any | None:
        self._validate_key(namespace, "namespace")
        self._validate_key(key, "key")
        with self._connection() as connection:
            row = connection.execute(
                "SELECT value_json FROM memories WHERE namespace = ? AND key = ?",
                (namespace, key),
            ).fetchone()
        return None if row is None else json.loads(row[0])

    def list_namespace(self, namespace: str) -> dict[str, Any]:
        self._validate_key(namespace, "namespace")
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT key, value_json FROM memories WHERE namespace = ? ORDER BY key",
                (namespace,),
            ).fetchall()
        return {key: json.loads(value_json) for key, value_json in rows}

    def delete(self, namespace: str, key: str) -> bool:
        with self._connection() as connection:
            cursor = connection.execute(
                "DELETE FROM memories WHERE namespace = ? AND key = ?",
                (namespace, key),
            )
        return cursor.rowcount > 0

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS memories(
                    namespace TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY(namespace, key)
                )
                """
            )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _validate_key(value: str, label: str) -> None:
        if not value or len(value) > 200:
            raise ValueError(f"{label} must contain 1 to 200 characters")

