"""仅监听本机的小红书完整运营Web控制台。"""

import contextlib
import io
import json
import math
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from config import (
    CACHE_DIR,
    COMMENTS_FILE,
    NOTE_DETAILS_FILE,
    PROJECT_ROOT,
    WEB_HOST,
    WEB_PORT,
)
from .cli_comment_view import (
    COMMENT_DISPLAY_RULES,
    build_comment_display_groups,
    save_comment_archive,
)
from .cli_support import build_comment_groups, write_json
from .cli_watch import watch_event_payload
from .comment_watcher import CommentWatchError, CommentWatcher
from .state_io import StateLockTimeout, json_state_exists, read_json_state
from .web_services import (
    WebServiceError,
    delete_draft,
    list_all_drafts,
    run_analysis,
    run_doctor,
    run_paths,
    run_protocol,
    reply_draft,
    send_all_drafts,
    send_reply_draft,
    skipped_records,
    update_draft_reply,
    update_skipped,
)
from .xhs_client import XHSClient


WEB_ASSET_DIR = os.path.join(PROJECT_ROOT, "web")
WEB_ARTICLES_FILE = os.path.join(CACHE_DIR, "articles.json")
MAX_REQUEST_BYTES = 64 * 1024
DEFAULT_CONTENT_PAGE_SIZE = 10
WEB_COMMENT_FETCH_LIMIT = 200


class WatchManager:
    """在Web进程内管理多个显式启动的监控线程。"""

    def __init__(self, watcher_factory=CommentWatcher):
        self.watcher_factory = watcher_factory
        self._lock = threading.Lock()
        self._entries = {}
        self._events = []

    def _record_event(self, profile_id: str, event: dict) -> None:
        safe_event = watch_event_payload(event)
        with self._lock:
            entry = self._entries.get(profile_id)
            if entry is not None:
                entry["last_event"] = safe_event
                if event.get("stop_required"):
                    entry["active"] = False
            self._events.append(safe_event)
            self._events = self._events[-100:]

    def start(self, config: dict) -> dict:
        watcher = self.watcher_factory(
            note_id=config.get("note_id", ""),
            user=config.get("user", ""),
            auto_reply=bool(config.get("auto_reply", False)),
            confirmed=bool(config.get("confirmed", False)),
            reply_text=config.get("reply_text", ""),
            interval=float(config.get("interval", 60) or 60),
            limit=int(config.get("limit", 50) or 50),
        )
        stop_event = threading.Event()
        with self._lock:
            current = self._entries.get(watcher.profile_id)
            if current and current.get("active"):
                raise CommentWatchError("相同过滤条件的监控已经开启")
            if sum(
                bool(item.get("active")) for item in self._entries.values()
            ) >= 8:
                raise CommentWatchError("最多同时开启8个监控")
            entry = {
                "profile_id": watcher.profile_id,
                "mode": watcher.mode,
                "profile": dict(watcher.profile),
                "auto_reply": watcher.auto_reply,
                "interval": watcher.interval,
                "limit": watcher.limit,
                "active": True,
                "last_event": None,
                "stop_event": stop_event,
                "thread": None,
            }
            self._entries[watcher.profile_id] = entry

        def target():
            try:
                watcher.run(
                    stop_event=stop_event,
                    reset=bool(config.get("reset", False)),
                    on_event=lambda event: self._record_event(
                        watcher.profile_id, event
                    ),
                )
            except StateLockTimeout as error:
                self._record_event(watcher.profile_id, {
                    "ok": False,
                    "event": "stopped",
                    "active": False,
                    "stop_required": True,
                    "stop_reason": "watch_already_running",
                    "error": str(error),
                    "items": [],
                })
            except Exception as error:
                self._record_event(watcher.profile_id, {
                    "ok": False,
                    "event": "stopped",
                    "active": False,
                    "stop_required": True,
                    "stop_reason": "watch_error",
                    "error": str(error),
                    "items": [],
                })
            finally:
                with self._lock:
                    current = self._entries.get(watcher.profile_id)
                    if current is not None:
                        current["active"] = False

        thread = threading.Thread(
            target=target,
            name=f"xhs-watch-{watcher.profile_id}",
            daemon=True,
        )
        with self._lock:
            self._entries[watcher.profile_id]["thread"] = thread
        thread.start()
        return self._public_entry(entry)

    @staticmethod
    def _public_entry(entry: dict) -> dict:
        return {
            key: entry.get(key)
            for key in (
                "profile_id", "mode", "profile", "auto_reply",
                "interval", "limit", "active", "last_event",
            )
        }

    def stop(self, profile_id: str) -> dict:
        with self._lock:
            entry = self._entries.get(str(profile_id or ""))
            if entry is None:
                raise CommentWatchError("没有找到该监控")
            entry["stop_event"].set()
            entry["active"] = False
            thread = entry.get("thread")
        if thread and thread is not threading.current_thread():
            thread.join(timeout=2)
        return self._public_entry(entry)

    def status(self) -> dict:
        with self._lock:
            monitors = [
                self._public_entry(entry)
                for entry in self._entries.values()
            ]
            events = list(self._events[-30:])
        return {
            "ok": True,
            "monitors": monitors,
            "active_count": sum(item["active"] for item in monitors),
            "events": events,
        }

    def shutdown(self) -> None:
        with self._lock:
            entries = list(self._entries.values())
        for entry in entries:
            entry["stop_event"].set()
        for entry in entries:
            thread = entry.get("thread")
            if thread and thread is not threading.current_thread():
                thread.join(timeout=2)


