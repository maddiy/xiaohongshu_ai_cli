"""登录、文章列表和评论列表命令。"""

import json
import math
import os
import shlex

from config import CACHE_DIR, COMMENTS_FILE
from .cli_support import (
    build_comment_groups,
    build_comment_display_groups,
    call_for_output,
    COMMENT_DISPLAY_RULES,
    print_json,
    save_comment_archive,
    write_json,
)
from .xhs_client import XHSClient
from .state_io import read_json_state


ARTICLE_COLUMNS = ["序号", "发布时间", "评论数", "标题", "笔记ID"]
ARTICLE_COLUMN_FIELDS = {
    "序号": "index",
    "发布时间": "time",
    "评论数": "comments_count",
    "标题": "title",
    "笔记ID": "note_id",
}


def cmd_login(args):
    client = XHSClient()
    if client.login():
        print(f"✅ 登录成功\n{client.whoami()}")
    else:
        print("❌ 登录失败，请确保 Firefox 已登录小红书账号")


def cmd_articles(args):
    """查看最新文章列表。"""
    client = XHSClient()
    output_path = os.path.abspath(
        getattr(args, "output", None)
        or os.path.join(CACHE_DIR, "articles.json")
    )
    use_cache = bool(getattr(args, "cache", False))
    page = getattr(args, "page", 1) or 1
    page_size = getattr(args, "page_size", 20) or 20
    if page < 1 or page_size < 1:
        error = "page和page-size必须大于0"
        print_json({"ok": False, "error": error}) if args.json \
            else print(f"❌ {error}")
        return
    try:
        if use_cache:
            cached = read_json_state(output_path)
            articles = [{
                "index": item.get("index", index),
                "note_id": item.get("note_id") or item.get("id", ""),
                "title": item.get("title", "") or "无标题",
                "comments_count": item.get("comments_count", 0),
                "time": item.get("time", ""),
            } for index, item in enumerate(
                cached.get("articles", []), start=1
            )]
            source = "cache"
        else:
            raw_articles = call_for_output(
                client.list_articles, limit=args.limit, quiet=args.json
            )
            articles = [{
                "index": index,
                "note_id": item["id"],
                "title": item.get("title", "") or "无标题",
                "comments_count": item.get("comments_count", 0),
                "time": item.get("time", ""),
            } for index, item in enumerate(raw_articles, start=1)]
            source = "online"
            if args.json and (
                len(articles) > page_size
                or getattr(args, "output", None)
            ):
                write_json({
                    "ok": True,
                    "columns": ARTICLE_COLUMNS,
                    "column_fields": ARTICLE_COLUMN_FIELDS,
                    "articles": articles,
                    "count": len(articles),
                }, output_path)
    except RuntimeError as error:
        print_json({"ok": False, "error": str(error)}) if args.json \
            else print(f"❌ {error}")
        return
    except (OSError, json.JSONDecodeError) as error:
        message = f"文章缓存读取失败: {error}"
        print_json({"ok": False, "error": message}) if args.json \
            else print(f"❌ {message}")
        return
    try:
        if not articles:
            print_json({
                "ok": True,
                "columns": ARTICLE_COLUMNS,
                "column_fields": ARTICLE_COLUMN_FIELDS,
                "articles": [],
                "count": 0,
                "total_count": 0,
            }) if args.json else print("暂无文章")
            return
        if args.json:
            total_count = len(articles)
            total_pages = math.ceil(total_count / page_size)
            start = (page - 1) * page_size
            page_articles = articles[start:start + page_size]
            has_more = page < total_pages
            cache_available = (
                use_cache
                or total_count > page_size
                or bool(getattr(args, "output", None))
            )
            next_command = ""
            if has_more and cache_available:
                next_command = (
                    "python3 main.py articles --cache --json "
                    f"--page {page + 1} --page-size {page_size} "
                    f"--output {shlex.quote(output_path)}"
                )
            print_json({
                "ok": True,
                "columns": ARTICLE_COLUMNS,
                "column_fields": ARTICLE_COLUMN_FIELDS,
                "articles": page_articles,
                "count": len(page_articles),
                "total_count": total_count,
                "pagination": {
                    "page": page,
                    "page_size": page_size,
                    "total_pages": total_pages,
                    "has_more": has_more,
                    "next_command": next_command,
                },
                "source": source,
                **({"cache_path": output_path} if cache_available else {}),
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
                f"{article.get('note_id') or article.get('id', '')}"
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
        raw_groups = build_comment_groups(notifications, args.note_id or "")
        groups = build_comment_display_groups(raw_groups)
    except RuntimeError as error:
        print_json({"ok": False, "error": str(error)}) if args.json \
            else print(f"❌ 读取最新评论失败: {error}")
        return
    try:
        archive = save_comment_archive(raw_groups)
    except (OSError, RuntimeError) as error:
        # 归档损坏时保护旧文件，但不让本地存储故障遮住已成功读取的评论。
        archive = {
            "ok": False,
            "path": os.path.abspath(COMMENTS_FILE),
            "write_skipped": True,
            "error": str(error),
        }
    if args.json:
        payload = {
            "ok": True,
            "columns": ["序号", "时间", "用户", "评论", "状态"],
            "groups": groups,
            "notes": len(groups),
            "comments": sum(len(group["comments"]) for group in groups),
            "archive": archive,
            "display": COMMENT_DISPLAY_RULES,
        }
        if not archive.get("ok", True):
            payload["warnings"] = [archive["error"]]
        print_json(payload)
        return
    if not archive.get("ok", True):
        print(f"⚠️ 评论已读取，但本地归档未更新: {archive['error']}")
    if not raw_groups:
        print("暂无评论通知")
        return
    for group in raw_groups:
        print(
            f"\n{group['note_index']}. {group['note_title']}"
            f"（{group['note_id']}）"
        )
        print(f"{'序号':<6} {'时间':<18} {'用户':<18} {'状态':<10} 评论")
        for index, item in enumerate(group["comments"], start=1):
            print(
                f"{index:<6} {item['time'][5:]:<18} "
                f"{item['nickname'][:16]:<18} {item['status']:<10} "
                f"{item['content']}"
            )
