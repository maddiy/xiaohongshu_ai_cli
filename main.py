#!/usr/bin/env python3
"""
小红书评论自动回复工具

功能:
  1. login    - 登录（自动尝试 Firefox/Chrome/Edge/Safari 等浏览器 Cookie）
  2. articles - 查看最新文章列表
  3. scan     - 扫描文章评论，列出未回复的（自动过滤跳过列表）
  4. drafts   - 生成回复草稿，逐条确认后保存
  5. send     - 发送已审核的草稿
  6. reply    - 扫描+回复（传统交互模式）
  7. analyze  - 对评论进行统计分析
  8. skipped  - 管理跳过列表（查看/移除）

依赖: xiaohongshu-cli (pip install xiaohongshu-cli)
配置: 修改 config.py 中的 AUTHOR_USER_ID
"""

import argparse
import json
import os
import tempfile
from lib.xhs_client import XHSClient
from lib.scanner import CommentScanner
from lib.replier import Replier
from lib.analyzer import CommentAnalyzer


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
        articles = client.list_articles(limit=args.limit)
        if not articles:
            print("暂无文章")
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
    """扫描未回复评论"""
    scanner = CommentScanner()

    # 通知快速模式：只从最新通知中提取新评论，不拉取全部评论
    if args.from_notifications:
        if args.note_id:
            print(f"{'='*60}")
            print(f"📬 通知快速扫描 — 笔记: {args.note_id}")
            print(f"{'='*60}")
            result = scanner.scan_via_notifications(
                note_id=args.note_id,
                verbose=True,
                num_notifications=args.num_notifications,
            )
        else:
            print(f"{'='*60}")
            print(f"📬 通知快速扫描 — 所有笔记")
            print(f"{'='*60}")
            result = scanner.scan_via_notifications(
                note_id=None,
                verbose=True,
                num_notifications=args.num_notifications,
            )
            per_note = result.get("per_note", [])
            if per_note:
                total_new = sum(r.get("total_new_notifications", 0) for r in per_note)
                total_unreplied = sum(len(r.get("unreplied_level1", [])) for r in per_note)
                print(f"\n📊 汇总: {len(per_note)}篇笔记 | 通知中 {total_new} 条新评论 | {total_unreplied} 条未回复")

        l1 = result.get("unreplied_level1", [])
        subs = result.get("unreplied_subs", [])
        note_id = result.get("note_id", args.note_id or "")
        note_xsec = result.get("note_xsec_token", args.xsec_token or "")

        if note_id and note_id != "all":
            output = {
                "note_id": note_id,
                "xsec_token": note_xsec,
                "unreplied_level1": l1,
                "unreplied_subs": subs,
                "source": "notifications",
            }
            path = os.path.join(tempfile.gettempdir(), f"unreplied_{note_id}.json")
            with open(path, "w") as f:
                json.dump(output, f, ensure_ascii=False, indent=2)
            print(f"\n📁 结果保存到 {path}")
        return

    # 全量扫描模式（原有逻辑）
    if args.note_id:
        print(f"{'='*60}")
        print(f"🔍 扫描笔记: {args.note_id}")
        if args.with_subs:
            print(f"  模式: 一级评论 + 完整楼中楼")
        else:
            print(f"  模式: 仅一级评论（楼中楼按需拉取）")
        print(f"{'='*60}")

        result = scanner.scan_note(
            args.note_id,
            args.xsec_token or "",
            include_sub_comments=args.with_subs,
            force_refresh=args.refresh,
        )

        l1 = result.get("unreplied_level1", [])
        subs = result.get("unreplied_subs", [])
        pending = result.get("pending_subs", 0)
        filtered = result.get("filtered_skipped", 0)
        print(f"\n📊 汇总: 一级未回 {len(l1)} | 楼中楼未回 {len(subs)} | 已跳过 {filtered} | 共需处理 {len(l1)+len(subs)} 条")
        if pending > 0:
            print(f"💡 还有 {pending} 个楼层的内联楼中楼数据不完整" +
                  "，使用 --with-subs 拉取完整楼中楼")

        output = {
            "note_id": result["note_id"],
            "unreplied_level1": l1,
            "unreplied_subs": subs,
            "pending_subs": pending,
        }
        path = os.path.join(tempfile.gettempdir(), f"unreplied_{args.note_id}.json")
        with open(path, "w") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        print(f"📁 结果保存到 {path}")

    else:
        results = scanner.scan_all_notes(
            include_sub_comments=args.with_subs,
            force_refresh=args.refresh,
            max_pages=args.max_pages,
        )
        total_l1 = sum(len(r.get("unreplied_level1", [])) for r in results)
        total_subs = sum(len(r.get("unreplied_subs", [])) for r in results)
        total_pending = sum(r.get("pending_subs", 0) for r in results)
        total_filtered = sum(r.get("filtered_skipped", 0) for r in results)
        print(f"\n{'='*60}")
        print(f"📊 全部汇总: {len(results)}篇笔记 | 一级未回 {total_l1} | 楼中楼未回 {total_subs} | 已跳过 {total_filtered} | 共 {total_l1+total_subs} 条")
        if total_pending > 0:
            print(f"💡 {total_pending} 个楼层楼中楼不完整，用 --with-subs 拉取")


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
    draft_path = args.output or os.path.join(
        tempfile.gettempdir(), f"drafts_{args.note_id}.json")
    with open(draft_path, "w") as f:
        json.dump(drafts, f, ensure_ascii=False, indent=2)
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
    to_send = [d for d in items if d.get("action") == "send"]
    to_skip = [d for d in items if d.get("action") == "skip"]
    to_archive = [d for d in items if d.get("action") == "archive"]

    print(f"\n📋 草稿概览")
    print(f"  ✅ 待发送: {len(to_send)}")
    print(f"  ⏭️ 本次跳过: {len(to_skip)}")
    print(f"  📁 永久跳过: {len(to_archive)}")

    if args.dry_run:
        print("\n🔍 预览模式（--dry-run），不实际发送：")
        for i, d in enumerate(to_send):
            print(f"  [{i+1}] @{d['nickname']}: {d['content'][:40]}")
            print(f"      → {d['reply'][:60]}")
        return

    if args.confirm and to_send:
        print(f"\n⚠️ 即将发送 {len(to_send)} 条回复")
        answer = input("确认发送? (y/n): ").strip().lower()
        if answer != "y":
            print("❌ 已取消")
            return

    replier = Replier()
    replier.send_drafts(drafts, resume=args.resume)


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