def _page_info(total_count: int, page: int, page_size: int) -> dict:
    total_pages = max(1, math.ceil(total_count / page_size))
    page = min(page, total_pages)
    return {
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "has_previous": page > 1,
        "has_next": page < total_pages,
    }


def _articles_from_notes(notes: list) -> list:
    """将 get_my_notes 返回的原始笔记列表转为文章字典列表。"""
    return [{
        "index": i,
        "time": item.get("time", ""),
        "comments_count": item.get("comments_count", 0),
        "view_count": item.get("view_count", 0),
        "title": item.get("title", "") or "无标题",
        "note_id": item.get("id", ""),
    } for i, item in enumerate(notes, start=1)]


def _merge_articles(fresh: list, cached: list) -> list:
    """将最新抓取文章插入缓存列表头部，按 note_id 去重，重新编号。"""
    fresh_ids = {a["note_id"] for a in fresh}
    merged = fresh + [
        a for a in cached
        if isinstance(a, dict) and a.get("note_id") not in fresh_ids
    ]
    for i, art in enumerate(merged):
        art["index"] = i + 1
    return merged


def _sort_articles(articles: list, sort: str) -> list:
    """按指定字段排序并重新编号。"""
    if sort == "views":
        articles = sorted(
            articles,
            key=lambda x: x.get("view_count", 0),
            reverse=True,
        )
        for i, art in enumerate(articles):
            art["index"] = i + 1
    return articles


def _load_articles(page: int = 1, page_size: int = DEFAULT_CONTENT_PAGE_SIZE,
                   refresh: bool = False, sort: str = "time",
                   full_refresh: bool = False) -> dict:
    columns = ["序号", "发布时间", "查看数", "评论数", "标题", "笔记ID"]
    has_cache = json_state_exists(WEB_ARTICLES_FILE)
    if full_refresh or not has_cache:
        # 全量拉取（仅首次无缓存时自动触发，或 API 显式传 full_refresh=1）
        max_pages = None
        with contextlib.redirect_stdout(io.StringIO()):
            online = XHSClient.get_my_notes(max_pages=max_pages, strict=True)
        all_articles = _articles_from_notes(online)
        write_json({
            "ok": True,
            "columns": columns,
            "articles": all_articles,
            "count": len(all_articles),
        }, WEB_ARTICLES_FILE, indent=2)
        source = "online_full" if full_refresh else "online_first"
    elif refresh:
        # 增量刷新：只读最新10篇，与本地缓存合并
        with contextlib.redirect_stdout(io.StringIO()):
            online = XHSClient.get_my_notes(max_pages=1, strict=True)
        fresh = _articles_from_notes(online)
        cached = read_json_state(WEB_ARTICLES_FILE, default={}) or {}
        cached_articles = [
            item for item in cached.get("articles", [])
            if isinstance(item, dict)
        ]
        all_articles = _merge_articles(fresh, cached_articles)
        write_json({
            "ok": True,
            "columns": columns,
            "articles": all_articles,
            "count": len(all_articles),
        }, WEB_ARTICLES_FILE, indent=2)
        source = "online_incremental"
    else:
        cached = read_json_state(WEB_ARTICLES_FILE, default={}) or {}
        all_articles = [
            item for item in cached.get("articles", [])
            if isinstance(item, dict)
        ]
        source = "cache"
    # 排序
    all_articles = _sort_articles(all_articles, sort)
    pagination = _page_info(len(all_articles), page, page_size)
    start = (pagination["page"] - 1) * page_size
    articles = all_articles[start:start + page_size]
    return {
        "ok": True,
        "columns": columns,
        "articles": articles,
        "count": len(articles),
        "total_count": len(all_articles),
        "pagination": pagination,
        "source": source,
        "sort": sort,
    }


