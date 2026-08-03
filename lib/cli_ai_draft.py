"""AI回复工作流的映射校验、在线复核和草稿生成阶段。"""

import datetime
import json
import os

from .state_io import json_state_exists
from .cli_ai_prepare import _clear_active_batch
from .cli_ai_support import (
    DRAFT_COLUMNS,
    INLINE_ROW_LIMIT,
    REVIEW_COLUMNS,
    REVIEW_COLUMN_FIELDS,
    _active_items,
    _duplicate_send_errors,
    _inline_rows,
    _load_json,
    _pending,
    _preview,
    _repair_reply_map_text_quotes,
    _reply_map_validation_error,
    _review_rows,
    _validate_reply_map,
    _with_quote_repair,
    _workflow_error,
)
from .cli_support import (
    call_for_output,
    filter_scan_local_state,
    merge_draft_history,
    new_batch_id,
    preview_hash,
    print_json,
    write_json,
)
from .replier import Replier
from .scanner import CommentScanner


def _draft(args, paths):
    scan_path = paths["scan"]
    reply_path = os.path.abspath(args.replies or paths["reply_map"])
    # 即使本次草稿失败也停用旧活动批次，防止调用方误把旧预览当作新批发送。
    _clear_active_batch(paths["drafts"])
    if not json_state_exists(scan_path):
        print_json({
            "ok": False, "action": "draft",
            "error": "scan.json 不存在，请先运行 --action prepare",
        })
        return
    try:
        scan = _load_json(scan_path)
    except (OSError, json.JSONDecodeError) as error:
        print_json({
            "ok": False, "action": "draft",
            "error": f"scan.json 不是有效 JSON: {error}",
        })
        return
    if not isinstance(scan, dict):
        print_json({
            "ok": False, "action": "draft",
            "error": "scan.json 顶层必须是 JSON 对象",
        })
        return
    if not scan.get("reply_status_verified", False):
        print_json({
            "ok": False, "action": "draft",
            "error": "扫描结果未通过在线核验，请重新 prepare",
        })
        return
    # prepare 与 draft 之间可能由其他客户端或 AI 完成发送；再次应用本地终态。
    filter_scan_local_state(scan)
    candidates = (
        scan.get("unreplied_level1", [])
        + scan.get("unreplied_subs", [])
    )
    if not candidates:
        print_json({
            "ok": True, "action": "draft", "count": 0,
            "columns": DRAFT_COLUMNS,
            "preview": [], "next": "没有可回复评论，停止",
        })
        return
    if not json_state_exists(reply_path):
        print_json({
            "ok": False, "action": "draft",
            "error": f"回复映射不存在: {reply_path}",
        })
        return
    # 在昂贵的在线复核前先检查映射文件语法，避免格式错误浪费平台请求。
    reply_map_repaired = False
    quote_replacements = 0
    try:
        reply_map = _load_json(reply_path)
    except json.JSONDecodeError as error:
        try:
            reply_map, quote_replacements = (
                _repair_reply_map_text_quotes(reply_path)
            )
        except OSError:
            reply_map = None
        if reply_map is None:
            print_json({
                "ok": False, "action": "draft",
                "error": f"reply_map.json 不是有效 JSON: {error}",
                "error_type": "invalid_reply_map_json",
                "automatic_retry": False,
                "requires_file_fix": True,
                "error_location": {
                    "line": error.lineno,
                    "column": error.colno,
                    "character": error.pos,
                },
                "quote_policy": {
                    "text_values": (
                        "只把reply、review等文本内容里的英文半角双引号"
                        "改为中文引号“”或「」"
                    ),
                    "json_structure": "JSON键名和结构引号必须保留英文半角双引号",
                    "forbidden": "禁止全文件查找替换英文双引号",
                    "preferred_writer": "优先使用标准JSON写入器自动转义",
                },
                "next": (
                    "检查error_location；若回复或审查正文含未转义的英文半角"
                    "双引号，只将正文中的引号改为中文引号“”或「」，不要替换"
                    "JSON结构引号；修复后重新运行draft"
                ),
            })
            return
        reply_map_repaired = True
    except OSError as error:
        print_json({
            "ok": False, "action": "draft",
            "error": f"无法读取 reply_map.json: {error}",
            "error_type": "reply_map_read_error",
            "automatic_retry": False,
            "requires_file_fix": True,
            "next": "检查文件路径和读取权限后重新运行draft",
        })
        return
    if not isinstance(reply_map, dict):
        print_json(_reply_map_validation_error([
            "顶层必须是 JSON 对象，以 comment_id 为键",
        ]))
        return
    # 语义错误也应在联网前发现，避免无效映射消耗在线核验请求。
    scan_candidate_ids = [
        item.get("comment_id") for item in candidates
        if item.get("comment_id")
    ]
    mapping_errors = _validate_reply_map(reply_map, scan_candidate_ids)
    if mapping_errors:
        print_json(_with_quote_repair(
            _reply_map_validation_error(mapping_errors),
            reply_map_repaired,
            quote_replacements,
        ))
        return
    duplicate_errors = _duplicate_send_errors(candidates, reply_map)
    if duplicate_errors:
        print_json(_with_quote_repair(
            _reply_map_validation_error(
                duplicate_errors,
                error_type="duplicate_send_mapping",
                next_step=(
                    "同一用户的相同评论只保留一条send，其余改为skip，"
                    "然后重新运行draft"
                ),
            ),
            reply_map_repaired,
            quote_replacements,
        ))
        return
    scanner = CommentScanner()
    try:
        candidates, excluded = call_for_output(
            scanner.verify_candidates_online,
            args.note_id,
            candidates,
            "",
            quiet=True,
        )
    except Exception as error:
        print_json(_with_quote_repair(
            _workflow_error(
                "draft", error, prefix="在线复核失败", paths=paths
            ),
            reply_map_repaired,
            quote_replacements,
        ))
        return
    if not candidates:
        print_json({
            "ok": True, "action": "draft", "count": 0,
            "excluded_online": len(excluded),
            "reply_map_repaired": reply_map_repaired,
            "quote_replacements": quote_replacements,
            "columns": DRAFT_COLUMNS,
            "preview": [], "next": "在线复核后没有可回复评论，停止",
            "paths": paths,
        })
        return
    candidate_ids = [
        item.get("comment_id") for item in candidates
        if item.get("comment_id")
    ]
    drafts = call_for_output(
        Replier().generate_drafts_from_mapping,
        candidates,
        reply_map,
        note_id=args.note_id,
        note_title=scan.get("note_title", ""),
        quiet=True,
    )
    drafts["active_comment_ids"] = candidate_ids
    existing_drafts = {}
    if json_state_exists(paths["drafts"]):
        try:
            existing_drafts = _load_json(paths["drafts"])
            drafts = merge_draft_history(existing_drafts, drafts)
        except (OSError, json.JSONDecodeError):
            pass
    if not isinstance(existing_drafts, dict):
        existing_drafts = {}
    active_items = _active_items(drafts)
    revision = int(existing_drafts.get("workflow_revision", 0) or 0) + 1
    batch_id = new_batch_id()
    current_preview_hash = preview_hash(active_items)
    drafts["workflow_revision"] = revision
    drafts["active_batch"] = {
        "batch_id": batch_id,
        "revision": revision,
        "preview_hash": current_preview_hash,
        "status": "previewed",
        "created_at": datetime.datetime.now().astimezone().isoformat(
            timespec="seconds"
        ),
    }
    write_json(drafts, paths["drafts"])
    pending = _pending(drafts)
    archives = [
        item for item in active_items
        if item.get("action") == "archive"
        and item.get("send_status") != "archived"
    ]
    preview_rows = _preview(active_items)
    review_rows = _review_rows(active_items)
    print_json({
        "ok": True,
        "action": "draft",
        "count": len(active_items),
        "send_count": len(pending),
        "skip_count": sum(
            item.get("action") == "skip" for item in active_items
        ),
        "archive_count": len(archives),
        "excluded_online": len(excluded),
        "reply_map_repaired": reply_map_repaired,
        "quote_replacements": quote_replacements,
        "batch_id": batch_id,
        "revision": revision,
        "preview_hash": current_preview_hash,
        "columns": DRAFT_COLUMNS,
        "preview": _inline_rows(preview_rows),
        "preview_total": len(preview_rows),
        "preview_returned": min(len(preview_rows), INLINE_ROW_LIMIT),
        "preview_truncated": len(preview_rows) > INLINE_ROW_LIMIT,
        "preview_source": paths["drafts"],
        "review_columns": REVIEW_COLUMNS,
        "review_column_fields": REVIEW_COLUMN_FIELDS,
        "review_display": {
            "use": "label + reason；fact_check.sources按链接展示",
            "comment_id_visible": False,
        },
        "reviews": _inline_rows(review_rows),
        "reviews_total": len(review_rows),
        "reviews_returned": min(len(review_rows), INLINE_ROW_LIMIT),
        "reviews_truncated": len(review_rows) > INLINE_ROW_LIMIT,
        "reviews_source": paths["drafts"],
        "next": (
            "向用户展示 preview；明确确认后运行 "
            "ai-reply --action send --confirmed "
            f"--batch-id {batch_id} --preview-hash {current_preview_hash}"
            if pending or archives else "本批全部为本次跳过，无需发送"
        ),
        "paths": paths,
    })
