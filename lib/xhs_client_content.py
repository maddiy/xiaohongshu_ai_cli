"""XHSClient的笔记、令牌索引和通知读取能力。"""

import datetime
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
from .note_ocr import enrich_note_with_image_text


class XHSContentMixin:
    """笔记列表、xsec索引和评论通知。"""

    @staticmethod
    def get_my_notes(
        max_pages: int = None,
        strict: bool = False,
        include_cached: bool = False,
    ):
        """获取我的笔记列表。

        默认遇到本地已有笔记时停止翻页，以降低历史列表读取成本；
        include_cached=True 时仍返回平台本页已有笔记，用于刷新互动指标。
        """
        from config import READ_PAGE_DELAY, CACHE_DIR

        # 加载本地已缓存的文章ID；命中后停止翻页避免全量拉取
        cached_ids = set()
        articles_cache_path = os.path.join(CACHE_DIR, "articles.json")
        if json_state_exists(articles_cache_path):
            try:
                cached_data = read_json_state(articles_cache_path) or {}
                for article in cached_data.get("articles", []):
                    note_id = (
                        article.get("note_id")
                        or article.get("id", "")
                    )
                    if note_id:
                        cached_ids.add(note_id)
            except (json.JSONDecodeError, IOError, TypeError):
                pass

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
            hit_cached = False
            for note in notes:
                note_id = note["id"]
                if note_id in cached_ids and not include_cached:
                    hit_cached = True
                    continue
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
            if hit_cached:
                break
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
    def get_note_detail(note_id: str, xsec_token: str = "") -> dict:
        """读取单篇笔记详情（标题、正文desc、图片数等），不依赖本地缓存。

        通过 xhs read <note_id> --json 读取。note_id 必须有值；
        xsec_token 可选，能显著提高带风控笔记的读取成功率。
        返回:
          {
            note_id, title, desc, image_count,
            comment_count, like_count, collected_count, share_count,
            nickname, xsec_token,
          }
        失败时抛出 RuntimeError；调用方决定是否降级。
        """
        note_id = str(note_id or "").strip()
        if not note_id:
            raise RuntimeError("笔记ID为空，无法读取笔记详情")
        command = ["xhs", "read", note_id, "--json"]
        if xsec_token:
            command += ["--xsec-token", xsec_token]
        data = XHSClient._run_xhs(command, timeout=30)
        if not data.get("ok"):
            err = data.get("error", {})
            raise RuntimeError(
                f"读取笔记详情失败: {err.get('message', str(data))}"
            )
        items = data.get("data", {}).get("items", []) or []
        if not items:
            raise RuntimeError("读取笔记详情返回为空")
        card = items[0].get("note_card", {}) or {}
        interact = card.get("interact_info", {}) or {}
        user = card.get("user", {}) or {}
        token = xsec_token or card.get("xsec_token", "") or ""
        # 提取图片 URL：图文笔记正文常写在图片里，需把图片交给 AI 识别。
        # image_list 元素结构随平台版本变化，这里做防御性多 key 提取。
        images = []
        for index, img in enumerate(card.get("image_list", []) or []):
            if not isinstance(img, dict):
                continue
            info = img.get("info_list", [{}])[0] if isinstance(
                img.get("info_list"), list
            ) and img.get("info_list") else img
            info = info if isinstance(info, dict) else {}
            url = (
                img.get("url_pre")
                or img.get("url_default")
                or img.get("url")
                or info.get("url_pre")
                or info.get("url_default")
                or info.get("url")
                or ""
            )
            if url:
                images.append({"index": index, "url": url})
        detail = {
            "note_id": note_id,
            "title": card.get("title", "") or "",
            "desc": card.get("desc", "") or "",
            "note_type": "video" if card.get("type") == "video" else "image",
            "image_count": len(card.get("image_list", []) or []),
            "images": images,
            "comment_count": interact.get("comment_count", ""),
            "like_count": interact.get("liked_count", ""),
            "collected_count": interact.get("collected_count", ""),
            "share_count": interact.get("share_count", ""),
            "nickname": user.get("nickname", ""),
            "xsec_token": token,
            "read_at": datetime.datetime.now().astimezone().isoformat(
                timespec="seconds"
            ),
        }
        return detail

    @staticmethod
    def get_note_detail_cached(
        note_id: str, xsec_token: str = "", force_refresh: bool = False
    ) -> dict:
        """读取笔记详情并缓存到 .cache/note_details.json。

        先查本地缓存；命中且未过时时直接返回。未命中或 force_refresh
        时调用 get_note_detail 读取平台并原子写入缓存（0600权限）。
        平台读取失败时不覆盖已有缓存，抛错由调用方决定是否降级。
        """
        from config import CACHE_TTL_MINUTES, NOTE_DETAILS_FILE

        note_id = str(note_id or "").strip()
        cache = {}
        path = NOTE_DETAILS_FILE
        if json_state_exists(path):
            try:
                cache = read_json_state(path) or {}
            except (json.JSONDecodeError, IOError, TypeError):
                cache = {}
        entry = cache.get(note_id) or {}

        def save_detail(value: dict) -> None:
            with file_lock(f"{path}.lock"):
                existing = {}
                if json_state_exists(path):
                    try:
                        existing = read_json_state(path) or {}
                    except (json.JSONDecodeError, IOError, TypeError):
                        pass
                existing[note_id] = value
                atomic_write_json(existing, path, mode=0o600)

        if (
            not force_refresh
            and isinstance(entry, dict)
            and entry.get("title")
            and entry.get("read_at")
        ):
            try:
                read_at = datetime.datetime.fromisoformat(entry["read_at"])
                age_minutes = (
                    datetime.datetime.now().astimezone() - read_at
                ).total_seconds() / 60.0
                if age_minutes < (CACHE_TTL_MINUTES or 30):
                    enriched, changed = enrich_note_with_image_text(entry)
                    if changed:
                        save_detail(enriched)
                    return enriched
            except (ValueError, TypeError):
                pass
        # 平台读取失败时保留旧缓存并重新抛出
        detail = XHSClient.get_note_detail(note_id, xsec_token=xsec_token)
        if not detail.get("title"):
            raise RuntimeError("笔记详情缺少标题，读取失败")
        # 合并保留旧 token（若本次未返回新 token）
        detail.setdefault("xsec_token", entry.get("xsec_token", "") or "")
        detail, _changed = enrich_note_with_image_text(detail)
        save_detail(detail)
        return detail

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
