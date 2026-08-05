"""本地Web控制台的结构化业务服务；复用CLI权威实现和安全状态机。"""

import contextlib
import datetime
import io
import json
import math
from types import SimpleNamespace

import os

from config import COMMENTS_FILE, GENERIC_REPLIES, WORK_DIR
from .cli_ai_audit import _record_send_attempt
from .cli_ai_support import _workflow_error
from .cli_admin import cmd_ai_help, cmd_analyze, cmd_doctor, cmd_paths
from .cli_support import (
    NON_RESEND_STATUSES,
    append_workflow_audit,
    call_for_output,
    compact_analysis,
    current_workflow_audit_id,
    merge_draft_history,
    new_batch_id,
    preview_hash,
    workflow_lock,
    workflow_paths,
    write_json,
)
from .replier import Replier
from .scanner import CommentScanner
from .state_io import (
    json_state_exists,
    read_json_state,
    read_workflow_state,
)
from .xhs_client import XHSClient


class WebServiceError(ValueError):
    """适合直接返回给本地网页的参数或业务错误。"""

    def __init__(self, message: str, error_type: str = "invalid_web_request",
                 details=None):
        super().__init__(message)
        self.error_type = error_type
        self.details = details


def _text(value, name: str, maximum: int = 4000,
          required: bool = False) -> str:
    result = str(value or "").strip()
    if required and not result:
        raise WebServiceError(f"{name}不能为空")
    if len(result) > maximum:
        raise WebServiceError(f"{name}不能超过{maximum}个字符")
    return result


def _integer(value, name: str, default: int, minimum: int,
             maximum: int) -> int:
    try:
        result = int(value if value not in (None, "") else default)
    except (TypeError, ValueError):
        raise WebServiceError(f"{name}必须是整数") from None
    if result < minimum or result > maximum:
        raise WebServiceError(f"{name}必须在{minimum}到{maximum}之间")
    return result


def _capture_json(handler, args) -> dict:
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        handler(args)
    raw = output.getvalue().strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise WebServiceError(
            f"程序没有返回有效JSON: {error}"
        ) from None
    if not isinstance(payload, dict):
        raise WebServiceError("程序返回的JSON顶层不是对象")
    return payload


def run_doctor() -> dict:
    report = _capture_json(cmd_doctor, SimpleNamespace(json=True))
    return {
        "ok": True,
        "healthy": bool(report.get("ok", False)),
        "checks": report.get("checks", {}),
    }


def run_paths(payload: dict) -> dict:
    return _capture_json(cmd_paths, SimpleNamespace(
        note_id=_text(payload.get("note_id") or "all", "笔记ID", 128),
        audit_limit=_integer(
            payload.get("audit_limit"), "审计数量", 20, 0, 100
        ),
    ))


def run_protocol(payload: dict) -> dict:
    mode = _text(payload.get("mode") or "summary", "协议模式", 20)
    command = _text(payload.get("command"), "命令名称", 40)
    if mode not in {"summary", "full", "command", "tests"}:
        raise WebServiceError("不支持的协议模式")
    if mode == "command" and not command:
        raise WebServiceError("请选择要查看的命令")
    return _capture_json(cmd_ai_help, SimpleNamespace(
        summary=mode == "summary",
        command_name=command if mode == "command" else None,
        tests=mode == "tests",
    ))


def run_analysis(payload: dict) -> dict:
    note_id = _text(payload.get("note_id"), "笔记ID", 128, required=True)
    result = _capture_json(cmd_analyze, SimpleNamespace(
        note_id=note_id,
        xsec_token="",
        note_title=_text(payload.get("note_title"), "笔记标题", 300),
        refresh=bool(payload.get("refresh", False)),
        with_subs=bool(payload.get("with_subs", False)),
        json=True,
        details=True,
    ))
    if result.get("data"):
        result["summary"] = compact_analysis(result["data"])
    return result


