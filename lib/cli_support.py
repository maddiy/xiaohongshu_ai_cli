"""命令层共享的存储、精简输出和状态合并工具。"""

import contextlib
import datetime
import hashlib
import html
import io
import json
import os
import uuid

from config import COMMENTS_FILE, STATE_DB_FILE, WORK_DIR
from .state_io import (
    atomic_write_json,
    file_lock,
    json_state_exists,
    read_json_state,
    StateLockTimeout,
)
from .xhs_client import XHSClient


# 默认终态：正常流程不会再次发送。failed只有在用户明确授权、移出
# skipped.json并重置草稿状态后才能重试，不能理解为物理上不可修改。
TERMINAL_SEND_STATUSES = ("sent", "failed", "archived")
INFLIGHT_SEND_STATUSES = ("sending",)
NON_RESEND_STATUSES = TERMINAL_SEND_STATUSES + INFLIGHT_SEND_STATUSES
WORKFLOW_AUDIT_LIMIT = 500


# comments --json 的统一界面协议。<wbr>只提供软换行机会，
# 实际是否换行由用户界面按当前窗口宽度决定。
COMMENT_DISPLAY_RULES = {
    "format": "markdown_table",
    "full_content_required": True,
    "adaptive_width": True,
    "groups_are_display_ready": True,
    "raw_content_source": "archive.path",
    "internal_fields_removed": ["comment_id"],
    "layout": "comment_flexible_other_columns_compact",
    "table_width": "100%",
    "column_fields": {
        "序号": "index",
        "时间": "time",
        "用户": "nickname",
        "评论": "content",
        "状态": "status",
    },
    "columns": {
        "序号": {
            "role": "compact",
            "preferred_display_width": 4,
            "nowrap": True,
        },
        "时间": {
            "role": "compact",
            "preferred_display_width": 10,
            "format": "YYYY-MM-DD<br>HH:mm",
        },
        "用户": {
            "role": "compact",
            "preferred_display_width": 10,
            "soft_break_tag": "<wbr>",
            "fallback_unbroken_width": 8,
        },
        "评论": {
            "role": "flexible",
            "grow_weight": 1,
            "min_display_width": 32,
            "preferred_display_width": 64,
            "soft_break_tag": "<wbr>",
            "soft_break_after": "句末标点",
            "fallback_unbroken_width": 40,
        },
        "状态": {
            "role": "compact",
            "preferred_display_width": 6,
            "soft_break_tag": "<wbr>",
            "soft_break_mode": "semantic_phrase",
        },
    },
    "comment_column": {
        "mode": "soft_wrap",
        "min_display_width": 32,
        "preferred_display_width": 64,
        "soft_break_tag": "<wbr>",
        "soft_break_after": "句末标点",
        "fallback_unbroken_width": 40,
        "explicit_newline": "<br>",
        "escape_pipe_as": "&#124;",
        "preserve_quotes": True,
    },
    "escaping": {
        "untrusted_html": "escaped",
        "pipe": "&#124;",
        "explicit_newline": "<br>",
        "soft_break": "<wbr>",
        "quotes": "preserved",
    },
    "forbidden": [
        "truncate", "ellipsis", "summarize", "rewrite",
        "escape_again", "insert_wbr_again",
    ],
}


_SENTENCE_BREAKS = frozenset("。！？；!?;")


def _markdown_safe_soft_wrap(value, width, punctuation=()):
    """转义不可信文本并添加不丢字符的Markdown软换行。"""
    value = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    output = []
    unbroken = 0
    last_index = len(value) - 1
    for index, char in enumerate(value):
        if char == "\n":
            output.append("<br>")
            unbroken = 0
            continue
        if char == "|":
            output.append("&#124;")
        else:
            # 评论属于不可信输入；引号无需HTML转义，显示仍保持原文。
            output.append(html.escape(char, quote=False))
        if char.isspace():
            unbroken = 0
            continue
        unbroken += 1
        should_break = char in punctuation or unbroken >= width
        if should_break and index < last_index:
            output.append("<wbr>")
            unbroken = 0
    return "".join(output)


def _display_time(value):
    """把日期和时分分行，压缩时间列而不丢失信息。"""
    escaped = _markdown_safe_soft_wrap(value, width=1000)
    return escaped.replace(" ", "<br>", 1)


