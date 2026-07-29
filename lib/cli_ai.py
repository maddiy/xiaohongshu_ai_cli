"""面向 AI 的低往返、紧凑 JSON 回复工作流。"""

import json
import os

from .cli_support import (
    call_for_output,
    compact_comment,
    compact_scan_result,
    filter_scan_local_state,
    merge_draft_history,
    print_json,
    TERMINAL_SEND_STATUSES,
    workflow_paths,
    write_json,
)
from .replier import Replier
from .scanner import CommentScanner


DRAFT_COLUMNS = ["序号", "用户", "原评论", "拟回复", "操作"]
RESULT_COLUMNS = ["序号", "用户", "回复摘要", "结果", "失败原因"]


def _load_json(path):
    with open(path, encoding="utf-8") as file:
        return json.load(file)


def _active_items(drafts):
    active_ids = drafts.get("active_comment_ids")
    active_ids = set(active_ids) if isinstance(active_ids, list) else None
    return [
        item for item in drafts.get("drafts", [])
        if active_ids is None or item.get("comment_id") in active_ids
    ]


def _pending(drafts):
    return [
        item for item in _active_items(drafts)
        if item.get("action") == "send"
        and item.get("send_status") not in TERMINAL_SEND_STATUSES
    ]


def _preview(items):
    return [{
        "index": index,
        "nickname": item.get("nickname", "?"),
        "content": item.get("content", ""),
        "reply": item.get("reply", ""),
        "action": item.get("action", "send"),
    } for index, item in enumerate(items, start=1)]


def _validate_reply_map(reply_map, candidate_ids):
    """校验当前候选使用的映射；允许文件中保留其他批次的旧键。"""
    if not isinstance(reply_map, dict):
        return ["顶层必须是 JSON 对象，以 comment_id 为键"]

    errors = []
    allowed_actions = {"send", "skip", "archive"}
    for comment_id in candidate_ids:
        if comment_id not in reply_map:
            continue
        entry = reply_map[comment_id]
        if isinstance(entry, str):
            if not entry.strip():
                errors.append(f"{comment_id}: 回复内容不能为空")
            continue
        if not isinstance(entry, dict):
            errors.append(f"{comment_id}: 值必须是字符串或对象")
            continue
        action = entry.get("action", "send")
        if action not in allowed_actions:
            errors.append(
                f"{comment_id}: action 必须是 send、skip 或 archive"
            )
            continue
        reply = entry.get("reply", "")
        if action == "send" and (
            not isinstance(reply, str) or not reply.strip()
        ):
            errors.append(f"{comment_id}: action=send 时 reply 不能为空")
    return errors


def _clear_active_batch(drafts_path):
    """prepare或draft开始时停用旧批次；保留历史草稿，仅禁止误发旧批次。"""
    if not os.path.exists(drafts_path):
        return
    try:
        drafts = _load_json(drafts_path)
    except (OSError, json.JSONDecodeError):
        return
    if isinstance(drafts, dict) and drafts.get("active_comment_ids") != []:
        drafts["active_comment_ids"] = []
        write_json(drafts, drafts_path)


def _prepare(args, paths):
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
        else:
            result = call_for_output(
                scanner.scan_via_notifications,
                note_id=args.note_id,
                num_notifications=getattr(args, "limit", 20),
                verify_replied=True,
                verbose=False,
                quiet=True,
            )
            scope = "latest"
    except Exception as error:
        print_json({"ok": False, "action": "prepare", "error": str(error)})
        return
    filter_scan_local_state(result)
    output = compact_scan_result(result)
    write_json(output, paths["scan"])
    if not output.get("reply_status_verified", False):
        print_json({
            "ok": False,
            "action": "prepare",
            "error": output.get("scan_error", "在线回复状态核验失败"),
            "paths": paths,
        })
        return
    candidates = (
        output.get("unreplied_level1", [])
        + output.get("unreplied_subs", [])
    )
    print_json({
        "ok": True,
        "action": "prepare",
        "scope": scope,
        "note_id": args.note_id,
        "candidates": candidates,
        "count": len(candidates),
        "next": (
            "将回复映射写入 paths.reply_map，再运行 "
            "ai-reply --action draft"
            if candidates else "没有可回复评论，停止"
        ),
        "paths": paths,
    })