def skipped_records(search: str = "", page: int = 1,
                    page_size: int = 15) -> dict:
    search_text = _text(search, "搜索词", 200)
    search_key = " ".join(search_text.split()).casefold()
    page = _integer(page, "页码", 1, 1, 1000000)
    page_size = _integer(page_size, "每页数量", 15, 5, 100)
    skipped = XHSClient.load_skipped(force_reload=True)
    matched = []
    for comment_id, item in skipped.items():
        searchable = " ".join("\n".join(
            str(item.get(field, "")) for field in (
                "skipped_at", "reason", "nickname", "content", "note_id",
                "error_type", "error_code", "error_message", "legacy_reason",
            )
        ).split()).casefold()
        if search_key and search_key not in searchable:
            continue
        matched.append((comment_id, item))
    matched.sort(
        key=lambda entry: (
            str(entry[1].get("skipped_at", "")), str(entry[0])
        ),
        reverse=True,
    )
    total_count = len(matched)
    total_pages = max(1, math.ceil(total_count / page_size))
    page = min(page, total_pages)
    start = (page - 1) * page_size
    records = []
    for offset, (comment_id, item) in enumerate(
        matched[start:start + page_size], start=start + 1
    ):
        records.append({
            "index": offset,
            "record_id": comment_id,
            "time": item.get("skipped_at", ""),
            "reason": item.get("reason", ""),
            "nickname": item.get("nickname", ""),
            "content": item.get("content", ""),
            "note_id": item.get("note_id", ""),
        })
    return {
        "ok": True,
        "records": records,
        "count": len(records),
        "total_count": total_count,
        "search": search_text,
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "has_previous": page > 1,
            "has_next": page < total_pages,
            "first_index": start + 1 if records else 0,
            "last_index": start + len(records),
        },
    }


def _find_archived_comment(note_id: str, comment_id: str) -> dict:
    """从权威评论归档读取网页动作使用的原始评论，不信任浏览器回传正文。"""
    archive = read_json_state(COMMENTS_FILE, default={}) or {}
    for group in archive.get("groups", []):
        if not isinstance(group, dict):
            continue
        if str(group.get("note_id", "") or "") != note_id:
            continue
        for comment in group.get("comments", []):
            if (
                isinstance(comment, dict)
                and str(comment.get("comment_id", "") or "") == comment_id
            ):
                return {
                    **comment,
                    "note_id": note_id,
                    "note_title": group.get("note_title", "") or "无标题",
                }
    raise WebServiceError(
        "本地评论归档中找不到这条评论，请刷新评论页后重试",
        error_type="comment_archive_missing",
    )


def _draft_item(drafts: dict, comment_id: str) -> dict:
    if not isinstance(drafts, dict):
        return {}
    return next((
        item for item in drafts.get("drafts", [])
        if isinstance(item, dict)
        and str(item.get("comment_id", "") or "") == comment_id
    ), {})


def reply_draft(note_id: str, comment_id: str) -> dict:
    """返回单条评论的可编辑草稿；不访问平台、不发送。"""
    note_id = _text(note_id, "笔记ID", 128, required=True)
    comment_id = _text(
        comment_id, "评论定位信息", 256, required=True
    )
    comment = _find_archived_comment(note_id, comment_id)
    paths = workflow_paths(note_id)
    drafts = {}
    if json_state_exists(paths["drafts"]):
        drafts = read_workflow_state(
            paths["drafts"], default={}, role="program_state"
        ) or {}
    item = _draft_item(drafts, comment_id)
    reply = str(item.get("reply", "") or "")
    source = "existing_draft" if reply else ""
    if not reply and json_state_exists(paths["reply_map"]):
        try:
            reply_map = read_workflow_state(
                paths["reply_map"], default={}, role="ai_input"
            ) or {}
            mapped = reply_map.get(comment_id, {})
            if isinstance(mapped, dict):
                reply = str(mapped.get("reply", "") or "")
            elif isinstance(mapped, str):
                reply = mapped
            if reply:
                source = "reply_map"
        except (OSError, json.JSONDecodeError, TypeError):
            reply = ""
    if not reply:
        reply = str((GENERIC_REPLIES or ["感谢你的评论！"])[0])
        source = "generic"

    skipped = XHSClient.load_skipped(force_reload=True)
    send_status = str(item.get("send_status", "") or "")
    blocked_reason = ""
    if comment_id in skipped:
        blocked_reason = "该评论已在回复排除列表中"
    elif send_status == "sent":
        blocked_reason = "该评论已经发送成功"
    elif send_status == "failed":
        blocked_reason = "该评论发送失败且处于终态，重试需要另行授权"
    elif send_status == "archived":
        blocked_reason = "该评论已经归档"
    elif send_status == "sending":
        blocked_reason = "该评论上次发送结果不确定，禁止自动重发"
    return {
        "ok": True,
        "note_title": comment.get("note_title", "无标题"),
        "nickname": comment.get("nickname", "?"),
        "content": comment.get("content", ""),
        "reply": reply,
        "draft_source": source,
        "send_status": send_status,
        "can_send": not blocked_reason,
        "blocked_reason": blocked_reason,
        "max_reply_length": 1000,
    }