def _display_status(value):
    """短状态保持单行，长状态在语义边界软换行。"""
    value = str(value or "")
    if value == "回复了你的评论":
        return "回复了<wbr>你的评论"
    return _markdown_safe_soft_wrap(value, width=6)


def build_comment_display_groups(groups):
    """返回可直接放入Markdown表格的安全、自适应评论分组。"""
    display_groups = []
    for group in groups:
        display_comments = []
        for index, item in enumerate(group.get("comments", []), start=1):
            # 仅输出column_fields需要的界面字段；comment_id保留在0600原始归档，
            # 避免其他AI误把内部定位ID展示给用户，同时减少机器输出长度。
            display_item = {
                "index": index,
                "time": _display_time(item.get("time", "")),
                "nickname": _markdown_safe_soft_wrap(
                    item.get("nickname", "?"), width=8
                ),
                "content": _markdown_safe_soft_wrap(
                    item.get("content", ""),
                    width=40,
                    punctuation=_SENTENCE_BREAKS,
                ),
                "status": _display_status(item.get("status", "")),
            }
            display_comments.append(display_item)
        display_groups.append({
            "note_index": group.get("note_index", len(display_groups) + 1),
            "note_id": str(group.get("note_id", "") or ""),
            "note_title": _markdown_safe_soft_wrap(
                group.get("note_title", "无标题") or "无标题",
                width=40,
                punctuation=_SENTENCE_BREAKS,
            ),
            "comments": display_comments,
        })
    return display_groups


@contextlib.contextmanager
def workflow_lock(note_id, timeout=5.0, directory=None):
    """串行化同一笔记的prepare/draft/send，其他笔记互不影响。"""
    directory = directory or workflow_paths(note_id)["directory"]
    with file_lock(
        os.path.join(directory, ".workflow.lock"), timeout=timeout
    ):
        yield


def new_batch_id():
    """生成短而不可猜错的草稿批次编号。"""
    return uuid.uuid4().hex[:16]


def preview_hash(items):
    """计算用户预览内容指纹；发送时必须与草稿内容完全一致。"""
    payload = [{
        "comment_id": item.get("comment_id", ""),
        "nickname": item.get("nickname", ""),
        "content": item.get("content", ""),
        "reply": item.get("reply", ""),
        "action": item.get("action", "send"),
        **({"review": item["review"]} if "review" in item else {}),
    } for item in items]
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def print_json(data):
    """输出紧凑 UTF-8 JSON，减少 AI 上下文 token。"""
    print(json.dumps(data, ensure_ascii=False, separators=(",", ":")))


def write_json(data, path, indent=None):
    """原子写入 JSON，避免程序中断时留下半个状态文件。"""
    return atomic_write_json(data, path, indent=indent)


def workflow_paths(note_id):
    """返回一篇笔记的固定工作文件路径。"""
    safe_note_id = "".join(
        char for char in str(note_id or "all")
        if char.isalnum() or char in ("-", "_")
    ) or "all"
    directory = os.path.abspath(os.path.join(WORK_DIR, safe_note_id))
    return {
        "directory": directory,
        "scan": os.path.join(directory, "scan.json"),
        "reply_map": os.path.join(directory, "reply_map.json"),
        "drafts": os.path.join(directory, "drafts.json"),
        "audit": os.path.join(directory, "audit.json"),
    }


def current_workflow_audit_id(path):
    """读取当前审计工作流编号；旧审计没有该字段时返回空字符串。"""
    if not json_state_exists(path):
        return ""
    audit = read_json_state(path)
    if not isinstance(audit, dict):
        raise RuntimeError("工作流审计文件结构无效")
    return str(audit.get("current_workflow_id", "") or "")