def _paginate_comment_groups(groups: list, page: int,
                             page_size: int) -> tuple[list, dict, int]:
    flattened = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        for comment in group.get("comments", []):
            if isinstance(comment, dict):
                flattened.append((group, comment))
    flattened.sort(
        key=lambda item: str(item[1].get("time", "") or ""),
        reverse=True,
    )
    pagination = _page_info(len(flattened), page, page_size)
    start = (pagination["page"] - 1) * page_size
    selected = flattened[start:start + page_size]
    grouped = {}
    order = []
    for group, comment in selected:
        note_id = str(group.get("note_id", "") or "")
        if note_id not in grouped:
            order.append(note_id)
            grouped[note_id] = {
                "note_index": len(order),
                "note_id": note_id,
                "note_title": group.get("note_title", "") or "无标题",
                "comments": [],
            }
        grouped[note_id]["comments"].append(comment)
    return [grouped[note_id] for note_id in order], pagination, len(flattened)


def _prefetch_missing_note_details(notifications: list,
                                   groups: list) -> dict:
    """为文章列表之外的评论笔记一次性补齐详情缓存。

    评论仍应优先展示，因此单篇笔记读取失败只记入 warnings，不中断整页。
    已存在的详情缓存不受 TTL 影响，避免后续生成提示词时反复访问平台。
    """
    articles = read_json_state(WEB_ARTICLES_FILE, default={}) or {}
    article_ids = {
        str(item.get("note_id", "") or "")
        for item in articles.get("articles", [])
        if isinstance(item, dict) and item.get("note_id")
    }
    details = read_json_state(NOTE_DETAILS_FILE, default={}) or {}
    if not isinstance(details, dict):
        details = {}

    tokens = {}
    for notification in notifications:
        if not isinstance(notification, dict):
            continue
        item = notification.get("item_info", {}) or {}
        note_id = str(item.get("id", "") or "")
        token = str(item.get("xsec_token", "") or "")
        if note_id and token:
            tokens[note_id] = token
    token_warning = ""
    if tokens:
        try:
            XHSClient._merge_xsec_index(tokens)
        except Exception as error:
            # 令牌索引写入异常不应让最新评论整页不可见；详情读取仍可尝试
            # 使用当前通知携带的令牌，错误随缓存摘要返回。
            token_warning = f"保存笔记访问令牌失败: {error}"

    group_by_id = {
        str(group.get("note_id", "") or ""): group
        for group in groups
        if isinstance(group, dict) and group.get("note_id")
    }
    summary = {
        "checked": len(group_by_id),
        "fetched": 0,
        "already_cached": 0,
        "present_in_articles": 0,
        "warnings": [],
        "cache_path": os.path.abspath(NOTE_DETAILS_FILE),
    }
    if token_warning:
        summary["warnings"].append({
            "note_id": "",
            "error": token_warning,
        })
    for note_id, group in group_by_id.items():
        if note_id in article_ids:
            summary["present_in_articles"] += 1
            continue
        cached = details.get(note_id)
        if isinstance(cached, dict) and cached:
            summary["already_cached"] += 1
            detail = cached
        else:
            try:
                detail = XHSClient.get_note_detail_cached(
                    note_id,
                    xsec_token=tokens.get(note_id, ""),
                    force_refresh=False,
                )
                summary["fetched"] += 1
                details[note_id] = detail
            except Exception as error:
                summary["warnings"].append({
                    "note_id": note_id,
                    "error": str(error),
                })
                continue
        if (
            str(group.get("note_title", "") or "") in {"", "无标题"}
            and isinstance(detail, dict)
            and detail.get("title")
        ):
            group["note_title"] = detail["title"]
    return summary


