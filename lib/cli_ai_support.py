"""AI回复工作流的常量、映射校验和展示转换工具。"""

import json
import os
import re

from .state_io import read_workflow_state
from .cli_support import NON_RESEND_STATUSES, write_json
from .reply_schema import (
    BOAST_VERDICTS,
    FACT_VERDICTS,
    LOGIC_VERDICTS,
    REPLY_ACTIONS,
)


DRAFT_COLUMNS = ["序号", "用户", "原评论", "拟回复", "操作"]
REVIEW_COLUMNS = ["序号", "用户", "逻辑分析", "事实核查", "吹牛判定"]
REVIEW_COLUMN_FIELDS = {
    "序号": "index",
    "用户": "nickname",
    "逻辑分析": "logic_analysis",
    "事实核查": "fact_check",
    "吹牛判定": "boast_check",
}
RESULT_COLUMNS = ["序号", "用户", "回复摘要", "结果", "失败原因"]
INLINE_ROW_LIMIT = 20
SEND_ATTEMPT_LIMIT = 100
AUDIT_RESULT_FIELDS = (
    "ok", "action", "error", "error_type", "automatic_retry",
    "requires_user_action", "requires_user_confirmation",
    "requires_file_fix", "error_location", "quote_policy", "scope",
    "scan_method", "verification_mode", "count", "deferred_count",
    "excluded_online", "send_count", "skip_count", "archive_count",
    "batch_id", "revision", "preview_hash", "sent", "failed", "skipped",
    "paused", "pause_reason", "remaining", "attempt_id", "mismatch",
    "current_revision", "current_batch_status", "diagnostic", "next",
    "reply_map_repaired", "quote_replacements",
    "changed", "batch_invalidated", "mapped_count", "remaining_count",
    "binding_reused", "new_confirmation_required",
    "candidate_count", "active_count", "failed_count", "state_reset",
    "skipped_removed", "mapping_remaining_count", "batch_status",
)

AI_TEXT_VALUE_LINE = re.compile(
    r'^(\s*"(?:reply|reason)"\s*:\s*")(.*)("\s*,?\s*)(\r?\n)?$'
)

REVIEW_VERDICT_LABELS = {
    "logic": {
        "sound": "逻辑成立",
        "partly_sound": "部分成立",
        "weak": "论证薄弱",
        "fallacious": "存在逻辑谬误",
        "non_argument": "非论证表达",
        "unclear": "无法判断",
    },
    "fact_check": {
        "supported": "有依据支持",
        "mixed": "部分支持",
        "contradicted": "与证据矛盾",
        "unverifiable": "无法核实",
        "not_applicable": "无外部事实主张",
    },
    "boast_check": {
        "none": "未发现吹牛",
        "possible": "可能吹牛",
        "likely": "较可能吹牛",
        "unverifiable": "无法判定",
        "not_applicable": "不适用",
    },
}

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


def _load_json(path, role=None):
    return read_workflow_state(path, role=role)


def _unescaped_quote_positions(value):
    positions = []
    for index, char in enumerate(value):
        if char != '"':
            continue
        backslashes = 0
        cursor = index - 1
        while cursor >= 0 and value[cursor] == "\\":
            backslashes += 1
            cursor -= 1
        if backslashes % 2 == 0:
            positions.append(index)
    return positions


def _repair_reply_map_text_quotes(path):
    """只修复独立reply/reason文本行中成对的未转义引号；歧义时不改文件。"""
    with open(path, encoding="utf-8") as file:
        source = file.read()
    replacements = 0
    repaired_lines = []
    for line in source.splitlines(keepends=True):
        match = AI_TEXT_VALUE_LINE.match(line)
        if not match:
            repaired_lines.append(line)
            continue
        prefix, value, suffix, newline = match.groups()
        positions = _unescaped_quote_positions(value)
        if not positions or len(positions) % 2:
            repaired_lines.append(line)
            continue
        position_set = set(positions)
        pair_index = 0
        output = []
        for index, char in enumerate(value):
            if index in position_set:
                output.append("“" if pair_index % 2 == 0 else "”")
                pair_index += 1
                replacements += 1
            else:
                output.append(char)
        repaired_lines.append(
            prefix + "".join(output) + suffix + (newline or "")
        )
    if not replacements:
        return None, 0
    repaired_source = "".join(repaired_lines)
    try:
        repaired = json.loads(repaired_source)
    except json.JSONDecodeError:
        return None, 0
    if not isinstance(repaired, dict):
        return None, 0
    # 通过标准写入器原子替换，保证修复后的文件不再含有半截JSON。
    write_json(repaired, path, indent=2)
    return repaired, replacements

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


