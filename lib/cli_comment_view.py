"""评论表格展示转换和通知正文兼容归档。"""

import datetime
import hashlib
import html
import json
import os

from config import COMMENTS_FILE, STATE_DB_FILE
from .state_io import (
    atomic_write_json,
    file_lock,
    json_state_exists,
    read_json_state,
)


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