def append_workflow_audit(
    path, note_id, event, current_workflow_id="",
):
    """以0600权限原子追加有界工作流审计；损坏时拒绝覆盖旧证据。"""
    path = os.path.abspath(path)
    lock_path = f"{path}.lock"
    with file_lock(lock_path, timeout=3.0):
        audit = {
            "schema_version": 2,
            "note_id": str(note_id or ""),
            "retained_limit": WORKFLOW_AUDIT_LIMIT,
            "events": [],
        }
        if json_state_exists(path):
            try:
                existing = read_json_state(path)
            except (OSError, json.JSONDecodeError) as error:
                raise RuntimeError(
                    f"工作流审计文件无法解析，已保留原文件: {error}"
                ) from error
            if not isinstance(existing, dict) or not isinstance(
                existing.get("events", []), list
            ):
                raise RuntimeError(
                    "工作流审计文件结构无效，已保留原文件"
                )
            audit.update(existing)
        events = list(audit.get("events", []))
        events.append(dict(event))
        audit.update({
            "schema_version": 2,
            "note_id": str(note_id or ""),
            "retained_limit": WORKFLOW_AUDIT_LIMIT,
            "events": events[-WORKFLOW_AUDIT_LIMIT:],
        })
        if current_workflow_id:
            audit["current_workflow_id"] = str(current_workflow_id)
        atomic_write_json(audit, path, mode=0o600)
    return path


def load_local_comment_states(note_id):
    """读取指定笔记的本地终态。"""
    states = {}
    draft_path = workflow_paths(note_id)["drafts"]
    if json_state_exists(draft_path):
        try:
            for item in read_json_state(draft_path).get("drafts", []):
                comment_id = item.get("comment_id", "")
                if comment_id and item.get("send_status"):
                    states[comment_id] = item["send_status"]
        except (OSError, json.JSONDecodeError):
            pass
    return states


def build_comment_groups(notifications, note_id=""):
    """把通知整理为不含凭据、按文章分组的最新评论列表。"""
    skipped_ids = XHSClient.get_skipped_ids()
    groups = []
    by_note = {}
    local_states = {}
    for notification in notifications:
        item = notification.get("item_info", {})
        current_note_id = item.get("id", "")
        if not current_note_id or (note_id and current_note_id != note_id):
            continue
        comment = notification.get("comment_info", {})
        comment_id = comment.get("id", "")
        if current_note_id not in local_states:
            local_states[current_note_id] = load_local_comment_states(
                current_note_id
            )
        illegal = comment.get("illegal_info", {}).get(
            "illegal_status", "NORMAL"
        )
        local_status = local_states[current_note_id].get(comment_id, "")
        if illegal not in ("", "NORMAL"):
            status = "已删除"
        elif local_status == "sent":
            status = "已回复"
        elif local_status == "failed":
            status = "发送失败"
        elif local_status == "archived" or comment_id in skipped_ids:
            status = "已跳过"
        elif notification.get("title") == "回复了你的评论":
            status = "回复了你的评论"
        else:
            status = "正常"
        if current_note_id not in by_note:
            group = {
                "note_index": len(groups) + 1,
                "note_id": current_note_id,
                "note_title": item.get("content", "") or "无标题",
                "comments": [],
            }
            groups.append(group)
            by_note[current_note_id] = group
        timestamp = notification.get("time", 0)
        time_text = (
            datetime.datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")
            if timestamp else ""
        )
        by_note[current_note_id]["comments"].append({
            "comment_id": comment_id,
            "time": time_text,
            "nickname": notification.get("user_info", {}).get("nickname", "?"),
            "content": comment.get("content", "") or "[图片]",
            "status": status,
        })
    return groups


