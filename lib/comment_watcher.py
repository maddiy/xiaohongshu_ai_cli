"""手动启动的新评论监控、筛选、在线核验和安全自动回复。"""

import datetime
import os
import time
from collections import defaultdict
from threading import Event
from typing import Callable, Optional

from config import (
    REQUEST_DELAY,
    WATCH_DIR,
    WATCH_NOTIFICATION_LIMIT,
    WATCH_POLL_INTERVAL_SECONDS,
    WATCH_SEEN_LIMIT,
)
from .cli_comment_view import save_comment_archive
from .cli_support import (
    NON_RESEND_STATUSES,
    load_local_comment_states,
    workflow_lock,
)
from .json_codec import json_digest
from .replier import Replier
from .scanner import CommentScanner
from .state_db import StateDB, state_db
from .state_io import StateLockTimeout, file_lock
from .xhs_client import XHSClient


WATCH_STATE_SCHEMA_VERSION = 1
WATCH_STOP_ERROR_TYPES = {
    "rate_limited",
    "verification_required",
    "not_authenticated",
    "session_error",
}


class CommentWatchError(RuntimeError):
    """监控参数、状态或平台操作无法安全继续。"""


def _now() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _time_text(timestamp) -> str:
    try:
        value = float(timestamp or 0)
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    return datetime.datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M")


def public_watch_item(item: dict) -> dict:
    """去掉评论ID、用户ID和令牌，只保留可展示字段。"""
    return {
        key: item.get(key, "")
        for key in (
            "note_id", "note_title", "time", "nickname", "content",
            "status", "reply",
        )
        if item.get(key, "") != ""
    }