def _draft(args, paths):
    scan_path = paths["scan"]
    reply_path = os.path.abspath(args.replies or paths["reply_map"])
    # 即使本次草稿失败也停用旧活动批次，防止调用方误把旧预览当作新批发送。
    _clear_active_batch(paths["drafts"])
    if not os.path.exists(scan_path):
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
    if not os.path.exists(reply_path):
        print_json({
            "ok": False, "action": "draft",
            "error": f"回复映射不存在: {reply_path}",
        })
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
        print_json({
            "ok": False, "action": "draft",
            "error": f"在线复核失败: {error}",
        })
        return
    if not candidates:
        print_json({
            "ok": True, "action": "draft", "count": 0,
            "excluded_online": len(excluded),
            "columns": DRAFT_COLUMNS,
            "preview": [], "next": "在线复核后没有可回复评论，停止",
            "paths": paths,
        })
        return
    try:
        reply_map = _load_json(reply_path)
    except (OSError, json.JSONDecodeError) as error:
        print_json({
            "ok": False, "action": "draft",
            "error": f"reply_map.json 不是有效 JSON: {error}",
        })
        return
    candidate_ids = [
        item.get("comment_id") for item in candidates
        if item.get("comment_id")
    ]
    mapping_errors = _validate_reply_map(reply_map, candidate_ids)
    if mapping_errors:
        print_json({
            "ok": False,
            "action": "draft",
            "error": "reply_map.json 格式错误",
            "details": mapping_errors,
            "accepted_formats": [
                {"<comment_id>": "非空回复字符串，等价于send"},
                {
                    "<comment_id>": {
                        "reply": "send时非空；skip/archive可为空",
                        "action": "send|skip|archive",
                    },
                },
            ],
        })
        return
    drafts = call_for_output(
        Replier().generate_drafts_from_mapping,
        candidates,
        reply_map,
        note_id=args.note_id,
        note_title=scan.get("note_title", ""),
        quiet=True,
    )
    drafts["active_comment_ids"] = candidate_ids
    if os.path.exists(paths["drafts"]):
        try:
            drafts = merge_draft_history(_load_json(paths["drafts"]), drafts)
        except (OSError, json.JSONDecodeError):
            pass
    write_json(drafts, paths["drafts"])
    active_items = _active_items(drafts)
    pending = _pending(drafts)
    archives = [
        item for item in active_items
        if item.get("action") == "archive"
        and item.get("send_status") != "archived"
    ]
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
        "columns": DRAFT_COLUMNS,
        "preview": _preview(active_items),
        "next": (
            "向用户展示 preview；明确确认后运行 "
            "ai-reply --action send --confirmed"
            if pending or archives else "本批全部为本次跳过，无需发送"
        ),
        "paths": paths,
    })


def _send(args, paths):
    if not args.confirmed:
        print_json({
            "ok": False, "action": "send",
            "error": "缺少 --confirmed；必须先向用户展示草稿并取得明确确认",
        })
        return
    if not os.path.exists(paths["drafts"]):
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
    active_items = _active_items(drafts)
    pending = _pending(drafts)
    archives = [
        item for item in active_items
        if item.get("action") == "archive"
        and item.get("send_status") != "archived"
    ]
    skipped_items = [
        item for item in active_items if item.get("action") == "skip"
    ]
    if not pending and not archives:
        print_json({
            "ok": True, "action": "send", "sent": 0,
            "failed": 0, "skipped": len(skipped_items),
            "columns": RESULT_COLUMNS,
            "results": [{
                "index": index,
                "nickname": item.get("nickname", "?"),
                "reply": item.get("reply", "")[:60],
                "status": "skipped",
                "error": "",
            } for index, item in enumerate(active_items, start=1)],
        })
        return

    # 发送前最后一次在线核验，防止用户预览期间已从其他客户端回复。
    excluded = []
    if pending:
        try:
            eligible, excluded = call_for_output(
                CommentScanner().verify_candidates_online,
                args.note_id,
                [compact_comment(item) for item in pending],
                "",
                quiet=True,
            )
        except Exception as error:
            print_json({
                "ok": False, "action": "send",
                "error": f"发送前在线复核失败: {error}",
            })
            return
    else:
        eligible = []
    eligible_ids = {item.get("comment_id") for item in eligible}
    excluded_by_id = {
        item.get("comment_id"): item.get("reason") for item in excluded
    }
    for item in pending:
        comment_id = item.get("comment_id")
        if comment_id not in eligible_ids:
            item["send_status"] = "archived"
            item["archive_reason"] = excluded_by_id.get(
                comment_id, "online_excluded"
            )
    write_json(drafts, paths["drafts"])

    stats = call_for_output(
        Replier().send_drafts,
        drafts,
        state_file=paths["drafts"],
        quiet=True,
    )
    results = [{
        "index": index,
        "nickname": item.get("nickname", "?"),
        "reply": item.get("reply", "")[:60],
        "status": item.get("send_status", "skipped"),
        "error": (
            item.get("last_error", "")
            or item.get("archive_reason", "")
        ),
        **({"error_type": item.get("error_type", "")}
           if item.get("send_status") == "failed" else {}),
    } for index, item in enumerate(active_items, start=1)]
    print_json({
        "ok": stats.get("fail", 0) == 0,
        "action": "send",
        "sent": stats.get("success", 0),
        "failed": stats.get("fail", 0),
        "skipped": (
            stats.get("skip", 0)
            + len(excluded)
            + len(skipped_items)
        ),
        "columns": RESULT_COLUMNS,
        "results": results,
        "state_file": paths["drafts"],
    })


def cmd_ai_reply(args):
    """执行 prepare、draft 或 send，并且只输出一个 JSON 文档。"""
    paths = workflow_paths(args.note_id)
    if args.action == "prepare":
        _prepare(args, paths)
    elif args.action == "draft":
        _draft(args, paths)
    else:
        _send(args, paths)
