"""发布、分析、环境检查、跳过列表和路径命令兼容门面。"""

import json
import os
import re
import shutil
import subprocess
import sys

from config import COMMENTS_FILE, STATE_DB_FILE, WORK_DIR
from . import poster
from .analyzer import CommentAnalyzer
from .cli_support import (
    call_for_output,
    compact_analysis,
    print_json,
    workflow_paths,
)
from .xhs_client import XHSClient
from .cli_protocol import cmd_ai_help
from .cli_release import _release_consistency
from .state_io import (
    json_state_exists,
    migrate_legacy_json,
    read_json_state,
)
from .state_db import DB_SCHEMA_VERSION, StateDBError, state_db


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
            f"{info.get('content','')}"
        )
    print("\n💡 移除: python3 main.py skipped --remove <comment_id>")
    print("💡 清空: python3 main.py skipped --clear")


def cmd_delete(args):
    result = poster.delete_note(args.note_id, confirmed=args.confirmed, dry_run=args.dry_run)
    print_json(result)
    if not result["ok"]:
        raise SystemExit(1)


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
    from config import LOGIN_COOKIE_SOURCE, XHS_CLI_VERSION

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    release = _release_consistency(project_root)
    try:
        migration = migrate_legacy_json()
        current_db_schema = state_db().get_setting(
            "db_schema_version", ""
        )
    except StateDBError as error:
        # doctor必须在状态库损坏、锁超时或版本过高时仍给出结构化修复信息，
        # 不能让原生SQLite异常中断整份诊断报告。
        migration = {
            "imported": 0,
            "deleted": 0,
            "skipped": 0,
            "errors": [str(error)],
        }
        current_db_schema = ""
    xhs_path = shutil.which("xhs") or ""
    xhs_version = ""
    if xhs_path:
        try:
            version_result = subprocess.run(
                [xhs_path, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            version_text = (
                version_result.stdout or version_result.stderr or ""
            )
            version_match = re.search(
                r"\bversion\s+([0-9]+(?:\.[0-9]+)+)", version_text
            )
            xhs_version = version_match.group(1) if version_match else ""
        except (OSError, subprocess.TimeoutExpired):
            pass
    identity_error = ""
    if not xhs_path:
        author_user_id = ""
        identity_error = "未安装xhs，无法自动识别账号"
    else:
        try:
            author_user_id = XHSClient.get_author_user_id()
        except (OSError, RuntimeError) as error:
            author_user_id = ""
            identity_error = str(error)

    checks = {
        "python": {"ok": True, "value": sys.version.split()[0]},
        "xhs": {
            "ok": bool(xhs_path) and xhs_version == XHS_CLI_VERSION,
            "value": xhs_path,
            "version": xhs_version,
            "expected_version": XHS_CLI_VERSION,
            "next": (
                "安装 requirements.txt 中固定的xiaohongshu-cli版本"
                if xhs_path and xhs_version != XHS_CLI_VERSION else ""
            ),
        },
        "author_user_id": {
            "ok": bool(author_user_id),
            "value": "已自动识别" if author_user_id else identity_error,
            "storage": STATE_DB_FILE,
        },
        "cookie_source": {
            "ok": bool(LOGIN_COOKIE_SOURCE), "value": LOGIN_COOKIE_SOURCE
        },
        "cache_writable": {
            "ok": os.access(os.path.dirname(os.path.abspath(WORK_DIR)), os.W_OK),
            "value": os.path.abspath(WORK_DIR),
        },
        "sqlite_state": {
            "ok": (
                not migration["errors"]
                and current_db_schema == str(DB_SCHEMA_VERSION)
            ),
            "value": STATE_DB_FILE,
            "schema_version": current_db_schema,
            "expected_schema_version": DB_SCHEMA_VERSION,
            "legacy_imported": migration["imported"],
            "legacy_deleted": migration["deleted"],
            "legacy_skipped": migration["skipped"],
            "migration_errors": migration["errors"],
        },
        "documentation_versions": {
            "ok": release["versions"]["ok"],
            "value": (
                f"{release['versions']['app_version']} / schema "
                f"{release['versions']['schema_version']}"
            ),
            "errors": release["versions"]["errors"],
        },
        "public_identity_privacy": {
            "ok": release["public_identity"]["ok"],
            "value": (
                "未发现公开固定账号ID"
                if release["public_identity"]["ok"]
                else "发现公开固定账号ID"
            ),
            "findings": release["public_identity"]["findings"],
        },
        "repository_release": {
            "ok": release["repository"]["ok"],
            "value": release["repository"]["status"],
            "details": release["repository"],
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


def cmd_paths(args):
    paths = workflow_paths(args.note_id)
    audit_limit = max(0, min(int(getattr(args, "audit_limit", 0) or 0), 100))
    files = {
        key: {"path": value, "exists": json_state_exists(value)}
        for key, value in paths.items() if key != "directory"
    }
    workflow_state = {
        "active_count": 0,
        "active_batch": None,
        "legacy_requires_redraft": False,
    }
    workflow_audit = {
        "path": paths.get(
            "audit", os.path.join(paths["directory"], "audit.json")
        ),
        "exists": False,
        "events_total": 0,
        "events_returned": 0,
        "events": [],
        "events_truncated": False,
    }
    if json_state_exists(paths["drafts"]):
        try:
            drafts = read_json_state(paths["drafts"])
            if isinstance(drafts, dict):
                active_ids = drafts.get("active_comment_ids", [])
                workflow_state["active_count"] = (
                    len(active_ids) if isinstance(active_ids, list) else 0
                )
                active_batch = drafts.get("active_batch")
                if isinstance(active_batch, dict):
                    workflow_state["active_batch"] = {
                        key: active_batch.get(key)
                        for key in (
                            "batch_id", "revision", "preview_hash", "status",
                            "created_at", "completed_at",
                        )
                        if active_batch.get(key) is not None
                    }
                elif workflow_state["active_count"]:
                    workflow_state["legacy_requires_redraft"] = True
        except (OSError, json.JSONDecodeError):
            workflow_state["state_error"] = "drafts.json无法解析"
    audit_path = workflow_audit["path"]
    if json_state_exists(audit_path):
        workflow_audit["exists"] = True
        try:
            audit = read_json_state(audit_path)
            events = audit.get("events", []) if isinstance(audit, dict) else []
            if not isinstance(events, list):
                raise ValueError("events不是数组")
            workflow_audit.update({
                "schema_version": audit.get("schema_version"),
                "current_workflow_id": audit.get("current_workflow_id", ""),
                "retained_limit": audit.get("retained_limit"),
                "events_total": len(events),
                "events_returned": min(len(events), audit_limit),
                "events": events[-audit_limit:] if audit_limit else [],
                "events_truncated": len(events) > audit_limit,
            })
        except (OSError, json.JSONDecodeError, ValueError):
            workflow_audit["state_error"] = "audit.json无法解析"
    print_json({
        "ok": True,
        "note_id": args.note_id,
        "state": {
            "backend": "sqlite",
            "database": os.path.abspath(STATE_DB_FILE),
            "database_exists": os.path.exists(STATE_DB_FILE),
            "json_role": "AI交换入口或兼容快照，SQLite为权威状态源",
        },
        "directory": paths["directory"],
        "files": files,
        "comment_archive": {
            "path": os.path.abspath(COMMENTS_FILE),
            "exists": json_state_exists(COMMENTS_FILE),
        },
        "workflow_state": workflow_state,
        "workflow_audit": workflow_audit,
        "post_note": os.path.abspath(os.path.join(WORK_DIR, "post", "note.json")),
    })
