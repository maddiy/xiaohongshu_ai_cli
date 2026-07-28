#!/usr/bin/env python3
"""
小红书AI智能运营

功能:
  1. login    - 登录（默认读取 Firefox 浏览器 Cookie）
  2. articles - 查看最新文章列表
  3. scan     - 扫描文章评论，列出未回复的（自动过滤跳过列表）
  4. drafts   - 生成回复草稿，逐条确认后保存
  5. send     - 发送已审核的草稿
  6. reply    - 扫描+回复（传统交互模式）
  7. analyze  - 对评论进行统计分析
  8. skipped  - 管理跳过列表（查看/移除）
  9. post     - 发布小红书笔记（内容由 AI 生成）

依赖: xiaohongshu-cli (pip install xiaohongshu-cli)
配置: 修改 config.py 中的 AUTHOR_USER_ID
"""

import argparse
import contextlib
import io
import json
import os
import shutil
import sys
from config import WORK_DIR
from lib.xhs_client import XHSClient
from lib.scanner import CommentScanner
from lib.replier import Replier
from lib.analyzer import CommentAnalyzer
from lib import poster as poster_lib


def print_json(data):
    """输出紧凑 UTF-8 JSON，减少 AI 上下文 token。"""
    print(json.dumps(data, ensure_ascii=False, separators=(",", ":")))


