#!/usr/bin/env python3
"""
小红书AI智能运营系统

准确命令清单和 AI 调用协议请运行：python3 main.py ai-help
依赖 xiaohongshu-cli；浏览器配置位于config.py，账号身份首次运行自动识别。
"""

import json
import os
from config import WORK_DIR
from lib.xhs_client import XHSClient
from lib.scanner import CommentScanner
from lib.replier import Replier
from lib.cli_support import (
    build_comment_groups,
    call_for_output,
    compact_analysis,
    compact_comment,
    compact_scan_result,
    filter_scan_local_state,
    load_local_comment_states,
    merge_draft_history,
    NON_RESEND_STATUSES,
    print_json,
    scan_summary,
    workflow_paths,
    write_json,
)
from lib.cli_parser import (
    COMMAND_NAMES,
    build_parser,
)
from lib.cli_view import cmd_articles, cmd_comments, cmd_login
from lib.cli_admin import (
    cmd_ai_help,
    cmd_analyze,
    cmd_doctor,
    cmd_paths,
    cmd_post,
    cmd_skipped,
)
from lib.cli_ai import cmd_ai_reply
from lib.state_io import json_state_exists, read_json_state


def cmd_scan(args):
    """扫描未回复评论（默认从通知快速读取，--full-scan 走全量拉取）"""
    scanner = CommentScanner()

    # 默认：通知快速模式 — 只从最新通知中提取新评论，不拉取全部评论
    if not args.full_scan:
        if args.note_id:
            if not args.json:
                print(f"{'='*60}")
                print(f"📬 通知快速扫描 — 笔记: {args.note_id}")
                print(f"{'='*60}")
            result = call_for_output(
                scanner.scan_via_notifications,
                note_id=args.note_id,
                verbose=not args.json,
                num_notifications=args.num_notifications,
                quiet=args.json,
            )
        else:
            if not args.json:
                print(f"{'='*60}")
                print(f"📬 通知快速扫描 — 所有笔记")
                print(f"{'='*60}")
            result = call_for_output(
                scanner.scan_via_notifications,
                note_id=None,
                verbose=not args.json,
                num_notifications=args.num_notifications,
                quiet=args.json,
            )
        filter_scan_local_state(result)
        if not args.note_id:
            per_note = result.get("per_note", [])
            if per_note and not args.json:
                total_new = sum(r.get("total_new_notifications", 0) for r in per_note)
                total_unreplied = sum(len(r.get("unreplied_level1", [])) for r in per_note)
                print(f"\n📊 汇总: {len(per_note)}篇笔记 | 通知中 {total_new} 条新评论 | {total_unreplied} 条未回复")

        l1 = result.get("unreplied_level1", [])
        subs = result.get("unreplied_subs", [])
        note_id = result.get("note_id", args.note_id or "")

        if note_id and note_id != "all":
            output = compact_scan_result(result)
            path = write_json(
                output,
                args.output or workflow_paths(note_id)["scan"],
            )
            if args.json:
                response = {
                    "ok": bool(output.get("reply_status_verified", False)),
                    "output_file": path,
                    "summary": scan_summary(output),
                }
                if result.get("scan_error"):
                    response["error"] = result["scan_error"]
                print_json(response)
            else:
                print(f"\n📁 结果保存到 {path}")
        else:
            output = compact_scan_result(result)
            output_file = write_json(
                output,
                args.output or workflow_paths("all")["scan"],
            )
            if args.json:
                response = {
                    "ok": bool(output.get("reply_status_verified", False)),
                    "summary": scan_summary(output),
                }
                if result.get("scan_error"):
                    response["error"] = result["scan_error"]
                response["output_file"] = output_file
                print_json(response)
            elif output_file:
                print(f"\n📁 结果保存到 {output_file}")
        return

    # 全量扫描模式（--full-scan，原来行为）
    if args.note_id:
        if not args.json:
            print(f"{'='*60}")
            print(f"🔍 扫描笔记: {args.note_id}")
            if args.with_subs:
                print(f"  模式: 一级评论 + 完整楼中楼")
            else:
                print(f"  模式: 仅一级评论（楼中楼按需拉取）")
            print(f"{'='*60}")

        result = call_for_output(
            scanner.scan_note,
            args.note_id,
            args.xsec_token or "",
            include_sub_comments=args.with_subs,
            force_refresh=args.refresh,
            verbose=not args.json,
            quiet=args.json,
        )
        filter_scan_local_state(result)

        l1 = result.get("unreplied_level1", [])
        subs = result.get("unreplied_subs", [])
        pending = result.get("pending_subs", 0)
        filtered = result.get("filtered_skipped", 0)
        if not args.json:
            print(f"\n📊 汇总: 一级未回 {len(l1)} | 楼中楼未回 {len(subs)} | 已跳过 {filtered} | 共需处理 {len(l1)+len(subs)} 条")
        if pending > 0 and not args.json:
            print(f"💡 还有 {pending} 个楼层的内联楼中楼数据不完整" +
                  "，使用 --with-subs 拉取完整楼中楼")

        output = compact_scan_result(result)
        path = write_json(
            output,
            args.output or workflow_paths(args.note_id)["scan"],
        )
        if args.json:
            response = {
                "ok": bool(output.get("reply_status_verified", False)),
                "output_file": path,
                "summary": scan_summary(output),
            }
            if output.get("scan_error"):
                response["error"] = output["scan_error"]
            print_json(response)
        else:
            print(f"📁 结果保存到 {path}")

    else:
        results = call_for_output(
            scanner.scan_all_notes,
            include_sub_comments=args.with_subs,
            force_refresh=args.refresh,
            max_pages=args.max_pages,
            verbose=not args.json,
            quiet=args.json,
        )
        for result in results:
            filter_scan_local_state(result)
        total_l1 = sum(len(r.get("unreplied_level1", [])) for r in results)
        total_subs = sum(len(r.get("unreplied_subs", [])) for r in results)
        total_pending = sum(r.get("pending_subs", 0) for r in results)
        total_filtered = sum(r.get("filtered_skipped", 0) for r in results)
        if not args.json:
            print(f"\n{'='*60}")
            print(f"📊 全部汇总: {len(results)}篇笔记 | 一级未回 {total_l1} | 楼中楼未回 {total_subs} | 已跳过 {total_filtered} | 共 {total_l1+total_subs} 条")
        if total_pending > 0 and not args.json:
            print(f"💡 {total_pending} 个楼层楼中楼不完整，用 --with-subs 拉取")
        compact_results = [compact_scan_result(item) for item in results]
        output_file = write_json(
            {"notes": compact_results, "source": "full_scan"},
            args.output or workflow_paths("all")["scan"],
        )
        if args.json:
            response = {
                "ok": all(
                    item.get("reply_status_verified", False)
                    for item in compact_results
                ),
                "summary": {
                    "notes": len(compact_results),
                    "unreplied": total_l1 + total_subs,
                },
            }
            errors = [
                item["scan_error"] for item in compact_results
                if item.get("scan_error")
            ]
            if errors:
                response["error"] = "；".join(errors)
            response["output_file"] = output_file
            print_json(response)
        elif output_file:
            print(f"\n📁 结果保存到 {output_file}")


