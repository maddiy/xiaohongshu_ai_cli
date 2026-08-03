"""面向 AI 的低往返、紧凑 JSON 回复工作流。"""

import contextlib
import datetime
import io
import json
import os

from .state_io import json_state_exists, read_json_state

from .cli_support import (
    append_workflow_audit,
    call_for_output,
    compact_comment,
    compact_scan_result,
    current_workflow_audit_id,
    filter_scan_local_state,
    load_local_comment_states,
    merge_draft_history,
    new_batch_id,
    NON_RESEND_STATUSES,
    preview_hash,
    print_json,
    StateLockTimeout,
    workflow_lock,
    workflow_paths,
    write_json,
)
from .replier import Replier
from .scanner import CommentScanner


from .cli_ai_audit import (
    _audit_inputs,
    _audit_result,
    _audit_text,
    _audit_timestamp,
    _audit_value,
    _record_send_attempt,
)
from .cli_ai_support import (
    AI_TEXT_VALUE_LINE,
    AUDIT_RESULT_FIELDS,
    BOAST_VERDICTS,
    DRAFT_COLUMNS,
    FACT_VERDICTS,
    INLINE_ROW_LIMIT,
    LOGIC_VERDICTS,
    RESULT_COLUMNS,
    REVIEW_COLUMN_FIELDS,
    REVIEW_COLUMNS,
    REVIEW_VERDICT_LABELS,
    SEND_ATTEMPT_LIMIT,
    _active_items,
    _duplicate_send_errors,
    _inflight,
    _inline_rows,
    _load_json,
    _pending,
    _preview,
    _repair_reply_map_text_quotes,
    _reply_map_validation_error,
    _result_rows,
    _review_rows,
    _unescaped_quote_positions,
    _validate_reply_map,
    _validate_review,
    _with_quote_repair,
    _workflow_error,
)


def _run_audited_action(handler, args, paths):
    """记录命令起止事件；无开始记录时禁止执行，避免出现无证据发送。"""
    command_id = new_batch_id()
    audit_path = paths.get(
        "audit", os.path.join(paths["directory"], "audit.json")
    )
    started_at = _audit_timestamp()
    try:
        previous_workflow_id = current_workflow_audit_id(audit_path)
        if args.action == "prepare":
            workflow_id = new_batch_id()
            workflow_origin = "prepare"
        elif previous_workflow_id:
            workflow_id = previous_workflow_id
            workflow_origin = "continued"
        else:
            workflow_id = new_batch_id()
            workflow_origin = "continued_without_audited_prepare"
        append_workflow_audit(audit_path, args.note_id, {
            "event_id": new_batch_id(),
            "workflow_id": workflow_id,
            "command_id": command_id,
            "timestamp": started_at,
            "action": args.action,
            "phase": "started",
            "workflow_origin": workflow_origin,
            "inputs": _audit_inputs(args, paths),
        }, current_workflow_id=workflow_id)
    except Exception as error:
        print_json({
            "ok": False,
            "action": args.action,
            "error": f"无法写入工作流审计，已停止执行: {error}",
            "error_type": "audit_unavailable",
            "automatic_retry": False,
            "audit": {"path": audit_path, "recorded": False},
        })
        return

    output = io.StringIO()
    try:
        with contextlib.redirect_stdout(output):
            handler(args, paths)
        raw = output.getvalue().strip()
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("工作流输出顶层不是JSON对象")
    except Exception as error:
        payload = _workflow_error(
            args.action, error, prefix="工作流执行异常", paths=paths
        )

    audit_recorded = True
    audit_error = ""
    try:
        append_workflow_audit(audit_path, args.note_id, {
            "event_id": new_batch_id(),
            "workflow_id": workflow_id,
            "command_id": command_id,
            "timestamp": _audit_timestamp(),
            "action": args.action,
            "phase": "completed" if payload.get("ok") else "failed",
            "started_at": started_at,
            "result": _audit_result(payload),
        }, current_workflow_id=workflow_id)
    except Exception as error:
        audit_recorded = False
        audit_error = _audit_text(error)
    payload["audit"] = {
        "command_id": command_id,
        "workflow_id": workflow_id,
        "path": audit_path,
        "recorded": audit_recorded,
        **({"error": audit_error} if audit_error else {}),
    }
    print_json(payload)


from . import cli_ai_draft as _draft_stage
from . import cli_ai_prepare as _prepare_stage
from . import cli_ai_send as _send_stage


_ACTION_RUNTIME_NAMES = (
    "CommentScanner",
    "Replier",
    "call_for_output",
    "compact_comment",
    "compact_scan_result",
    "filter_scan_local_state",
    "load_local_comment_states",
    "merge_draft_history",
    "new_batch_id",
    "preview_hash",
    "print_json",
    "write_json",
    "_active_items",
    "_duplicate_send_errors",
    "_inflight",
    "_inline_rows",
    "_load_json",
    "_pending",
    "_preview",
    "_record_send_attempt",
    "_repair_reply_map_text_quotes",
    "_reply_map_validation_error",
    "_result_rows",
    "_review_rows",
    "_validate_reply_map",
    "_with_quote_repair",
    "_workflow_error",
)


def _sync_action_runtime(module):
    """把兼容门面中可能被测试或调用方替换的依赖同步到阶段模块。"""
    namespace = globals()
    for name in _ACTION_RUNTIME_NAMES:
        if name in namespace and hasattr(module, name):
            setattr(module, name, namespace[name])


def _clear_active_batch(drafts_path):
    _sync_action_runtime(_prepare_stage)
    return _prepare_stage._clear_active_batch(drafts_path)


def _prepare(args, paths):
    _sync_action_runtime(_prepare_stage)
    return _prepare_stage._prepare(args, paths)


def _draft(args, paths):
    _sync_action_runtime(_draft_stage)
    _draft_stage._clear_active_batch = _clear_active_batch
    return _draft_stage._draft(args, paths)


def _send(args, paths):
    _sync_action_runtime(_send_stage)
    return _send_stage._send(args, paths)


def cmd_ai_reply(args):
    """执行 prepare、draft 或 send，并且只输出一个 JSON 文档。"""
    paths = workflow_paths(args.note_id)
    try:
        with workflow_lock(
            args.note_id, timeout=3.0, directory=paths["directory"]
        ):
            handler = {
                "prepare": _prepare,
                "draft": _draft,
                "send": _send,
            }[args.action]
            _run_audited_action(handler, args, paths)
    except StateLockTimeout as error:
        print_json({
            "ok": False,
            "action": args.action,
            "error": str(error),
            "error_type": "workflow_busy",
            "automatic_retry": True,
            "retry_after_seconds": 5,
            "next": "不要启动第二个进程；等待当前工作流结束后重试本action",
            "paths": paths,
        })
