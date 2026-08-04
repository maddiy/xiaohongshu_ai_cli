"""AI回复工作流的结构化映射写入阶段。"""

import json
import os

from .state_io import json_state_exists
from .cli_ai_prepare import _clear_active_batch
from .cli_ai_support import (
    _duplicate_send_errors,
    _load_json,
    _validate_reply_map,
)
from .cli_support import filter_scan_local_state, print_json, write_json


def _mapping_error(message, details=None, error_type="invalid_map_arguments"):
    payload = {
        "ok": False,
        "action": "map",
        "error": message,
        "error_type": error_type,
        "automatic_retry": False,
    }
    if details:
        payload["details"] = details
    return payload


def _map_reply(args, paths):
    """用命令行字段构造一条合法映射，避免AI手工编辑JSON。"""
    scan_path = paths["scan"]
    reply_path = os.path.abspath(args.replies or paths["reply_map"])
    if not json_state_exists(scan_path):
        print_json(_mapping_error(
            "scan.json 不存在，请先运行 --action prepare",
            error_type="scan_required",
        ))
        return
    try:
        scan = _load_json(scan_path, role="program_state")
    except (OSError, json.JSONDecodeError) as error:
        print_json(_mapping_error(
            f"无法读取扫描状态: {error}",
            error_type="scan_read_error",
        ))
        return
    if not isinstance(scan, dict) or not scan.get(
        "reply_status_verified", False
    ):
        print_json(_mapping_error(
            "扫描结果未通过在线核验，请重新 prepare",
            error_type="verified_scan_required",
        ))
        return

    filter_scan_local_state(scan)
    candidates = (
        scan.get("unreplied_level1", [])
        + scan.get("unreplied_subs", [])
    )
    candidate_ids = [
        str(item.get("comment_id", "")) for item in candidates
        if item.get("comment_id")
    ]
    candidate_index_to_id = {}
    candidate_id_to_index = {}
    for fallback_index, item in enumerate(candidates, start=1):
        candidate_id = str(item.get("comment_id", "") or "")
        if not candidate_id:
            continue
        try:
            stable_index = int(
                item.get("candidate_index", fallback_index)
            )
        except (TypeError, ValueError):
            stable_index = fallback_index
        candidate_index_to_id[stable_index] = candidate_id
        candidate_id_to_index[candidate_id] = stable_index
    comment_id = str(getattr(args, "comment_id", "") or "").strip()
    candidate_index = getattr(args, "candidate_index", None)
    if comment_id and candidate_index is not None:
        print_json(_mapping_error(
            "--comment-id与--candidate-index只能使用一个",
        ))
        return
    if candidate_index is not None:
        if candidate_index not in candidate_index_to_id:
            print_json(_mapping_error(
                "candidate-index超出本次候选范围",
                details={
                    "available_candidate_indexes": sorted(
                        candidate_index_to_id
                    ),
                },
                error_type="invalid_mapping_target",
            ))
            return
        comment_id = candidate_index_to_id[candidate_index]
    elif comment_id in candidate_ids:
        candidate_index = candidate_id_to_index[comment_id]
    decision = str(getattr(args, "decision", "") or "").strip()
    required_fields = {
        "--comment-id或--candidate-index": comment_id,
        "--decision": decision,
        "--logic-verdict": getattr(args, "logic_verdict", None),
        "--logic-reason": getattr(args, "logic_reason", None),
        "--fact-verdict": getattr(args, "fact_verdict", None),
        "--fact-reason": getattr(args, "fact_reason", None),
        "--boast-verdict": getattr(args, "boast_verdict", None),
        "--boast-reason": getattr(args, "boast_reason", None),
    }
    missing = [name for name, value in required_fields.items() if not value]
    if missing:
        print_json(_mapping_error(
            "map动作缺少必填字段",
            details=missing,
        ))
        return
    if comment_id not in candidate_ids:
        print_json(_mapping_error(
            "comment_id不属于本次扫描候选",
            details=[comment_id],
            error_type="invalid_mapping_target",
        ))
        return

    sources = [
        {"title": str(title), "url": str(url)}
        for title, url in (getattr(args, "fact_source", None) or [])
    ]
    entry = {
        "reply": str(getattr(args, "reply_text", "") or ""),
        "action": decision,
        "review": {
            "logic": {
                "verdict": args.logic_verdict,
                "reason": args.logic_reason,
            },
            "fact_check": {
                "verdict": args.fact_verdict,
                "reason": args.fact_reason,
                "sources": sources,
            },
            "boast_check": {
                "verdict": args.boast_verdict,
                "reason": args.boast_reason,
            },
        },
    }
    errors = _validate_reply_map({comment_id: entry}, [comment_id])
    if errors:
        print_json(_mapping_error(
            "结构化回复映射校验失败",
            details=errors,
            error_type="invalid_map_entry",
        ))
        return

    reply_map = {}
    if json_state_exists(reply_path):
        try:
            reply_map = _load_json(reply_path, role="ai_input")
        except (OSError, json.JSONDecodeError) as error:
            print_json(_mapping_error(
                f"现有回复映射无法读取: {error}",
                error_type="reply_map_read_error",
            ))
            return
        if not isinstance(reply_map, dict):
            print_json(_mapping_error(
                "现有回复映射顶层必须是JSON对象",
                error_type="reply_map_read_error",
            ))
            return

    proposed_map = dict(reply_map)
    proposed_map[comment_id] = entry
    duplicate_errors = _duplicate_send_errors(candidates, proposed_map)
    if duplicate_errors:
        print_json(_mapping_error(
            "结构化映射包含重复send决策",
            details=duplicate_errors,
            error_type="duplicate_send_mapping",
        ))
        return

    changed = reply_map.get(comment_id) != entry
    batch_invalidated = False
    if changed:
        if json_state_exists(paths["drafts"]):
            try:
                drafts = _load_json(
                    paths["drafts"], role="program_state"
                )
                batch_invalidated = bool(
                    isinstance(drafts, dict)
                    and drafts.get("active_comment_ids")
                )
            except (OSError, json.JSONDecodeError):
                batch_invalidated = True
            _clear_active_batch(paths["drafts"])
        reply_map = proposed_map
        write_json(reply_map, reply_path, indent=2)

    mapped_ids = [
        candidate_id for candidate_id in candidate_ids
        if not _validate_reply_map(reply_map, [candidate_id])
    ]
    remaining_ids = [
        candidate_id for candidate_id in candidate_ids
        if candidate_id not in mapped_ids
    ]
    remaining_indexes = [
        candidate_id_to_index[candidate_id]
        for candidate_id in remaining_ids
    ]
    print_json({
        "ok": True,
        "action": "map",
        "comment_id": comment_id,
        "candidate_index": candidate_index,
        "decision": decision,
        "changed": changed,
        "batch_invalidated": batch_invalidated,
        "mapped_count": len(mapped_ids),
        "remaining_count": len(remaining_ids),
        "remaining_comment_ids": remaining_ids,
        "remaining_candidate_indexes": remaining_indexes,
        "reply_map_path": reply_path,
        "next": (
            "继续为remaining_candidate_indexes执行map"
            if remaining_ids else "映射完整，运行ai-reply --action draft"
        ),
        "paths": paths,
    })
