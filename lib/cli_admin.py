"""发布、分析、环境检查、跳过列表和 AI 协议命令。"""

import json
import os
import shutil
import sys

from config import WORK_DIR
from . import poster
from .analyzer import CommentAnalyzer
from .cli_support import (
    call_for_output,
    compact_analysis,
    print_json,
    workflow_paths,
)
from .xhs_client import XHSClient


def cmd_skipped(args):
    client = XHSClient()
    if args.remove:
        print(
            f"✅ 已从跳过列表移除: {args.remove}"
            if client.remove_skipped(args.remove)
            else f"⚠️ 未找到: {args.remove}"
        )
        return
    skipped = client.load_skipped()
    if not skipped:
        print("📭 跳过列表为空")
        return
    if args.clear:
        print(f"⚠️ 即将清空 {len(skipped)} 条跳过记录")
        if input("确认? (y/n): ").strip().lower() == "y":
            client.save_skipped({})
            print("✅ 已清空")
        else:
            print("❌ 已取消")
        return
    print(f"\n{'='*60}\n📋 跳过列表（共 {len(skipped)} 条）\n{'='*60}")
    print(f"{'#':<4} {'时间':<20} {'原因':<14} {'用户':<16} 内容")
    for index, info in enumerate(skipped.values(), start=1):
        print(
            f"{index:<4} {info.get('skipped_at',''):<20} "
            f"{info.get('reason',''):<14} {info.get('nickname',''):<16} "
            f"{info.get('content','')[:40]}"
        )
    print("\n💡 移除: python3 main.py skipped --remove <comment_id>")
    print("💡 清空: python3 main.py skipped --clear")


def cmd_post(args):
    payload = {}
    if args.input:
        try:
            with open(args.input, encoding="utf-8") as file:
                payload = json.load(file)
        except (OSError, json.JSONDecodeError) as error:
            print(f"❌ 无法读取笔记 JSON: {error}")
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
    topics = (
        [item.strip() for item in args.topics.split(",") if item.strip()]
        if args.topics else payload.get("topics")
    )
    try:
        ok = poster.publish(
            title, body, images, topics,
            private=args.private or bool(payload.get("private", False)),
            dry_run=args.dry_run,
        )
        if not ok:
            print("⚠️ 发布可能未成功，请检查小红书客户端状态")
    except Exception as error:
        print(f"❌ 发布失败: {error}")


def cmd_analyze(args):
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
    from config import AUTHOR_USER_ID, LOGIN_COOKIE_SOURCE

    xhs_path = shutil.which("xhs") or ""
    checks = {
        "python": {"ok": True, "value": sys.version.split()[0]},
        "xhs": {"ok": bool(xhs_path), "value": xhs_path},
        "author_user_id": {
            "ok": bool(AUTHOR_USER_ID and "你的小红书" not in AUTHOR_USER_ID),
            "value": AUTHOR_USER_ID,
        },
        "cookie_source": {
            "ok": bool(LOGIN_COOKIE_SOURCE), "value": LOGIN_COOKIE_SOURCE
        },
        "cache_writable": {
            "ok": os.access(os.path.dirname(os.path.abspath(WORK_DIR)), os.W_OK),
            "value": os.path.abspath(WORK_DIR),
        },
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
    print_json({
        "schema_version": "3",
        "language": "zh-CN",
        "safety": {
            "scan_is_read_only": True,
            "draft_is_local_only": True,
            "send_requires_user_review": True,
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
            "view_articles": [
                "python3 main.py articles --limit 10 --json",
                "按 columns 和 index 展示，必须显示标题",
            ],
            "view_comments": [
                "python3 main.py comments --limit 20 --json",
                "按 columns 和 groups[].note_index 分组展示评论",
                "面向用户的表格不得显示 comment_id",
                "查看流程到此结束，不运行 scan、drafts 或 send",
            ],
            "reply": [
                "python3 main.py paths --note-id <id>",
                "读取已有 drafts.json 和 .cache/skipped.json",
                "python3 main.py scan --note-id <id> --json",
                "scan 已过滤删除、跳过、本地终态并在线核验平台回复",
                "仅为过滤后的评论生成 reply_map.json",
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
    })


def cmd_paths(args):
    paths = workflow_paths(args.note_id)
    files = {
        key: {"path": value, "exists": os.path.exists(value)}
        for key, value in paths.items() if key != "directory"
    }
    print_json({
        "ok": True,
        "note_id": args.note_id,
        "directory": paths["directory"],
        "files": files,
        "post_note": os.path.abspath(os.path.join(WORK_DIR, "post", "note.json")),
    })