def write_json(data, path):
    """将结果写入指定 JSON 文件，并返回绝对路径。"""
    path = os.path.abspath(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    return path


def workflow_paths(note_id):
    """返回一篇笔记的固定工作文件路径，供不同 AI 接续使用。"""
    safe_note_id = "".join(
        char for char in str(note_id or "all")
        if char.isalnum() or char in ("-", "_")
    ) or "all"
    directory = os.path.abspath(os.path.join(WORK_DIR, safe_note_id))
    return {
        "directory": directory,
        "scan": os.path.join(directory, "scan.json"),
        "reply_map": os.path.join(directory, "reply_map.json"),
        "drafts": os.path.join(directory, "drafts.json"),
    }


def call_for_output(func, *args, quiet=False, **kwargs):
    """机器模式下收起过程日志，保证 stdout 是单一 JSON 文档。"""
    if not quiet:
        return func(*args, **kwargs)
    with contextlib.redirect_stdout(io.StringIO()):
        return func(*args, **kwargs)


def compact_comment(comment):
    """只保留生成回复所需字段，丢弃内部楼中楼原始数据。"""
    keys = (
        "comment_id", "nickname", "content", "likes", "sub_count",
        "parent_comment_id", "parent_nickname",
    )
    return {key: comment[key] for key in keys if key in comment}


def compact_scan_result(result):
    """将扫描结果转换为适合 AI 消费的最小稳定结构。"""
    compact = {
        "note_id": result.get("note_id", ""),
        "note_title": result.get("note_title", ""),
        "source": result.get("source", "full_scan"),
        "unreplied_level1": [
            compact_comment(item) for item in result.get("unreplied_level1", [])
        ],
        "unreplied_subs": [
            compact_comment(item) for item in result.get("unreplied_subs", [])
        ],
    }
    for key in (
        "total", "total_new_notifications", "pending_subs",
        "filtered_skipped", "reply_status_verified",
    ):
        if key in result:
            compact[key] = result[key]
    if result.get("per_note"):
        compact["per_note"] = []
        for index, item in enumerate(result["per_note"], start=1):
            compact_item = compact_scan_result(item)
            compact_item["note_index"] = index
            compact["per_note"].append(compact_item)
    return compact


def scan_summary(result):
    """返回不重复评论正文的扫描摘要。"""
    if result.get("per_note"):
        notes = result["per_note"]
        return {
            "notes": len(notes),
            "unreplied": sum(
                len(item.get("unreplied_level1", []))
                + len(item.get("unreplied_subs", []))
                for item in notes
            ),
        }
    return {
        "note_id": result.get("note_id", ""),
        "unreplied_level1": len(result.get("unreplied_level1", [])),
        "unreplied_subs": len(result.get("unreplied_subs", [])),
        "pending_subs": result.get("pending_subs", 0),
    }


def compact_analysis(result):
    """移除重复评论数组，只保留统计、热门评论和活跃用户。"""
    if not result:
        return None
    return {
        "note_id": result.get("note_id", ""),
        "note_title": result.get("note_title", ""),
        "total_comments": result.get("total_comments", 0),
        "unique_users": result.get("unique_users", 0),
        "total_likes": result.get("total_likes", 0),
        "total_subs": result.get("total_subs", 0),
        "self_comments": result.get("self_comments", 0),
        "replied": result.get("replied", 0),
        "unreplied": result.get("unreplied", 0),
        "counts": result.get("counts", {}),
        "top_comments": result.get("sorted_by_likes", [])[:10],
        "active_users": dict(
            sorted(
                result.get("active_users", {}).items(),
                key=lambda item: item[1],
                reverse=True,
            )[:10]
        ),
    }


def merge_draft_history(existing, new):
    """合并固定草稿文件，保留历史发送状态并追加新评论。"""
    merged = dict(new)
    old_items = existing.get("drafts", []) if isinstance(existing, dict) else []
    new_items = new.get("drafts", [])
    by_id = {
        item.get("comment_id"): item
        for item in old_items
        if item.get("comment_id")
    }
    order = [item.get("comment_id") for item in old_items if item.get("comment_id")]
    for item in new_items:
        comment_id = item.get("comment_id")
        old = by_id.get(comment_id)
        if old and old.get("send_status") in ("sent", "failed", "archived"):
            continue
        by_id[comment_id] = item
        if comment_id not in order:
            order.append(comment_id)
    merged["drafts"] = [by_id[comment_id] for comment_id in order]
    return merged


def cmd_login(args):
    """登录小红书（仅 Firefox）"""
    client = XHSClient()
    if client.login():
        info = client.whoami()
        print(f"✅ 登录成功\n{info}")
    else:
        print("❌ 登录失败，请确保 Firefox 已登录小红书账号")


def cmd_articles(args):
    """查看最新文章列表"""
    client = XHSClient()
    try:
        articles = call_for_output(client.list_articles, limit=args.limit, quiet=args.json)
        if not articles:
            print("暂无文章")
            return

        if args.json:
            safe_articles = [
                {
                    "id": item["id"],
                    "title": item.get("title", ""),
                    "comments_count": item.get("comments_count", 0),
                    "time": item.get("time", ""),
                }
                for item in articles
            ]
            print_json({"ok": True, "articles": safe_articles, "count": len(safe_articles)})
            return

        print(f"\n{'='*80}")
        print(f"📋 最新文章列表（共 {len(articles)} 篇）")
        print(f"{'='*80}")
        print(f"{'#':<4} {'时间':<18} {'评论':<6} {'文章ID':<28} 标题")
        print(f"{'-'*4} {'-'*18} {'-'*6} {'-'*28} {'-'*30}")

        for i, a in enumerate(articles):
            print(f"{i+1:<4} {a['time']:<18} {a['comments_count']:<6} {a['id']:<28} {a['title'][:50]}")

        print(f"\n💡 使用 --note-id <文章ID> 来操作指定文章")

        total_comments = sum(a["comments_count"] for a in articles)
        with_comments = sum(1 for a in articles if a["comments_count"] > 0)
        print(f"📊 共 {len(articles)} 篇文章 | {with_comments} 篇有评论 | 总计 {total_comments} 条评论")

    except RuntimeError as e:
        print(f"❌ {e}")


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
                print_json({"ok": True, "output_file": path, "summary": scan_summary(output)})
            else:
                print(f"\n📁 结果保存到 {path}")
        else:
            output = compact_scan_result(result)
            output_file = write_json(
                output,
                args.output or workflow_paths("all")["scan"],
            )
            if args.json:
                response = {"ok": True, "summary": scan_summary(output)}
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
            print_json({"ok": True, "output_file": path, "summary": scan_summary(output)})
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
                "ok": True,
                "summary": {
                    "notes": len(compact_results),
                    "unreplied": total_l1 + total_subs,
                },
            }
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
        if not os.path.exists(args.from_scan):
            print(f"❌ 文件不存在: {args.from_scan}")
            return
        with open(args.from_scan) as f:
            data = json.load(f)
        if not data.get("reply_status_verified", False) and not args.allow_unverified:
            print("❌ 扫描文件未确认评论回复状态，已停止生成草稿")
            print("💡 请重新运行 scan；仅在明确接受重复回复风险时使用 --allow-unverified")
            return
        all_unreplied = data.get("unreplied_level1", []) + data.get("unreplied_subs", [])
        result = {"note_title": ""}  # from-scan 没有标题
        print(f"📂 从扫描结果读取: {len(all_unreplied)} 条未回复")
    else:
        result = scanner.scan_note(
            args.note_id,
            args.xsec_token or "",
            include_sub_comments=args.with_subs,
            force_refresh=args.refresh,
        )
        all_unreplied = result.get("unreplied_level1", []) + result.get("unreplied_subs", [])

    if not all_unreplied:
        print("✨ 全部已回复，无需操作")
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
        if not os.path.exists(args.batch):
            print(f"❌ 映射文件不存在: {args.batch}")
            return
        with open(args.batch) as f:
            reply_map = json.load(f)
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
    if os.path.exists(draft_path):
        try:
            with open(draft_path, encoding="utf-8") as f:
                existing_drafts = json.load(f)
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

    if not os.path.exists(args.file):
        print(f"❌ 文件不存在: {args.file}")
        return

    with open(args.file) as f:
        drafts = json.load(f)

    items = drafts.get("drafts", [])
    to_send = [
        d for d in items
        if d.get("action") == "send"
        and d.get("send_status") not in ("sent", "failed", "archived")
    ]
    sent = [d for d in items if d.get("send_status") == "sent"]
    to_skip = [d for d in items if d.get("action") == "skip"]
    to_archive = [d for d in items if d.get("action") == "archive"]

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

    replier = Replier()
    replier.send_drafts(drafts, resume=args.resume, state_file=args.file)
    saved_path = write_json(drafts, args.file)
    print(f"💾 发送状态已保存到 {saved_path}")


