"""
小红书 CLI 统一封装层
所有对 xhs 命令的调用集中在此，便于维护和升级
"""
import json
import os
import shutil
import subprocess
import sys
import time
from typing import Optional

from config import (
    BATCH_REPLY_DELAY,
    CACHE_DIR,
    CACHE_TTL_MINUTES,
    LOGIN_COOKIE_SOURCE,
)
from .state_io import atomic_write_json, file_lock


class _PersistentReplySession:
    """复用一个 xhs 登录会话发送多条回复，并逐条返回结构化结果。"""

    def __init__(self, client, note_id):
        self.client = client
        self.note_id = note_id
        self.process = None

    def __enter__(self):
        tool_python = self.client._find_xhs_tool_python()
        helper = os.path.join(
            os.path.dirname(__file__), "xhs_reply_helper.py"
        )
        if not tool_python or not os.path.exists(helper):
            return self
        try:
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
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
        except OSError:
            self.process = None
        return self

    def __exit__(self, *_args):
        if self.process is None:
            return
        try:
            self.process.stdin.close()
        except (AttributeError, OSError):
            pass
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
        try:
            self.process.stdout.close()
        except (AttributeError, OSError):
            pass

    def reply(self, comment_id, content):
        """保持与 XHSClient.reply 相同的三元组返回约定。"""
        if self.process is None:
            return self.client.reply(self.note_id, comment_id, content)
        request = json.dumps(
            {"comment_id": comment_id, "content": content},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        try:
            self.process.stdin.write(request + "\n")
            self.process.stdin.flush()
            line = self.process.stdout.readline()
        except (AttributeError, BrokenPipeError, OSError):
            line = ""
        if not line:
            return False, "批量回复会话意外结束", "session_error"
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            return False, "批量回复会话返回了无效数据", "session_error"
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

    def comment(self, content, sequence=None):
        """在当前笔记发布顶层评论，复用同一个登录会话。"""
        if self.process is None:
            return False, "批量评论需要持久会话", "session_error", ""
        request = json.dumps(
            {
                "action": "comment",
                "sequence": sequence,
                "content": content,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        try:
            self.process.stdin.write(request + "\n")
            self.process.stdin.flush()
            line = self.process.stdout.readline()
        except (AttributeError, BrokenPipeError, OSError):
            line = ""
        if not line:
            return (
                False,
                "批量评论会话意外结束",
                "session_error",
                "",
            )
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            return (
                False,
                "批量评论会话返回了无效数据",
                "session_error",
                "",
            )
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


class XHSClient:
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

    # 内存缓存：避免批量操作中反复读取 skipped.json
    _skipped_cache: Optional[dict] = None
    _skipped_mtime: float = 0.0  # 文件修改时间，用于自动刷新
    _XHS_TOOL_PYTHON = os.environ.get("XHS_TOOL_PYTHON", "")

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

    # ---------- 笔记 ----------
    @staticmethod
    def get_my_notes(max_pages: int = None, strict: bool = False):
        """
        获取我的笔记列表（自动翻页直到取完或达到 max_pages 页）
        返回 [{id, title, comments_count, xsec_token, time}]
        参数:
            max_pages: 最多翻多少页（None 表示取完所有）
        """
        from config import READ_PAGE_DELAY

        all_notes = []
        xsec_entries = {}
        page = 0
        while True:
            if max_pages is not None and page >= max_pages:
                break
            try:
                data = XHSClient._run_xhs(
                    ["xhs", "my-notes", "--page", str(page), "--json"])
            except RuntimeError:
                if strict:
                    raise
                break
            if not data.get("ok"):
                if strict:
                    err = data.get("error", {}).get("message", str(data))
                    raise RuntimeError(f"获取文章列表失败: {err}")
                break
            notes = data["data"]["notes"]
            if not notes:
                break

            for n in notes:
                note_id = n["id"]
                token = n.get("xsec_token", "")
                all_notes.append({
                    "id": note_id,
                    "title": n.get("display_title", ""),
                    "comments_count": int(n.get("comments_count", 0) or 0),
                    "xsec_token": token,
                    "time": n.get("time", ""),
                })
                if note_id and token:
                    xsec_entries[note_id] = token
            page += 1
            if READ_PAGE_DELAY:
                time.sleep(READ_PAGE_DELAY)

        if xsec_entries:
            XHSClient._merge_xsec_index(xsec_entries)
        return all_notes

    @staticmethod
    def list_articles(limit: int = 20):
        """
        获取最新文章列表（格式化输出用）
        返回 [{id, title, comments_count, time, xsec_token}]
        """
        # 计算需要翻多少页（每页10条）
        pages_needed = (limit + 9) // 10
        notes = XHSClient.get_my_notes(max_pages=pages_needed, strict=True)
        return notes[:limit]

    @staticmethod
    def find_note_xsec(note_id, max_pages: int = None):
        """
        通过翻页查找指定笔记的 xsec_token（带本地索引缓存，O(1) 命中时无需翻页）

        参数:
            max_pages: 最多翻多少页（None 表示翻到底）
        """
        from config import AUTHOR_USER_ID, READ_PAGE_DELAY

        # 先查本地索引缓存
        cache = XHSClient._load_xsec_index()
        if note_id in cache:
            return cache[note_id]

        # 缓存未命中，翻页查找
        page = 0
        cursor = None
        new_entries = {}  # 本次发现的新条目，统一写入索引
        while True:
            if max_pages is not None and page >= max_pages:
                break
            page += 1
            cmd = ["xhs", "user-posts", AUTHOR_USER_ID, "--json"]
            if cursor:
                cmd += ["--cursor", cursor]
            try:
                data = XHSClient._run_xhs(cmd)
            except RuntimeError:
                break
            if not data.get("ok"):
                break
            notes = data.get("data", {}).get("notes", [])
            cursor = data.get("data", {}).get("cursor", "")
            for n in notes:
                nid = n.get("id", "")
                xtoken = n.get("xsec_token", "")
                if nid:
                    new_entries[nid] = xtoken
                if nid == note_id:
                    # 找到后仍继续记录本页其他条目，减少后续查找开销
                    pass
            if not cursor or not notes:
                break
            if READ_PAGE_DELAY:
                time.sleep(READ_PAGE_DELAY)

        # 写入索引缓存
        if new_entries:
            XHSClient._merge_xsec_index(new_entries)

        if note_id in new_entries:
            return new_entries[note_id]

        raise RuntimeError(f"在已发布的笔记中未找到 {note_id}（已翻 {page} 页）")

    @staticmethod
    def _xsec_index_path() -> str:
        from config import CACHE_DIR
        return os.path.join(CACHE_DIR, "xsec_index.json")

    @staticmethod
    def _load_xsec_index() -> dict:
        """加载 xsec_token 本地索引"""
        path = XHSClient._xsec_index_path()
        if not os.path.exists(path):
            return {}
        try:
            with open(path, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}

    @staticmethod
    def _merge_xsec_index(new_entries: dict):
        """原子合并令牌索引，并将文件权限限制为当前用户可读写。"""
        path = XHSClient._xsec_index_path()
        with file_lock(f"{path}.lock"):
            existing = {}
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as file:
                        existing = json.load(file)
                except (json.JSONDecodeError, IOError):
                    pass
            existing.update(new_entries)
            atomic_write_json(existing, path, mode=0o600)

    # ---------- 通知 ----------
    @staticmethod
    def get_notifications(num: int = 50, notification_type: str = "mentions",
                          strict: bool = False) -> list:
        """
        获取最新通知列表

        参数:
            num: 获取数量（默认50）
            notification_type: 通知类型 "mentions"（评论和@）、"likes"（赞和收藏）、"connections"（新增关注）

        返回: 通知条目列表，每条包含 type, title, user_info, item_info, comment_info 等
        """
        try:
            data = XHSClient._run_xhs(
                ["xhs", "notifications", "--type", notification_type,
                 "--num", str(num), "--json"])
        except RuntimeError:
            if strict:
                raise
            return []
        if not data.get("ok"):
            return []
        return data.get("data", {}).get("message_list", [])

    @staticmethod
    def get_new_comment_notifications(num: int = 50) -> list:
        """
        从最新通知中提取"评论了你的笔记"类型的通知，按笔记聚合

        返回: [
            {
                "note_id": str,
                "note_title": str,
                "note_xsec_token": str,
                "new_comments": [
                    {
                        "comment_id": str,
                        "nickname": str,
                        "content": str,
                        "time": int,       # unix timestamp
                    }
                ]
            }
        ]
        """
        notifications = XHSClient.get_notifications(
            num=num,
            notification_type="mentions",
            strict=True,
        )
        if not notifications:
            return []

        # 按 note_id 聚合
        by_note = {}  # note_id -> {note_title, note_xsec_token, new_comments: []}
        seen_comment_ids = {}
        xsec_entries = {}
        for n in notifications:
            ntype = n.get("type", "")
            # 只处理"评论了你的笔记"类型的通知
            if "comment" not in ntype and "item" not in ntype:
                continue

            item_info = n.get("item_info", {})
            note_id = item_info.get("id", "")
            if not note_id:
                continue
            note_xsec_token = item_info.get("xsec_token", "")
            if note_xsec_token:
                xsec_entries[note_id] = note_xsec_token

            comment_info = n.get("comment_info", {})
            comment_content = comment_info.get("content", "")

            # 从 link 中提取 anchorCommentId
            link = item_info.get("link", "")
            comment_id = ""
            if "anchorCommentId=" in link:
                comment_id = link.split("anchorCommentId=")[-1].split("&")[0]
            if not comment_id:
                comment_id = comment_info.get("id", "")

            if note_id not in by_note:
                by_note[note_id] = {
                    "note_id": note_id,
                    "note_title": item_info.get("content", ""),
                    "note_xsec_token": note_xsec_token,
                    "new_comments": [],
                }
                seen_comment_ids[note_id] = set()
            elif (
                note_xsec_token
                and not by_note[note_id].get("note_xsec_token")
            ):
                by_note[note_id]["note_xsec_token"] = note_xsec_token

            # 同一通知可能因分页或平台重复投递出现多次；禁止形成重复草稿。
            if not comment_id or comment_id in seen_comment_ids[note_id]:
                continue
            seen_comment_ids[note_id].add(comment_id)

            by_note[note_id]["new_comments"].append({
                "comment_id": comment_id,
                "nickname": n.get("user_info", {}).get("nickname", "?"),
                "content": comment_content,
                "time": n.get("time", 0),
                "target_comment_id": (
                    comment_info.get("target_comment", {}).get("id", "")
                ),
                "deleted": comment_info.get("illegal_info", {}).get(
                    "illegal_status", "NORMAL"
                ) not in ("", "NORMAL"),
            })

        # scan.json会主动移除敏感令牌；写入0600索引供draft/send安全接续。
        if xsec_entries:
            XHSClient._merge_xsec_index(xsec_entries)
        return list(by_note.values())

    # ---------- 评论 ----------
    @staticmethod
    def get_all_comments(note_id, xsec_token="",
                         include_sub_comments=False):
        """在一个登录会话内读取全部评论，必要时同时补全楼中楼。"""
        from config import READ_PAGE_DELAY

        tool_python = XHSClient._find_xhs_tool_python()
        helper = os.path.join(
            os.path.dirname(__file__), "xhs_comments_helper.py"
        )
        if tool_python and os.path.exists(helper):
            data = XHSClient._run_xhs([
                tool_python,
                helper,
                note_id,
                xsec_token,
                LOGIN_COOKIE_SOURCE,
                "500",
                "[]",
                "[]",
                "[]",
                "1" if include_sub_comments else "0",
                "",
            ], timeout=300)
            if not data.get("ok"):
                error_info = data.get("error", {})
                detail = (
                    error_info.get("message", "")
                    if isinstance(error_info, dict) else str(error_info)
                )
                raise RuntimeError(f"获取评论失败: {detail or data}")
            return data.get("data", {}).get("comments", [])

        # 兼容无法定位 xiaohongshu-cli Python 环境的安装方式。
        comments = []
        cursor = ""
        seen_cursors = set()
        while True:
            cmd = ["xhs", "comments", note_id, "--json"]
            if xsec_token:
                cmd += ["--xsec-token", xsec_token]
            if cursor:
                cmd += ["--cursor", cursor]
            try:
                data = XHSClient._run_xhs(cmd, timeout=30)
            except RuntimeError as error:
                raise RuntimeError(f"获取评论失败: {error}")
            if not data.get("ok"):
                err = data.get("error", {}).get("message", str(data))
                raise RuntimeError(f"获取评论失败: {err}")

            page = data.get("data", {})
            page_comments = page.get("comments", [])
            comments.extend(page_comments)
            next_cursor = str(page.get("cursor", "") or "")
            has_more = bool(page.get("has_more", False))
            if not has_more or not page_comments or not next_cursor:
                break
            if next_cursor in seen_cursors:
                raise RuntimeError("获取评论失败: 平台返回了重复游标")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
            if READ_PAGE_DELAY:
                time.sleep(READ_PAGE_DELAY)
        return comments

    @staticmethod
    def get_comments_until_ids(note_id, target_ids, xsec_token="",
                               max_pages=50, with_status=False,
                               target_groups=None, target_anchors=None,
                               expand_unresolved=True):
        """逐页读取，找到全部目标评论后立即停止，减少在线核验耗时。

        max_pages 为安全上限：每页约 10 条，50 页可覆盖约 500 条评论，
        足以覆盖绝大多数笔记；找到全部目标 ID 或无更多页时立即返回。
        target_groups 可表示候选定位的替代条件，例如候选ID或其目标评论ID
        任一出现即认为该候选的楼层上下文已定位。
        """
        from config import READ_PAGE_DELAY

        target_ids = set(target_ids)
        groups = [
            set(group) for group in (target_groups or [])
            if set(group)
        ]
        lookup_ids = set(target_ids)
        for group in groups:
            lookup_ids.update(group)
        anchor_groups = [
            list(dict.fromkeys(str(item) for item in anchors if item))
            for anchors in (target_anchors or [])
        ]
        # 原生 CLI 每翻一页都会新建进程并重新读取浏览器 Cookie。在线核验
        # 已有令牌时，优先在同一登录会话中连续翻页，保持原有停止条件。
        tool_python = XHSClient._find_xhs_tool_python()
        helper = os.path.join(
            os.path.dirname(__file__), "xhs_comments_helper.py"
        )
        if xsec_token and groups and tool_python and os.path.exists(helper):
            # 少量候选走楼层直达，通常十几秒完成；全量候选会在助手内改为
            # 顺序翻页并补全相关楼层，需要按规模放宽总进程时间。单次HTTP
            # 请求仍由助手限制为8秒，放宽这里只避免完整批次被45秒误杀。
            helper_timeout = min(
                300,
                max(45, 30 + max_pages * 3 + len(groups) // 5),
            )
            data = XHSClient._run_xhs([
                tool_python,
                helper,
                note_id,
                xsec_token,
                LOGIN_COOKIE_SOURCE,
                str(max_pages),
                json.dumps(
                    [sorted(group) for group in groups],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                json.dumps(
                    sorted(target_ids),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                json.dumps(
                    anchor_groups,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                "1" if expand_unresolved else "0",
                "",
            ], timeout=helper_timeout)
            if not data.get("ok"):
                error_info = data.get("error", {})
                detail = (
                    error_info.get("message", "")
                    if isinstance(error_info, dict) else str(error_info)
                )
                raise RuntimeError(f"获取评论失败: {detail or data}")
            payload = data.get("data", {})
            comments = payload.get("comments", [])
            search_complete = bool(payload.get("search_complete", False))
            return (
                (comments, search_complete)
                if with_status else comments
            )

        comments = []
        found_ids = set()
        cursor = ""
        seen_cursors = set()
        for _ in range(max_pages):
            cmd = ["xhs", "comments", note_id, "--json"]
            if xsec_token:
                cmd += ["--xsec-token", xsec_token]
            if cursor:
                cmd += ["--cursor", cursor]
            data = XHSClient._run_xhs(cmd, timeout=30)
            if not data.get("ok"):
                err = data.get("error", {}).get("message", str(data))
                raise RuntimeError(f"获取评论失败: {err}")
            page = data.get("data", {})
            page_comments = page.get("comments", [])
            comments.extend(page_comments)
            for comment in page_comments:
                comment_id = comment.get("id", "")
                if comment_id in lookup_ids:
                    found_ids.add(comment_id)
                for sub in comment.get("sub_comments", []):
                    sub_id = sub.get("id", "")
                    if sub_id in lookup_ids:
                        found_ids.add(sub_id)
            contexts_found = (
                all(group & found_ids for group in groups)
                if groups else found_ids == target_ids
            )
            if contexts_found:
                return (comments, True) if with_status else comments
            next_cursor = str(page.get("cursor", "") or "")
            if (
                not page.get("has_more", False)
                or not page_comments
                or not next_cursor
            ):
                return (comments, True) if with_status else comments
            if next_cursor in seen_cursors:
                raise RuntimeError("获取评论失败: 平台返回了重复游标")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
            if READ_PAGE_DELAY:
                time.sleep(READ_PAGE_DELAY)
        return (comments, False) if with_status else comments

    @staticmethod
    def get_sub_comments(note_id, comment_id, xsec_token="", strict=False):
        """
        获取某条评论下的所有楼中楼（自动翻页）。

        有令牌时优先使用兼容helper；普通helper故障才回退原生CLI。
        strict=True时验证码/verification_required立即停止，其他路径全部失败
        也会抛异常，供回复核验保留真实失败原因。
        """
        from config import READ_PAGE_DELAY
        all_subs = []
        cursor = None
        seen_cursors = set()
        while True:
            tool_python = XHSClient._find_xhs_tool_python()
            commands = []
            if xsec_token and tool_python:
                helper = os.path.join(
                    os.path.dirname(__file__), "xhs_subcomments_helper.py"
                )
                commands.append([
                    tool_python,
                    helper,
                    note_id,
                    comment_id,
                    cursor or "",
                    xsec_token,
                    LOGIN_COOKIE_SOURCE,
                ])
            native_cmd = [
                "xhs", "sub-comments", note_id, comment_id, "--json",
            ]
            if cursor:
                native_cmd += ["--cursor", cursor]
            commands.append(native_cmd)

            data = None
            errors = []
            for command in commands:
                try:
                    candidate_data = XHSClient._run_xhs(command)
                except (RuntimeError, json.JSONDecodeError) as error:
                    message = str(error)
                    errors.append(message)
                    if (
                        strict
                        and (
                            "verification_required" in message.casefold()
                            or "captcha" in message.casefold()
                            or "验证码" in message
                        )
                    ):
                        # 验证码是会话/接口风控；换另一传输立即重试只会重复
                        # 同一平台请求，不能提高成功率，还可能加重风控。
                        raise RuntimeError(
                            f"获取楼中楼失败: {message}"
                        ) from error
                    continue
                if candidate_data.get("ok"):
                    data = candidate_data
                    break
                error_info = candidate_data.get("error", {})
                if isinstance(error_info, dict):
                    code = str(error_info.get("code", "") or "").strip()
                    message = str(
                        error_info.get("message", "") or ""
                    ).strip()
                    detail = (
                        f"{code}: {message}".strip(": ")
                        or str(candidate_data)
                    )
                    errors.append(detail)
                    normalized = detail.casefold()
                    if (
                        strict
                        and (
                            "verification_required" in normalized
                            or "captcha" in normalized
                            or "验证码" in detail
                        )
                    ):
                        raise RuntimeError(
                            f"获取楼中楼失败: {detail}"
                        )
                else:
                    errors.append(str(candidate_data))
            if data is None:
                message = "；".join(errors[-2:]) or "未知错误"
                if strict:
                    raise RuntimeError(f"获取楼中楼失败: {message}")
                if any("verification" in error for error in errors):
                    print("    ⚠️ 楼中楼需要验证，返回已获取的数据")
                break

            page_comments = data.get("data", {}).get("comments", [])
            if page_comments:
                all_subs.extend(page_comments)
            next_cursor = str(data.get("data", {}).get("cursor", "") or "")
            if not next_cursor or not page_comments:
                break
            if next_cursor in seen_cursors:
                if strict:
                    raise RuntimeError("获取楼中楼失败: 平台返回了重复游标")
                break
            seen_cursors.add(next_cursor)
            cursor = next_cursor
            if READ_PAGE_DELAY:
                time.sleep(READ_PAGE_DELAY)
        return all_subs

    @staticmethod
    def comment_exists(note_id, comment_id):
        """
        检查评论是否仍然存在（是否已被删除）

        返回: (exists: bool, err_msg: str)
        """
        result = subprocess.run(
            ["xhs", "sub-comments", note_id, comment_id],
            capture_output=True, text=True, timeout=15
        )
        output = result.stdout + result.stderr
        # 正常返回子评论列表或空列表 → 评论存在
        if "ok: true" in output or '"ok": true' in output:
            return True, ""
        # 尝试检测评论不存在
        for keyword in ["not found", "不存在", "已删除", "deleted", "-1"]:
            if keyword in output.lower():
                return False, output[:200]
        # 返回空但没报错也可能是存在（只是无子回复），保守认为存在
        if result.returncode == 0 and output.strip() == "":
            return True, ""
        return True, ""  # 无法判定时保守认为存在

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

    # ---------- 缓存 ----------
    @staticmethod
    def _cache_path(note_id: str) -> str:
        """获取缓存文件路径"""
        return os.path.join(CACHE_DIR, f"{note_id}.json")

    @staticmethod
    def _ensure_cache_dir():
        """确保缓存目录存在"""
        os.makedirs(CACHE_DIR, exist_ok=True)

    @staticmethod
    def load_cache(note_id: str, max_age_minutes: int = None) -> list | None:
        """
        加载缓存的评论数据，如果缓存有效（未过期）则返回评论列表，否则返回 None

        参数:
            note_id: 笔记ID
            max_age_minutes: 最大缓存年龄（分钟），默认使用 CACHE_TTL_MINUTES
        """
        if max_age_minutes is None:
            max_age_minutes = CACHE_TTL_MINUTES

        cache_path = XHSClient._cache_path(note_id)
        if not os.path.exists(cache_path):
            return None

        try:
            with open(cache_path, "r") as f:
                cached = json.load(f)

            age = time.time() - cached.get("timestamp", 0)
            if age > max_age_minutes * 60:
                print(f"  ⏰ 缓存已过期（{age/60:.1f}分钟），重新拉取...")
                return None

            print(f"  📦 使用缓存（{age/60:.1f}分钟前）")
            return cached.get("comments", [])

        except (json.JSONDecodeError, KeyError):
            return None

    @staticmethod
    def save_cache(note_id: str, comments: list):
        """保存评论到缓存"""
        XHSClient._ensure_cache_dir()
        cache_path = XHSClient._cache_path(note_id)
        with open(cache_path, "w") as f:
            json.dump({
                "timestamp": time.time(),
                "comments": comments,
            }, f, ensure_ascii=False)
        print(f"  💾 评论已缓存（{len(comments)}条）")

    @staticmethod
    def get_comments_cached(note_id: str, xsec_token: str = "",
                            force_refresh: bool = False,
                            max_age_minutes: int = None,
                            include_sub_comments: bool = False
                            ) -> tuple[list, bool]:
        """
        获取评论（优先使用缓存）

        默认不带 xsec_token 请求；如果平台拒绝（如部分笔记需要 token），
        自动降级：查找 xsec_token 后重试。

        返回: (comments_list, from_cache: bool)
        """
        if not force_refresh:
            cached = XHSClient.load_cache(note_id, max_age_minutes)
            if cached is not None:
                nested_complete = all(
                    int(item.get("sub_comment_count", 0) or 0)
                    <= len(item.get("sub_comments", []))
                    for item in cached
                )
                if not include_sub_comments or nested_complete:
                    return cached, True

        comments = None
        # 第一次：不带 xsec_token（优先）
        try:
            comments = XHSClient.get_all_comments(
                note_id,
                "",
                include_sub_comments=include_sub_comments,
            )
            XHSClient.save_cache(note_id, comments)
            return comments, False
        except RuntimeError:
            pass

        # 降级：查找 xsec_token 后重试
        if xsec_token:
            resolved_token = xsec_token
        else:
            try:
                resolved_token = XHSClient.find_note_xsec(note_id)
            except RuntimeError:
                resolved_token = ""

        if resolved_token:
            try:
                comments = XHSClient.get_all_comments(
                    note_id,
                    resolved_token,
                    include_sub_comments=include_sub_comments,
                )
                XHSClient.save_cache(note_id, comments)
                return comments, False
            except RuntimeError:
                pass

        # 两次都失败了
        if comments is None:
            raise RuntimeError(f"获取评论失败: 尝试了不带/带 xsec_token 两种方式均失败")
        return comments, False

    @staticmethod
    def invalidate_cache(note_id: str):
        """清除指定笔记的缓存"""
        cache_path = XHSClient._cache_path(note_id)
        if os.path.exists(cache_path):
            os.remove(cache_path)
            return True
        return False

    # ---------- 跳过列表 ----------
    @staticmethod
    def _skipped_path() -> str:
        from config import SKIPPED_FILE
        return SKIPPED_FILE

    @staticmethod
    def load_skipped(force_reload: bool = False) -> dict:
        """
        加载跳过列表（带内存缓存，文件 mtime 变化时自动刷新）

        返回: {comment_id: {nickname, content, reason, skipped_at, note_id}}
        """
        path = XHSClient._skipped_path()
        if not os.path.exists(path):
            XHSClient._skipped_cache = {}
            XHSClient._skipped_mtime = 0
            return {}
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

        # 检查文件是否被外部修改过
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return {}

        if (not force_reload and XHSClient._skipped_cache is not None
                and mtime <= XHSClient._skipped_mtime):
            return XHSClient._skipped_cache

        try:
            with open(path, "r") as f:
                XHSClient._skipped_cache = json.load(f)
                XHSClient._skipped_mtime = mtime
                return XHSClient._skipped_cache
        except (json.JSONDecodeError, IOError):
            XHSClient._skipped_cache = {}
            XHSClient._skipped_mtime = 0
            return {}

    @staticmethod
    def _write_skipped():
        """将内存缓存写回磁盘并记录 mtime"""
        if XHSClient._skipped_cache is None:
            return
        path = XHSClient._skipped_path()
        with file_lock(f"{path}.lock"):
            XHSClient._write_skipped_unlocked(path)

    @staticmethod
    def _write_skipped_unlocked(path=None):
        """调用方已持有锁时写入跳过列表。"""
        if XHSClient._skipped_cache is None:
            return
        path = path or XHSClient._skipped_path()
        atomic_write_json(
            XHSClient._skipped_cache, path, mode=0o600, indent=2
        )
        XHSClient._skipped_mtime = os.path.getmtime(path)

    @staticmethod
    def save_skipped(skipped: dict):
        """保存跳过列表（直接写入磁盘，用于外部导入场景）"""
        path = XHSClient._skipped_path()
        with file_lock(f"{path}.lock"):
            XHSClient._skipped_cache = skipped
            XHSClient._write_skipped_unlocked(path)

    @staticmethod
    def add_skipped(comment_id: str, nickname: str = "", content: str = "",
                    reason: str = "manual", note_id: str = ""):
        """将一条评论加入跳过列表（先更新内存缓存，再写盘）"""
        path = XHSClient._skipped_path()
        with file_lock(f"{path}.lock"):
            skipped = XHSClient.load_skipped(force_reload=True)
            skipped[comment_id] = {
                "nickname": nickname,
                "content": content[:80],
                "reason": reason,
                "skipped_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "note_id": note_id,
            }
            XHSClient._write_skipped_unlocked(path)

    @staticmethod
    def remove_skipped(comment_id: str) -> bool:
        """从跳过列表中移除"""
        path = XHSClient._skipped_path()
        with file_lock(f"{path}.lock"):
            skipped = XHSClient.load_skipped(force_reload=True)
            if comment_id in skipped:
                del skipped[comment_id]
                XHSClient._write_skipped_unlocked(path)
                return True
            return False

    @staticmethod
    def is_skipped(comment_id: str) -> bool:
        """检查评论是否在跳过列表中（O(1) 内存查找）"""
        return comment_id in XHSClient.load_skipped()

    @staticmethod
    def get_skipped_ids() -> set:
        """获取所有跳过的评论ID集合"""
        return set(XHSClient.load_skipped().keys())