def save_comment_archive(groups, path=None):
    """累计保存comments命令从通知接口读取的评论正文。

    程序不截断、不替换通知返回的字符串；该标记不等于已用
    完整评论树对通知内容进行二次核对。
    """
    path = os.path.abspath(path or COMMENTS_FILE)
    with file_lock(f"{path}.lock"):
        existing = {}
        if json_state_exists(path):
            try:
                existing = read_json_state(path)
            except (OSError, json.JSONDecodeError) as error:
                raise RuntimeError(
                    f"评论归档无法解析，已停止写入以保护历史数据: {path}"
                ) from error
            if not isinstance(existing, dict):
                raise RuntimeError(
                    f"评论归档顶层必须是JSON对象，已停止写入: {path}"
                )
        old_notes = existing.get("groups", [])
        if not isinstance(old_notes, list):
            raise RuntimeError(
                f"评论归档groups必须是数组，已停止写入: {path}"
            )
        notes_by_id = {
            str(item.get("note_id", "")): item
            for item in old_notes
            if isinstance(item, dict) and item.get("note_id")
        }
        new_note_ids = []
        for group in groups:
            note_id = str(group.get("note_id", "") or "")
            if not note_id:
                continue
            new_note_ids.append(note_id)
            previous = notes_by_id.get(note_id, {})
            previous_comments = previous.get("comments", [])
            if not isinstance(previous_comments, list):
                previous_comments = []
            comments_by_id = {
                str(item.get("comment_id", "")): item
                for item in previous_comments
                if isinstance(item, dict) and item.get("comment_id")
            }
            current_ids = []
            for comment in group.get("comments", []):
                if not isinstance(comment, dict):
                    continue
                comment_id = str(comment.get("comment_id", "") or "")
                if not comment_id:
                    # 通知正常都带ID；极端情况使用原文组合生成稳定键。
                    raw_key = json.dumps(
                        {
                            "time": comment.get("time", ""),
                            "nickname": comment.get("nickname", ""),
                            "content": comment.get("content", ""),
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    comment_id = "missing-" + hashlib.sha256(
                        raw_key.encode("utf-8")
                    ).hexdigest()[:16]
                current_ids.append(comment_id)
                comments_by_id[comment_id] = {
                    "comment_id": comment_id,
                    "time": comment.get("time", ""),
                    "nickname": comment.get("nickname", "?"),
                    "content": comment.get("content", ""),
                    "status": comment.get("status", ""),
                }
            previous_ids = [
                str(item.get("comment_id", ""))
                for item in previous_comments
                if isinstance(item, dict) and item.get("comment_id")
            ]
            ordered_ids = list(dict.fromkeys(current_ids + previous_ids))
            notes_by_id[note_id] = {
                "note_index": group.get("note_index", previous.get("note_index")),
                "note_id": note_id,
                "note_title": (
                    group.get("note_title") or previous.get("note_title")
                    or "无标题"
                ),
                "comments": [comments_by_id[key] for key in ordered_ids],
            }
        previous_note_ids = [
            str(item.get("note_id", ""))
            for item in old_notes
            if isinstance(item, dict) and item.get("note_id")
        ]
        ordered_note_ids = list(dict.fromkeys(new_note_ids + previous_note_ids))
        saved_groups = [notes_by_id[key] for key in ordered_note_ids]
        for note_index, group in enumerate(saved_groups, start=1):
            group["note_index"] = note_index
        archive = {
            "schema_version": "1",
            "updated_at": datetime.datetime.now().astimezone().isoformat(
                timespec="seconds"
            ),
            "content_complete": True,
            "content_complete_scope": "notification_payload",
            "content_untruncated_locally": True,
            "platform_tree_verified": False,
            "source": "notifications",
            "quote_handling": "json_escape_preserve_original",
            "columns": ["序号", "时间", "用户", "评论", "状态"],
            "groups": saved_groups,
            "notes": len(saved_groups),
            "comments": sum(
                len(item.get("comments", [])) for item in saved_groups
            ),
        }
        atomic_write_json(archive, path, mode=0o600, indent=2)
    return {
        "ok": True,
        "path": path,
        "storage_backend": "sqlite",
        "database": os.path.abspath(STATE_DB_FILE),
        "state_key": "comments.json",
        "path_role": "JSON兼容快照",
        "notes": archive["notes"],
        "comments": archive["comments"],
        "content_complete": True,
        "content_complete_scope": "notification_payload",
        "content_untruncated_locally": True,
        "platform_tree_verified": False,
    }


def call_for_output(func, *args, quiet=False, **kwargs):
    """机器模式下收起过程日志，保证 stdout 是单一 JSON 文档。"""
    if not quiet:
        return func(*args, **kwargs)
    with contextlib.redirect_stdout(io.StringIO()):
        return func(*args, **kwargs)


def compact_comment(comment):
    """只保留生成回复所需字段。"""
    keys = (
        "comment_id", "nickname", "content", "likes", "sub_count",
        "parent_comment_id", "parent_nickname", "target_comment_id",
    )
    return {key: comment[key] for key in keys if key in comment}


def compact_scan_result(result):
    """将扫描结果转换为适合 AI 消费的最小稳定结构。"""
    compact = {
        "note_id": result.get("note_id", ""),
        "note_title": result.get("note_title", ""),
        "source": result.get("source", "full_scan"),
        "unreplied_level1": [
            compact_comment(item) for item in result.get("unreplied_level1", [])
        ],
        "unreplied_subs": [
            compact_comment(item) for item in result.get("unreplied_subs", [])
        ],
    }
    for key in (
        "total", "total_new_notifications", "pending_subs", "skipped",
        "filtered_skipped", "filtered_deleted", "filtered_online",
        "filtered_local", "deferred_online", "reply_status_verified",
        "scan_error",
    ):
        if key in result:
            compact[key] = result[key]
    if result.get("per_note"):
        compact["per_note"] = []
        for index, item in enumerate(result["per_note"], start=1):
            compact_item = compact_scan_result(item)
            compact_item["note_index"] = index
            compact["per_note"].append(compact_item)
    return compact


def filter_scan_local_state(result):
    """从在线扫描候选中排除本地 sent/failed/archived 终态。"""
    if result.get("per_note"):
        for item in result["per_note"]:
            filter_scan_local_state(item)
        result["unreplied_level1"] = [
            comment for item in result["per_note"]
            for comment in item.get("unreplied_level1", [])
        ]
        result["unreplied_subs"] = [
            comment for item in result["per_note"]
            for comment in item.get("unreplied_subs", [])
        ]
        result["filtered_local"] = sum(
            item.get("filtered_local", 0) for item in result["per_note"]
        )
        return result
    states = load_local_comment_states(result.get("note_id", ""))
    filtered = int(result.get("filtered_local", 0) or 0)
    for key in ("unreplied_level1", "unreplied_subs"):
        kept = []
        for comment in result.get(key, []):
            if (
                states.get(comment.get("comment_id", ""))
                in NON_RESEND_STATUSES
            ):
                filtered += 1
            else:
                kept.append(comment)
        result[key] = kept
    result["filtered_local"] = filtered
    return result


def scan_summary(result):
    """返回不重复评论正文的扫描摘要。"""
    if result.get("per_note"):
        notes = result["per_note"]
        return {
            "notes": len(notes),
            "unreplied": sum(
                len(item.get("unreplied_level1", []))
                + len(item.get("unreplied_subs", []))
                for item in notes
            ),
        }
    return {
        "note_id": result.get("note_id", ""),
        "unreplied_level1": len(result.get("unreplied_level1", [])),
        "unreplied_subs": len(result.get("unreplied_subs", [])),
        "pending_subs": result.get("pending_subs", 0),
    }


def compact_analysis(result):
    """移除重复评论数组，只保留统计、热门评论和活跃用户。"""
    if not result:
        return None
    return {
        "note_id": result.get("note_id", ""),
        "note_title": result.get("note_title", ""),
        "total_comments": result.get("total_comments", 0),
        "unique_users": result.get("unique_users", 0),
        "total_likes": result.get("total_likes", 0),
        "total_subs": result.get("total_subs", 0),
        "self_comments": result.get("self_comments", 0),
        "replied": result.get("replied", 0),
        "unreplied": result.get("unreplied", 0),
        "counts": result.get("counts", {}),
        "top_comments": result.get("sorted_by_likes", [])[:10],
        "active_users": dict(sorted(
            result.get("active_users", {}).items(),
            key=lambda item: item[1], reverse=True,
        )[:10]),
    }


def merge_draft_history(existing, new):
    """合并草稿历史并保留新批次标记。

    全部旧条目先保留；同ID旧终态或sending不被覆盖，同ID其他非终态可由
    新草稿更新，本次未再次出现的旧非终态也继续保留，但不在新的
    active_comment_ids中。
    """
    merged = dict(new)
    old_items = existing.get("drafts", []) if isinstance(existing, dict) else []
    new_items = new.get("drafts", [])
    by_id = {
        item.get("comment_id"): item for item in old_items
        if item.get("comment_id")
    }
    order = [
        item.get("comment_id") for item in old_items if item.get("comment_id")
    ]
    for item in new_items:
        comment_id = item.get("comment_id")
        old = by_id.get(comment_id)
        if old and old.get("send_status") in NON_RESEND_STATUSES:
            continue
        by_id[comment_id] = item
        if comment_id not in order:
            order.append(comment_id)
    merged["drafts"] = [by_id[comment_id] for comment_id in order]
    # 发送尝试是独立于活动草稿的审计历史；新draft不能把它抹掉。
    if isinstance(existing, dict) and isinstance(
        existing.get("send_attempts"), list
    ):
        merged["send_attempts"] = list(existing["send_attempts"])
    return merged