def cmd_reply(args):
    """批量回复（传统交互模式）"""
    replier = Replier()

    if args.from_file:
        with open(args.from_file) as f:
            data = json.load(f)
        note_id = data.get("note_id", args.note_id or "")
        if not note_id:
            print("❌ 无法确定 note_id")
            return
        unreplied = data.get("unreplied_level1", []) + data.get("unreplied_subs", [])
        replier.reply_batch(note_id, unreplied, args.strategy)
    else:
        if not args.note_id:
            print("❌ 请指定笔记ID: --note-id <id>")
            return

        scanner = CommentScanner()
        result = scanner.scan_note(
            args.note_id,
            args.xsec_token or "",
            include_sub_comments=args.with_subs,
            force_refresh=args.refresh,
        )

        l1 = result.get("unreplied_level1", [])
        subs = result.get("unreplied_subs", [])
        all_unreplied = l1 + subs

        if all_unreplied:
            replier.reply_batch(args.note_id, all_unreplied, args.strategy)
        else:
            print("✨ 全部已回复，无需操作")


def cmd_skipped(args):
    """管理跳过列表"""
    client = XHSClient()

    if args.remove:
        removed = client.remove_skipped(args.remove)
        if removed:
            print(f"✅ 已从跳过列表移除: {args.remove}")
        else:
            print(f"⚠️ 未找到: {args.remove}")
        return

    skipped = client.load_skipped()
    if not skipped:
        print("📭 跳过列表为空")
        return

    if args.clear:
        print(f"⚠️ 即将清空 {len(skipped)} 条跳过记录")
        answer = input("确认? (y/n): ").strip().lower()
        if answer == "y":
            client.save_skipped({})
            print("✅ 已清空")
        else:
            print("❌ 已取消")
        return

    # 显示列表
    print(f"\n{'='*60}")
    print(f"📋 跳过列表（共 {len(skipped)} 条）")
    print(f"{'='*60}")
    print(f"{'#':<4} {'时间':<20} {'原因':<14} {'用户':<16} 内容")
    print(f"{'-'*4} {'-'*20} {'-'*14} {'-'*16} {'-'*30}")

    items = list(skipped.items())
    for i, (cid, info) in enumerate(items):
        print(f"{i+1:<4} {info.get('skipped_at',''):<20} {info.get('reason',''):<14} "
              f"{info.get('nickname',''):<16} {info.get('content','')[:40]}")

    print(f"\n💡 移除: python3 main.py skipped --remove <comment_id>")
    print(f"💡 清空: python3 main.py skipped --clear")


