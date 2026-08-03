"""发布、分析、环境检查、跳过列表和 AI 协议命令。"""

import ast
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
    COMMENT_DISPLAY_RULES,
    compact_analysis,
    print_json,
    TERMINAL_SEND_STATUSES,
    workflow_paths,
)
from .xhs_client import XHSClient
from .cli_parser import COMMAND_NAMES, build_command_contract
from .state_io import (
    json_state_exists,
    migrate_legacy_json,
    read_json_state,
)


_FIXED_AUTHOR_ID_PATTERN = re.compile(
    r"(?i)(?:AUTHOR_USER_ID|author_user_id|user_id)"
    r"[^\n]{0,80}?[\"']([0-9a-f]{24})[\"']"
)


def _extract_release_values(config_source):
    app_match = re.search(
        r'^APP_VERSION\s*=\s*[\"\']([^\"\']+)[\"\']',
        config_source,
        flags=re.MULTILINE,
    )
    schema_match = re.search(
        r'^AI_SCHEMA_VERSION\s*=\s*[\"\']([^\"\']+)[\"\']',
        config_source,
        flags=re.MULTILINE,
    )
    return (
        app_match.group(1) if app_match else "",
        schema_match.group(1) if schema_match else "",
    )


def _release_consistency(project_root):
    """检查当前文档、公开账号信息和Git发布快照是否一致。"""
    from config import AI_SCHEMA_VERSION, APP_VERSION

    expectations = {
        "README.md": (
            f"# 小红书AI智能运营系统 v{APP_VERSION}",
            f"schema_version: {AI_SCHEMA_VERSION}",
        ),
        "AGENTS.md": (
            f"发布版本为`{APP_VERSION}`",
            f"schema`{AI_SCHEMA_VERSION}`",
        ),
        "SKILL.md": (
            f"发布为`{APP_VERSION}`",
            f"schema为`{AI_SCHEMA_VERSION}`",
        ),
        "references/commands.md": (
            f"应用版本为 `{APP_VERSION}`",
            f"协议版本为`{AI_SCHEMA_VERSION}`",
        ),
    }
    version_errors = []
    identity_findings = []
    public_files = []
    for relative_path in expectations:
        absolute = os.path.join(project_root, relative_path)
        try:
            with open(absolute, encoding="utf-8") as file:
                source = file.read()
        except OSError as error:
            version_errors.append(f"{relative_path}: {error}")
            continue
        public_files.append((relative_path, source))
        for expected in expectations[relative_path]:
            if expected not in source:
                version_errors.append(
                    f"{relative_path}缺少当前声明: {expected}"
                )

    source_candidates = ["config.py", "main.py"]
    lib_dir = os.path.join(project_root, "lib")
    if os.path.isdir(lib_dir):
        source_candidates.extend(
            f"lib/{name}" for name in sorted(os.listdir(lib_dir))
            if name.endswith(".py")
        )
    source_candidates.append("tests/test_compact_output.py")
    for relative_path in source_candidates:
        absolute = os.path.join(project_root, relative_path)
        try:
            with open(absolute, encoding="utf-8") as file:
                source = file.read()
        except OSError:
            continue
        public_files.append((relative_path, source))
    for relative_path, source in public_files:
        for match in _FIXED_AUTHOR_ID_PATTERN.finditer(source):
            identity_findings.append({
                "file": relative_path,
                "line": source.count("\n", 0, match.start()) + 1,
                "rule": "固定24位账号ID",
            })
    config_source = next(
        (source for path, source in public_files if path == "config.py"), ""
    )
    if re.search(r"^AUTHOR_USER_ID\s*=", config_source, re.MULTILINE):
        identity_findings.append({
            "file": "config.py",
            "line": 0,
            "rule": "公开配置包含AUTHOR_USER_ID赋值",
        })

    repository = {
        "available": False,
        "ok": True,
        "status": "not_git_checkout",
    }
    if os.path.isdir(os.path.join(project_root, ".git")):
        result = subprocess.run(
            ["git", "show", "HEAD:config.py"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            head_app, head_schema = _extract_release_values(result.stdout)
            head_has_fixed_id = bool(
                re.search(r"^AUTHOR_USER_ID\s*=", result.stdout, re.MULTILINE)
                or _FIXED_AUTHOR_ID_PATTERN.search(result.stdout)
            )
            repository = {
                "available": True,
                "ok": (
                    head_app == APP_VERSION
                    and head_schema == AI_SCHEMA_VERSION
                    and not head_has_fixed_id
                ),
                "status": "synced" if (
                    head_app == APP_VERSION
                    and head_schema == AI_SCHEMA_VERSION
                    and not head_has_fixed_id
                ) else "working_tree_not_published",
                "head_app_version": head_app,
                "head_schema_version": head_schema,
                "head_contains_fixed_author_id": head_has_fixed_id,
                "working_app_version": APP_VERSION,
                "working_schema_version": AI_SCHEMA_VERSION,
                "next": (
                    "提交并推送当前修复后，GitHub才会显示新版本"
                    if head_app != APP_VERSION
                    or head_schema != AI_SCHEMA_VERSION
                    or head_has_fixed_id else ""
                ),
            }
    return {
        "versions": {
            "ok": not version_errors,
            "app_version": APP_VERSION,
            "schema_version": AI_SCHEMA_VERSION,
            "errors": version_errors,
        },
        "public_identity": {
            "ok": not identity_findings,
            "findings": identity_findings,
            "identity_source": "xhs whoami --json → 本地SQLite metadata",
        },
        "repository": repository,
    }


def _build_test_inventory(project_root, test_files):
    """从当前测试源码提取unittest方法；只描述当前快照，不推断历史变化。"""
    tests = []
    for relative_path in test_files:
        absolute_path = os.path.join(project_root, relative_path)
        try:
            with open(absolute_path, encoding="utf-8") as file:
                tree = ast.parse(file.read(), filename=relative_path)
        except (OSError, SyntaxError):
            continue
        module_name = os.path.splitext(os.path.basename(relative_path))[0]
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            for item in node.body:
                if (
                    isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and item.name.startswith("test_")
                ):
                    tests.append(f"{module_name}.{node.name}.{item.name}")
    return {
        "count": len(tests),
        "files": test_files,
        "tests": tests,
        "scope": "当前工作区快照中的测试方法",
        "history_rule": (
            "此清单不能证明哪些测试是新增、删除或修改；比较历史必须有"
            "明确的旧版本快照或版本控制差异"
        ),
    }


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
    from config import LOGIN_COOKIE_SOURCE

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    release = _release_consistency(project_root)
    migration = migrate_legacy_json()
    xhs_path = shutil.which("xhs") or ""
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
        "xhs": {"ok": bool(xhs_path), "value": xhs_path},
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
            "ok": not migration["errors"],
            "value": STATE_DB_FILE,
            "legacy_imported": migration["imported"],
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


def cmd_ai_help(args):
    from config import (
        AI_SCHEMA_VERSION,
        APP_VERSION,
        BATCH_REPLY_DELAY,
        BATCH_REPLY_PAUSE_EVERY,
        BATCH_REPLY_PAUSE_SECONDS,
        CACHE_TTL_MINUTES,
        LOGIN_COOKIE_SOURCE,
        READ_PAGE_DELAY,
        REQUEST_DELAY,
        SYSTEM_NAME,
    )
    requested_command = getattr(args, "command_name", None)
    if requested_command:
        print_json({
            "app_name": SYSTEM_NAME,
            "app_version": APP_VERSION,
            "schema_version": AI_SCHEMA_VERSION,
            "command_contract": build_command_contract(requested_command),
        })
        return
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    source_files = ["config.py", "main.py"]
    lib_dir = os.path.join(project_root, "lib")
    if os.path.isdir(lib_dir):
        source_files.extend(
            f"lib/{name}" for name in sorted(os.listdir(lib_dir))
            if name.endswith(".py")
            and os.path.isfile(os.path.join(lib_dir, name))
        )
    test_files = [
        "tests/test_compact_output.py"
    ] if os.path.isfile(
        os.path.join(project_root, "tests", "test_compact_output.py")
    ) else []
    test_inventory = _build_test_inventory(project_root, test_files)
    release_consistency = _release_consistency(project_root)
    if getattr(args, "tests", False):
        print_json({
            "app_name": SYSTEM_NAME,
            "app_version": APP_VERSION,
            "schema_version": AI_SCHEMA_VERSION,
            "test_inventory": test_inventory,
        })
        return
    documentation_files = [
        path for path in (
            "AGENTS.md",
            "README.md",
            "SKILL.md",
            "references/commands.md",
        )
        if os.path.isfile(os.path.join(project_root, path))
    ]
    project_files = source_files + test_files + documentation_files

    payload = {
        "app_name": SYSTEM_NAME,
        "system_name": SYSTEM_NAME,
        "app_version": APP_VERSION,
        "schema_version": AI_SCHEMA_VERSION,
        "release_consistency": release_consistency,
        "command_count": len(COMMAND_NAMES),
        "commands": list(COMMAND_NAMES),
        "command_consistency": {
            "parser_vs_manifest": "build_parser运行时检查",
            "handlers_vs_manifest": "main.COMMAND_HANDLERS运行时检查",
        },
        "language": "zh-CN",
        "agent_guide": "AGENTS.md",
        "command_discovery": {
            "summary": "python3 main.py ai-help --summary",
            "exact_command": (
                "python3 main.py ai-help --command <command>"
            ),
            "current_tests": "python3 main.py ai-help --tests",
            "full_protocol": "python3 main.py ai-help",
            "rule": "命令参数和副作用以运行时协议为准，不手工抄写",
        },
        "source_inventory": {
            "count": len(source_files),
            "files": source_files,
            "rule": "运行时生成；不要手工推测文件数或行数",
        },
        "project_inventory": {
            "count": len(project_files),
            "production_python_count": len(source_files),
            "tests": test_files,
            "documentation": documentation_files,
            "rule": "不含.cache、软著材料及外部附件",
        },
        "architecture": {
            "AGENTS.md": "其他AI首先读取的最短执行协议",
            "main.py": "COMMAND_HANDLERS命令分发及传统回复工作流编排",
            "config.py": "应用名称、版本、浏览器、延迟和工作目录配置",
            "lib/cli_parser.py": "唯一命令清单和参数定义",
            "lib/cli_view.py": "登录、文章、评论查看",
            "lib/cli_admin.py": "管理、发布、分析和机器协议",
            "lib/cli_ai.py": "AI回复兼容门面、审计调度和命令入口",
            "lib/cli_ai_prepare.py": "候选准备、扫描和旧批次停用",
            "lib/cli_ai_draft.py": "映射校验、在线复核和草稿生成",
            "lib/cli_ai_send.py": "确认绑定、在线对账和回复发送",
            "lib/cli_ai_support.py": "回复映射校验、展示转换和错误结构",
            "lib/cli_ai_audit.py": "脱敏审计和发送尝试记录",
            "lib/cli_support.py": "存储、状态合并和精简输出",
            "lib/state_db.py": "SQLite权威状态库、账号身份和旧JSON迁移",
            "lib/state_io.py": "跨进程锁、SQLite读写和JSON兼容快照",
            "lib/scanner.py": "最新评论/全量扫描和在线核验",
            "lib/replier.py": "草稿生成、发送和失败排除",
            "lib/analyzer.py": "评论统计与摘要分析",
            "lib/poster.py": "图文笔记校验、预览和发布",
            "lib/xhs_client.py": "XHSClient兼容门面、账号和回复接口",
            "lib/xhs_client_content.py": "笔记、通知和私有令牌索引",
            "lib/xhs_client_comments.py": "评论树、楼中楼和在线查询",
            "lib/xhs_client_state.py": "评论缓存和排除状态",
            "lib/xhs_client_proxy.py": "拆分模块访问兼容门面的延迟绑定层",
            "lib/xhs_comments_helper.py": "单会话评论分页和候选楼层补全加速层",
            "lib/xhs_reply_helper.py": "单会话批量回复与逐条结果回传加速层",
            "lib/xhs_subcomments_helper.py": "楼中楼xsec_token兼容层",
            "tests/test_compact_output.py": (
                "自动化回归测试；数量以实际运行结果为准"
            ),
        },
        "defaults": {
            "articles": 10,
            "comments_notifications": 20,
            "reply_notifications": 20,
            "limit_flags_optional": True,
            "reply_scope": "latest",
            "full_history_requires": "--full-scan",
            "cache_ttl_minutes": CACHE_TTL_MINUTES,
            "request_delay_seconds": REQUEST_DELAY,
            "batch_reply_delay_seconds": BATCH_REPLY_DELAY,
            "batch_reply_pause_every": BATCH_REPLY_PAUSE_EVERY,
            "batch_reply_pause_seconds": BATCH_REPLY_PAUSE_SECONDS,
            "read_page_delay_seconds": READ_PAGE_DELAY,
            "login_cookie_source": LOGIN_COOKIE_SOURCE,
        },
        "output_contract": {
            "ai_reply": "始终为单一紧凑JSON",
            "json_capable_commands": [
                "articles", "comments", "scan", "analyze", "doctor",
                "ai-help", "paths", "ai-reply",
            ],
            "draft_preview": (
                "prepare/draft/send明细最多内联20行，完整数据读取返回的"
                "*_source状态文件；preview含send/skip/archive，"
                "由AI按columns转换为Markdown表格"
            ),
            "send_results": (
                "results是本次活动批次的JSON数组，按columns展示"
            ),
            "draft_confirmation": (
                "draft返回batch_id、revision、preview_hash；send必须原样"
                "提交batch_id和preview_hash，防止确认后内容被替换"
            ),
            "scan_candidates": "仅为候选，不能直接发送",
            "article_list": (
                "严格按columns和column_fields展示全部列，笔记ID对应note_id；"
                "大列表按pagination.next_command读取缓存后续页，直到has_more=false"
            ),
            "comment_list": (
                "comments返回的groups已经安全转义、加入自适应软换行并移除"
                "comment_id，可直接按display.column_fields展示；正文不得截断、"
                "摘要或使用省略号，也不得二次转义或再次插入wbr。通知接口已返回"
                "且程序未截断的原文累计原子写入archive.path"
            ),
            "comment_archive_completeness": {
                "content_complete_scope": "notification_payload",
                "content_untruncated_locally": True,
                "platform_tree_verified": False,
                "meaning": (
                    "只保证comments读取到的通知正文未被本地截断；不代表"
                    "超出limit读取了全部历史通知或核对了平台完整评论树"
                ),
                "failure_behavior": (
                    "旧归档无法解析时不覆盖；groups仍返回且archive.ok=false，"
                    "顶层warnings提示归档未更新"
                ),
            },
            "comment_display": COMMENT_DISPLAY_RULES,
            "prepare_metadata": (
                "scan_method明确实际扫描入口；verification_mode为"
                "candidate_online_recheck或full_tree_scan，禁止据scope猜测"
            ),
            "candidate_context": (
                "通知楼中楼候选保留target_comment_id供内部定位；"
                "面向用户展示时不显示该内部ID"
            ),
            "reply_map_schema": {
                "<comment_id>": {
                    "reply": "非空字符串（action=send时必填）",
                    "action": "send|skip|archive",
                    "review": {
                        "logic": {
                            "verdict": (
                                "sound|partly_sound|weak|fallacious|"
                                "non_argument|unclear"
                            ),
                            "reason": "逻辑结构与依据",
                        },
                        "fact_check": {
                            "verdict": (
                                "supported|mixed|contradicted|unverifiable|"
                                "not_applicable"
                            ),
                            "reason": "事实核查说明",
                            "sources": [{
                                "title": "来源名称", "url": "https://...",
                            }],
                        },
                        "boast_check": {
                            "verdict": (
                                "none|possible|likely|unverifiable|"
                                "not_applicable"
                            ),
                            "reason": "吹牛判定依据",
                        },
                    },
                },
            },
            "reply_map_review_required": (
                "ai-reply要求每条本次候选都有对象映射和review；缺少映射或"
                "字符串简写均在联网前拒绝"
            ),
            "fact_check_sources": (
                "supported、mixed、contradicted至少需要一个含http(s) URL的"
                "可核对来源；unverifiable或not_applicable可为空数组"
            ),
            "reply_map_json_error": (
                "返回error_type=invalid_reply_map_json、"
                "automatic_retry=false、requires_file_fix=true；"
                "error_location给出行列；reply/review正文默认使用中文引号，"
                "JSON结构引号不得替换，正文英文双引号必须转义；独立"
                "reply/reason行的成对未转义引号仅在整文件修复后有效时自动处理"
            ),
            "reply_map_mapping_error": (
                "JSON语法正确但action、reply或review不合法时返回"
                "error_type=invalid_reply_map_mapping；语法、顶层类型、"
                "本次候选映射和审查语义都在在线复核前检查"
            ),
            "duplicate_send_error": (
                "同一用户、标准化后相同正文有多条action=send时返回"
                "error_type=duplicate_send_mapping；只保留一条send，"
                "其余改为skip后再draft"
            ),
            "reply_actions": {
                "send": "用户确认后向平台发送回复",
                "skip": (
                    "reply可为空；仅跳过本次活动批次，不写入全局排除列表"
                ),
                "archive": (
                    "reply可为空；用户确认执行send动作后写入全局排除列表，"
                    "但不向平台发送回复"
                ),
            },
        },
        "safety": {
            "scan_platform_read_only": True,
            "scan_writes_local_state": True,
            "draft_is_local_only": True,
            "ai_reply_send_requires_confirmed": True,
            "ai_reply_confirmation_binding": (
                "send同时强制batch_id和preview_hash；任一不匹配即拒绝"
            ),
            "traditional_send_confirmation": (
                "调用方必须先取得用户确认；--confirm可启用终端二次询问"
            ),
            "post_dry_run_recommended": True,
            "traditional_send_online_recheck": True,
            "reply_error_types": list(XHSClient.REPLY_ERROR_TYPES),
        },
        "verification": {
            "tests": "python3 -m unittest discover -s tests",
            "syntax": "python3 -m py_compile main.py config.py lib/*.py",
            "test_count": "以本次命令运行结果为准，不在协议中硬编码",
        },
        "test_inventory": {
            "count": test_inventory["count"],
            "files": test_inventory["files"],
            "command": "python3 main.py ai-help --tests",
            "history_rule": test_inventory["history_rule"],
        },
        "online_verification_stages": {
            "model": (
                "条件式多阶段核验；不得简写为每个动作都无条件执行的"
                "固定三次核验"
            ),
            "preferred_term": "条件式多阶段在线核验",
            "prepare": (
                "通知模式显式传verify_replied=true，有候选时调用"
                "verify_candidates_online；在线核验前先排除本地终态，"
                "评论分页和候选楼层补全在单一登录会话中完成，找到候选后"
                "立即停止；默认最多用6页快速定位预算，仍未定位的深层"
                "楼中楼计入deferred_count且不进入本批；全量模式由scan_note以"
                "include_sub_comments=true、force_refresh=true完整扫描，"
                "绕过评论TTL缓存且不调用该核验函数"
            ),
            "draft": (
                "先停用旧active_comment_ids但保留历史草稿和终态，"
                "先校验reply_map的JSON语法、顶层类型和本次候选映射语义，"
                "通过后才在生成本批草稿前在线核验；成功后生成batch_id、"
                "revision和preview_hash"
            ),
            "send": (
                "先校验batch_id和preview_hash，再对action=send待发送项核验；"
                "请求/数据不完整时硬停止且不归档，online_replied/"
                "online_missing才标记archived；批量发送复用一个登录会话并"
                "逐条保存结果，账号级错误会暂停剩余批次"
            ),
            "skip_or_archive": "不向平台发送回复，因此不执行发送阶段核验",
            "incomplete_sub_comments": (
                "候选所在楼层补拉后仍少于sub_comment_count时硬停止；"
                "候选尚未定位时按需搜索其他不完整楼层，最终仍未定位且"
                "存在拉取失败时硬停止"
            ),
            "expansion_scope": (
                "候选已定位时只严格展开候选所在楼层；候选尚未定位时才"
                "先使用通知中的target_comment_id定位楼层，再按需搜索其他"
                "不完整楼层。候选最终在完整楼层中找到后，确定无关楼层的"
                "拉取失败不再阻断整批"
            ),
            "sub_comment_transport": (
                "有xsec_token时优先兼容helper；普通helper故障才回退原生CLI，"
                "verification_required/captcha立即停止，禁止用第二种传输重复"
                "请求；回复核验使用strict模式保留真实失败原因"
            ),
            "verification_required": (
                "返回error_type=verification_required、automatic_retry=false、"
                "requires_user_action=true；AI必须停止自动重试。若type和uuid"
                "均为unknown，这是没有可见挑战信息的API风控，浏览器页面"
                "正常也可能发生；重新导入Firefox Cookie后重跑当前action，"
                "仍失败则等待风控解除"
            ),
        },
        "storage": {
            "canonical": (
                ".cache/state.sqlite3（0600，SQLite权威状态源；账号身份、"
                "工作流、评论归档、排除列表、缓存和令牌索引统一存储）"
            ),
            "migration": (
                "doctor首次运行幂等导入旧.cache/**/*.json；旧文件保留为"
                "可恢复兼容快照，不再作为程序判断的权威来源"
            ),
            "workflow": (
                ".cache/workflows/<note_id>/"
                "{scan,reply_map,drafts,audit}.json（AI交换/兼容快照）"
            ),
            "workflow_audit": (
                "audit.json（0600，最多500条命令事件；不含评论正文、"
                "回复正文和凭据；paths --audit-limit按需内联）"
            ),
            "audit_evidence_rule": (
                "先按workflow_id限定同一轮prepare/draft/send，再按command_id"
                "配对started与completed/failed；新prepare生成新workflow_id；"
                "旧事件无编号时不得跨prepare拼接；没有审计事件时"
                "只能报告当前状态，不得从最终状态反推历史命令或错误"
            ),
            "comment_archive": (
                ".cache/comments.json（0600，累计保存通知接口已返回且程序"
                "未截断的原始正文；不代表完整评论树核验）"
            ),
            "global_exclusions": "SQLite文档skipped.json；同名JSON为兼容快照",
            "sensitive_token_cache": (
                "SQLite文档xsec_index.json（数据库0600，禁止展示）；"
                "同名JSON为兼容快照"
            ),
            "account_identity": (
                "首次运行通过xhs whoami --json自动识别，保存到SQLite metadata；"
                "公开仓库不含用户ID"
            ),
            "notification_token_handoff": (
                "通知中的xsec_token不写入scan.json，而是立即保存到0600"
                "敏感索引，供后续draft/send接续"
            ),
            "workflow_lock": (
                "同一note_id的ai-reply动作由.workflow.lock跨进程串行化；"
                "冲突返回error_type=workflow_busy"
            ),
        },
        "reply_decision": {
            "warning": "scan.json 的 unreplied_* 只是平台候选，不是最终待回复清单",
            "required_files": [
                ".cache/workflows/<id>/scan.json",
                ".cache/workflows/<id>/drafts.json",
                ".cache/workflows/<id>/reply_map.json",
                ".cache/skipped.json",
            ],
            "execution_evidence": (
                ".cache/workflows/<id>/audit.json；用于报告命令过程，"
                "不参与候选资格判定"
            ),
            "key": "comment_id",
            "terminal_statuses": list(TERMINAL_SEND_STATUSES),
            "status_precedence": [
                "drafts.send_status=sent",
                "drafts.send_status=failed",
                "drafts.send_status=archived 或 skipped.json",
                "drafts.send_status=sending（结果不确定，禁止自动重发）",
                "平台已回复或评论已删除",
                "scan.json 候选（仅进入后续资格判断，不代表可回复）",
            ],
            "eligible_when_all": [
                "scan.reply_status_verified=true",
                "comment_id 在本次 unreplied_level1 或 unreplied_subs",
                "drafts 中同 comment_id 不为 sent、failed、archived、sending",
                "comment_id 不在 skipped.json",
                "评论未删除",
                "draft前在线复核通过；action=send时发送前再次在线复核通过",
            ],
            "empty_result": "报告没有可回复评论；禁止复用旧 reply_map.json",
            "failed_handling": "所有失败评论自动加入 skipped.json；默认不重试",
            "failed_retry": (
                "failed是草稿终态；重试需用户明确授权，同时移出"
                "skipped.json并重置drafts.json中的failed状态"
            ),
            "incomplete_online_data": "立即停止；禁止使用内联数据猜测回复状态",
            "active_batch": (
                "prepare和draft都会停用上一批次但保留历史草稿；"
                "draft成功后写入active_comment_ids和active_batch；send必须"
                "提交相同batch_id与preview_hash，只处理该批次"
            ),
            "inflight_safety": (
                "每条平台写请求前先持久化send_status=sending；进程中断后"
                "先在线对账，仍无法确定时返回uncertain_send_state且不重发"
            ),
            "duplicate_policy": (
                "同一用户的相同评论最多回复一次；程序在联网前拒绝"
                "重复send映射"
            ),
            "pre_reply_review": {
                "required_for": "本次每条候选，包括send、skip和archive",
                "dimensions": ["逻辑分析", "事实核查", "吹牛判定"],
                "program_role": "校验结构、枚举值和事实来源URL格式",
                "ai_role": "理解语境、完成推理、联网查证并给出审慎判定",
                "draft_output": (
                    "draft返回review_columns、review_column_fields和reviews；"
                    "reviews含中文label；先展示审查表，再展示preview草稿表"
                ),
            },
            "user_visible_ids": (
                "comment_id仅供AI写映射和程序定位；评论、草稿、执行说明"
                "面向用户展示时不得显示comment_id"
            ),
            "reply_quality": [
                "回复前逐条完成逻辑分析、事实核查和吹牛判定",
                "逻辑成立不代表事实为真；事实成立也不代表推理完整",
                "个人经历通常标记unverifiable，不得武断判为虚假或吹牛",
                "supported/mixed/contradicted必须提供可点击核对来源",
                "只有辱骂、贴标签且没有实质观点的评论默认skip",
                "不得编造来源、数据或使用无法核实的绝对化结论",
                "保持克制，不升级冲突；有实质观点时再生成针对性回复",
            ],
        },
        "workflows": {
            "view_articles": [
                "python3 main.py articles --json",
                "--limit可省略，默认10；读取平台时可能更新敏感xsec索引",
                "按columns和column_fields展示，必须显示标题和笔记ID",
                (
                    "若pagination.has_more=true，依次执行next_command读取本地"
                    "缓存直到false；不得用一条超长stdout或省略中间条目"
                ),
            ],
            "view_comments": [
                "python3 main.py comments --json",
                "--limit可省略，默认20",
                "groups已预先安全转义并加入软换行；直接按display.column_fields展示",
                "不得二次转义、再次插入wbr、截断、摘要或使用省略号",
                "原始通知正文累计保存到archive.path；其完整性范围不是平台评论树",
                "展示groups已移除comment_id，面向用户不得从归档补回该字段",
                "查看流程到此结束，不运行 scan、drafts 或 send",
            ],
            "reply": [
                "python3 main.py ai-reply --note-id <id> --action prepare",
                "prepare 默认只处理最新20条评论通知中的评论和楼中楼",
                "仅当用户明确要求全部历史评论时使用 --full-scan",
                "逐条完成逻辑、事实和吹牛审查后写入paths.reply_map",
                "python3 main.py ai-reply --note-id <id> --action draft",
                "向用户展示 preview 并取得明确确认",
                (
                    "python3 main.py ai-reply --note-id <id> --action send "
                    "--confirmed --batch-id <draft返回值> "
                    "--preview-hash <draft返回值>"
                ),
            ],
            "traditional_reply": {
                "purpose": "兼容人工操作和旧脚本；AI不优先使用",
                "steps": ["scan", "drafts", "send"],
                "draft_modes": ["逐条交互", "drafts --batch <reply_map>"],
                "output": "终端文本并非统一JSON",
                "send_online_recheck": True,
                "online_excluded": (
                    "online_replied/online_missing标记archived并写回草稿"
                ),
                "resume": (
                    "--resume仅改变提示文案；终态和排除过滤始终执行"
                ),
            },
            "post": [
                "创建 .cache/workflows/post/note.json",
                "python3 main.py post --input .cache/workflows/post/note.json --dry-run",
                "python3 main.py post --input .cache/workflows/post/note.json",
            ],
        },
        "common_misunderstandings": {
            "notification_entrypoint": (
                "默认prepare调用scan_via_notifications，不存在本流程所称的"
                "scanner.scan_notifications调用"
            ),
            "full_prepare_scope": (
                "--full-scan读取整篇笔记的全部评论树，不是拉取最新评论通知；"
                "它使用scan_note且不调用verify_candidates_online"
            ),
            "expansion_scope": (
                "禁止描述为展开本次查询返回的所有不完整楼层；候选已定位时"
                "只严格补全候选相关楼层，未定位时才按需搜索其他不完整楼层"
            ),
            "quote_error": (
                "JSON错误来自字符串内未转义的英文半角双引号，不是中文引号"
                "被解析器误判；只把AI生成的reply/review正文引号改成中文"
                "引号，JSON结构引号必须保留且禁止全文件替换；comments.json"
                "由程序用标准JSON生成，不得手工替换原评论"
            ),
            "captcha": (
                "验证码不是保证等待冷却后即可恢复的普通暂时错误；"
                "automatic_retry=false。只有存在可见挑战信息时才要求用户"
                "在Firefox完成验证；type/uuid均为unknown时浏览器可能完全正常"
            ),
            "browser_vs_api_verification": (
                "浏览器可正常浏览、whoami成功，不代表楼中楼API没有单独风控；"
                "不得仅凭浏览器正常就把verification_required判为误报"
            ),
            "notification_token_loss": (
                "通知prepare会把xsec_token安全写入0600索引后再从scan.json"
                "移除；不得只删除令牌却让draft退回无令牌楼中楼请求"
            ),
            "fixed_three_stages": (
                "禁止把流程概括成prepare、draft、send固定三次调用"
                "verify_candidates_online；全量prepare使用scan_note完整扫描"
            ),
            "hard_stop_state": (
                "硬停止不会把候选标记为archived或failed，但prepare/draft"
                "开始时仍会清空旧active_comment_ids；不能笼统称为完全不写状态"
            ),
            "history_merge": (
                "merge_draft_history保留全部旧条目；同ID非终态可被新草稿替换，"
                "同ID终态保留，旧批未再次出现的非终态也保留但不进入新活动批次"
            ),
            "terminal_retry": (
                "failed与sent、archived同为默认终态；failed重试必须有用户"
                "明确授权，并同时移出skipped和重置drafts中的failed状态"
            ),
            "workflow_sequence": (
                "新批次必须prepare→写映射→draft→用户确认→send；CLI依据"
                "状态文件校验，不证明这些命令刚在同一轮依次执行。已有合法"
                "active批次可在稍后继续send，不能把规则描述成物理上无法跳转"
            ),
            "online_excluded_storage": (
                "发送前确认online_replied/online_missing时写本地"
                "send_status=archived；只有用户映射action=archive才由"
                "send_drafts写入全局skipped.json"
            ),
            "mapping_validation_scope": (
                "只校验本次scan候选对应的映射；reply_map中其他批次的旧键"
                "允许保留，不参与本次校验或发送"
            ),
            "review_is_not_automatic_truth": (
                "程序只校验review结构、枚举和来源URL格式，不会自动证明评论"
                "真伪；事实查证和吹牛判定仍由AI完成，结论必须与证据强度匹配"
            ),
            "runtime_frequency": (
                "没有持续、可核对的运行日志统计时，禁止根据一两次执行声称"
                "某类错误最常见，或给出验证码与数据不一致的发生比例"
            ),
            "duplicate_send": (
                "同一用户、相同正文的多条候选不得全部send；程序返回"
                "duplicate_send_mapping，AI必须只保留一条send"
            ),
            "internal_comment_ids": (
                "comment_id是机器内部主键，允许出现在AI回复紧凑JSON、映射"
                "和原始归档；comments展示groups已主动移除该字段。任何面向"
                "用户的评论、草稿或流程追踪表格都不得显示comment_id"
            ),
            "reply_decision_quality": (
                "纯辱骂或贴标签且无实质观点时默认skip；不得编造数据、来源"
                "或绝对化事实，也不得用回复升级冲突"
            ),
            "send_scope": (
                "send处理当前active_comment_ids：发送send、持久化archive、"
                "忽略skip；在线复核针对send的pending项和需要对账的sending项"
            ),
            "confirmation_binding": (
                "--confirmed本身不够；send必须同时提交draft返回的batch_id和"
                "preview_hash。草稿被其他AI更新后旧确认立即失效"
            ),
            "inflight_retry": (
                "send_status=sending不是普通pending；它表示平台写结果可能"
                "不确定，程序必须在线对账，不能自动重复发送"
            ),
            "atomic_vs_locked": (
                "原子替换只防止半个JSON，不防止两个AI丢失更新；同笔记工作流"
                "还必须持有跨进程锁，xsec和skipped读改写也分别加锁"
            ),
        },
    }
    if getattr(args, "summary", False):
        print_json({
            "app_name": SYSTEM_NAME,
            "app_version": APP_VERSION,
            "schema_version": payload["schema_version"],
            "release_consistency": payload["release_consistency"],
            "command_count": payload["command_count"],
            "commands": payload["commands"],
            "entrypoints": {
                "agent_guide": "AGENTS.md",
                "command": "python3 main.py ai-help --command <command>",
                "full": "python3 main.py ai-help",
                "tests": "python3 main.py ai-help --tests",
            },
            "defaults": {
                "articles": payload["defaults"]["articles"],
                "articles_page_size": 20,
                "comments": payload["defaults"]["comments_notifications"],
                "reply_scope": payload["defaults"]["reply_scope"],
                "full_history_requires": "--full-scan",
            },
            "reply_workflow": {
                "prepare": (
                    "python3 main.py ai-reply --note-id <id> --action prepare"
                ),
                "draft": (
                    "python3 main.py ai-reply --note-id <id> --action draft"
                ),
                "send": (
                    "python3 main.py ai-reply --note-id <id> --action send "
                    "--confirmed --batch-id <batch_id> "
                    "--preview-hash <preview_hash>"
                ),
                "rule": (
                    "展示draft.preview并取得确认；发送时原样提交draft返回的"
                    "batch_id和preview_hash"
                ),
            },
            "critical_rules": [
                "文章大列表按pagination.next_command读完缓存页；笔记ID对应note_id",
                "scan候选不等于可回复，draft和send继续在线核验",
                "prepare快速预算内未定位的深层楼中楼计入deferred_count，不生成草稿",
                "同一笔记工作流由跨进程锁串行化；workflow_busy时等待",
                "旧批次或内容变化返回stale_preview，必须重新预览确认",
                "执行流程只按audit.json报告；无审计不得从最终状态倒推历史",
                "平台写入前先保存sending；uncertain_send_state禁止自动重发",
                "全量读取和批量发送复用单一登录会话；账号级错误暂停剩余批次",
                "楼中楼数据不完整、验证码或网络核验失败时硬停止",
                "send失败默认写入skipped；sent/failed/archived不自动重试",
                "评论ID和xsec_token不得显示给用户",
                "评论列表和草稿的原评论必须完整显示，不得截断",
                "每条候选先完成逻辑分析、事实核查和吹牛判定，再决定动作和回复",
                "明确事实真伪结论必须带可核对来源；个人经历不得武断判假",
            ],
            "state": {
                "database": ".cache/state.sqlite3（0600，权威状态源）",
                "directory": ".cache/workflows/<note_id>/",
                "files": [
                    "scan.json", "reply_map.json", "drafts.json", "audit.json",
                ],
                "compatibility": (
                    "上述JSON是AI交换入口或兼容快照；程序状态判断以SQLite为准"
                ),
                "audit": (
                    "0600有界事件日志；paths --audit-limit按需内联，完整保留窗口读取"
                    "workflow_audit.path"
                ),
                "comment_archive": (
                    ".cache/comments.json（0600，通知载荷原文未被本地截断；"
                    "不代表平台完整评论树）"
                ),
                "batch_fields": [
                    "batch_id", "revision", "preview_hash", "status",
                ],
                "sensitive_token": (
                    "SQLite敏感令牌索引（数据库0600）；xsec_index.json仅为"
                    "兼容快照"
                ),
            },
            "error_actions": {
                "workflow_busy": "等待当前进程结束后重试，不并行启动",
                "audit_unavailable": "审计未落盘，业务动作未执行；先修复审计文件",
                "verification_required": "停止自动重试，按next提示处理",
                "rate_limited": "当前失败项进入排除列表，剩余批次暂停",
                "not_authenticated": "重新登录后接续暂停批次",
                "session_error": "检查本地环境后接续暂停批次",
                "stale_preview": "重新draft、展示并确认",
                "uncertain_send_state": "用户在线核对，禁止自动重发",
            },
            "verification": payload["verification"],
        })
        return
    print_json(payload)


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