def cmd_analyze(args):
    """分析评论"""
    if not args.note_id:
        print("❌ 请指定笔记ID: --note-id <id>")
        return

    analyzer = CommentAnalyzer()
    result = analyzer.analyze(
        args.note_id,
        args.xsec_token or "",
        args.note_title or "",
        force_refresh=args.refresh,
    )
    CommentAnalyzer.print_report(result)


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
        description="小红书评论自动回复工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python3 main.py login                                    # 登录（自动尝试多浏览器 Cookie）
  python3 main.py articles                                 # 查看最新文章列表
  python3 main.py articles --limit 50                      # 查看最近50篇文章
  python3 main.py scan                                     # 扫描所有有评论的笔记（仅一级评论，过滤跳过列表）
  python3 main.py scan --note-id <note_id>                  # 扫描指定笔记
  python3 main.py scan --note-id <note_id> --with-subs     # 同时拉取完整楼中楼
  python3 main.py scan --note-id <note_id> --from-notifications  # 快速模式：从通知中提取新评论（不拉全部）

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
"""
    )
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # login
    subparsers.add_parser("login", help="登录小红书（自动尝试多个浏览器 Cookie）")

    # articles
    p_articles = subparsers.add_parser("articles", help="查看最新文章列表")
    p_articles.add_argument("--limit", type=int, default=20,
                            help="显示文章数量（默认20）")

    # scan
    p_scan = subparsers.add_parser("scan", help="扫描未回复评论")
    add_common_args(p_scan)
    add_limit_args(p_scan)
    p_scan.add_argument("--from-notifications", action="store_true",
                        help="快速模式：只从最新评论通知中提取新评论（不拉取全部评论）")
    p_scan.add_argument("--num-notifications", type=int, default=50,
                        help="通知模式下拉取的通知数量（默认50）")

    # drafts — 生成回复草稿
    p_drafts = subparsers.add_parser("drafts", help="生成回复草稿（逐条确认后保存）")
    add_common_args(p_drafts)
    p_drafts.add_argument("--output", help="草稿文件保存路径（默认系统临时目录）")
    p_drafts.add_argument("--from-scan", metavar="FILE",
                          help="从已有扫描结果文件加载（跳过重扫，如 /tmp/unreplied_xxx.json）")
    p_drafts.add_argument("--batch", metavar="FILE",
                          help="批量导入AI预写的回复映射JSON（非交互），格式: {\"comment_id\": \"回复文案\"}")

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
        "skipped": cmd_skipped,
        "analyze": cmd_analyze,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
