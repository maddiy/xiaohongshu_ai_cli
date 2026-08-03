"""AI回复工作流的候选准备阶段。"""

import datetime
import json

from .state_io import json_state_exists
from .cli_ai_support import (
    INLINE_ROW_LIMIT,
    _inline_rows,
    _load_json,
    _workflow_error,
)
from .cli_support import (
    NON_RESEND_STATUSES,
    call_for_output,
    compact_scan_result,
    filter_scan_local_state,
    load_local_comment_states,
    print_json,
    write_json,
)
from .scanner import CommentScanner


def _clear_active_batch(drafts_path):
    """prepare或draft开始时停用旧批次；保留历史草稿，仅禁止误发旧批次。"""
    if not json_state_exists(drafts_path):
        return
    try:
        drafts = _load_json(drafts_path)
    except (OSError, json.JSONDecodeError):
        return
    if isinstance(drafts, dict) and drafts.get("active_comment_ids") != []:
        drafts["active_comment_ids"] = []
        active_batch = drafts.get("active_batch")
        if (
            isinstance(active_batch, dict)
            and active_batch.get("status") not in {
                "completed", "completed_with_failures", "superseded",
            }
        ):
            active_batch["status"] = "superseded"
            active_batch["superseded_at"] = (
                datetime.datetime.now().astimezone().isoformat(
                    timespec="seconds"
                )
            )
        write_json(drafts, drafts_path)


def _prepare(args, paths):
    """创建新候选批次；输出字段明确实际扫描方法和核验模式。"""
    _clear_active_batch(paths["drafts"])
    scanner = CommentScanner()
    try:
        if getattr(args, "full_scan", False):
            # 全量回复必须绕过TTL缓存并拉取完整楼中楼。
            result = call_for_output(
                scanner.scan_note,
                args.note_id,
                "",
                include_sub_comments=True,
                force_refresh=True,
                verbose=False,
                quiet=True,
            )
            scope = "full"
            scan_method = "scan_note"
            verification_mode = "full_tree_scan"
        else:
            local_states = load_local_comment_states(args.note_id)
            excluded_comment_ids = {
                comment_id for comment_id, status in local_states.items()
                if status in NON_RESEND_STATUSES
            }
            result = call_for_output(
                scanner.scan_via_notifications,
                note_id=args.note_id,
                num_notifications=getattr(args, "limit", 20) or 20,
                verify_replied=True,
                excluded_comment_ids=excluded_comment_ids,
                verification_max_pages=6,
                allow_partial_verification=True,
                verbose=False,
                quiet=True,
            )
            scope = "latest"
            scan_method = "scan_via_notifications"
            verification_mode = "candidate_online_recheck"
    except Exception as error:
        print_json(_workflow_error(
            "prepare", error, paths=paths
        ))
        return
    filter_scan_local_state(result)
    output = compact_scan_result(result)
    write_json(output, paths["scan"])
    if not output.get("reply_status_verified", False):
        print_json(_workflow_error(
            "prepare",
            output.get("scan_error", "在线回复状态核验失败"),
            paths=paths,
        ))
        return
    candidates = (
        output.get("unreplied_level1", [])
        + output.get("unreplied_subs", [])
    )
    print_json({
        "ok": True,
        "action": "prepare",
        "scope": scope,
        "scan_method": scan_method,
        "verification_mode": verification_mode,
        "note_id": args.note_id,
        "candidates": _inline_rows(candidates),
        "count": len(candidates),
        "candidates_returned": min(len(candidates), INLINE_ROW_LIMIT),
        "candidates_truncated": len(candidates) > INLINE_ROW_LIMIT,
        "candidates_source": paths["scan"],
        "deferred_count": output.get("deferred_online", 0),
        "next": (
            (
                "将回复映射写入 paths.reply_map，再运行 "
                "ai-reply --action draft"
                + (
                    "；另有深层楼中楼超过快速核验预算，未进入本批；"
                    "确需处理全部评论时使用 --full-scan"
                    if output.get("deferred_online", 0) else ""
                )
            )
            if candidates else (
                "本次没有已安全定位的可回复评论；存在超过快速核验预算的"
                "深层楼中楼，确需处理时使用 --full-scan"
                if output.get("deferred_online", 0)
                else "没有可回复评论，停止"
            )
        ),
        "paths": paths,
    })
