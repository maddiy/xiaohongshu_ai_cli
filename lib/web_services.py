"""本地Web控制台的结构化业务服务；复用CLI权威实现和安全状态机。"""

import contextlib
import io
import json
import math
from types import SimpleNamespace

from config import COMMENTS_FILE
from .cli_admin import cmd_ai_help, cmd_analyze, cmd_doctor, cmd_paths
from .cli_support import compact_analysis
from .state_io import read_json_state
from .xhs_client import XHSClient


class WebServiceError(ValueError):
    """适合直接返回给本地网页的参数或业务错误。"""


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

        archive = read_json_state(COMMENTS_FILE, default={}) or {}
        archived_comment = None
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
                    archived_comment = comment
                    break
            if archived_comment is not None:
                break
        if archived_comment is None:
            raise WebServiceError(
                "本地评论归档中找不到这条评论，请刷新评论页后重试"
            )
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
