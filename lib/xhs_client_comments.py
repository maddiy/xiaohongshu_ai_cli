"""XHSClient的平台评论树读取和楼中楼核验能力。"""

import json
import os
import subprocess
import time

from config import LOGIN_COOKIE_SOURCE
from .xhs_client_proxy import XHSClient


class XHSCommentsMixin:
    """评论分页、候选定位和楼中楼读取。"""

    @staticmethod
    def get_all_comments(
        note_id, xsec_token="", include_sub_comments=False
    ):
        """在一个登录会话内读取全部评论。"""
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

        comments = []
        cursor = ""
        seen_cursors = set()
        while True:
            command = ["xhs", "comments", note_id, "--json"]
            if xsec_token:
                command += ["--xsec-token", xsec_token]
            if cursor:
                command += ["--cursor", cursor]
            try:
                data = XHSClient._run_xhs(command, timeout=30)
            except RuntimeError as error:
                raise RuntimeError(f"获取评论失败: {error}") from error
            if not data.get("ok"):
                message = data.get("error", {}).get("message", str(data))
                raise RuntimeError(f"获取评论失败: {message}")
            page = data.get("data", {})
            page_comments = page.get("comments", [])
            comments.extend(page_comments)
            next_cursor = str(page.get("cursor", "") or "")
            if (
                not page.get("has_more", False)
                or not page_comments
                or not next_cursor
            ):
                break
            if next_cursor in seen_cursors:
                raise RuntimeError("获取评论失败: 平台返回了重复游标")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
            if READ_PAGE_DELAY:
                time.sleep(READ_PAGE_DELAY)
        return comments

    @staticmethod
    def get_comments_until_ids(
        note_id,
        target_ids,
        xsec_token="",
        max_pages=50,
        with_status=False,
        target_groups=None,
        target_anchors=None,
        expand_unresolved=True,
    ):
        """逐页读取并在定位全部候选楼层后立即停止。"""
        from config import READ_PAGE_DELAY

        target_ids = set(target_ids)
        groups = [
            set(group) for group in (target_groups or []) if set(group)
        ]
        lookup_ids = set(target_ids)
        for group in groups:
            lookup_ids.update(group)
        anchor_groups = [
            list(dict.fromkeys(str(item) for item in anchors if item))
            for anchors in (target_anchors or [])
        ]
        tool_python = XHSClient._find_xhs_tool_python()
        helper = os.path.join(
            os.path.dirname(__file__), "xhs_comments_helper.py"
        )
        if xsec_token and groups and tool_python and os.path.exists(helper):
            helper_timeout = min(
                300, max(45, 30 + max_pages * 3 + len(groups) // 5)
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
                (comments, search_complete) if with_status else comments
            )

        comments = []
        found_ids = set()
        cursor = ""
        seen_cursors = set()
        for _ in range(max_pages):
            command = ["xhs", "comments", note_id, "--json"]
            if xsec_token:
                command += ["--xsec-token", xsec_token]
            if cursor:
                command += ["--cursor", cursor]
            data = XHSClient._run_xhs(command, timeout=30)
            if not data.get("ok"):
                message = data.get("error", {}).get("message", str(data))
                raise RuntimeError(f"获取评论失败: {message}")
            page = data.get("data", {})
            page_comments = page.get("comments", [])
            comments.extend(page_comments)
            for comment in page_comments:
                comment_id = comment.get("id", "")
                if comment_id in lookup_ids:
                    found_ids.add(comment_id)
                for sub_comment in comment.get("sub_comments", []):
                    sub_id = sub_comment.get("id", "")
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
    def get_sub_comments(
        note_id, comment_id, xsec_token="", strict=False
    ):
        """获取指定一级评论下的全部楼中楼。"""
        from config import READ_PAGE_DELAY

        all_subs = []
        cursor = None
        seen_cursors = set()
        while True:
            tool_python = XHSClient._find_xhs_tool_python()
            commands = []
            if xsec_token and tool_python:
                helper = os.path.join(
                    os.path.dirname(__file__),
                    "xhs_subcomments_helper.py",
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
            native_command = [
                "xhs", "sub-comments", note_id, comment_id, "--json",
            ]
            if cursor:
                native_command += ["--cursor", cursor]
            commands.append(native_command)

            data = None
            errors = []
            for command in commands:
                try:
                    candidate_data = XHSClient._run_xhs(command)
                except (RuntimeError, json.JSONDecodeError) as error:
                    message = str(error)
                    errors.append(message)
                    if strict and (
                        "verification_required" in message.casefold()
                        or "captcha" in message.casefold()
                        or "验证码" in message
                    ):
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
                    detail = f"{code}: {message}".strip(": ") or str(
                        candidate_data
                    )
                    errors.append(detail)
                    normalized = detail.casefold()
                    if strict and (
                        "verification_required" in normalized
                        or "captcha" in normalized
                        or "验证码" in detail
                    ):
                        raise RuntimeError(f"获取楼中楼失败: {detail}")
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
            next_cursor = str(
                data.get("data", {}).get("cursor", "") or ""
            )
            if not next_cursor or not page_comments:
                break
            if next_cursor in seen_cursors:
                if strict:
                    raise RuntimeError(
                        "获取楼中楼失败: 平台返回了重复游标"
                    )
                break
            seen_cursors.add(next_cursor)
            cursor = next_cursor
            if READ_PAGE_DELAY:
                time.sleep(READ_PAGE_DELAY)
        return all_subs

    @staticmethod
    def comment_exists(note_id, comment_id):
        """保守检查指定评论是否仍然存在。"""
        result = subprocess.run(
            ["xhs", "sub-comments", note_id, comment_id],
            capture_output=True,
            text=True,
            timeout=15,
        )
        output = result.stdout + result.stderr
        if "ok: true" in output or '"ok": true' in output:
            return True, ""
        for keyword in ["not found", "不存在", "已删除", "deleted", "-1"]:
            if keyword in output.lower():
                return False, output[:200]
        if result.returncode == 0 and output.strip() == "":
            return True, ""
        return True, output[:200]