def _review_rows(items):
    """输出与草稿序号一一对应的结构化评论审查结果。"""
    def labeled(field, block):
        block = dict(block) if isinstance(block, dict) else {}
        block["label"] = REVIEW_VERDICT_LABELS.get(field, {}).get(
            block.get("verdict", ""), ""
        )
        return block

    rows = []
    for index, item in enumerate(items, start=1):
        review = item.get("review", {})
        rows.append({
            "index": index,
            "nickname": item.get("nickname", "?"),
            "logic_analysis": labeled("logic", review.get("logic", {})),
            "fact_check": labeled(
                "fact_check", review.get("fact_check", {})
            ),
            "boast_check": labeled(
                "boast_check", review.get("boast_check", {})
            ),
        })
    return rows


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


def _validate_review(comment_id, review):
    """校验AI对单条评论的逻辑、事实和吹牛审查记录。review缺失时不报错。"""
    if review is None:
        return []
    if not isinstance(review, dict):
        return [f"{comment_id}: review 必须是对象"]

    errors = []
    specifications = (
        ("logic", LOGIC_VERDICTS),
        ("fact_check", FACT_VERDICTS),
        ("boast_check", BOAST_VERDICTS),
    )
    for field, allowed in specifications:
        block = review.get(field)
        if block is None:
            continue
        if not isinstance(block, dict):
            errors.append(f"{comment_id}: review.{field} 必须是对象")
            continue
        verdict = block.get("verdict", "")
        if not verdict:
            continue
        if verdict not in allowed:
            errors.append(
                f"{comment_id}: review.{field}.verdict 必须是 "
                + "、".join(sorted(allowed))
            )
        reason = block.get("reason", "")
        if verdict and (not isinstance(reason, str) or not reason.strip()):
            errors.append(
                f"{comment_id}: review.{field}.reason 不能为空"
            )

    facts = review.get("fact_check")
    if isinstance(facts, dict):
        sources = facts.get("sources", [])
        if not isinstance(sources, list):
            errors.append(
                f"{comment_id}: review.fact_check.sources 必须是数组"
            )
        else:
            invalid_sources = [
                source for source in sources
                if not isinstance(source, dict)
                or not isinstance(source.get("url"), str)
                or not source["url"].startswith(("https://", "http://"))
            ]
            if invalid_sources:
                errors.append(
                    f"{comment_id}: fact_check来源必须包含有效的http(s) URL"
                )
            if (
                facts.get("verdict")
                in {"supported", "mixed", "contradicted"}
                and not sources
            ):
                errors.append(
                    f"{comment_id}: 事实判定为{facts.get('verdict')}时"
                    "至少需要一个可核对来源"
                )
    return errors


def _validate_reply_map(reply_map, candidate_ids):
    """校验当前候选映射及逐条审查；允许保留其他批次旧键。"""
    if not isinstance(reply_map, dict):
        return ["顶层必须是 JSON 对象，以 comment_id 为键"]

    errors = []
    allowed_actions = set(REPLY_ACTIONS)
    for comment_id in candidate_ids:
        if comment_id not in reply_map:
            errors.append(
                f"{comment_id}: 缺少映射；每条候选都必须有reply和action"
            )
            continue
        entry = reply_map[comment_id]
        if isinstance(entry, str):
            errors.append(
                f"{comment_id}: AI回复流程不接受字符串简写，必须使用"
                "包含reply和action的对象"
            )
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
        # review可选：有则校验，无则跳过
        errors.extend(_validate_review(comment_id, entry.get("review")))
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
            {
                "<comment_id>": {
                    "reply": "send时非空；skip/archive可为空",
                    "action": "send|skip|archive",
                },
            },
            {
                "<comment_id>": {
                    "reply": "send时非空；skip/archive可为空",
                    "action": "send|skip|archive",
                    "review": {
                        "logic": {"verdict": "枚举值", "reason": "依据"},
                        "fact_check": {
                            "verdict": "枚举值",
                            "reason": "依据",
                            "sources": [{"title": "来源名", "url": "https://..."}],
                        },
                        "boast_check": {
                            "verdict": "枚举值", "reason": "依据"
                        },
                    },
                },
            },
        ],
        "next": (
            next_step
            or "修复 reply_map.json 后重新运行 draft"
        ),
    }


def _with_quote_repair(payload, repaired, replacements):
    """在后续成功或失败响应中保留本次已落盘的安全修复事实。"""
    payload["reply_map_repaired"] = bool(repaired)
    payload["quote_replacements"] = int(replacements or 0)
    return payload


def _duplicate_send_errors(candidates, reply_map):
    """同一用户的相同正文最多允许一条映射为send。"""
    groups = {}
    for candidate in candidates:
        comment_id = candidate.get("comment_id", "")
        entry = reply_map.get(comment_id)
        if entry is None:
            continue  # 前置校验会拒绝缺失；此处仅作防御性忽略。
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