def cmd_drafts(args):
    """生成回复草稿（逐条确认后保存）"""
    if not args.note_id:
        print("❌ 请指定笔记ID: --note-id <id>")
        return

    scanner = CommentScanner()

    # 加载未回复列表：from-scan（跳过重扫）或 from-batch（批量导入）或实时扫描
    if args.from_scan:
        if not json_state_exists(args.from_scan):
            print(f"❌ 文件不存在: {args.from_scan}")
            return
        data = read_json_state(args.from_scan)
        if not data.get("reply_status_verified", False) and not args.allow_unverified:
            print("❌ 扫描文件未确认评论回复状态，已停止生成草稿")
            print("💡 请重新运行 scan；仅在明确接受重复回复风险时使用 --allow-unverified")
            return
        all_unreplied = data.get("unreplied_level1", []) + data.get("unreplied_subs", [])
        result = {"note_title": ""}  # from-scan 没有标题
        print(f"📂 从扫描结果读取: {len(all_unreplied)} 条未回复")
    else:
        if getattr(args, "full_scan", False):
            result = scanner.scan_note(
                args.note_id,
                args.xsec_token or "",
                include_sub_comments=True,
                force_refresh=args.refresh,
            )
        else:
            result = scanner.scan_via_notifications(
                note_id=args.note_id,
                num_notifications=getattr(args, "num_notifications", 20),
            )
        if not result.get("reply_status_verified", False):
            print(
                "❌ 最新评论在线核验失败，已停止生成草稿: "
                f"{result.get('scan_error', '未知错误')}"
            )
            return
        all_unreplied = result.get("unreplied_level1", []) + result.get("unreplied_subs", [])

    if not all_unreplied:
        print("✨ 全部已回复，无需操作")
        return

    local_result = {
        "note_id": args.note_id,
        "unreplied_level1": all_unreplied,
        "unreplied_subs": [],
    }
    filter_scan_local_state(local_result)
    all_unreplied = local_result["unreplied_level1"]
    if not all_unreplied:
        print("✨ 本地记录显示候选均已处理，无需生成草稿")
        return

    # 本地状态只能防止本程序重复发送；人工或其他客户端的回复必须在线确认。
    # 在线核验失败时宁可停止，也不能继续生成可能重复的草稿。
    try:
        all_unreplied, online_excluded = scanner.verify_candidates_online(
            args.note_id,
            all_unreplied,
            args.xsec_token or "",
        )
    except Exception as error:
        print(f"❌ 在线核验评论回复状态失败，已停止生成草稿: {error}")
        print("💡 请完成验证码或恢复网络后重试；不会使用本地记录代替在线核验")
        return

    if online_excluded:
        replied_count = sum(
            item["reason"] == "online_replied" for item in online_excluded
        )
        missing_count = sum(
            item["reason"] == "online_missing" for item in online_excluded
        )
        print(
            f"🌐 在线核验已排除: 已回复 {replied_count} | "
            f"已删除或不可见 {missing_count}"
        )

    if not all_unreplied:
        print("✨ 在线核验后没有可回复评论，不生成草稿")
        return

    replier = Replier()

    if args.batch:
        # 批量模式：从映射文件导入AI预写的回复
        if not json_state_exists(args.batch):
            print(f"❌ 映射文件不存在: {args.batch}")
            return
        reply_map = read_json_state(args.batch, prefer_snapshot=True)
        print(f"📂 从批量映射读取: {len(reply_map)} 条回复映射")
        drafts = replier.generate_drafts_from_mapping(
            all_unreplied, reply_map,
            note_id=args.note_id,
            note_title=result.get("note_title", ""),
        )
    else:
        # 交互模式：逐条手动输入
        drafts = replier.generate_drafts(
            all_unreplied,
            note_id=args.note_id,
            note_title=result.get("note_title", ""),
        )

    # 保存草稿文件
    draft_path = args.output or workflow_paths(args.note_id)["drafts"]
    if json_state_exists(draft_path):
        try:
            existing_drafts = read_json_state(draft_path)
            drafts = merge_draft_history(existing_drafts, drafts)
            print(f"🔄 已合并固定草稿中的历史发送状态")
        except (OSError, json.JSONDecodeError):
            pass
    draft_path = write_json(drafts, draft_path)
    print(f"\n📁 草稿已保存到 {draft_path}")
    print(f"💡 审核修改后运行: python3 main.py send --file {draft_path}")