class CommentWatcher:
    """一个过滤条件对应一个可持久接续的手动评论监控器。"""

    def __init__(
        self,
        note_id: str = "",
        user: str = "",
        auto_reply: bool = False,
        confirmed: bool = False,
        reply_text: str = "",
        interval: float = WATCH_POLL_INTERVAL_SECONDS,
        limit: int = WATCH_NOTIFICATION_LIMIT,
        client: Optional[XHSClient] = None,
        scanner: Optional[CommentScanner] = None,
        database: Optional[StateDB] = None,
        lock_factory: Callable = workflow_lock,
        archive_writer: Callable = save_comment_archive,
    ):
        self.note_id = str(note_id or "").strip()
        self.user = str(user or "").strip()
        self.auto_reply = bool(auto_reply)
        self.confirmed = bool(confirmed)
        self.reply_text = str(reply_text or "").strip()
        self.interval = float(interval)
        self.limit = int(limit)
        self.client = client or XHSClient()
        self.scanner = scanner or CommentScanner(client=self.client)
        self.database = database or state_db()
        self.lock_factory = lock_factory
        self.archive_writer = archive_writer
        self.last_archive = {"ok": True}
        self._validate()
        self.profile = {
            "note_id": self.note_id,
            "user": self.user,
        }
        self.profile_id = json_digest(self.profile)[:16]
        self.state_key = f"watch/{self.profile_id}"
        self.lock_path = os.path.join(
            WATCH_DIR, f"{self.profile_id}.lock"
        )

    def _validate(self) -> None:
        if self.interval < 10:
            raise CommentWatchError("监控间隔不能少于10秒，避免触发平台限流")
        if self.limit < 1 or self.limit > 200:
            raise CommentWatchError("每轮通知数量必须在1到200之间")
        if self.confirmed and not self.auto_reply:
            raise CommentWatchError("--confirmed只能和--auto-reply一起使用")
        if self.auto_reply and not self.confirmed:
            raise CommentWatchError(
                "自动回复必须由用户明确确认；请同时提供--confirmed"
            )
        if self.reply_text and not self.auto_reply:
            raise CommentWatchError("--reply-text只能和--auto-reply一起使用")

    @property
    def mode(self) -> str:
        if self.note_id and self.user:
            return "note_and_user"
        if self.note_id:
            return "note"
        if self.user:
            return "user"
        return "all"

    def _new_checkpoint(self, items: list) -> dict:
        return {
            "schema_version": WATCH_STATE_SCHEMA_VERSION,
            "profile_id": self.profile_id,
            "profile": dict(self.profile),
            "created_at": _now(),
            "updated_at": _now(),
            "seen_comment_ids": [
                item["comment_id"] for item in items if item.get("comment_id")
            ][-WATCH_SEEN_LIMIT:],
            "replies": {},
            "poll_count": 1,
        }

    def _load_checkpoint(self) -> Optional[dict]:
        checkpoint = self.database.get_document(self.state_key, default=None)
        if checkpoint is None:
            return None
        if not isinstance(checkpoint, dict):
            raise CommentWatchError("监控检查点结构无效")
        if checkpoint.get("profile") != self.profile:
            raise CommentWatchError("监控检查点与当前过滤条件不一致")
        if not isinstance(checkpoint.get("seen_comment_ids", []), list):
            raise CommentWatchError("监控检查点seen_comment_ids必须是数组")
        if not isinstance(checkpoint.get("replies", {}), dict):
            raise CommentWatchError("监控检查点replies必须是对象")
        return checkpoint

    def _save_checkpoint(self, checkpoint: dict) -> None:
        checkpoint["updated_at"] = _now()
        self.database.put_document(self.state_key, checkpoint)

    def reset_checkpoint(self) -> bool:
        """删除当前过滤条件的检查点；下一轮重新建立基线。"""
        return self.database.delete_document(self.state_key)

    def status(self) -> dict:
        checkpoint = self._load_checkpoint()
        return {
            "ok": True,
            "profile_id": self.profile_id,
            "mode": self.mode,
            "profile": dict(self.profile),
            "checkpoint_exists": checkpoint is not None,
            "seen_count": len(checkpoint.get("seen_comment_ids", []))
            if checkpoint else 0,
            "reply_status_counts": self._reply_status_counts(checkpoint),
            "last_poll_at": checkpoint.get("updated_at", "")
            if checkpoint else "",
            "active": False,
            "rule": "仅进程实际运行期间启用；检查点存在不代表后台正在监控",
        }

    @staticmethod
    def _reply_status_counts(checkpoint: Optional[dict]) -> dict:
        counts = defaultdict(int)
        if checkpoint:
            for value in checkpoint.get("replies", {}).values():
                if isinstance(value, dict):
                    counts[str(value.get("status", "unknown"))] += 1
        return dict(counts)

    def _matches_user(self, item: dict) -> bool:
        if not self.user:
            return True
        wanted = self.user.casefold()
        return (
            str(item.get("user_id", "")).casefold() == wanted
            or str(item.get("nickname", "")).casefold() == wanted
        )

    def _fetch_matching_items(self) -> list:
        aggregated = self.client.get_new_comment_notifications(num=self.limit)
        all_items = []
        seen = set()
        for entry in aggregated:
            note_id = str(entry.get("note_id", "") or "")
            if not note_id:
                continue
            for comment in entry.get("new_comments", []):
                comment_id = str(comment.get("comment_id", "") or "")
                if (
                    not comment_id
                    or comment_id in seen
                ):
                    continue
                item = {
                    "comment_id": comment_id,
                    "note_id": note_id,
                    "note_title": entry.get("note_title", "") or "无标题",
                    "_xsec_token": entry.get("note_xsec_token", ""),
                    "nickname": comment.get("nickname", "?"),
                    "user_id": comment.get("user_id", ""),
                    "content": comment.get("content", ""),
                    "timestamp": comment.get("time", 0),
                    "time": _time_text(comment.get("time", 0)),
                    "target_comment_id": comment.get(
                        "target_comment_id", ""
                    ),
                    "deleted": bool(comment.get("deleted", False)),
                }
                seen.add(comment_id)
                all_items.append(item)
        self._archive_notification_items(all_items)
        items = [
            item for item in all_items
            if not item.get("deleted", False)
            and (not self.note_id or item["note_id"] == self.note_id)
            and self._matches_user(item)
        ]
        return sorted(items, key=lambda item: item.get("timestamp", 0))

    def _archive_notification_items(self, items: list) -> None:
        """监控读取到的通知正文也累计归档，且不因归档故障遮住评论。"""
        grouped = defaultdict(list)
        titles = {}
        for item in items:
            note_id = item.get("note_id", "")
            if not note_id:
                continue
            titles[note_id] = item.get("note_title", "") or "无标题"
            grouped[note_id].append({
                "comment_id": item.get("comment_id", ""),
                "time": item.get("time", ""),
                "nickname": item.get("nickname", "?"),
                "content": item.get("content", ""),
                "status": "已删除" if item.get("deleted") else "正常",
            })
        groups = [{
            "note_index": index,
            "note_id": note_id,
            "note_title": titles.get(note_id, "无标题"),
            "comments": comments,
        } for index, (note_id, comments) in enumerate(grouped.items(), start=1)]
        try:
            self.last_archive = self.archive_writer(groups)
        except (OSError, RuntimeError) as error:
            self.last_archive = {
                "ok": False,
                "error": str(error),
                "write_skipped": True,
            }

    @staticmethod
    def _remember(checkpoint: dict, comment_ids) -> None:
        existing = list(checkpoint.get("seen_comment_ids", []))
        existing.extend(str(item) for item in comment_ids if item)
        checkpoint["seen_comment_ids"] = list(dict.fromkeys(existing))[
            -WATCH_SEEN_LIMIT:
        ]

    def _fixed_or_generic_reply(self, candidate: dict) -> str:
        if self.reply_text:
            return self.reply_text
        return Replier.generate_reply(candidate, strategy="generic")

    def _local_excluded(self, note_id: str, items: list,
                        checkpoint: dict) -> tuple[list, list]:
        skipped_ids = self.client.get_skipped_ids()
        local_states = load_local_comment_states(note_id)
        replies = checkpoint.get("replies", {})
        eligible = []
        excluded = []
        for item in items:
            comment_id = item["comment_id"]
            watch_status = (
                replies.get(comment_id, {}).get("status", "")
                if isinstance(replies.get(comment_id), dict) else ""
            )
            if (
                comment_id in skipped_ids
                or local_states.get(comment_id) in NON_RESEND_STATUSES
                or watch_status in NON_RESEND_STATUSES
            ):
                excluded.append({**item, "status": "本地终态已排除"})
            else:
                eligible.append(item)
        return eligible, excluded

    def _record_reply(self, checkpoint: dict, item: dict, status: str,
                      reply: str = "", error_type: str = "") -> None:
        record = {
            "note_id": item["note_id"],
            "nickname": item.get("nickname", ""),
            "status": status,
            "updated_at": _now(),
        }
        if reply:
            record["reply"] = reply
        if error_type:
            record["error_type"] = error_type
        checkpoint.setdefault("replies", {})[item["comment_id"]] = record
        self._save_checkpoint(checkpoint)

    def _process_note(self, note_id: str, items: list,
                      checkpoint: dict) -> tuple[list, list, str]:
        """返回(已处理结果, 本轮仍待处理, 需要停止的错误类型)。"""
        try:
            with self.lock_factory(note_id, timeout=3.0):
                eligible, results = self._local_excluded(
                    note_id, items, checkpoint
                )
                if not eligible:
                    return results, [], ""
                candidates = [{
                    "comment_id": item["comment_id"],
                    "nickname": item.get("nickname", "?"),
                    "content": item.get("content", ""),
                    "likes": 0,
                    "sub_count": 0,
                    **({
                        "target_comment_id": item["target_comment_id"],
                    } if item.get("target_comment_id") else {}),
                } for item in eligible]
                token = next(
                    (item.get("_xsec_token", "") for item in eligible
                     if item.get("_xsec_token")),
                    "",
                )
                try:
                    verified, online_excluded = (
                        self.scanner.verify_candidates_online(
                            note_id, candidates, token
                        )
                    )
                except Exception as error:
                    failed = [{
                        **item,
                        "status": "在线核验失败，已停止",
                        "error": str(error),
                    } for item in eligible]
                    return results + failed, eligible, "verification_failed"

                verified_ids = {
                    item.get("comment_id", "") for item in verified
                }
                excluded_ids = {
                    item.get("comment_id", "") for item in online_excluded
                }
                for item in eligible:
                    if item["comment_id"] in excluded_ids:
                        results.append({
                            **item, "status": "平台已回复或评论不可见",
                        })

                unresolved = [
                    item for item in eligible
                    if item["comment_id"] not in verified_ids
                    and item["comment_id"] not in excluded_ids
                ]
                if unresolved:
                    unresolved_results = [{
                        **item,
                        "status": "在线核验未返回明确结论，已停止",
                    } for item in unresolved]
                    return (
                        results + unresolved_results,
                        unresolved,
                        "verification_failed",
                    )

                send_items = [
                    item for item in eligible
                    if item["comment_id"] in verified_ids
                ]
                if not send_items:
                    return results, [], ""

                with self.client.reply_session(note_id) as session:
                    persistent = getattr(session, "process", None) is not None
                    for index, item in enumerate(send_items):
                        reply = self._fixed_or_generic_reply(item)
                        self._record_reply(
                            checkpoint, item, "sending", reply=reply
                        )
                        try:
                            ok, error, error_type = session.reply(
                                item["comment_id"], reply
                            )
                        except Exception as exception:
                            ok, error, error_type = (
                                False, str(exception), "session_error"
                            )
                        if ok:
                            self._record_reply(
                                checkpoint, item, "sent", reply=reply
                            )
                            results.append({
                                **item,
                                "status": "已自动回复",
                                "reply": reply,
                            })
                        else:
                            uncertain = error_type == "session_error"
                            state = "sending" if uncertain else "failed"
                            self._record_reply(
                                checkpoint,
                                item,
                                state,
                                reply=reply,
                                error_type=error_type,
                            )
                            self.client.add_skipped(
                                item["comment_id"],
                                item.get("nickname", ""),
                                item.get("content", ""),
                                reason=(
                                    f"watch_{'uncertain' if uncertain else 'failed'}"
                                    f"[{error_type}]: {str(error)[:50]}"
                                ),
                                note_id=note_id,
                            )
                            results.append({
                                **item,
                                "status": (
                                    "发送结果不确定，已停止且禁止自动重发"
                                    if uncertain else "自动回复失败，已排除"
                                ),
                                "reply": reply,
                                "error": str(error),
                                "error_type": error_type,
                            })
                        if not ok and error_type in WATCH_STOP_ERROR_TYPES:
                            remaining = send_items[index + 1:]
                            return results, remaining, error_type
                        if not persistent and index + 1 < len(send_items):
                            time.sleep(REQUEST_DELAY)
                return results, [], ""
        except StateLockTimeout:
            return [], items, ""

    def _process_new(self, items: list, checkpoint: dict) -> tuple:
        if not self.auto_reply:
            return [
                {**item, "status": "发现新评论"} for item in items
            ], [], ""
        by_note = defaultdict(list)
        for item in items:
            by_note[item["note_id"]].append(item)
        results = []
        pending = []
        stop_reason = ""
        for note_id, note_items in by_note.items():
            processed, deferred, reason = self._process_note(
                note_id, note_items, checkpoint
            )
            results.extend(processed)
            pending.extend(deferred)
            if reason:
                stop_reason = reason
                # 当前笔记后面的其他笔记也留待用户再次手动开启后处理。
                remaining_notes = False
                for other_note_id, other_items in by_note.items():
                    if remaining_notes:
                        pending.extend(other_items)
                    elif other_note_id == note_id:
                        remaining_notes = True
                break
        return results, pending, stop_reason

    def poll(self) -> dict:
        items = self._fetch_matching_items()
        checkpoint = self._load_checkpoint()
        if checkpoint is None:
            checkpoint = self._new_checkpoint(items)
            self._save_checkpoint(checkpoint)
            return {
                "ok": True,
                "event": "baseline",
                "profile_id": self.profile_id,
                "mode": self.mode,
                "baseline_count": len(items),
                "new_count": 0,
                "items": [],
                "active": True,
                "message": "首次启动只建立基线，未处理已有通知",
                "polled_at": _now(),
                "archive": self.last_archive,
            }

        seen = set(checkpoint.get("seen_comment_ids", []))
        new_items = [
            item for item in items if item.get("comment_id") not in seen
        ]
        checkpoint["poll_count"] = int(checkpoint.get("poll_count", 0)) + 1
        processed, pending, stop_reason = self._process_new(
            new_items, checkpoint
        )
        pending_ids = {item.get("comment_id", "") for item in pending}
        completed_ids = [
            item.get("comment_id", "") for item in new_items
            if item.get("comment_id", "") not in pending_ids
        ]
        self._remember(checkpoint, completed_ids)
        self._save_checkpoint(checkpoint)
        return {
            "ok": not bool(stop_reason),
            "event": "new_comments" if new_items else "heartbeat",
            "profile_id": self.profile_id,
            "mode": self.mode,
            "detected_count": len(new_items),
            "processed_count": len(processed),
            "deferred_count": len(pending),
            "items": processed,
            "active": not bool(stop_reason),
            "stop_required": bool(stop_reason),
            "stop_reason": stop_reason,
            "polled_at": _now(),
            "archive": self.last_archive,
        }

    def run(self, stop_event: Optional[Event] = None,
            once: bool = False, on_event: Optional[Callable] = None,
            reset: bool = False) -> dict:
        """前台循环；返回最后一个事件，stop_event用于Web页面手动停止。"""
        stop_event = stop_event or Event()
        last_event = {}
        with file_lock(self.lock_path, timeout=0):
            if reset:
                self.reset_checkpoint()
            while not stop_event.is_set():
                last_event = self.poll()
                if on_event:
                    on_event(last_event)
                if once or last_event.get("stop_required"):
                    break
                stop_event.wait(self.interval)
        return last_event
