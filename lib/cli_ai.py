"""面向 AI 的低往返、紧凑 JSON 回复工作流。"""

import datetime
import json
import os

from .cli_support import (
    call_for_output,
    compact_comment,
    compact_scan_result,
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


DRAFT_COLUMNS = ["序号", "用户", "原评论", "拟回复", "操作"]
RESULT_COLUMNS = ["序号", "用户", "回复摘要", "结果", "失败原因"]
INLINE_ROW_LIMIT = 20


def _workflow_error(action, error, prefix="", paths=None):
    """把工作流异常转换为 AI 可直接决策的结构化错误。"""
    detail = str(error)
    message = f"{prefix}: {detail}" if prefix else detail
    payload = {
        "ok": False,
        "action": action,
        "error": message,
    }
    normalized = message.casefold()
    verification_markers = (
        "verification_required",
        "verification required",
        "captcha",
        "验证码",
    )
    if any(marker in normalized for marker in verification_markers):
        payload.update({
            "error_type": "verification_required",
            "automatic_retry": False,
            "requires_user_action": True,
        })
        if "type=unknown" in normalized and "uuid=unknown" in normalized:
            payload.update({
                "verification_context": "api_risk_control",
                "next": (
                    "停止自动重试；这是没有可见挑战信息的API接口风控，"
                    "浏览器页面正常也可能发生。先重新导入Firefox Cookie，"
                    "再运行当前action；若仍失败则等待风控解除"
                ),
            })
        else:
            payload["next"] = (
                "停止自动重试；请用户先在 Firefox 中打开小红书并完成"
                "验证码，完成后重新运行当前 action"
            )
    if paths is not None:
        payload["paths"] = paths
    return payload


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
        and item.get("send_status") not in NON_RESEND_STATUSES
    ]


def _inflight(drafts):
    return [
        item for item in _active_items(drafts)
        if item.get("action") == "send"
        and item.get("send_status") == "sending"
    ]


def _preview(items):
    return [{
        "index": index,
        "nickname": item.get("nickname", "?"),
        "content": item.get("content", ""),
        "reply": item.get("reply", ""),
        "action": item.get("action", "send"),
    } for index, item in enumerate(items, start=1)]


def _result_rows(items):
    """把活动批次转换为稳定的发送结果，不把已发送项目误报为跳过。"""
    return [{
        "index": index,
        "nickname": item.get("nickname", "?"),
        "reply": item.get("reply", "")[:60],
        "status": (
            item.get("send_status")
            or ("skipped" if item.get("action") == "skip" else "pending")
        ),
        "error": (
            item.get("last_error", "")
            or item.get("archive_reason", "")
        ),
        **({"error_type": item.get("error_type", "")}
           if item.get("send_status") == "failed" else {}),
    } for index, item in enumerate(items, start=1)]


def _inline_rows(rows):
    """限制机器接口内联明细；完整数据始终保留在状态文件。"""
    return rows[:INLINE_ROW_LIMIT]


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


def _reply_map_validation_error(errors, error_type="invalid_reply_map_mapping",
                                next_step=None):
    """返回统一、可操作的回复映射语义错误。"""
    return {
        "ok": False,
        "action": "draft",
        "error": "reply_map.json 映射格式错误",
        "error_type": error_type,
        "automatic_retry": False,
        "requires_file_fix": True,
        "details": errors,
        "accepted_formats": [
            {"<comment_id>": "非空回复字符串，等价于send"},
            {
                "<comment_id>": {
                    "reply": "send时非空；skip/archive可为空",
                    "action": "send|skip|archive",
                },
            },
        ],
        "next": (
            next_step
            or "修复 reply_map.json 后重新运行 draft"
        ),
    }


