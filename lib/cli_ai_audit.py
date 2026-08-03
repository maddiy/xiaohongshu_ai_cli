"""AI回复工作流的脱敏审计与发送尝试记录工具。"""

import datetime
import os

from .state_io import json_state_exists
from .cli_support import new_batch_id
from .cli_ai_support import AUDIT_RESULT_FIELDS, SEND_ATTEMPT_LIMIT


def _audit_timestamp():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _audit_text(value):
    """审计保留可诊断文本，但发现凭据字段名时不落盘原始字符串。"""
    text = str(value)
    normalized = text.casefold()
    if any(marker in normalized for marker in (
        "xsec_token", "cookie", "authorization", "set-cookie",
    )):
        return "[包含敏感凭据字段，审计已隐藏原文]"
    return text[:4000]


def _audit_value(value):
    if isinstance(value, str):
        return _audit_text(value)
    if isinstance(value, dict):
        return {
            str(key): _audit_value(item)
            for key, item in value.items()
            if not any(marker in str(key).casefold() for marker in (
                "xsec", "cookie", "authorization",
            ))
        }
    if isinstance(value, list):
        return [_audit_value(item) for item in value[:50]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _audit_text(value)


def _audit_inputs(args, paths):
    """只记录复现命令所需参数，不记录评论、回复或认证数据。"""
    action = getattr(args, "action", "")
    if action == "prepare":
        return {
            "full_scan": bool(getattr(args, "full_scan", False)),
            "limit": int(getattr(args, "limit", 20) or 20),
        }
    if action == "draft":
        reply_path = os.path.abspath(
            getattr(args, "replies", None) or paths["reply_map"]
        )
        evidence = {
            "path": reply_path,
            "exists": json_state_exists(reply_path),
        }
        if evidence["exists"]:
            try:
                stat = os.stat(reply_path)
                evidence.update({
                    "size": stat.st_size,
                    "modified_at": datetime.datetime.fromtimestamp(
                        stat.st_mtime
                    ).astimezone().isoformat(timespec="seconds"),
                })
            except OSError:
                pass
        return {"reply_map": evidence}
    return {
        "confirmed": bool(getattr(args, "confirmed", False)),
        "batch_id": str(getattr(args, "batch_id", "") or ""),
        "preview_hash": str(getattr(args, "preview_hash", "") or ""),
    }


def _audit_result(payload):
    """从命令响应提取有证明力、无评论正文的结果摘要。"""
    return {
        key: _audit_value(payload[key])
        for key in AUDIT_RESULT_FIELDS if key in payload
    }

def _record_send_attempt(
    drafts, supplied_batch_id, supplied_preview_hash, outcome,
    error_type="", mismatch=None, result=None,
):
    """在drafts.json内补充有界发送尝试，便于与命令审计交叉核对。"""
    attempts = drafts.get("send_attempts", [])
    if not isinstance(attempts, list):
        attempts = []
    attempt = {
        "attempt_id": new_batch_id(),
        "attempted_at": datetime.datetime.now().astimezone().isoformat(
            timespec="seconds"
        ),
        "outcome": outcome,
        "supplied_batch_id": supplied_batch_id,
        "supplied_preview_hash": supplied_preview_hash,
    }
    if error_type:
        attempt["error_type"] = error_type
    if mismatch is not None:
        attempt["mismatch"] = mismatch
    if result is not None:
        attempt["result"] = result
    attempts.append(attempt)
    drafts["send_attempts"] = attempts[-SEND_ATTEMPT_LIMIT:]
    return attempt

