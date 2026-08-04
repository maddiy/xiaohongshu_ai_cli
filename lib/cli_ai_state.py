"""AI回复工作流的状态摘要与受控失败重试。"""

import json

from .state_io import json_state_exists
from .cli_ai_prepare import _clear_active_batch
from .cli_ai_support import _load_json, _validate_reply_map
from .cli_support import print_json, write_json
from .xhs_client import XHSClient


def _optional_state(path, role):
    if not json_state_exists(path):
        return {}
    value = _load_json(path, role=role)
    if not isinstance(value, dict):
        raise ValueError(f"{path}顶层必须是JSON对象")
    return value


def _status(args, paths):
    """输出跨AI接续所需的最小状态，不泄露可直接发送的确认绑定。"""
    try:
        scan = _optional_state(paths["scan"], "program_state")
        drafts = _optional_state(paths["drafts"], "program_state")
    except (OSError, json.JSONDecodeError, ValueError) as error:
        print_json({
            "ok": False,
            "action": "status",
            "error": f"工作流状态无法读取: {error}",
            "error_type": "workflow_state_read_error",
            "automatic_retry": False,
            "paths": paths,
        })
        return
    reply_map_error = ""
    try:
        reply_map = _optional_state(paths["reply_map"], "ai_input")
    except (OSError, json.JSONDecodeError, ValueError) as error:
        reply_map = {}
        reply_map_error = str(error)

    candidates = (
        scan.get("unreplied_level1", [])
        + scan.get("unreplied_subs", [])
    )
    candidate_ids = [
        str(item.get("comment_id", "")) for item in candidates
        if item.get("comment_id")
    ]
    candidate_id_to_index = {}
    for fallback_index, item in enumerate(candidates, start=1):
        comment_id = str(item.get("comment_id", "") or "")
        if not comment_id:
            continue
        try:
            candidate_id_to_index[comment_id] = int(
                item.get("candidate_index", fallback_index)
            )
        except (TypeError, ValueError):
            candidate_id_to_index[comment_id] = fallback_index
    mapped_ids = [
        comment_id for comment_id in candidate_ids
        if not _validate_reply_map(reply_map, [comment_id])
    ]
    remaining_ids = [
        comment_id for comment_id in candidate_ids
        if comment_id not in mapped_ids
    ]
    items = drafts.get("drafts", [])
    if not isinstance(items, list):
        items = []
    counts = {}
    for item in items:
        status = str(item.get("send_status", "pending") or "pending")
        counts[status] = counts.get(status, 0) + 1
    active_ids = drafts.get("active_comment_ids", [])
    if not isinstance(active_ids, list):
        active_ids = []
    active_batch = drafts.get("active_batch", {})
    if not isinstance(active_batch, dict):
        active_batch = {}
    failed_ids = [
        str(item.get("comment_id", "")) for item in items
        if item.get("comment_id") and item.get("send_status") == "failed"
    ]

    if not scan:
        next_step = "运行ai-reply --action prepare"
    elif reply_map_error:
        next_step = "按reply_map_error修复映射文件，再使用map或draft"
    elif remaining_ids:
        next_step = "继续运行map完成remaining_comment_ids"
    elif active_batch.get("status") == "previewed" and active_ids:
        next_step = (
            "重新运行draft以安全展示当前preview；内容未变化时复用原确认绑定"
        )
    elif candidate_ids:
        next_step = "运行ai-reply --action draft"
    else:
        next_step = "当前没有候选；需要新一轮时运行prepare"

    print_json({
        "ok": True,
        "action": "status",
        "note_id": args.note_id,
        "scan_verified": bool(scan.get("reply_status_verified", False)),
        "candidate_count": len(candidate_ids),
        "mapped_count": len(mapped_ids),
        "mapping_remaining_count": len(remaining_ids),
        "reply_map_readable": not reply_map_error,
        **({"reply_map_error": reply_map_error} if reply_map_error else {}),
        "remaining_comment_ids": remaining_ids,
        "remaining_candidate_indexes": [
            candidate_id_to_index[comment_id] for comment_id in remaining_ids
        ],
        "active_count": len(active_ids),
        "batch_status": str(active_batch.get("status", "") or "none"),
        "workflow_revision": int(
            drafts.get("workflow_revision", 0) or 0
        ),
        "draft_status_counts": counts,
        "failed_count": len(failed_ids),
        "failed_comment_ids": failed_ids,
        "retry_rule": (
            "failed只能在用户明确授权后用retry重置；sending禁止自动重试"
        ),
        "confirmation_binding_exposed": False,
        "next": next_step,
        "paths": paths,
    })


def _retry(args, paths):
    """经用户明确授权后重置一条failed终态，之后必须重新走草稿确认。"""
    comment_id = str(getattr(args, "comment_id", "") or "").strip()
    if not comment_id:
        print_json({
            "ok": False,
            "action": "retry",
            "error": "retry动作必须提供--comment-id",
            "error_type": "invalid_retry_target",
            "automatic_retry": False,
        })
        return
    if not getattr(args, "retry_authorized", False):
        print_json({
            "ok": False,
            "action": "retry",
            "error": "缺少--retry-authorized；必须先取得用户明确授权",
            "error_type": "retry_confirmation_required",
            "automatic_retry": False,
            "requires_user_confirmation": True,
        })
        return
    try:
        drafts = _optional_state(paths["drafts"], "program_state")
    except (OSError, json.JSONDecodeError, ValueError) as error:
        print_json({
            "ok": False,
            "action": "retry",
            "error": f"草稿状态无法读取: {error}",
            "error_type": "workflow_state_read_error",
            "automatic_retry": False,
        })
        return
    items = drafts.get("drafts", [])
    target = next((
        item for item in items
        if str(item.get("comment_id", "")) == comment_id
    ), None) if isinstance(items, list) else None
    if target is None or target.get("send_status") != "failed":
        print_json({
            "ok": False,
            "action": "retry",
            "error": "目标评论不存在或当前不是failed终态",
            "error_type": "invalid_retry_target",
            "automatic_retry": False,
        })
        return

    # 先移出全局排除列表；若后续写草稿失败，原failed状态仍会阻止误发。
    skipped_removed = XHSClient.remove_skipped(comment_id)
    _clear_active_batch(paths["drafts"])
    drafts = _optional_state(paths["drafts"], "program_state")
    target = next(
        item for item in drafts.get("drafts", [])
        if str(item.get("comment_id", "")) == comment_id
    )
    for field in (
        "send_status", "send_started_at", "sent_at", "last_error",
        "error_type", "archive_reason", "reconciled_online",
    ):
        target.pop(field, None)
    write_json(drafts, paths["drafts"])
    print_json({
        "ok": True,
        "action": "retry",
        "comment_id": comment_id,
        "state_reset": True,
        "skipped_removed": skipped_removed,
        "batch_invalidated": True,
        "requires_redraft": True,
        "next": (
            "重新运行prepare并完成map、draft、用户确认和send；若评论不在"
            "最新通知范围，用户明确要求全部历史时使用--full-scan"
        ),
        "paths": paths,
    })