def cmd_send(args):
    """发送已审核的草稿"""
    if not args.file:
        print("❌ 请指定草稿文件: --file <path>")
        return

    if not json_state_exists(args.file):
        print(f"❌ 文件不存在: {args.file}")
        return

    try:
        drafts = read_json_state(args.file)
    except (OSError, json.JSONDecodeError) as error:
        print(f"❌ 无法读取草稿文件: {error}")
        return
    if not isinstance(drafts, dict):
        print("❌ 草稿文件顶层必须是 JSON 对象")
        return

    items = drafts.get("drafts", [])
    active_ids = drafts.get("active_comment_ids")
    if isinstance(active_ids, list):
        active_ids = set(active_ids)
        items = [
            item for item in items
            if item.get("comment_id") in active_ids
        ]
    to_send = [
        d for d in items
        if d.get("action") == "send"
        and d.get("send_status") not in NON_RESEND_STATUSES
    ]
    sent = [d for d in items if d.get("send_status") == "sent"]
    to_skip = [d for d in items if d.get("action") == "skip"]
    to_archive = [
        d for d in items
        if d.get("action") == "archive"
        and d.get("send_status") != "archived"
    ]

    print(f"\n📋 草稿概览")
    print(f"  ✅ 待发送: {len(to_send)}")
    print(f"  ☑️ 已发送: {len(sent)}")
    print(f"  ⏭️ 本次跳过: {len(to_skip)}")
    print(f"  📁 永久跳过: {len(to_archive)}")

    if args.dry_run:
        print("\n🔍 预览模式（--dry-run），不实际发送：")
        for i, d in enumerate(to_send):
            print(f"\n  [{i+1}/{len(to_send)}] @{d['nickname']}")
            print(f"      💬 评论：{d['content']}")
            print(f"      ✏️ 回复：{d['reply']}")
        return

    if args.confirm and to_send:
        print(f"\n{'─'*40}")
        print(f"📋 待发送 {len(to_send)} 条回复：")
        for i, d in enumerate(to_send):
            print(f"\n  [{i+1}/{len(to_send)}] @{d['nickname']}")
            print(f"      💬 评论：{d['content']}")
            print(f"      ✏️ 回复：{d['reply']}")
        print(f"\n{'─'*40}")
        print(f"⚠️ 即将发送 {len(to_send)} 条回复")
        answer = input("确认发送? (y/n): ").strip().lower()
        if answer != "y":
            print("❌ 已取消")
            return

    # 传统入口也必须在实际发送前在线复核，避免预览期间从其他客户端回复。
    if to_send:
        note_id = drafts.get("note_id", "")
        if not note_id:
            print("❌ 草稿缺少 note_id，无法执行发送前在线核验")
            return
        try:
            eligible, excluded = CommentScanner().verify_candidates_online(
                note_id,
                [compact_comment(item) for item in to_send],
                "",
            )
        except Exception as error:
            print(f"❌ 发送前在线核验失败，已停止发送: {error}")
            return
        eligible_ids = {
            item.get("comment_id") for item in eligible
            if item.get("comment_id")
        }
        excluded_by_id = {
            item.get("comment_id"): item.get("reason", "online_excluded")
            for item in excluded
        }
        for item in to_send:
            comment_id = item.get("comment_id", "")
            if comment_id not in eligible_ids:
                item["send_status"] = "archived"
                item["archive_reason"] = excluded_by_id.get(
                    comment_id, "online_excluded"
                )
        if excluded:
            print(f"🌐 发送前在线核验排除 {len(excluded)} 条评论")
        write_json(drafts, args.file)

    replier = Replier()
    replier.send_drafts(drafts, resume=args.resume, state_file=args.file)
    saved_path = write_json(drafts, args.file)
    print(f"💾 发送状态已保存到 {saved_path}")