def _duplicate_send_errors(candidates, reply_map):
    """同一用户的相同正文最多允许一条映射为send。"""
    groups = {}
    for candidate in candidates:
        comment_id = candidate.get("comment_id", "")
        entry = reply_map.get(comment_id)
        if entry is None:
            continue  # 缺少映射默认为skip
        action = (
            entry.get("action", "send")
            if isinstance(entry, dict) else "send"
        )
        if action != "send":
            continue
        nickname = " ".join(
            str(candidate.get("nickname", "")).split()
        ).casefold()
        content = " ".join(
            str(candidate.get("content", "")).split()
        ).casefold()
        if not nickname or not content:
            continue
        groups.setdefault((nickname, content), []).append(comment_id)

    errors = []
    for (nickname, content), comment_ids in groups.items():
        unique_ids = list(dict.fromkeys(comment_ids))
        if len(unique_ids) < 2:
            continue
        errors.append(
            "同一用户的相同评论只能保留一条send："
            f"用户={nickname}，评论={content}，"
            f"comment_ids={','.join(unique_ids)}"
        )
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
    # 在昂贵的在线复核前先检查映射文件语法，避免格式错误浪费平台请求。
    try:
        reply_map = _load_json(reply_path)
    except (OSError, json.JSONDecodeError) as error:
        print_json({
            "ok": False, "action": "draft",
            "error": f"reply_map.json 不是有效 JSON: {error}",
            "error_type": "invalid_reply_map_json",
            "automatic_retry": False,
            "requires_file_fix": True,
            "next": (
                "先修复 reply_map.json 再重跑 draft；JSON字符串中的英文"
                "半角双引号必须写成 \\\"，也可改用中文引号“”或「」"
            ),
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
        print_json(_reply_map_validation_error(mapping_errors))
        return
    duplicate_errors = _duplicate_send_errors(candidates, reply_map)
    if duplicate_errors:
        print_json(_reply_map_validation_error(
            duplicate_errors,
            error_type="duplicate_send_mapping",
            next_step=(
                "同一用户的相同评论只保留一条send，其余改为skip，"
                "然后重新运行draft"
            ),
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
        print_json(_workflow_error(
            "draft", error, prefix="在线复核失败", paths=paths
        ))
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
    if os.path.exists(paths["drafts"]):
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
        "batch_id": batch_id,
        "revision": revision,
        "preview_hash": current_preview_hash,
        "columns": DRAFT_COLUMNS,
        "preview": _inline_rows(preview_rows),
        "preview_total": len(preview_rows),
        "preview_returned": min(len(preview_rows), INLINE_ROW_LIMIT),
        "preview_truncated": len(preview_rows) > INLINE_ROW_LIMIT,
        "preview_source": paths["drafts"],
        "next": (
            "向用户展示 preview；明确确认后运行 "
            "ai-reply --action send --confirmed "
            f"--batch-id {batch_id} --preview-hash {current_preview_hash}"
            if pending or archives else "本批全部为本次跳过，无需发送"
        ),
        "paths": paths,
    })


def _send(args, paths):
    """处理已有活动批次；不证明本轮刚执行过prepare或draft。"""
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
        print_json({
            "ok": False,
            "action": "send",
            "error": "确认信息与当前活动批次不一致，草稿可能已被其他AI更新",
            "error_type": "stale_preview",
            "automatic_retry": False,
            "next": "重新运行draft、展示新preview并取得用户确认",
        })
        return
    if active_batch.get("status") == "superseded":
        print_json({
            "ok": False,
            "action": "send",
            "error": "该批次已被更新批次替代，禁止发送旧预览",
            "error_type": "stale_preview",
            "automatic_retry": False,
        })
        return
    active_items = _active_items(drafts)
    actual_preview_hash = preview_hash(active_items)
    if actual_preview_hash != expected_preview_hash:
        print_json({
            "ok": False,
            "action": "send",
            "error": "草稿内容在用户确认后发生变化，已停止发送",
            "error_type": "preview_content_changed",
            "automatic_retry": False,
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
        "columns": RESULT_COLUMNS,
        "results": _inline_rows(result_rows),
        "results_total": len(result_rows),
        "results_returned": min(len(result_rows), INLINE_ROW_LIMIT),
        "results_truncated": len(result_rows) > INLINE_ROW_LIMIT,
        "results_source": paths["drafts"],
        "state_file": paths["drafts"],
    })


def cmd_ai_reply(args):
    """执行 prepare、draft 或 send，并且只输出一个 JSON 文档。"""
    paths = workflow_paths(args.note_id)
    try:
        with workflow_lock(
            args.note_id, timeout=3.0, directory=paths["directory"]
        ):
            if args.action == "prepare":
                _prepare(args, paths)
            elif args.action == "draft":
                _draft(args, paths)
            else:
                _send(args, paths)
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
