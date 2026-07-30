"""跨进程状态锁和原子JSON写入；不依赖业务模块。"""

import fcntl
import json
import os
import tempfile
import time
from contextlib import contextmanager


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


def atomic_write_json(data, path, mode=None, indent=None):
    """刷新到磁盘后原子替换JSON；可选设置最终文件权限。"""
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
