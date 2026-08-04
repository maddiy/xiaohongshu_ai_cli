"""
小红书 CLI 统一封装层
所有对 xhs 命令的调用集中在此，便于维护和升级
"""
import json
import os
import select
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Optional, TextIO

from config import (
    BATCH_REPLY_DELAY,
    CACHE_DIR,
    CACHE_TTL_MINUTES,
    LOGIN_COOKIE_SOURCE,
    PERSISTENT_HELPER_RESPONSE_TIMEOUT,
)
from .state_io import (
    atomic_write_json,
    delete_json_state,
    file_lock,
    json_state_exists,
    read_json_state,
)
from .state_db import state_db
from .xhs_client_comments import XHSCommentsMixin
from .xhs_client_content import XHSContentMixin
from .xhs_client_state import XHSStateMixin


class _PersistentReplySession:
    """复用一个 xhs 登录会话发送多条回复，并逐条返回结构化结果。"""

    def __init__(self, client: Any, note_id: str):
        self.client = client
        self.note_id = note_id
        self.process: Optional[subprocess.Popen] = None
        self.stderr_file: Optional[TextIO] = None
        self.response_timeout = PERSISTENT_HELPER_RESPONSE_TIMEOUT

    def __enter__(self) -> "_PersistentReplySession":
        tool_python = self.client._find_xhs_tool_python()
        helper = os.path.join(
            os.path.dirname(__file__), "xhs_reply_helper.py"
        )
        if not tool_python or not os.path.exists(helper):
            return self
        try:
            self.stderr_file = tempfile.TemporaryFile(
                mode="w+t", encoding="utf-8"
            )
            self.process = subprocess.Popen(
                [
                    tool_python,
                    helper,
                    self.note_id,
                    LOGIN_COOKIE_SOURCE,
                    str(BATCH_REPLY_DELAY),
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=self.stderr_file,
                text=True,
                bufsize=1,
            )
        except OSError:
            self.process = None
            if self.stderr_file is not None:
                self.stderr_file.close()
                self.stderr_file = None
        return self

    def _terminate_process(self) -> None:
        if self.process is None:
            return
        try:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
        except (AttributeError, OSError):
            pass

    def _stderr_tail(self, limit: int = 400) -> str:
        """只回读临时诊断尾部；发现凭据字段名时隐藏原文。"""
        if self.stderr_file is None or self.stderr_file.closed:
            return ""
        try:
            self.stderr_file.flush()
            end = self.stderr_file.seek(0, os.SEEK_END)
            self.stderr_file.seek(max(0, end - limit))
            text = self.stderr_file.read().strip()
            self.stderr_file.seek(0, os.SEEK_END)
        except (OSError, ValueError):
            return ""
        if any(marker in text.casefold() for marker in (
            "xsec_token", "cookie", "authorization", "set-cookie",
        )):
            return "[诊断包含敏感凭据字段，已隐藏]"
        return text[-limit:]

    def _readline_with_timeout(self) -> Optional[str]:
        if self.process is None or self.process.stdout is None:
            return ""
        stream = self.process.stdout
        try:
            ready, _, _ = select.select(
                [stream], [], [], max(float(self.response_timeout), 0.1)
            )
        except (TypeError, ValueError, OSError):
            # 测试替身或非POSIX流没有可select的文件描述符；真实helper管道
            # 在支持环境中总是走上面的有界等待。
            return stream.readline()
        if not ready:
            return None
        return stream.readline()

    def _exchange(self, request: dict, label: str) -> tuple:
        if self.process is None or self.process.stdin is None:
            return None, f"{label}需要持久会话"
        encoded = json.dumps(
            request, ensure_ascii=False, separators=(",", ":")
        )
        try:
            self.process.stdin.write(encoded + "\n")
            self.process.stdin.flush()
            line = self._readline_with_timeout()
        except (AttributeError, BrokenPipeError, OSError):
            line = ""
        if line is None:
            self._terminate_process()
            diagnostic = self._stderr_tail()
            detail = f"{label}响应超时（{self.response_timeout:g}秒）"
            if diagnostic:
                detail += f"；helper诊断: {diagnostic}"
            return None, detail
        if not line:
            diagnostic = self._stderr_tail()
            detail = f"{label}意外结束"
            if diagnostic:
                detail += f"；helper诊断: {diagnostic}"
            return None, detail
        try:
            return json.loads(line), ""
        except json.JSONDecodeError:
            return None, f"{label}返回了无效数据"

    def __exit__(self, *_args: Any) -> None:
        if self.process is not None:
            try:
                self.process.stdin.close()
            except (AttributeError, OSError):
                pass
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._terminate_process()
            try:
                self.process.stdout.close()
            except (AttributeError, OSError):
                pass
        if self.stderr_file is not None:
            self.stderr_file.close()
            self.stderr_file = None

    def reply(self, comment_id: str, content: str) -> tuple:
        """保持与 XHSClient.reply 相同的三元组返回约定。"""
        if self.process is None:
            return self.client.reply(self.note_id, comment_id, content)
        payload, session_error = self._exchange(
            {"comment_id": comment_id, "content": content},
            "批量回复会话",
        )
        if payload is None:
            return False, session_error, "session_error"
        if payload.get("ok"):
            return True, "", ""
        error = payload.get("error", {})
        code = (
            str(error.get("code", "") or "")
            if isinstance(error, dict) else ""
        )
        message = (
            str(error.get("message", "") or "")
            if isinstance(error, dict) else str(error)
        )
        detail = f"{code}: {message}".strip(": ") or "批量回复失败"
        error_type = (
            code
            if code in self.client.REPLY_ERROR_TYPES
            else self.client._classify_reply_error(message, detail)
        )
        return False, detail[:200], error_type

    def comment(
        self, content: str, sequence: Optional[int] = None
    ) -> tuple:
        """在当前笔记发布顶层评论，复用同一个登录会话。"""
        if self.process is None:
            return False, "批量评论需要持久会话", "session_error", ""
        payload, session_error = self._exchange({
            "action": "comment",
            "sequence": sequence,
            "content": content,
        }, "批量评论会话")
        if payload is None:
            return False, session_error, "session_error", ""
        if payload.get("ok"):
            return True, "", "", str(payload.get("comment_id", "") or "")
        error = payload.get("error", {})
        code = (
            str(error.get("code", "") or "")
            if isinstance(error, dict) else ""
        )
        message = (
            str(error.get("message", "") or "")
            if isinstance(error, dict) else str(error)
        )
        detail = f"{code}: {message}".strip(": ") or "批量评论失败"
        error_type = (
            code
            if code in self.client.REPLY_ERROR_TYPES
            else self.client._classify_reply_error(message, detail)
        )
        return False, detail[:200], error_type, ""


class XHSClient(XHSContentMixin, XHSCommentsMixin, XHSStateMixin):
    """封装 xiaohongshu-cli 的所有功能"""

    REPLY_ERROR_TYPES = (
        "comment_deleted",
        "rate_limited",
        "content_rejected",
        "permission_denied",
        "verification_required",
        "not_authenticated",
        "session_error",
        "unknown_error",
    )
    REPLY_ERROR_MARKERS = {
        "comment_deleted": ("评论已删除",),
        "rate_limited": ("-9043", "太快", "过快", "频率", "请稍后"),
        "content_rejected": ("-9126", "-9128"),
        "permission_denied": (
            "-9131", "无法发表评论", "对方设置",
        ),
        "verification_required": (
            "verification_required", "captcha", "验证码",
        ),
        "not_authenticated": (
            "not_authenticated", "未登录", "登录已过期",
        ),
        "session_error": ("批量回复会话",),
    }

    # 内存缓存：避免批量操作中反复解析排除列表兼容快照。
    _skipped_cache: Optional[dict] = None
    _skipped_mtime: float = 0.0  # 文件修改时间，用于自动刷新
    _XHS_TOOL_PYTHON = os.environ.get("XHS_TOOL_PYTHON", "")
    _author_user_id_cache = ""

    @classmethod
    def _find_xhs_tool_python(cls) -> str:
        """从环境变量、xhs 启动脚本或 uv 默认位置定位工具解释器。"""
        candidates = []
        if cls._XHS_TOOL_PYTHON:
            candidates.append(cls._XHS_TOOL_PYTHON)
        xhs_path = shutil.which("xhs")
        if xhs_path:
            try:
                with open(os.path.realpath(xhs_path), encoding="utf-8") as file:
                    first_line = file.readline().strip()
                if first_line.startswith("#!"):
                    candidates.append(first_line[2:])
            except OSError:
                pass
        candidates.append(os.path.expanduser(
            "~/.local/share/uv/tools/xiaohongshu-cli/bin/python"
        ))
        return next(
            (path for path in candidates if path and os.path.exists(path)), ""
        )

    @staticmethod
    def _cli_error_message(result) -> str:
        """优先保留 CLI stdout 中的结构化错误码和消息。"""
        stdout = (result.stdout or "").strip()
        if stdout:
            try:
                payload = json.loads(stdout)
            except (TypeError, json.JSONDecodeError):
                payload = None
            if isinstance(payload, dict):
                error = payload.get("error")
                if isinstance(error, dict):
                    code = str(error.get("code", "") or "").strip()
                    message = str(error.get("message", "") or "").strip()
                    if code and message:
                        return f"{code}: {message}"
                    if code or message:
                        return code or message
        return (result.stderr or "").strip() or stdout[:200] or "无错误详情"

    @staticmethod
    def _run_xhs(cmd: list, timeout: int = 30) -> dict:
        """执行 xhs CLI 命令并检查返回码，返回解析后的 JSON"""
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout
            )
        except subprocess.TimeoutExpired:
            # TimeoutExpired 的默认文本会包含完整命令参数，其中可能带有
            # xsec_token。只返回脱敏后的固定错误。
            raise RuntimeError(f"xhs 请求超时（{timeout}秒）") from None
        if result.returncode != 0:
            detail = XHSClient._cli_error_message(result)
            raise RuntimeError(
                f"xhs 命令失败 (exit={result.returncode}): {detail}"
            )
        data = json.loads(result.stdout)
        if not data.get("ok"):
            return data  # 非 ok 但命令本身成功（如无更多数据）
        return data

    # ---------- 账户 ----------
    @staticmethod
    def login():
        """从配置指定的浏览器 Cookie 登录。"""
        browser = LOGIN_COOKIE_SOURCE
        print(f"🔑 使用 {browser} 浏览器 cookies 登录...")
        result = subprocess.run(
            ["xhs", "login", "--cookie-source", browser],
            capture_output=True, text=True, timeout=30
        )
        output = result.stdout + result.stderr
        if "登录成功" in output or "ok" in result.stdout.lower():
            try:
                XHSClient.get_author_user_id(force_refresh=True)
            except RuntimeError:
                # 登录成功优先返回；身份会在第一次需要判断作者回复时重试。
                pass
            print(f"  ✅ {browser} 登录成功")
            return True
        print(f"  ❌ {browser} 失败: {result.stderr.strip() or result.stdout.strip()[:200]}")
        print(f"  💡 请确保 {browser} 已登录小红书账号")
        return False

    @staticmethod
    def whoami():
        """获取当前登录账户信息"""
        result = subprocess.run(
            ["xhs", "whoami"],
            capture_output=True, text=True, timeout=15
        )
        return result.stdout.strip()

    @classmethod
    def get_author_user_id(cls, force_refresh: bool = False) -> str:
        """自动识别并缓存当前登录账号ID，不依赖公开仓库配置。"""
        environment_id = os.environ.get("XHS_AUTHOR_USER_ID", "").strip()
        if environment_id:
            state_db().set_setting("author_user_id", environment_id)
            cls._author_user_id_cache = environment_id
            return environment_id
        if cls._author_user_id_cache and not force_refresh:
            return cls._author_user_id_cache
        database = state_db()
        if not force_refresh:
            cached = database.get_setting("author_user_id", "").strip()
            if cached:
                cls._author_user_id_cache = cached
                return cached
        data = cls._run_xhs(["xhs", "whoami", "--json"], timeout=30)
        user = data.get("data", {}).get("user", {}) if data.get("ok") else {}
        user_id = str(
            user.get("id") or user.get("user_id") or user.get("userid") or ""
        ).strip()
        if not user_id:
            message = data.get("error", {}).get("message", "未返回账号ID")
            raise RuntimeError(f"无法自动识别当前小红书账号: {message}")
        database.set_setting("author_user_id", user_id)
        cls._author_user_id_cache = user_id
        return user_id

    # ---------- 回复 ----------
    @staticmethod
    def _extract_error_from_output(output: str) -> tuple:
        """
        从 xhs CLI 输出中提取错误信息（支持 JSON 和 YAML 两种格式）

        返回: (err_msg: str, error_str: str)
        """
        err_msg = ""
        error_str = ""

        def unwrap_api_error(message):
            """提取“API error: {json}”内真正的中文msg和错误码。"""
            if "API error:" not in message:
                return message
            api_part = message.split("API error:", 1)[1].strip()
            try:
                api_data = json.loads(api_part)
            except json.JSONDecodeError:
                return message
            if not isinstance(api_data, dict):
                return message
            inner_message = str(api_data.get("msg", "") or "").strip()
            inner_code = str(api_data.get("code", "") or "").strip()
            if inner_message and inner_code:
                return f"{inner_message} ({inner_code})"
            return inner_message or message

        # 尝试1: JSON 格式解析
        try:
            err_data = json.loads(output.strip())
            if isinstance(err_data, dict):
                err_msg = err_data.get("error", {}).get("message", "")
                error_str = json.dumps(err_data, ensure_ascii=False)[:200]
                if err_msg:
                    return unwrap_api_error(err_msg), error_str
        except (json.JSONDecodeError, AttributeError):
            pass

        # 尝试2: YAML 格式解析（手动提取 message 字段）
        try:
            # 查找 message 行: "  message: '...'" 或 "  message: ..."
            for line in output.split("\n"):
                stripped = line.strip()
                if stripped.startswith("message:") or stripped.startswith("message: "):
                    # 提取 message 值（支持单引号和双引号）
                    val = stripped.split(":", 1)[1].strip()
                    if val.startswith("'") and val.endswith("'"):
                        err_msg = val[1:-1]
                    elif val.startswith('"') and val.endswith('"'):
                        err_msg = val[1:-1]
                    else:
                        err_msg = val
                    # 如果 msg 中包含 'API error: {...}', 提取真正的 msg
                    err_msg = unwrap_api_error(err_msg)
                    error_str = output[:200]
                    break
        except Exception:
            pass

        if not error_str:
            error_str = output[:200]
        return err_msg, error_str

    @staticmethod
    def _classify_reply_error(err_msg: str, output: str) -> str:
        """按权威标记和固定优先级识别回复失败类型。"""
        combined = f"{err_msg}\n{output}".lower()
        for error_type, markers in XHSClient.REPLY_ERROR_MARKERS.items():
            if any(marker.lower() in combined for marker in markers):
                return error_type
        return "unknown_error"

    @staticmethod
    def reply(note_id, comment_id, content):
        """
        回复评论

        返回: (ok: bool, err_msg: str, err_type: str)
          成功时 err_type=""；失败时取 REPLY_ERROR_TYPES 中的值。
        """
        result = subprocess.run(
            ["xhs", "reply", note_id, "--comment-id", comment_id, "-c", content],
            capture_output=True, text=True, timeout=20
        )
        output = result.stdout + result.stderr
        if "ok: true" in output or '"ok": true' in output:
            return True, "", ""

        # 提取错误信息（优先从 stdout，fallback 到合并输出）
        raw_output = result.stdout or output
        err_msg, error_str = XHSClient._extract_error_from_output(raw_output)

        err_type = XHSClient._classify_reply_error(err_msg, output)
        return False, error_str, err_type

    def reply_session(self, note_id):
        """创建复用Cookie和网络连接的批量回复会话。"""
        return _PersistentReplySession(self, note_id)
