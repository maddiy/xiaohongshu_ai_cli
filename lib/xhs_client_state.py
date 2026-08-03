"""XHSClient的评论缓存和排除列表状态能力。"""

import json
import os
import time

from config import CACHE_DIR, CACHE_TTL_MINUTES
from .state_io import (
    atomic_write_json,
    delete_json_state,
    file_lock,
    json_state_exists,
    read_json_state,
)
from .xhs_client_proxy import XHSClient


class XHSStateMixin:
    """评论缓存和跳过列表。"""

    @staticmethod
    def _cache_path(note_id: str) -> str:
        return os.path.join(CACHE_DIR, f"{note_id}.json")

    @staticmethod
    def _ensure_cache_dir():
        os.makedirs(CACHE_DIR, exist_ok=True)

    @staticmethod
    def load_cache(
        note_id: str, max_age_minutes: int = None
    ) -> list | None:
        if max_age_minutes is None:
            max_age_minutes = CACHE_TTL_MINUTES
        cache_path = XHSClient._cache_path(note_id)
        if not json_state_exists(cache_path):
            return None
        try:
            cached = read_json_state(cache_path)
            age = time.time() - cached.get("timestamp", 0)
            if age > max_age_minutes * 60:
                print(
                    f"  ⏰ 缓存已过期（{age/60:.1f}分钟），重新拉取..."
                )
                return None
            print(f"  📦 使用缓存（{age/60:.1f}分钟前）")
            return cached.get("comments", [])
        except (json.JSONDecodeError, KeyError, TypeError):
            return None

    @staticmethod
    def save_cache(note_id: str, comments: list):
        XHSClient._ensure_cache_dir()
        cache_path = XHSClient._cache_path(note_id)
        atomic_write_json({
            "timestamp": time.time(),
            "comments": comments,
        }, cache_path, mode=0o600)
        print(f"  💾 评论已缓存（{len(comments)}条）")

    @staticmethod
    def get_comments_cached(
        note_id: str,
        xsec_token: str = "",
        force_refresh: bool = False,
        max_age_minutes: int = None,
        include_sub_comments: bool = False,
    ) -> tuple[list, bool]:
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
        if comments is None:
            raise RuntimeError(
                "获取评论失败: 尝试了不带/带 xsec_token 两种方式均失败"
            )
        return comments, False

    @staticmethod
    def invalidate_cache(note_id: str):
        return delete_json_state(XHSClient._cache_path(note_id))

    @staticmethod
    def _skipped_path() -> str:
        from config import SKIPPED_FILE

        return SKIPPED_FILE

    @staticmethod
    def load_skipped(force_reload: bool = False) -> dict:
        path = XHSClient._skipped_path()
        if not json_state_exists(path):
            XHSClient._skipped_cache = {}
            XHSClient._skipped_mtime = 0
            return XHSClient._skipped_cache
        if not force_reload and XHSClient._skipped_cache is not None:
            return XHSClient._skipped_cache
        try:
            XHSClient._skipped_cache = read_json_state(
                path, default={}
            ) or {}
            XHSClient._skipped_mtime = time.time()
            return XHSClient._skipped_cache
        except (json.JSONDecodeError, IOError, TypeError):
            XHSClient._skipped_cache = {}
            XHSClient._skipped_mtime = 0
            return XHSClient._skipped_cache

    @staticmethod
    def _write_skipped():
        if XHSClient._skipped_cache is None:
            return
        path = XHSClient._skipped_path()
        with file_lock(f"{path}.lock"):
            XHSClient._write_skipped_unlocked(path)

    @staticmethod
    def _write_skipped_unlocked(path=None):
        if XHSClient._skipped_cache is None:
            return
        path = path or XHSClient._skipped_path()
        atomic_write_json(
            XHSClient._skipped_cache, path, mode=0o600, indent=2
        )
        XHSClient._skipped_mtime = time.time()

    @staticmethod
    def save_skipped(skipped: dict):
        path = XHSClient._skipped_path()
        with file_lock(f"{path}.lock"):
            XHSClient._skipped_cache = skipped
            XHSClient._write_skipped_unlocked(path)

    @staticmethod
    def add_skipped(
        comment_id: str,
        nickname: str = "",
        content: str = "",
        reason: str = "manual",
        note_id: str = "",
    ):
        path = XHSClient._skipped_path()
        with file_lock(f"{path}.lock"):
            skipped = XHSClient.load_skipped(force_reload=True)
            skipped[comment_id] = {
                "nickname": nickname,
                "content": content,
                "reason": reason,
                "skipped_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "note_id": note_id,
            }
            XHSClient._write_skipped_unlocked(path)

    @staticmethod
    def remove_skipped(comment_id: str) -> bool:
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
        return comment_id in XHSClient.load_skipped()

    @staticmethod
    def get_skipped_ids() -> set:
        return set(XHSClient.load_skipped().keys())
