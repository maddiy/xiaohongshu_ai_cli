"""命令层共享的存储、精简输出和状态合并工具。"""

import contextlib
import datetime
import io
import json
import os
import tempfile

from config import WORK_DIR
from .xhs_client import XHSClient


def print_json(data):
    """输出紧凑 UTF-8 JSON，减少 AI 上下文 token。"""
    print(json.dumps(data, ensure_ascii=False, separators=(",", ":")))


def write_json(data, path):
    """原子写入 JSON，避免程序中断时留下半个状态文件。"""
    path = os.path.abspath(path)
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    temp_path = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=directory,
            prefix=f".{os.path.basename(path)}.", suffix=".tmp", delete=False,
        ) as file:
            temp_path = file.name
            json.dump(data, file, ensure_ascii=False, separators=(",", ":"))
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)
    return path


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
    }


def load_local_comment_states(note_id):
    """读取指定笔记的本地终态。"""
    states = {}
    draft_path = workflow_paths(note_id)["drafts"]
    if os.path.exists(draft_path):
        try:
            with open(draft_path, encoding="utf-8") as file:
                for item in json.load(file).get("drafts", []):
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
        "parent_comment_id", "parent_nickname",
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
        "filtered_local", "reply_status_verified", "scan_error",
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
    terminal = {"sent", "failed", "archived"}
    filtered = 0
    for key in ("unreplied_level1", "unreplied_subs"):
        kept = []
        for comment in result.get(key, []):
            if states.get(comment.get("comment_id", "")) in terminal:
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
    """合并固定草稿文件，保留历史发送状态并追加新评论。"""
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
        if old and old.get("send_status") in ("sent", "failed", "archived"):
            continue
        by_id[comment_id] = item
        if comment_id not in order:
            order.append(comment_id)
    merged["drafts"] = [by_id[comment_id] for comment_id in order]
    return merged
