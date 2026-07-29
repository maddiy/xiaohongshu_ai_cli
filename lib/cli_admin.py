"""发布、分析、环境检查、跳过列表和 AI 协议命令。"""

import ast
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
    TERMINAL_SEND_STATUSES,
    workflow_paths,
)
from .xhs_client import XHSClient
from .cli_parser import COMMAND_NAMES, build_command_contract


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
    from config import (
        APP_VERSION,
        CACHE_TTL_MINUTES,
        CLI_NAME,
        LOGIN_COOKIE_SOURCE,
        REQUEST_DELAY,
        SYSTEM_NAME,
    )
    requested_command = getattr(args, "command_name", None)
    if requested_command:
        print_json({
            "app_name": CLI_NAME,
            "app_version": APP_VERSION,
            "schema_version": "3",
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
    if getattr(args, "tests", False):
        print_json({
            "app_name": CLI_NAME,
            "app_version": APP_VERSION,
            "schema_version": "3",
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
        "app_name": CLI_NAME,
        "cli_name": CLI_NAME,
        "system_name": SYSTEM_NAME,
        "app_version": APP_VERSION,
        "schema_version": "3",
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
            "config.py": "应用名称、账号、浏览器、延迟和工作目录配置",
            "lib/cli_parser.py": "唯一命令清单和参数定义",
            "lib/cli_view.py": "登录、文章、评论查看",
            "lib/cli_admin.py": "管理、发布、分析和机器协议",
            "lib/cli_ai.py": "AI专用prepare/draft/send工作流",
            "lib/cli_support.py": "存储、状态合并和精简输出",
            "lib/scanner.py": "最新评论/全量扫描和在线核验",
            "lib/replier.py": "草稿生成、发送和失败排除",
            "lib/analyzer.py": "评论统计与摘要分析",
            "lib/poster.py": "图文笔记校验、预览和发布",
            "lib/xhs_client.py": "xhs CLI封装、缓存和令牌索引",
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
            "login_cookie_source": LOGIN_COOKIE_SOURCE,
        },
        "output_contract": {
            "ai_reply": "始终为单一紧凑JSON",
            "json_capable_commands": [
                "articles", "comments", "scan", "analyze", "doctor",
                "ai-help", "paths", "ai-reply",
            ],
            "draft_preview": (
                "preview是本次活动批次的JSON数组，含send/skip/archive；"
                "由AI按columns转换为Markdown表格"
            ),
            "send_results": (
                "results是本次活动批次的JSON数组，按columns展示"
            ),
            "scan_candidates": "仅为候选，不能直接发送",
            "reply_map_schema": {
                "<comment_id>": {
                    "reply": "非空字符串（action=send时必填）",
                    "action": "send|skip|archive",
                },
            },
            "reply_map_string_shorthand": {
                "<comment_id>": "非空回复文案，等价于 action=send",
            },
            "reply_map_missing_key": "当前候选没有映射时默认本次跳过",
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
                "verify_candidates_online；全量模式由scan_note以"
                "include_sub_comments=true、force_refresh=true完整扫描，"
                "绕过评论TTL缓存且不调用该核验函数"
            ),
            "draft": (
                "先停用旧active_comment_ids但保留历史草稿和终态，"
                "再在生成本批草稿前在线核验"
            ),
            "send": (
                "仅对action=send待发送项核验；请求/数据不完整时硬停止且"
                "不归档，online_replied/online_missing才标记archived"
            ),
            "skip_or_archive": "不向平台发送回复，因此不执行发送阶段核验",
            "incomplete_sub_comments": (
                "补拉后数量仍少于sub_comment_count时抛RuntimeError硬停止，"
                "禁止使用部分楼中楼数据"
            ),
            "expansion_scope": (
                "展开本次在线查询返回的所有不完整一级评论楼层；原因是内联"
                "数据缺失时无法预先可靠定位候选或作者直接回复所在楼层，"
                "不表示回复关系可以跨楼层"
            ),
        },
        "storage": {
            "workflow": ".cache/workflows/<note_id>/{scan,reply_map,drafts}.json",
            "global_exclusions": ".cache/skipped.json",
            "sensitive_token_cache": ".cache/xsec_index.json（0600，禁止展示）",
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
            "terminal_statuses": list(TERMINAL_SEND_STATUSES),
            "status_precedence": [
                "drafts.send_status=sent",
                "drafts.send_status=failed",
                "drafts.send_status=archived 或 skipped.json",
                "平台已回复或评论已删除",
                "scan.json 候选（仅进入后续资格判断，不代表可回复）",
            ],
            "eligible_when_all": [
                "scan.reply_status_verified=true",
                "comment_id 在本次 unreplied_level1 或 unreplied_subs",
                "drafts 中同 comment_id 不为 sent、failed、archived",
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
                "draft成功后写入新active_comment_ids，send只处理该批次"
            ),
        },
        "workflows": {
            "view_articles": [
                "python3 main.py articles --json",
                "--limit可省略，默认10；读取平台时可能更新敏感xsec索引",
                "按 columns 和 index 展示，必须显示标题",
            ],
            "view_comments": [
                "python3 main.py comments --json",
                "--limit可省略，默认20",
                "按 columns 和 groups[].note_index 分组展示评论",
                "面向用户的表格不得显示 comment_id",
                "查看流程到此结束，不运行 scan、drafts 或 send",
            ],
            "reply": [
                "python3 main.py ai-reply --note-id <id> --action prepare",
                "prepare 默认只处理最新20条评论通知中的评论和楼中楼",
                "仅当用户明确要求全部历史评论时使用 --full-scan",
                "根据 candidates 写入返回路径中的 reply_map",
                "python3 main.py ai-reply --note-id <id> --action draft",
                "向用户展示 preview 并取得明确确认",
                "python3 main.py ai-reply --note-id <id> --action send --confirmed",
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
    }
    if getattr(args, "summary", False):
        print_json({
            "app_name": CLI_NAME,
            "cli_name": CLI_NAME,
            "system_name": SYSTEM_NAME,
            "app_version": APP_VERSION,
            "schema_version": payload["schema_version"],
            "command_count": payload["command_count"],
            "commands": payload["commands"],
            "canonical_facts": {
                "naming": (
                    f"系统名必须写作“{SYSTEM_NAME}”；"
                    f"命令行程序名必须写作“{CLI_NAME}”"
                ),
                "command_checks": (
                    "build_parser校验参数命令；"
                    "main.COMMAND_HANDLERS校验实际处理器"
                ),
                "ai_reply": (
                    "prepare→draft→send；通知prepare有候选时才调用"
                    "verify_candidates_online，全量prepare使用scan_note；"
                    "draft有候选时复核，send仅复核action=send待发送项"
                ),
                "batch_reset": (
                    "prepare和draft都会清空旧active_comment_ids但不删除历史"
                    "草稿；draft成功后写入新活动批次"
                ),
                "full_prepare": (
                    "--full-scan使用include_sub_comments=true和"
                    "force_refresh=true，绕过评论TTL缓存"
                ),
                "traditional_reply": (
                    "scan支持JSON；drafts/send/reply主要输出终端文本；"
                    "drafts同时支持交互和--batch"
                ),
                "traditional_send": (
                    "发送前在线排除项标记archived并写回草稿；--resume只改变"
                    "提示文案，终态和排除过滤始终执行"
                ),
                "login": (
                    "login会导入浏览器Cookie并更新本地认证状态，不能称为只读命令"
                ),
                "module_mutability": (
                    "lib/cli_view.py包含会更新认证状态的login；"
                    "不能称为只读模块，只能把articles和comments称为"
                    "平台只读查看命令"
                ),
                "confirmation": (
                    "ai-reply send强制--confirmed；传统send的--confirm仅是"
                    "可选终端二次询问，但调用方仍必须事先取得用户确认"
                ),
                "failure_types": {
                    "current": list(XHSClient.REPLY_ERROR_TYPES),
                    "handling": "所有当前失败类型都会进入排除列表",
                    "source": "XHSClient.REPLY_ERROR_TYPES",
                    "detection": XHSClient.REPLY_ERROR_MARKERS,
                },
                "unverified_scan": (
                    "默认拒绝reply_status_verified=false；"
                    "--allow-unverified仅兼容旧文件，之后仍强制在线核验"
                ),
                "candidate_eligibility": (
                    "scan.json候选不等于可回复；还必须通过reply_status_verified、"
                    "本地终态、skipped、删除状态及后续在线复核"
                ),
                "verification_model": (
                    "统一称为条件式多阶段在线核验；核验按候选、扫描范围和"
                    "action条件执行，不得笼统称为固定三层或三个动作都"
                    "无条件调用verify_candidates_online"
                ),
                "sub_comment_expansion": (
                    "核验展开本次查询返回的所有不完整楼层，是因为内联数据"
                    "不足时无法预先定位候选/作者回复所在楼层；不表示回复"
                    "关系会跨楼层"
                ),
                "reply_map": (
                    "值可用非空字符串简写send，也可用reply/action对象；"
                    "本次候选缺少映射时默认skip"
                ),
                "reply_actions": (
                    "skip/archive的reply都可为空；skip只跳过本批；archive要在"
                    "用户确认后的send动作中写入skipped.json；两者都不向平台回复"
                ),
                "limits": (
                    "articles和comments的--limit均可省略，默认分别为10和20"
                ),
                "article_local_effect": (
                    "articles是平台只读，但可能更新本地0600敏感xsec索引"
                ),
                "online_outcomes": (
                    "在线请求或完整性核验失败会硬停止且不归档；只有确认"
                    "online_replied或online_missing的候选才在发送阶段归档"
                ),
                "history_merge": (
                    "merge_draft_history保留sent、failed、archived全部终态，"
                    "不只保留sent"
                ),
                "tests": (
                    "ai-help --tests只描述当前测试快照；测试数量和文件行数"
                    "以当次工具输出为准；没有旧快照或版本差异时禁止声称"
                    "哪些测试是新增、删除或修改"
                ),
                "credentials": (
                    ".cache/xsec_index.json是0600敏感令牌缓存，"
                    "不是普通工作流状态"
                ),
            },
            "defaults": payload["defaults"],
            "source_inventory": payload["source_inventory"],
            "project_inventory": payload["project_inventory"],
            "verification": payload["verification"],
            "test_inventory": payload["test_inventory"],
            "exact_command_protocol": (
                "python3 main.py ai-help --command <command>"
            ),
            "recommended_reply": {
                "commands": [
                    "python3 main.py ai-reply --note-id <id> --action prepare",
                    "python3 main.py ai-reply --note-id <id> --action draft",
                    (
                        "python3 main.py ai-reply --note-id <id> "
                        "--action send --confirmed"
                    ),
                ],
                "rule": (
                    "默认最新20条通知；仅用户要求全部历史时加--full-scan；"
                    "draft后展示preview并取得确认"
                ),
            },
            "full_protocol": "python3 main.py ai-help",
        })
        return
    print_json(payload)


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