def _load_comments(page: int = 1, page_size: int = DEFAULT_CONTENT_PAGE_SIZE,
                   note_id: str = "", refresh: bool = False) -> dict:
    archive = None
    note_content_cache = {
        "checked": 0,
        "fetched": 0,
        "already_cached": 0,
        "present_in_articles": 0,
        "warnings": [],
        "cache_path": os.path.abspath(NOTE_DETAILS_FILE),
        "source": "not_refreshed",
    }
    if refresh or not json_state_exists(COMMENTS_FILE):
        with contextlib.redirect_stdout(io.StringIO()):
            notifications = XHSClient.get_notifications(
                num=WEB_COMMENT_FETCH_LIMIT,
                notification_type="mentions",
                strict=True,
            )
            # 先完整归档本轮通知，再把筛选条件应用到网页展示。
            current_groups = build_comment_groups(notifications, "")
            note_content_cache = _prefetch_missing_note_details(
                notifications, current_groups
            )
            note_content_cache["source"] = "notification_refresh"
            archive = save_comment_archive(current_groups)
    saved = read_json_state(COMMENTS_FILE, default={}) or {}
    groups = [
        group for group in saved.get("groups", [])
        if isinstance(group, dict)
        and (not note_id or str(group.get("note_id", "")) == note_id)
    ]
    raw_groups, pagination, total_count = _paginate_comment_groups(
        groups, page, page_size
    )
    display_groups = build_comment_display_groups(raw_groups)
    skipped = XHSClient.load_skipped(force_reload=True)
    for display_group, raw_group in zip(display_groups, raw_groups):
        for display_comment, raw_comment in zip(
            display_group.get("comments", []), raw_group.get("comments", [])
        ):
            comment_id = str(raw_comment.get("comment_id", "") or "")
            skipped_item = skipped.get(comment_id, {})
            ignore_reason = str(skipped_item.get("reason", "") or "")
            display_comment["action_data"] = {
                # 仅供本机网页按钮定位及复制提示词，不加入表格显示字段。
                "comment_id": comment_id,
                "nickname": str(raw_comment.get("nickname", "?") or "?"),
                "content": str(raw_comment.get("content", "") or ""),
                "ignored": bool(skipped_item),
                "ignore_reason": ignore_reason,
            }
            if skipped_item:
                display_comment["status"] = (
                    "人工忽略" if ignore_reason == "人工忽略" else "已排除"
                )
    if archive is None:
        archive = {
            "ok": True,
            "path": os.path.abspath(COMMENTS_FILE),
            "comments": saved.get("comments", total_count),
            "content_complete_scope": saved.get(
                "content_complete_scope", "notification_payload"
            ),
            "content_untruncated_locally": bool(
                saved.get("content_untruncated_locally", True)
            ),
            "platform_tree_verified": bool(
                saved.get("platform_tree_verified", False)
            ),
        }
    web_display = dict(COMMENT_DISPLAY_RULES)
    web_display["column_fields"] = dict(
        COMMENT_DISPLAY_RULES.get("column_fields", {})
    )
    web_display["column_fields"]["操作"] = "action_data"
    web_display["columns"] = dict(COMMENT_DISPLAY_RULES.get("columns", {}))
    web_display["columns"]["操作"] = {
        "role": "compact",
        "nowrap": True,
        "controls": ["回复", "忽略"],
    }
    web_display["internal_fields_removed"] = []
    web_display["internal_fields_hidden_from_table"] = [
        "action_data.comment_id",
        "action_data.nickname",
        "action_data.content",
    ]
    return {
        "ok": True,
        "columns": ["序号", "时间", "用户", "评论", "状态", "操作"],
        "groups": display_groups,
        "display": web_display,
        "archive": archive,
        "note_content_cache": note_content_cache,
        "count": sum(len(group["comments"]) for group in raw_groups),
        "total_count": total_count,
        "pagination": pagination,
    }


def _login() -> dict:
    with contextlib.redirect_stdout(io.StringIO()):
        ok = XHSClient().login()
    return {
        "ok": bool(ok),
        "message": "已重新读取浏览器Cookie" if ok else "登录失败，请检查浏览器登录状态",
    }


