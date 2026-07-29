"""登录、文章列表和评论列表命令。"""

from .cli_support import (
    build_comment_groups,
    call_for_output,
    print_json,
)
from .xhs_client import XHSClient


def cmd_login(args):
    client = XHSClient()
    if client.login():
        print(f"✅ 登录成功\n{client.whoami()}")
    else:
        print("❌ 登录失败，请确保 Firefox 已登录小红书账号")


def cmd_articles(args):
    """查看最新文章列表。"""
    client = XHSClient()
    try:
        articles = call_for_output(
            client.list_articles, limit=args.limit, quiet=args.json
        )
        if not articles:
            print_json({"ok": True, "articles": [], "count": 0}) if args.json \
                else print("暂无文章")
            return
        if args.json:
            safe_articles = [{
                "index": index,
                "id": item["id"],
                "title": item.get("title", "") or "无标题",
                "comments_count": item.get("comments_count", 0),
                "time": item.get("time", ""),
            } for index, item in enumerate(articles, start=1)]
            print_json({
                "ok": True,
                "columns": ["序号", "发布时间", "评论数", "标题", "笔记ID"],
                "articles": safe_articles,
                "count": len(safe_articles),
            })
            return
        print(f"\n{'='*80}")
        print(f"📋 最新文章列表（共 {len(articles)} 篇）")
        print(f"{'='*80}")
        print(f"{'#':<4} {'时间':<18} {'评论':<6} {'标题':<52} 文章ID")
        print(f"{'-'*4} {'-'*18} {'-'*6} {'-'*52} {'-'*28}")
        for index, article in enumerate(articles, start=1):
            title = article.get("title", "") or "无标题"
            print(
                f"{index:<4} {article['time']:<18} "
                f"{article['comments_count']:<6} {title[:50]:<52} "
                f"{article['id']}"
            )
        total = sum(item["comments_count"] for item in articles)
        with_comments = sum(
            1 for item in articles if item["comments_count"] > 0
        )
        print("\n💡 使用 --note-id <文章ID> 来操作指定文章")
        print(
            f"📊 共 {len(articles)} 篇文章 | {with_comments} 篇有评论 | "
            f"总计 {total} 条评论"
        )
    except RuntimeError as error:
        print_json({"ok": False, "error": str(error)}) if args.json \
            else print(f"❌ {error}")


def cmd_comments(args):
    """查看最新评论；不生成回复候选。"""
    try:
        notifications = call_for_output(
            XHSClient.get_notifications,
            num=args.limit,
            notification_type="mentions",
            strict=True,
            quiet=args.json,
        )
        groups = build_comment_groups(notifications, args.note_id or "")
    except RuntimeError as error:
        print_json({"ok": False, "error": str(error)}) if args.json \
            else print(f"❌ 读取最新评论失败: {error}")
        return
    if args.json:
        print_json({
            "ok": True,
            "columns": ["序号", "时间", "用户", "评论", "状态"],
            "groups": groups,
            "notes": len(groups),
            "comments": sum(len(group["comments"]) for group in groups),
        })
        return
    if not groups:
        print("暂无评论通知")
        return
    for group in groups:
        print(
            f"\n{group['note_index']}. {group['note_title']}"
            f"（{group['note_id']}）"
        )
        print(f"{'序号':<6} {'时间':<18} {'用户':<18} {'状态':<10} 评论")
        for index, item in enumerate(group["comments"], start=1):
            print(
                f"{index:<6} {item['time'][5:]:<18} "
                f"{item['nickname'][:16]:<18} {item['status']:<10} "
                f"{item['content'][:80]}"
            )