def _web_send_audit(paths: dict, note_id: str, command_id: str,
                    workflow_id: str, phase: str, payload: dict) -> None:
    append_workflow_audit(paths["audit"], note_id, {
        "event_id": new_batch_id(),
        "workflow_id": workflow_id,
        "command_id": command_id,
        "timestamp": datetime.datetime.now().astimezone().isoformat(
            timespec="seconds"
        ),
        "action": "web-send",
        "phase": phase,
        **payload,
    }, current_workflow_id=workflow_id)


def _persist_online_exclusion(paths: dict, existing: dict, comment: dict,
                              reason: str) -> None:
    item = {
        "comment_id": comment["comment_id"],
        "nickname": comment.get("nickname", "?"),
        "content": comment.get("content", ""),
        "reply": "",
        "action": "send",
        "send_status": "archived",
        "archive_reason": reason,
    }
    state = merge_draft_history(existing, {
        "note_id": comment["note_id"],
        "note_title": comment.get("note_title", "无标题"),
        "generated_at": datetime.datetime.now().astimezone().isoformat(
            timespec="seconds"
        ),
        "drafts": [item],
        "active_comment_ids": [],
        "workflow_revision": int(existing.get("workflow_revision", 0) or 0),
    })
    write_json(state, paths["drafts"])