def _handler_class(manager: WatchManager, csrf_token: str):
    class LocalWebHandler(BaseHTTPRequestHandler):
        server_version = "XHSLocalWeb/2"

        def log_message(self, _format, *_args):
            return

        def _security_headers(self) -> None:
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self' data:; connect-src 'self'; "
                "frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            )
            self.send_header("Cache-Control", "no-store")

        def _send_bytes(self, status: int, data: bytes,
                        content_type: str) -> None:
            self.send_response(status)
            self._security_headers()
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_json(self, status: int, payload: dict) -> None:
            data = json.dumps(
                payload, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            self._send_bytes(status, data, "application/json; charset=utf-8")

        def _asset(self, filename: str, content_type: str,
                   inject_token: bool = False) -> None:
            path = os.path.join(WEB_ASSET_DIR, filename)
            try:
                with open(path, "rb") as file:
                    data = file.read()
            except OSError:
                self._send_json(500, {
                    "ok": False, "error": f"Web资源缺失: {filename}",
                })
                return
            if inject_token:
                data = data.replace(
                    b"__CSRF_TOKEN__", csrf_token.encode("ascii")
                )
            self.send_response(200)
            self._security_headers()
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.end_headers()
            self.wfile.write(data)

        def _query_int(self, query: dict, name: str, default: int,
                       maximum: int) -> int:
            raw = query.get(name, [str(default)])[0]
            try:
                value = int(raw)
            except (TypeError, ValueError):
                raise CommentWatchError(f"{name}必须是整数") from None
            if value < 1 or value > maximum:
                raise CommentWatchError(f"{name}必须在1到{maximum}之间")
            return value

        def do_GET(self):
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            try:
                if parsed.path in ("/", "/index.html"):
                    self._asset(
                        "index.html", "text/html; charset=utf-8", True
                    )
                elif parsed.path == "/app.js":
                    self._asset(
                        "app.js", "application/javascript; charset=utf-8"
                    )
                elif parsed.path == "/styles.css":
                    self._asset("styles.css", "text/css; charset=utf-8")
                elif parsed.path == "/logo.svg":
                    self._asset("logo.svg", "image/svg+xml; charset=utf-8")
                elif parsed.path == "/api/health":
                    self._send_json(200, {
                        "ok": True,
                        "local_only": True,
                        "app": "小红书AI智能运营系统",
                        "capabilities": [
                            "login", "articles", "comments", "reply_prompt",
                            "reply_draft", "reply_send", "comment_ignore",
                            "analyze", "skipped", "doctor",
                            "drafts", "paths", "protocol", "watch",
                        ],
                    })
                elif parsed.path == "/api/watch/status":
                    self._send_json(200, manager.status())
                elif parsed.path == "/api/articles":
                    page = self._query_int(query, "page", 1, 1000000)
                    page_size = self._query_int(query, "page_size", 10, 100)
                    refresh = query.get("refresh", [""])[0] == "1"
                    sort = query.get("sort", [""])[0] or "time"
                    full_refresh = query.get("full_refresh", [""])[0] == "1"
                    self._send_json(200, _load_articles(
                        page=page, page_size=page_size, refresh=refresh,
                        sort=sort, full_refresh=full_refresh,
                    ))
                elif parsed.path == "/api/comments":
                    page = self._query_int(query, "page", 1, 1000000)
                    page_size = self._query_int(query, "page_size", 10, 100)
                    note_id = query.get("note_id", [""])[0]
                    refresh = query.get("refresh", [""])[0] == "1"
                    self._send_json(200, _load_comments(
                        page=page,
                        page_size=page_size,
                        note_id=note_id,
                        refresh=refresh,
                    ))
                elif parsed.path == "/api/reply-draft":
                    self._send_json(200, reply_draft(
                        note_id=query.get("note_id", [""])[0],
                        comment_id=query.get("comment_id", [""])[0],
                    ))
                elif parsed.path == "/api/skipped":
                    page = self._query_int(query, "page", 1, 1000000)
                    page_size = self._query_int(
                        query, "page_size", 15, 100
                    )
                    search = query.get("search", [""])[0]
                    self._send_json(200, skipped_records(
                        search=search, page=page, page_size=page_size
                    ))
                elif parsed.path == "/api/drafts":
                    self._send_json(200, list_all_drafts())
                elif parsed.path == "/api/daily-stats":
                    from .web_services import daily_stats
                    self._send_json(200, daily_stats())
                elif parsed.path == "/api/note-detail":
                    from .web_services import note_detail
                    self._send_json(200, note_detail(
                        {"note_id": query.get("note_id", [""])[0]}
                    ))
                else:
                    self._send_json(404, {"ok": False, "error": "未找到"})
            except (CommentWatchError, WebServiceError) as error:
                response = {
                    "ok": False,
                    "error": str(error),
                    "error_type": getattr(
                        error, "error_type", "invalid_web_request"
                    ),
                }
                if getattr(error, "details", None) is not None:
                    response["details"] = error.details
                self._send_json(400, response)
            except Exception as error:
                self._send_json(500, {"ok": False, "error": str(error)})

        def _read_json(self, maximum: int = MAX_REQUEST_BYTES) -> dict:
            try:
                length = int(self.headers.get("Content-Length", "0") or 0)
            except ValueError:
                raise CommentWatchError("Content-Length无效") from None
            if length < 0 or length > maximum:
                raise CommentWatchError("请求内容过大")
            if length == 0:
                return {}
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise CommentWatchError("请求必须是有效JSON") from None
            if not isinstance(payload, dict):
                raise CommentWatchError("JSON顶层必须是对象")
            return payload

        def do_POST(self):
            if self.headers.get("X-CSRF-Token", "") != csrf_token:
                self._send_json(403, {
                    "ok": False, "error": "页面令牌无效，请刷新页面",
                })
                return
            parsed = urlparse(self.path)
            try:
                payload = self._read_json()
                if parsed.path == "/api/watch/start":
                    result = manager.start(payload)
                    self._send_json(200, {"ok": True, "monitor": result})
                elif parsed.path == "/api/watch/stop":
                    result = manager.stop(payload.get("profile_id", ""))
                    self._send_json(200, {"ok": True, "monitor": result})
                elif parsed.path == "/api/login":
                    self._send_json(200, _login())
                elif parsed.path == "/api/analyze":
                    self._send_json(200, run_analysis(payload))
                elif parsed.path == "/api/skipped":
                    self._send_json(200, update_skipped(payload))
                elif parsed.path == "/api/drafts/update-reply":
                    self._send_json(200, update_draft_reply(payload))
                elif parsed.path == "/api/drafts/delete":
                    self._send_json(200, delete_draft(payload))
                elif parsed.path == "/api/drafts/send-all":
                    self._send_json(200, send_all_drafts(payload))
                elif parsed.path == "/api/reply/send":
                    self._send_json(200, send_reply_draft(payload))
                elif parsed.path == "/api/doctor":
                    self._send_json(200, run_doctor())
                elif parsed.path == "/api/paths":
                    self._send_json(200, run_paths(payload))
                elif parsed.path == "/api/protocol":
                    self._send_json(200, run_protocol(payload))
                else:
                    self._send_json(404, {"ok": False, "error": "未找到"})
            except CommentWatchError as error:
                self._send_json(400, {"ok": False, "error": str(error)})
            except WebServiceError as error:
                response = {
                    "ok": False,
                    "error": str(error),
                    "error_type": getattr(
                        error, "error_type", "invalid_web_request"
                    ),
                }
                if getattr(error, "details", None) is not None:
                    response["details"] = error.details
                self._send_json(400, response)
            except Exception as error:
                self._send_json(500, {"ok": False, "error": str(error)})

    return LocalWebHandler


def create_web_server(port: int = WEB_PORT,
                      manager: WatchManager = None):
    """创建只绑定127.0.0.1的服务，返回(server, csrf_token, manager)。"""
    port = int(port)
    if port < 0 or port > 65535:
        raise ValueError("端口必须在0到65535之间")
    manager = manager or WatchManager()
    token = secrets.token_hex(24)
    server = ThreadingHTTPServer(
        (WEB_HOST, port), _handler_class(manager, token)
    )
    server.daemon_threads = True
    return server, token, manager


def serve_web(port: int = WEB_PORT) -> None:
    server, _token, manager = create_web_server(port=port)
    actual_port = server.server_address[1]
    print(f"🌐 本地Web控制台：http://{WEB_HOST}:{actual_port}")
    print("仅监听本机；按 Ctrl+C 停止，关闭后监控不会在后台继续运行。")
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("\n⏹️ Web控制台已停止")
    finally:
        manager.shutdown()
        server.server_close()


def cmd_web(args) -> None:
    try:
        serve_web(port=args.port)
    except (OSError, ValueError) as error:
        print(f"❌ 无法启动Web控制台: {error}")
