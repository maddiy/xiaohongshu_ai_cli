"""跨进程锁、SQLite权威状态和JSON兼容快照。"""

import fcntl
import hashlib
import json
import os
import tempfile
import time
from contextlib import contextmanager

from .state_db import state_db, state_key_for_path


class StateLockTimeout(RuntimeError):
    """另一进程正在修改同一状态文件。"""


@contextmanager
def file_lock(lock_path, timeout=5.0):
    """在限定时间内取得跨进程文件锁。"""
    lock_path = os.path.abspath(lock_path)
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    with open(lock_path, "a+", encoding="utf-8") as lock_file:
        deadline = time.monotonic() + max(float(timeout), 0.0)
        while True:
            try:
                fcntl.flock(
                    lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB
                )
                break
            except BlockingIOError as error:
                if time.monotonic() >= deadline:
                    raise StateLockTimeout(
                        f"状态文件正由另一进程处理: {lock_path}"
                    ) from error
                time.sleep(0.1)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _write_json_snapshot(data, path, mode=None, indent=None):
    """刷新到磁盘后原子替换兼容快照。"""
    path = os.path.abspath(path)
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    temp_path = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=directory,
            prefix=f".{os.path.basename(path)}.",
            suffix=".tmp",
            delete=False,
        ) as file:
            temp_path = file.name
            json.dump(
                data,
                file,
                ensure_ascii=False,
                indent=indent,
                **({"separators": (",", ":")} if indent is None else {}),
            )
            file.flush()
            os.fsync(file.fileno())
        if mode is not None:
            os.chmod(temp_path, mode)
        os.replace(temp_path, path)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)
    return path


def atomic_write_json(data, path, mode=None, indent=None):
    """事务写入SQLite，并刷新兼容JSON快照。"""
    path = os.path.abspath(path)
    key = state_key_for_path(path)
    if key:
        state_db().put_document(key, data)
        mode = 0o600 if mode is None else mode
    return _write_json_snapshot(data, path, mode=mode, indent=indent)


def read_json_state(path, default=None, prefer_snapshot=False):
    """读取权威状态；reply_map等外部编辑入口可优先同步兼容快照。"""
    path = os.path.abspath(path)
    key = state_key_for_path(path)
    if key and prefer_snapshot and os.path.exists(path):
        with open(path, encoding="utf-8") as file:
            value = json.load(file)
        encoded = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        if hashlib.sha256(encoded).hexdigest() != state_db().document_hash(key):
            state_db().put_document(key, value)
        return value
    if key:
        value = state_db().get_document(key, default=None)
        if value is not None:
            return value
    if os.path.exists(path):
        with open(path, encoding="utf-8") as file:
            value = json.load(file)
        if key:
            state_db().put_document(key, value)
        return value
    return default


def json_state_exists(path):
    key = state_key_for_path(path)
    if key and state_db().document_hash(key):
        return True
    return os.path.exists(path)


def delete_json_state(path):
    """删除数据库记录和兼容快照。"""
    path = os.path.abspath(path)
    key = state_key_for_path(path)
    deleted = state_db().delete_document(key) if key else False
    if os.path.exists(path):
        os.unlink(path)
        deleted = True
    return deleted


def migrate_legacy_json():
    return state_db().migrate_legacy_json()