def _send_reply_draft_locked(note_id: str, comment_id: str,
                             reply_text: str, comment: dict) -> dict:
    paths = workflow_paths(note_id)
    existing = {}
    if json_state_exists(paths["drafts"]):
        existing = read_workflow_state(
            paths["drafts"], default={}, role="program_state"
        ) or {}
    old_item = _draft_item(existing, comment_id)
    old_status = str(old_item.get("send_status", "") or "")
    if old_status in NON_RESEND_STATUSES:
        labels = {
            "sent": "该评论已经发送成功",
            "failed": "该评论发送失败且处于终态，重试需要另行授权",
            "archived": "该评论已经归档",
            "sending": "该评论发送结果不确定，禁止自动重发",
        }
        raise WebServiceError(
            labels.get(old_status, "该评论当前不能再次发送"),
            error_type=(
                "uncertain_send_state"
                if old_status == "sending" else "terminal_reply_state"
            ),
        )
    if comment_id in XHSClient.load_skipped(force_reload=True):
        raise WebServiceError(
            "该评论已在回复排除列表中，不能发送",
            error_type="comment_excluded",
        )

    candidate = {
        "comment_id": comment_id,
        "nickname": comment.get("nickname", "?"),
        "content": comment.get("content", ""),
        "likes": 0,
        "sub_count": 0,
    }
    if comment.get("target_comment_id"):
        candidate["target_comment_id"] = comment["target_comment_id"]
    try:
        eligible, excluded = call_for_output(
            CommentScanner().verify_candidates_online,
            note_id,
            [candidate],
            "",
            quiet=True,
        )
    except Exception as error:
        detail = _workflow_error(
            "web-send", error, prefix="发送前在线复核失败", paths=paths
        )
        raise WebServiceError(
            detail.get("error", str(error)),
            error_type=detail.get("error_type", "online_verification_failed"),
            details={
                key: detail[key] for key in (
                    "automatic_retry", "requires_user_action", "next"
                ) if key in detail
            },
        ) from None
    if not eligible:
        reason = next((
            item.get("reason", "online_excluded") for item in excluded
            if item.get("comment_id") == comment_id
        ), "online_excluded")
        _persist_online_exclusion(paths, existing, comment, reason)
        messages = {
            "online_replied": "平台显示该评论已经回复，已停止发送",
            "online_missing": "平台已找不到该评论，已停止发送",
        }
        raise WebServiceError(
            messages.get(reason, "该评论未通过发送前在线核验"),
            error_type=reason,
        )

    revision = int(existing.get("workflow_revision", 0) or 0) + 1
    batch_id = new_batch_id()
    item = {
        "comment_id": comment_id,
        "nickname": comment.get("nickname", "?"),
        "content": comment.get("content", ""),
        "reply": reply_text,
        "action": "send",
        "draft_source": "web_manual_edit",
    }
    state = merge_draft_history(existing, {
        "note_id": note_id,
        "note_title": comment.get("note_title", "无标题"),
        "generated_at": datetime.datetime.now().astimezone().isoformat(
            timespec="seconds"
        ),
        "drafts": [item],
        "active_comment_ids": [comment_id],
        "workflow_revision": revision,
    })
    active_item = _draft_item(state, comment_id)
    content_hash = preview_hash([active_item])
    state["active_batch"] = {
        "batch_id": batch_id,
        "revision": revision,
        "preview_hash": content_hash,
        "status": "sending",
        "source": "web_manual_edit",
        "created_at": datetime.datetime.now().astimezone().isoformat(
            timespec="seconds"
        ),
        "confirmed_at": datetime.datetime.now().astimezone().isoformat(
            timespec="seconds"
        ),
    }
    attempt = _record_send_attempt(
        state, batch_id, content_hash, "started"
    )
    write_json(state, paths["drafts"])
    try:
        stats = call_for_output(
            Replier().send_drafts,
            state,
            state_file=paths["drafts"],
            quiet=True,
        )
    except Exception as error:
        state["active_batch"]["status"] = "paused"
        state["active_batch"]["pause_reason"] = "uncertain_send_state"
        write_json(state, paths["drafts"])
        raise WebServiceError(
            f"发送过程异常，平台结果可能不确定：{error}",
            error_type="uncertain_send_state",
        ) from None

    sent_item = _draft_item(state, comment_id)
    sent = int(stats.get("success", 0) or 0)
    failed = int(stats.get("fail", 0) or 0)
    if stats.get("stopped"):
        state["active_batch"]["status"] = "paused"
        state["active_batch"]["pause_reason"] = stats.get(
            "stop_reason", ""
        )
    else:
        state["active_batch"]["status"] = (
            "completed" if sent and not failed else "completed_with_failures"
        )
        state["active_batch"]["completed_at"] = (
            datetime.datetime.now().astimezone().isoformat(
                timespec="seconds"
            )
        )
    _record_send_attempt(
        state,
        batch_id,
        content_hash,
        "completed" if sent and not failed else "failed",
        error_type=str(sent_item.get("error_type", "") or ""),
        result={"sent": sent, "failed": failed},
    )
    write_json(state, paths["drafts"])
    if not sent or failed:
        return {
            "ok": False,
            "error": sent_item.get("last_error", "回复发送失败"),
            "error_type": sent_item.get("error_type", "unknown_error"),
            "sent": sent,
            "failed": failed,
            "attempt_id": attempt["attempt_id"],
        }
    return {
        "ok": True,
        "message": "回复已发送",
        "sent": sent,
        "failed": failed,
        "send_status": sent_item.get("send_status", "sent"),
        "attempt_id": attempt["attempt_id"],
    }


