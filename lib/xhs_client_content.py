"""XHSClient的笔记、令牌索引和通知读取能力。"""

import json
import os
import time

from .state_io import (
    atomic_write_json,
    file_lock,
    json_state_exists,
    read_json_state,
)
from .xhs_client_proxy import XHSClient


class XHSContentMixin:
    """笔记列表、xsec索引和评论通知。"""

    @staticmethod
    def get_my_notes(max_pages: int = None, strict: bool = False):
        """获取我的笔记列表，自动翻页。"""
        from config import READ_PAGE_DELAY

        all_notes = []
        xsec_entries = {}
        page = 0
        while True:
            if max_pages is not None and page >= max_pages:
                break
            try:
                data = XHSClient._run_xhs(
                    ["xhs", "my-notes", "--page", str(page), "--json"]
                )
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
            for note in notes:
                note_id = note["id"]
                token = note.get("xsec_token", "")
                all_notes.append({
                    "id": note_id,
                    "title": note.get("display_title", ""),
                    "comments_count": int(
                        note.get("comments_count", 0) or 0
                    ),
                    "view_count": int(note.get("view_count", 0) or 0),
                    "xsec_token": token,
                    "time": note.get("time", ""),
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
        """获取指定数量的最新文章。"""
        pages_needed = (limit + 9) // 10
        notes = XHSClient.get_my_notes(
            max_pages=pages_needed, strict=True
        )
        return notes[:limit]

    @staticmethod
    def find_note_xsec(note_id, max_pages: int = None):
        """从本地索引或已发布笔记中解析xsec_token。"""
        from config import READ_PAGE_DELAY

        cache = XHSClient._load_xsec_index()
        if note_id in cache:
            return cache[note_id]
        page = 0
        cursor = None
        new_entries = {}
        author_user_id = XHSClient.get_author_user_id()
        while True:
            if max_pages is not None and page >= max_pages:
                break
            page += 1
            command = ["xhs", "user-posts", author_user_id, "--json"]
            if cursor:
                command += ["--cursor", cursor]
            try:
                data = XHSClient._run_xhs(command)
            except RuntimeError:
                break
            if not data.get("ok"):
                break
            notes = data.get("data", {}).get("notes", [])
            cursor = data.get("data", {}).get("cursor", "")
            for note in notes:
                current_id = note.get("id", "")
                token = note.get("xsec_token", "")
                if current_id:
                    new_entries[current_id] = token
            if not cursor or not notes:
                break
            if READ_PAGE_DELAY:
                time.sleep(READ_PAGE_DELAY)
        if new_entries:
            XHSClient._merge_xsec_index(new_entries)
        if note_id in new_entries:
            return new_entries[note_id]
        raise RuntimeError(
            f"在已发布的笔记中未找到 {note_id}（已翻 {page} 页）"
        )

    @staticmethod
    def _xsec_index_path() -> str:
        from config import CACHE_DIR

        return os.path.join(CACHE_DIR, "xsec_index.json")

    @staticmethod
    def _load_xsec_index() -> dict:
        path = XHSClient._xsec_index_path()
        if not json_state_exists(path):
            return {}
        try:
            return read_json_state(path) or {}
        except (json.JSONDecodeError, IOError, TypeError):
            return {}

    @staticmethod
    def _merge_xsec_index(new_entries: dict):
        path = XHSClient._xsec_index_path()
        with file_lock(f"{path}.lock"):
            existing = {}
            if json_state_exists(path):
                try:
                    existing = read_json_state(path) or {}
                except (json.JSONDecodeError, IOError, TypeError):
                    pass
            existing.update(new_entries)
            atomic_write_json(existing, path, mode=0o600)

    @staticmethod
    def get_notifications(
        num: int = 50,
        notification_type: str = "mentions",
        strict: bool = False,
    ) -> list:
        """读取指定类型的最新通知。"""
        try:
            data = XHSClient._run_xhs([
                "xhs", "notifications", "--type", notification_type,
                "--num", str(num), "--json",
            ])
        except RuntimeError:
            if strict:
                raise
            return []
        if not data.get("ok"):
            return []
        return data.get("data", {}).get("message_list", [])

    @staticmethod
    def get_new_comment_notifications(num: int = 50) -> list:
        """提取评论通知，按笔记聚合并去重。"""
        notifications = XHSClient.get_notifications(
            num=num, notification_type="mentions", strict=True
        )
        if not notifications:
            return []
        by_note = {}
        seen_comment_ids = {}
        xsec_entries = {}
        for notification in notifications:
            notification_type = notification.get("type", "")
            if (
                "comment" not in notification_type
                and "item" not in notification_type
            ):
                continue
            item_info = notification.get("item_info", {})
            note_id = item_info.get("id", "")
            if not note_id:
                continue
            note_xsec_token = item_info.get("xsec_token", "")
            if note_xsec_token:
                xsec_entries[note_id] = note_xsec_token
            comment_info = notification.get("comment_info", {})
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
            if not comment_id or comment_id in seen_comment_ids[note_id]:
                continue
            seen_comment_ids[note_id].add(comment_id)
            user_info = notification.get("user_info", {})
            by_note[note_id]["new_comments"].append({
                "comment_id": comment_id,
                "nickname": user_info.get("nickname", "?"),
                # 仅供watch精确匹配用户；紧凑扫描和界面输出不会展示该字段。
                "user_id": str(
                    user_info.get("user_id") or user_info.get("id") or ""
                ),
                "content": comment_info.get("content", ""),
                "time": notification.get("time", 0),
                "target_comment_id": comment_info.get(
                    "target_comment", {}
                ).get("id", ""),
                "deleted": comment_info.get("illegal_info", {}).get(
                    "illegal_status", "NORMAL"
                ) not in ("", "NORMAL"),
            })
        if xsec_entries:
            XHSClient._merge_xsec_index(xsec_entries)
        return list(by_note.values())
