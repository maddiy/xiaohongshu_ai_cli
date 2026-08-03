"""跨进程锁、SQLite权威状态和JSON兼容快照。"""

import fcntl
import json
import os
import tempfile
import time
from contextlib import contextmanager

from .state_db import state_db, state_key_for_path
from .json_codec import json_digest


WORKFLOW_STATE_READ_POLICIES = {
    "scan.json": "program_state",
    "reply_map.json": "ai_input",
    "drafts.json": "program_state",
    "audit.json": "program_state",
}


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
        if json_digest(value) != state_db().document_hash(key):
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


def read_workflow_state(path, default=None, role=None):
    """按文件职责选择权威来源，并修复程序状态的失配兼容快照。

    ai_input由AI编辑的JSON快照优先；program_state以SQLite为准，若快照
    缺失、损坏或语义不同，则用SQLite值恢复快照。
    """
    path = os.path.abspath(path)
    role = role or WORKFLOW_STATE_READ_POLICIES.get(
        os.path.basename(path), "program_state"
    )
    if role not in {"ai_input", "program_state"}:
        raise ValueError(f"未知工作流状态读取角色: {role}")
    value = read_json_state(
        path,
        default=default,
        prefer_snapshot=role == "ai_input",
    )
    key = state_key_for_path(path)
    if role != "program_state" or not key or value is None:
        return value

    snapshot_matches = False
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as file:
                snapshot_matches = json_digest(json.load(file)) == json_digest(
                    value
                )
        except (OSError, json.JSONDecodeError):
            snapshot_matches = False
    if not snapshot_matches:
        _write_json_snapshot(value, path, mode=0o600, indent=2)
    return value


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