def cmd_post(args):
    """发布小红书笔记（内容由 AI 生成，本命令只负责发布）"""
    payload = {}
    if args.input:
        try:
            with open(args.input, encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            print(f"❌ 无法读取笔记 JSON: {e}")
            return

    title = args.title or payload.get("title", "")
    body = args.body or payload.get("body", "")
    if not title or not body:
        print("❌ 必须指定 --title 和 --body")
        return

    images = args.images or payload.get("images", [])
    if not images:
        print("❌ 至少需要一张图片: --images 图片1.jpg [图片2.jpg ...]")
        return

    topics = ([t.strip() for t in args.topics.split(",") if t.strip()]
              if args.topics else payload.get("topics"))
    private = args.private or bool(payload.get("private", False))

    try:
        ok = poster_lib.publish(
            title, body, images, topics,
            private=private,
            dry_run=args.dry_run,
        )
        if not ok:
            print("⚠️ 发布可能未成功，请检查小红书客户端状态")
    except Exception as e:
        print(f"❌ 发布失败: {e}")


def cmd_analyze(args):
    """分析评论"""
    if not args.note_id:
        print("❌ 请指定笔记ID: --note-id <id>")
        return

    analyzer = CommentAnalyzer()
    result = call_for_output(
        analyzer.analyze,
        args.note_id,
        args.xsec_token or "",
        args.note_title or "",
        force_refresh=args.refresh,
        quiet=args.json,
    )
    if args.json:
        data = result if args.details else compact_analysis(result)
        print_json({"ok": result is not None, "data": data})
    else:
        CommentAnalyzer.print_report(result)


def cmd_doctor(args):
    """检查运行环境，默认不联网、不修改任何数据。"""
    from config import AUTHOR_USER_ID, LOGIN_COOKIE_SOURCE

    checks = {
        "python": {"ok": True, "value": sys.version.split()[0]},
        "xhs": {"ok": bool(shutil.which("xhs")), "value": shutil.which("xhs") or ""},
        "author_user_id": {
            "ok": bool(AUTHOR_USER_ID and "你的小红书" not in AUTHOR_USER_ID),
            "value": AUTHOR_USER_ID,
        },
        "cookie_source": {"ok": bool(LOGIN_COOKIE_SOURCE), "value": LOGIN_COOKIE_SOURCE},
        "cache_writable": {"ok": os.access(".", os.W_OK), "value": os.path.abspath(".cache")},
    }
    ok = all(item["ok"] for item in checks.values())
    if args.json:
        print_json({"ok": ok, "checks": checks})
        return
    print("🩺 环境检查")
    for name, item in checks.items():
        print(f"  {'✅' if item['ok'] else '❌'} {name}: {item['value']}")
    print("✅ 可以运行" if ok else "❌ 请先修复失败项")


def cmd_ai_help(args):
    """输出稳定的机器可读调用协议。"""
    spec = {
        "schema_version": "2",
        "language": "zh-CN",
        "safety": {
            "scan_is_read_only": True,
            "draft_is_local_only": True,
            "send_requires_user_review_recommended": True,
            "post_dry_run_recommended": True,
        },
        "reply_decision": {
            "warning": "scan.json 的 unreplied_* 只是平台候选，不是最终待回复清单",
            "required_files": [
                ".cache/workflows/<id>/scan.json",
                ".cache/workflows/<id>/drafts.json",
                ".cache/workflows/<id>/reply_map.json",
                ".cache/skipped.json",
            ],
            "key": "comment_id",
            "terminal_statuses": ["sent", "failed", "archived"],
            "status_precedence": [
                "drafts.send_status=sent",
                "drafts.send_status=archived 或 skipped.json",
                "drafts.send_status=failed",
                "平台已回复或评论已删除",
                "scan.json 候选",
            ],
            "eligible_when_all": [
                "scan.reply_status_verified=true",
                "comment_id 在本次 unreplied_level1 或 unreplied_subs",
                "drafts 中同 comment_id 不为 sent、failed、archived",
                "comment_id 不在 skipped.json",
                "评论未删除",
            ],
            "empty_result": "报告没有可回复评论；禁止复用旧 reply_map.json",
            "failed_handling": "所有失败评论自动加入 skipped.json；默认不重试",
            "failed_retry": "展示失败原因，取得用户明确授权，并从 skipped.json 移除后再处理",
        },
        "workflows": {
            "reply": [
                "python3 main.py paths --note-id <id>",
                "读取已有 drafts.json 和 .cache/skipped.json",
                "python3 main.py scan --note-id <id> --json",
                "按 comment_id 和 reply_decision 过滤平台候选",
                "仅为过滤后可回复评论生成同目录 reply_map.json",
                "若过滤后为空，报告没有可回复评论并停止",
                "python3 main.py drafts --note-id <id> --from-scan .cache/workflows/<id>/scan.json --batch .cache/workflows/<id>/reply_map.json",
                "python3 main.py send --file .cache/workflows/<id>/drafts.json --dry-run",
                "获得用户明确确认",
                "python3 main.py send --file .cache/workflows/<id>/drafts.json",
            ],
            "post": [
                "创建 .cache/workflows/post/note.json",
                "python3 main.py post --input .cache/workflows/post/note.json --dry-run",
                "python3 main.py post --input .cache/workflows/post/note.json",
            ],
        },
    }
    print_json(spec)


def cmd_paths(args):
    """显示固定工作目录及文件状态，方便不同 AI 接续任务。"""
    paths = workflow_paths(args.note_id)
    files = {
        key: {"path": value, "exists": os.path.exists(value)}
        for key, value in paths.items()
        if key != "directory"
    }
    print_json({
        "ok": True,
        "note_id": args.note_id,
        "directory": paths["directory"],
        "files": files,
        "post_note": os.path.abspath(os.path.join(WORK_DIR, "post", "note.json")),
    })


def add_common_args(parser):
    """给子命令添加通用参数"""
    parser.add_argument("--note-id", help="笔记ID")
    parser.add_argument("--xsec-token", default="", help="笔记的 xsec_token")
    parser.add_argument("--refresh", action="store_true",
                        help="强制刷新，忽略缓存重新拉取评论")
    parser.add_argument("--with-subs", action="store_true",
                        help="同时拉取楼中楼（默认不拉取，仅检查一级评论）")


def add_limit_args(parser):
    """给需要翻页控制的命令添加参数"""
    parser.add_argument("--max-pages", type=int, default=None,
                        help="最多翻页数（默认翻到底，每页约10篇）")


def main():
    parser = argparse.ArgumentParser(
        description="面向中文用户与 AI 助手的小红书发布、评论管理命令行工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python3 main.py login                                    # 登录（默认读取 Firefox Cookie）
  python3 main.py articles                                 # 查看最新文章列表
  python3 main.py articles --limit 50                      # 查看最近50篇文章
  python3 main.py scan                                     # 默认通知模式：从通知中提取最新评论
  python3 main.py scan --note-id <note_id>                  # 指定笔记的通知快速扫描
  python3 main.py scan --note-id <note_id> --full-scan     # 全量扫描：拉取全部评论逐一比对
  python3 main.py scan --note-id <note_id> --full-scan --with-subs  # 全量扫描+拉取完整楼中楼

  python3 main.py drafts --note-id <note_id>               # 生成回复草稿（逐条确认）
  python3 main.py drafts --note-id <note_id> --from-scan /tmp/unreplied_xxx.json  # 从扫描结果加载
  python3 main.py drafts --note-id <note_id> --batch /tmp/reply_map.json  # 批量导入AI预写回复（非交互）
  python3 main.py send --file /tmp/drafts_xxx.json         # 发送已审核草稿
  python3 main.py send --file /tmp/drafts_xxx.json --dry-run  # 预览不发送
  python3 main.py send --file /tmp/drafts_xxx.json --confirm  # 发送前二次确认
  python3 main.py send --file /tmp/drafts_xxx.json --resume  # 断点续发（跳过已完成的）

  python3 main.py reply --note-id <note_id>                # 传统交互式回复
  python3 main.py reply --note-id <note_id> --strategy generic

  python3 main.py skipped                                  # 查看跳过列表
  python3 main.py skipped --remove <comment_id>            # 移除指定跳过记录
  python3 main.py skipped --clear                          # 清空跳过列表

  python3 main.py analyze --note-id <note_id>              # 分析评论（优先缓存）
  python3 main.py doctor                                   # 检查本地环境
  python3 main.py ai-help                                  # 输出 AI 调用协议
  python3 main.py post --title "标题" --body "正文" --images 图1.jpg 图2.jpg
  python3 main.py post --title "标题" --body "正文" --images 图1.jpg --topics "读书,成长"
  python3 main.py post --input note.json --dry-run         # 从 JSON 预览笔记
"""
    )
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # login
    subparsers.add_parser("login", help="登录小红书（默认读取 Firefox Cookie）")

    # articles
    p_articles = subparsers.add_parser("articles", help="查看最新文章列表")
    p_articles.add_argument("--limit", type=int, default=20,
                            help="显示文章数量（默认20）")
    p_articles.add_argument("--json", action="store_true",
                            help="输出机器可读 JSON（适合 AI/脚本）")

    # scan
    p_scan = subparsers.add_parser("scan", help="扫描未回复评论（默认从通知快速读取）")
    add_common_args(p_scan)
    add_limit_args(p_scan)
    p_scan.add_argument("--full-scan", action="store_true",
                        help="全量扫描模式：拉取笔记全部评论逐一比对（默认只从通知中提取）")
    p_scan.add_argument("--num-notifications", type=int, default=20,
                        help="通知模式下拉取的通知数量（默认20）")
    p_scan.add_argument("--output", help="扫描结果 JSON 保存路径")
    p_scan.add_argument("--json", action="store_true",
                        help="仅输出机器可读 JSON（适合 AI/脚本）")

    # drafts — 生成回复草稿
    p_drafts = subparsers.add_parser("drafts", help="生成回复草稿（逐条确认后保存）")
    add_common_args(p_drafts)
    p_drafts.add_argument("--output", help="草稿文件保存路径（默认固定工作目录）")
    p_drafts.add_argument("--from-scan", metavar="FILE",
                          help="从已有扫描结果文件加载（跳过重新扫描）")
    p_drafts.add_argument("--batch", metavar="FILE",
                          help="批量导入AI预写的回复映射JSON（非交互），格式: {\"comment_id\": \"回复文案\"}")
    p_drafts.add_argument(
        "--allow-unverified",
        action="store_true",
        help="允许使用未核验回复状态的扫描文件（有重复回复风险）",
    )

    # send — 发送草稿
    p_send = subparsers.add_parser("send", help="发送已审核的回复草稿")
    p_send.add_argument("--file", required=True, help="草稿文件路径")
    p_send.add_argument("--dry-run", action="store_true", help="预览模式，不实际发送")
    p_send.add_argument("--confirm", action="store_true", help="发送前二次确认")
    p_send.add_argument("--resume", action="store_true",
                        help="断点续发：跳过已在跳过列表中的评论（适用于中断后恢复）")

    # reply
    p_reply = subparsers.add_parser("reply", help="回复评论（传统交互模式）")
    add_common_args(p_reply)
    p_reply.add_argument("--strategy", default="smart", choices=["smart", "generic"],
                          help="回复策略: smart=逐条确认, generic=随机话术")
    p_reply.add_argument("--from-file", help="从JSON文件读取未回复列表")

    # skipped
    p_skipped = subparsers.add_parser("skipped", help="管理跳过列表")
    p_skipped.add_argument("--remove", help="移除指定评论ID")
    p_skipped.add_argument("--clear", action="store_true", help="清空所有跳过记录")

    # analyze
    p_analyze = subparsers.add_parser("analyze", help="分析评论")
    add_common_args(p_analyze)
    p_analyze.add_argument("--note-title", default="", help="笔记标题（可选）")
    p_analyze.add_argument("--json", action="store_true",
                           help="输出机器可读 JSON（适合 AI/脚本）")
    p_analyze.add_argument("--details", action="store_true",
                           help="JSON 中包含全部评论明细（默认仅输出摘要以节省 token）")

    # post — 发布小红书笔记（内容由 AI 生成）
    p_post = subparsers.add_parser("post", help="发布小红书笔记")
    p_post.add_argument("--input", metavar="FILE",
                        help="从 JSON 文件读取 title/body/images/topics/private")
    p_post.add_argument("--title", help="笔记标题（可覆盖 JSON 中的值）")
    p_post.add_argument("--body", help="笔记正文（可覆盖 JSON 中的值）")
    p_post.add_argument("--images", nargs="+", help="图片路径（可覆盖 JSON 中的值）")
    p_post.add_argument("--topics", help="话题标签，逗号分隔（如: 读书,成长）")
    p_post.add_argument("--private", action="store_true", help="私密发布")
    p_post.add_argument("--dry-run", action="store_true", help="只校验和预览，不实际发布")

    # 面向人和 AI 的辅助命令
    p_doctor = subparsers.add_parser("doctor", help="检查本地环境与配置（不联网）")
    p_doctor.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    subparsers.add_parser("ai-help", help="输出 AI 调用协议（JSON）")
    p_paths = subparsers.add_parser("paths", help="显示指定笔记的固定工作文件路径")
    p_paths.add_argument("--note-id", default="all", help="笔记ID（默认 all）")
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    commands = {
        "login": cmd_login,
        "articles": cmd_articles,
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
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