def send_reply_draft(payload: dict) -> dict:
    """用户在本机网页点击发送后，在线核验并发送当前可编辑草稿。"""
    if not payload.get("confirmed"):
        raise WebServiceError(
            "发送前必须由用户点击确认",
            error_type="confirmation_required",
        )
    note_id = _text(payload.get("note_id"), "笔记ID", 128, required=True)
    comment_id = _text(
        payload.get("comment_id"), "评论定位信息", 256, required=True
    )
    reply_text = _text(
        payload.get("reply"), "回复草稿", 1000, required=True
    )
    comment = _find_archived_comment(note_id, comment_id)
    paths = workflow_paths(note_id)
    command_id = new_batch_id()
    try:
        workflow_id = (
            current_workflow_audit_id(paths["audit"]) or new_batch_id()
        )
    except Exception as error:
        raise WebServiceError(
            f"无法读取发送审计，已停止执行：{error}",
            error_type="audit_unavailable",
        ) from None
    try:
        _web_send_audit(
            paths, note_id, command_id, workflow_id, "started",
            {"inputs": {"confirmed": True, "reply_length": len(reply_text)}},
        )
    except Exception as error:
        raise WebServiceError(
            f"无法写入发送审计，已停止执行：{error}",
            error_type="audit_unavailable",
        ) from None

    try:
        with workflow_lock(note_id, timeout=5.0):
            result = _send_reply_draft_locked(
                note_id, comment_id, reply_text, comment
            )
    except Exception as error:
        try:
            _web_send_audit(
                paths, note_id, command_id, workflow_id, "failed",
                {"error": str(error), "error_type": getattr(
                    error, "error_type", "web_send_failed"
                )},
            )
        except Exception:
            pass
        raise

    try:
        _web_send_audit(
            paths,
            note_id,
            command_id,
            workflow_id,
            "completed" if result.get("ok") else "failed",
            {"result": {
                "sent": result.get("sent", 0),
                "failed": result.get("failed", 0),
                "error_type": result.get("error_type", ""),
            }},
        )
        result["audit"] = {"recorded": True, "path": paths["audit"]}
    except Exception as error:
        result["audit"] = {
            "recorded": False,
            "path": paths["audit"],
            "error": str(error),
        }
    return result


def update_skipped(payload: dict) -> dict:
    action = _text(payload.get("action"), "排除列表动作", 20, required=True)
    if action == "ignore":
        comment_id = _text(
            payload.get("comment_id"), "评论定位信息", 256, required=True
        )
        note_id = _text(payload.get("note_id"), "笔记ID", 128, required=True)
        skipped = XHSClient.load_skipped(force_reload=True)
        if comment_id in skipped:
            item = skipped[comment_id]
            return {
                "ok": True,
                "ignored": True,
                "already_ignored": True,
                "reason": item.get("reason", "已排除"),
            }

        archived_comment = _find_archived_comment(note_id, comment_id)
        XHSClient.add_skipped(
            comment_id,
            nickname=str(archived_comment.get("nickname", "") or ""),
            content=str(archived_comment.get("content", "") or ""),
            reason="人工忽略",
            note_id=note_id,
        )
        return {
            "ok": True,
            "ignored": True,
            "already_ignored": False,
            "reason": "人工忽略",
        }
    if action == "remove":
        record_id = _text(
            payload.get("record_id"), "排除记录", 256, required=True
        )
        removed = XHSClient.remove_skipped(record_id)
        return {"ok": True, "removed": removed}
    if action == "clear":
        if not payload.get("confirmed"):
            raise WebServiceError("清空排除列表前必须明确确认")
        count = len(XHSClient.load_skipped(force_reload=True))
        XHSClient.save_skipped({})
        return {"ok": True, "cleared": count}
    raise WebServiceError("不支持的排除列表动作")


def list_all_drafts() -> dict:
    """列出所有笔记中当前未终态的回复草稿（发送状态为待发送或发送中）。"""
    all_drafts = []
    if not os.path.isdir(WORK_DIR):
        return {"drafts": [], "total_count": 0}

    skipped = XHSClient.load_skipped(force_reload=True)
    for note_dir in sorted(os.listdir(WORK_DIR)):
        note_path = os.path.join(WORK_DIR, note_dir)
        if not os.path.isdir(note_path):
            continue
        drafts_path = os.path.join(note_path, "drafts.json")
        if not json_state_exists(drafts_path):
            continue
        try:
            state = read_workflow_state(
                drafts_path, default={}, role="program_state"
            ) or {}
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(state, dict):
            continue

        note_id = str(state.get("note_id", note_dir) or note_dir)
        note_title = str(state.get("note_title", "") or "")
        active_ids = set(
            str(c) for c in (state.get("active_comment_ids", []) or [])
        )

        for item in state.get("drafts", []):
            if not isinstance(item, dict):
                continue
            comment_id = str(item.get("comment_id", "") or "")
            if not comment_id:
                continue
            send_status = str(item.get("send_status", "") or "")
            action = str(item.get("action", "") or "")

            # 只展示未终态且action为send的草稿
            if send_status in ("sent", "failed", "archived"):
                continue
            if action not in ("send", ""):
                continue

            reply = str(item.get("reply", "") or "")
            review = item.get("review")
            if isinstance(review, dict):
                review_data = {
                    "logic": review.get("logic"),
                    "fact_check": review.get("fact_check"),
                    "boast_check": review.get("boast_check"),
                }
            else:
                review_data = None

            all_drafts.append({
                "note_id": note_id,
                "note_title": note_title or "无标题",
                "comment_id": comment_id,
                "nickname": str(item.get("nickname", "?") or "?"),
                "content": str(item.get("content", "") or ""),
                "reply": reply,
                "send_status": send_status,
                "is_active": comment_id in active_ids,
                "in_skipped": comment_id in skipped,
                "review": review_data,
            })

    # 按笔记+评论排序
    all_drafts.sort(key=lambda d: (d["note_title"], d["nickname"]))

    return {
        "drafts": all_drafts,
        "total_count": len(all_drafts),
    }


