"""命令层共享的存储、精简输出和状态合并工具。"""

import contextlib
import datetime
import io
import json
import os
import uuid

from config import WORK_DIR
from .state_io import (
    atomic_write_json,
    file_lock,
    json_state_exists,
    read_json_state,
    StateLockTimeout,
)
from .xhs_client import XHSClient
from .json_codec import json_digest
from .cli_comment_view import (
    COMMENT_DISPLAY_RULES,
    build_comment_display_groups,
    save_comment_archive,
)


# 默认终态：正常流程不会再次发送。failed只有在用户明确授权、移出
# skipped.json并重置草稿状态后才能重试，不能理解为物理上不可修改。
TERMINAL_SEND_STATUSES = ("sent", "failed", "archived")
INFLIGHT_SEND_STATUSES = ("sending",)
NON_RESEND_STATUSES = TERMINAL_SEND_STATUSES + INFLIGHT_SEND_STATUSES
WORKFLOW_AUDIT_LIMIT = 500


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
    return json_digest(payload)


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
            "target_comment_id": str(
                comment.get("target_comment", {}).get("id", "") or ""
            ),
        })
    return groups


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
