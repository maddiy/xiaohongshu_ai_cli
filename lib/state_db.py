"""SQLite 权威状态库；使用 JSON 文档值兼容现有业务数据结构。"""

import datetime
import hashlib
import json
import os
import sqlite3
import threading
from contextlib import closing
from pathlib import Path

from config import CACHE_DIR, STATE_DB_FILE


DB_SCHEMA_VERSION = 1
_MISSING = object()


def _timestamp():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _canonical_json(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _payload_hash(value):
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def state_key_for_path(path, cache_root=CACHE_DIR):
    """把.cache内的JSON兼容路径映射为稳定数据库键。"""
    absolute = Path(path).expanduser().resolve()
    cache_root = Path(cache_root).resolve()
    try:
        relative = absolute.relative_to(cache_root)
    except ValueError:
        return ""
    if absolute.suffix.lower() != ".json":
        return ""
    return relative.as_posix()


class StateDB:
    """小型事务状态库；每次操作独立连接，适合多AI跨进程接续。"""

    def __init__(self, path=STATE_DB_FILE):
        self.path = os.path.abspath(path)
        self._initialized = False
        self._initialize_lock = threading.Lock()

    def _connect(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA foreign_keys=ON")
        if not self._initialized:
            with self._initialize_lock:
                if not self._initialized:
                    connection.execute("PRAGMA journal_mode=WAL")
                    connection.executescript(
                        """
                        CREATE TABLE IF NOT EXISTS metadata (
                            key TEXT PRIMARY KEY,
                            value TEXT NOT NULL,
                            updated_at TEXT NOT NULL
                        );
                        CREATE TABLE IF NOT EXISTS documents (
                            key TEXT PRIMARY KEY,
                            payload TEXT NOT NULL,
                            payload_hash TEXT NOT NULL,
                            updated_at TEXT NOT NULL
                        );
                        CREATE TABLE IF NOT EXISTS migrations (
                            source_path TEXT PRIMARY KEY,
                            imported_at TEXT NOT NULL,
                            payload_hash TEXT NOT NULL
                        );
                        """
                    )
                    connection.execute(
                        "INSERT INTO metadata(key,value,updated_at) "
                        "VALUES(?,?,?) ON CONFLICT(key) DO NOTHING",
                        (
                            "db_schema_version",
                            str(DB_SCHEMA_VERSION),
                            _timestamp(),
                        ),
                    )
                    connection.commit()
                    try:
                        os.chmod(self.path, 0o600)
                    except OSError:
                        pass
                    self._initialized = True
        return connection

    def get_document(self, key, default=_MISSING):
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT payload FROM documents WHERE key=?", (str(key),)
            ).fetchone()
        if row is None:
            if default is _MISSING:
                raise KeyError(key)
            return default
        return json.loads(row[0])

    def document_hash(self, key):
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT payload_hash FROM documents WHERE key=?", (str(key),)
            ).fetchone()
        return row[0] if row else ""

    def put_document(self, key, value):
        payload = _canonical_json(value)
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO documents(key,payload,payload_hash,updated_at)
                VALUES(?,?,?,?)
                ON CONFLICT(key) DO UPDATE SET
                    payload=excluded.payload,
                    payload_hash=excluded.payload_hash,
                    updated_at=excluded.updated_at
                """,
                (str(key), payload, digest, _timestamp()),
            )
            connection.commit()
        return digest

    def delete_document(self, key):
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "DELETE FROM documents WHERE key=?", (str(key),)
            )
            connection.commit()
        return bool(cursor.rowcount)

    def get_setting(self, key, default=""):
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT value FROM metadata WHERE key=?", (str(key),)
            ).fetchone()
        return row[0] if row else default

    def set_setting(self, key, value):
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO metadata(key,value,updated_at) VALUES(?,?,?)
                ON CONFLICT(key) DO UPDATE SET
                    value=excluded.value, updated_at=excluded.updated_at
                """,
                (str(key), str(value), _timestamp()),
            )
            connection.commit()

    def migrate_legacy_json(self, cache_dir=CACHE_DIR):
        """幂等导入旧JSON；保留原文件作为可恢复兼容快照。"""
        root = Path(cache_dir).resolve()
        imported = 0
        skipped = 0
        errors = []
        parsed = []
        if not root.exists():
            return {"imported": 0, "skipped": 0, "errors": []}
        with closing(self._connect()) as connection:
            existing_keys = {
                row[0] for row in connection.execute(
                    "SELECT key FROM documents"
                ).fetchall()
            }
        for path in sorted(root.rglob("*.json")):
            key = state_key_for_path(path, root)
            if not key:
                continue
            if key in existing_keys:
                skipped += 1
                continue
            try:
                with path.open(encoding="utf-8") as file:
                    value = json.load(file)
            except (OSError, json.JSONDecodeError) as error:
                errors.append({"path": str(path), "error": str(error)})
                continue
            digest = _payload_hash(value)
            parsed.append((path, key, value, digest))
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            for path, key, value, digest in parsed:
                known = connection.execute(
                    "SELECT 1 FROM documents WHERE key=?", (key,)
                ).fetchone()
                if known:
                    skipped += 1
                    continue
                connection.execute(
                    "INSERT INTO documents(key,payload,payload_hash,updated_at) "
                    "VALUES(?,?,?,?)",
                    (key, _canonical_json(value), digest, _timestamp()),
                )
                connection.execute(
                    "INSERT OR REPLACE INTO migrations"
                    "(source_path,imported_at,payload_hash) VALUES(?,?,?)",
                    (str(path), _timestamp(), digest),
                )
                imported += 1
            connection.commit()
        return {"imported": imported, "skipped": skipped, "errors": errors}


_STATE_DB = StateDB()


def state_db():
    return _STATE_DB