def update_draft_reply(payload: dict) -> dict:
    """更新草稿中的回复正文，不改变发送状态或批次绑定。"""
    note_id = _text(payload.get("note_id"), "笔记ID", 128, required=True)
    comment_id = _text(
        payload.get("comment_id"), "评论定位信息", 256, required=True
    )
    reply_text = _text(
        payload.get("reply"), "回复草稿", 1000, required=True
    )

    paths = workflow_paths(note_id)
    if not json_state_exists(paths["drafts"]):
        raise WebServiceError(
            "该笔记没有草稿文件", error_type="draft_not_found"
        )

    state = read_workflow_state(
        paths["drafts"], default={}, role="program_state"
    ) or {}
    if not isinstance(state, dict):
        raise WebServiceError("草稿文件格式错误", error_type="draft_corrupt")

    found = False
    for item in state.get("drafts", []):
        if (
            isinstance(item, dict)
            and str(item.get("comment_id", "") or "") == comment_id
        ):
            item["reply"] = reply_text
            item["draft_source"] = "web_manual_edit"
            found = True
            break

    if not found:
        raise WebServiceError(
            "草稿中找不到该评论", error_type="comment_not_in_draft"
        )

    write_json(state, paths["drafts"])
    return {"ok": True, "updated": True}


def send_all_drafts(payload: dict) -> dict:
    """批量发送所有可见草稿，每条复用现有安全流程。"""
    if not payload.get("confirmed"):
        raise WebServiceError(
            "批量发送前必须由用户点击确认",
            error_type="confirmation_required",
        )

    all_drafts = list_all_drafts()
    drafts = all_drafts.get("drafts", [])
    if not drafts:
        return {"ok": True, "message": "没有可发送的草稿", "sent": 0, "failed": 0}

    results = []
    total_sent = 0
    total_failed = 0

    for draft in drafts:
        if draft.get("in_skipped"):
            results.append({
                "comment_id": draft["comment_id"],
                "nickname": draft["nickname"],
                "ok": False,
                "error": "该评论已在回复排除列表中",
            })
            total_failed += 1
            continue

        try:
            result = send_reply_draft({
                "note_id": draft["note_id"],
                "comment_id": draft["comment_id"],
                "reply": draft["reply"],
                "confirmed": True,
            })
            results.append({
                "comment_id": draft["comment_id"],
                "nickname": draft["nickname"],
                "ok": result.get("ok", False),
                "message": result.get("message", "") or result.get("error", ""),
            })
            if result.get("ok"):
                total_sent += 1
            else:
                total_failed += 1
        except WebServiceError as error:
            results.append({
                "comment_id": draft["comment_id"],
                "nickname": draft["nickname"],
                "ok": False,
                "error_type": error.error_type,
                "error": str(error),
            })
            total_failed += 1
            # 不确定发送状态时停止批量操作
            if error.error_type == "uncertain_send_state":
                break
        except Exception as error:
            results.append({
                "comment_id": draft["comment_id"],
                "nickname": draft["nickname"],
                "ok": False,
                "error": str(error),
            })
            total_failed += 1

    return {
        "ok": total_sent > 0 or total_failed == 0,
        "sent": total_sent,
        "failed": total_failed,
        "total": len(drafts),
        "results": results,
    }