def cmd_reply(args):
    """兼容旧入口；发送前仍强制执行本地过滤和平台在线核验。"""
    replier = Replier()
    scanner = CommentScanner()

    if args.from_file:
        data = read_json_state(args.from_file)
        note_id = data.get("note_id", args.note_id or "")
        if not note_id:
            print("❌ 无法确定 note_id")
            return
        unreplied = data.get("unreplied_level1", []) + data.get("unreplied_subs", [])
    else:
        if not args.note_id:
            print("❌ 请指定笔记ID: --note-id <id>")
            return

        if getattr(args, "full_scan", False):
            result = scanner.scan_note(
                args.note_id,
                args.xsec_token or "",
                include_sub_comments=True,
                force_refresh=args.refresh,
            )
        else:
            result = scanner.scan_via_notifications(
                note_id=args.note_id,
                num_notifications=getattr(args, "num_notifications", 20),
            )
        if not result.get("reply_status_verified", False):
            print(
                "❌ 最新评论在线核验失败，已停止回复: "
                f"{result.get('scan_error', '未知错误')}"
            )
            return

        note_id = args.note_id
        unreplied = (
            result.get("unreplied_level1", [])
            + result.get("unreplied_subs", [])
        )

    local_result = {
        "note_id": note_id,
        "unreplied_level1": unreplied,
        "unreplied_subs": [],
    }
    filter_scan_local_state(local_result)
    unreplied = local_result["unreplied_level1"]
    if not unreplied:
        print("✨ 全部已回复或已处理，无需操作")
        return
    try:
        unreplied, excluded = scanner.verify_candidates_online(
            note_id, unreplied, args.xsec_token or ""
        )
    except Exception as error:
        print(f"❌ 在线核验失败，已停止回复: {error}")
        return
    if excluded:
        print(f"🌐 在线核验排除 {len(excluded)} 条已回复或不可见评论")
    if not unreplied:
        print("✨ 在线核验后没有可回复评论")
        return
    replier.reply_batch(note_id, unreplied, args.strategy)


COMMAND_HANDLERS = {
    "login": cmd_login,
    "articles": cmd_articles,
    "comments": cmd_comments,
    "scan": cmd_scan,
    "drafts": cmd_drafts,
    "send": cmd_send,
    "reply": cmd_reply,
    "post": cmd_post,
    "skipped": cmd_skipped,
    "analyze": cmd_analyze,
    "doctor": cmd_doctor,
    "ai-help": cmd_ai_help,
    "paths": cmd_paths,
    "ai-reply": cmd_ai_reply,
}


def main():
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    if set(COMMAND_HANDLERS) != set(COMMAND_NAMES):
        raise RuntimeError("命令处理器与 cli_parser.COMMAND_NAMES 不一致")
    COMMAND_HANDLERS[args.command](args)


if __name__ == "__main__":
    main()
