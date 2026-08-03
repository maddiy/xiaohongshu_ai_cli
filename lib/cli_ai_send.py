"""AI回复工作流的确认绑定、在线对账和发送阶段。"""

import datetime
import json

from .state_io import json_state_exists
from .cli_ai_audit import _record_send_attempt
from .cli_ai_support import (
    INLINE_ROW_LIMIT,
    RESULT_COLUMNS,
    _active_items,
    _inflight,
    _inline_rows,
    _load_json,
    _pending,
    _result_rows,
    _workflow_error,
)
from .cli_support import (
    call_for_output,
    compact_comment,
    preview_hash,
    print_json,
    write_json,
)
from .replier import Replier
from .scanner import CommentScanner


def _send(args, paths):
    """处理已有活动批次；不证明本轮刚执行过prepare或draft。"""
    if not args.confirmed:
        print_json({
            "ok": False, "action": "send",
            "error": "缺少 --confirmed；必须先向用户展示草稿并取得明确确认",
        })
        return
    if not json_state_exists(paths["drafts"]):
        print_json({
            "ok": False, "action": "send",
            "error": "drafts.json 不存在，请先运行 --action draft",
        })
        return
    try:
        drafts = _load_json(paths["drafts"])
    except (OSError, json.JSONDecodeError) as error:
        print_json({
            "ok": False, "action": "send",
            "error": f"drafts.json 不是有效 JSON: {error}",
        })
        return
    if not isinstance(drafts, dict):
        print_json({
            "ok": False, "action": "send",
            "error": "drafts.json 顶层必须是 JSON 对象",
        })
        return
    if "active_comment_ids" not in drafts:
        print_json({
            "ok": False, "action": "send",
            "error": "草稿缺少本次批次标记，请重新运行 --action draft",
        })
        return
    active_batch = drafts.get("active_batch")
    if not isinstance(active_batch, dict):
        print_json({
            "ok": False,
            "action": "send",
            "error": "草稿缺少安全批次信息，请重新运行 --action draft",
            "error_type": "legacy_batch_requires_redraft",
            "automatic_retry": False,
        })
        return
    expected_batch_id = str(active_batch.get("batch_id", ""))
    expected_preview_hash = str(active_batch.get("preview_hash", ""))
    supplied_batch_id = str(getattr(args, "batch_id", "") or "")
    supplied_preview_hash = str(
        getattr(args, "preview_hash", "") or ""
    )
    if not supplied_batch_id or not supplied_preview_hash:
        print_json({
            "ok": False,
            "action": "send",
            "error": "缺少 --batch-id 或 --preview-hash，无法确认用户审核的是当前草稿",
            "error_type": "confirmation_not_bound",
            "automatic_retry": False,
            "batch_id": expected_batch_id,
            "preview_hash": expected_preview_hash,
        })
        return
    if (
        supplied_batch_id != expected_batch_id
        or supplied_preview_hash != expected_preview_hash
    ):
        mismatch = {
            "batch_id": supplied_batch_id != expected_batch_id,
            "preview_hash": supplied_preview_hash != expected_preview_hash,
        }
        attempt = _record_send_attempt(
            drafts,
            supplied_batch_id,
            supplied_preview_hash,
            "rejected",
            error_type="stale_preview",
            mismatch=mismatch,
        )
        write_json(drafts, paths["drafts"])
        print_json({
            "ok": False,
            "action": "send",
            "error": "确认信息与当前活动批次不一致，提交的可能是旧预览或混用了确认参数",
            "error_type": "stale_preview",
            "automatic_retry": False,
            "requires_user_confirmation": True,
            "mismatch": mismatch,
            "current_revision": active_batch.get("revision"),
            "current_batch_status": active_batch.get("status", ""),
            "attempt_id": attempt["attempt_id"],
            "attempt_log": paths["drafts"],
            "diagnostic": (
                "只能确认提交值与当前活动批次不一致；可能由新draft覆盖、"
                "复制旧参数或混用不同预览的参数造成"
            ),
            "next": "重新运行draft、展示新preview并取得用户确认",
        })
        return
    if active_batch.get("status") == "superseded":
        attempt = _record_send_attempt(
            drafts,
            supplied_batch_id,
            supplied_preview_hash,
            "rejected",
            error_type="stale_preview",
        )
        write_json(drafts, paths["drafts"])
        print_json({
            "ok": False,
            "action": "send",
            "error": "该批次已被更新批次替代，禁止发送旧预览",
            "error_type": "stale_preview",
            "automatic_retry": False,
            "attempt_id": attempt["attempt_id"],
            "attempt_log": paths["drafts"],
        })
        return
    active_items = _active_items(drafts)
    actual_preview_hash = preview_hash(active_items)
    if actual_preview_hash != expected_preview_hash:
        attempt = _record_send_attempt(
            drafts,
            supplied_batch_id,
            supplied_preview_hash,
            "rejected",
            error_type="preview_content_changed",
        )
        write_json(drafts, paths["drafts"])
        print_json({
            "ok": False,
            "action": "send",
            "error": "草稿内容在用户确认后发生变化，已停止发送",
            "error_type": "preview_content_changed",
            "automatic_retry": False,
            "attempt_id": attempt["attempt_id"],
            "attempt_log": paths["drafts"],
            "next": "重新运行draft、展示新preview并取得用户确认",
        })
        return
    pending = _pending(drafts)
    inflight = _inflight(drafts)
    archives = [
        item for item in active_items
        if item.get("action") == "archive"
        and item.get("send_status") != "archived"
    ]
    skipped_items = [
        item for item in active_items if item.get("action") == "skip"
    ]

    # 发送中状态表示上次进程可能在平台请求期间退出。先在线对账：
    # 已出现作者回复则视为已处理；仍显示未回复时结果不确定，禁止自动重发。
    verify_items = inflight + pending
    excluded = []
    if verify_items:
        try:
            eligible, excluded = call_for_output(
                CommentScanner().verify_candidates_online,
                args.note_id,
                [compact_comment(item) for item in verify_items],
                "",
                quiet=True,
            )
        except Exception as error:
            print_json(_workflow_error(
                "send", error, prefix="发送前在线复核失败", paths=paths
            ))
            return
    else:
        eligible = []
    eligible_ids = {item.get("comment_id") for item in eligible}
    excluded_by_id = {
        item.get("comment_id"): item.get("reason") for item in excluded
    }
    uncertain_inflight = []
    reconciled_sent = 0
    online_archived_count = 0
    for item in inflight:
        comment_id = item.get("comment_id")
        if comment_id in eligible_ids:
            uncertain_inflight.append(item)
        elif excluded_by_id.get(comment_id) == "online_replied":
            item["send_status"] = "sent"
            item["reconciled_online"] = True
            item["sent_at"] = datetime.datetime.now().astimezone().isoformat(
                timespec="seconds"
            )
            item.pop("send_started_at", None)
            reconciled_sent += 1
        else:
            item["send_status"] = "archived"
            item["archive_reason"] = excluded_by_id.get(
                comment_id, "online_missing"
            )
            item.pop("send_started_at", None)
            online_archived_count += 1
    if uncertain_inflight:
        write_json(drafts, paths["drafts"])
        print_json({
            "ok": False,
            "action": "send",
            "error": "存在发送结果不确定的评论，已禁止自动重发",
            "error_type": "uncertain_send_state",
            "automatic_retry": False,
            "requires_user_action": True,
            "count": len(uncertain_inflight),
            "next": "请先在线确认这些评论是否已经回复，再决定是否人工重置",
            "state_file": paths["drafts"],
        })
        return
    for item in pending:
        comment_id = item.get("comment_id")
        if comment_id not in eligible_ids:
            item["send_status"] = "archived"
            item["archive_reason"] = excluded_by_id.get(
                comment_id, "online_excluded"
            )
            online_archived_count += 1
    write_json(drafts, paths["drafts"])
    pending = _pending(drafts)

    if not pending and not archives:
        active_batch["status"] = "completed"
        active_batch["completed_at"] = (
            datetime.datetime.now().astimezone().isoformat(
                timespec="seconds"
            )
        )
        attempt = _record_send_attempt(
            drafts,
            supplied_batch_id,
            supplied_preview_hash,
            "completed",
            result={
                "sent": reconciled_sent,
                "failed": 0,
                "skipped": len(skipped_items) + online_archived_count,
            },
        )
        write_json(drafts, paths["drafts"])
        result_rows = _result_rows(active_items)
        print_json({
            "ok": True, "action": "send", "sent": reconciled_sent,
            "failed": 0,
            "skipped": len(skipped_items) + online_archived_count,
            "columns": RESULT_COLUMNS,
            "results": _inline_rows(result_rows),
            "results_total": len(result_rows),
            "results_returned": min(len(result_rows), INLINE_ROW_LIMIT),
            "results_truncated": len(result_rows) > INLINE_ROW_LIMIT,
            "results_source": paths["drafts"],
            "batch_id": expected_batch_id,
            "attempt_id": attempt["attempt_id"],
            "attempt_log": paths["drafts"],
        })
        return

    active_batch["status"] = "sending"
    active_batch["send_started_at"] = (
        datetime.datetime.now().astimezone().isoformat(
            timespec="seconds"
        )
    )
    write_json(drafts, paths["drafts"])

    stats = call_for_output(
        Replier().send_drafts,
        drafts,
        state_file=paths["drafts"],
        quiet=True,
    )
    if stats.get("stopped"):
        active_batch["status"] = "paused"
        active_batch["paused_at"] = (
            datetime.datetime.now().astimezone().isoformat(
                timespec="seconds"
            )
        )
        active_batch["pause_reason"] = stats.get("stop_reason", "")
    else:
        active_batch["status"] = (
            "completed_with_failures"
            if stats.get("fail", 0) else "completed"
        )
        active_batch["completed_at"] = (
            datetime.datetime.now().astimezone().isoformat(
                timespec="seconds"
            )
        )
    active_batch.pop("send_started_at", None)
    attempt = _record_send_attempt(
        drafts,
        supplied_batch_id,
        supplied_preview_hash,
        "paused" if stats.get("stopped") else "completed",
        error_type=("send_failed" if stats.get("fail", 0) else ""),
        result={
            "sent": stats.get("success", 0) + reconciled_sent,
            "failed": stats.get("fail", 0),
            "skipped": (
                stats.get("skip", 0)
                + online_archived_count
                + len(skipped_items)
            ),
            "remaining": stats.get("remaining", 0),
        },
    )
    write_json(drafts, paths["drafts"])
    result_rows = _result_rows(active_items)
    print_json({
        "ok": stats.get("fail", 0) == 0 and not stats.get("stopped"),
        "action": "send",
        "sent": stats.get("success", 0) + reconciled_sent,
        "failed": stats.get("fail", 0),
        "paused": bool(stats.get("stopped")),
        "pause_reason": stats.get("stop_reason", ""),
        "remaining": stats.get("remaining", 0),
        "skipped": (
            stats.get("skip", 0)
            + online_archived_count
            + len(skipped_items)
        ),
        "batch_id": expected_batch_id,
        "attempt_id": attempt["attempt_id"],
        "attempt_log": paths["drafts"],
        "columns": RESULT_COLUMNS,
        "results": _inline_rows(result_rows),
        "results_total": len(result_rows),
        "results_returned": min(len(result_rows), INLINE_ROW_LIMIT),
        "results_truncated": len(result_rows) > INLINE_ROW_LIMIT,
        "results_source": paths["drafts"],
        "state_file": paths["drafts"],
    })
