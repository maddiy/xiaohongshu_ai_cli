"""命令行参数定义；与业务命令解耦，便于单独测试和扩展。"""

import argparse
from config import APP_VERSION, SYSTEM_NAME


COMMAND_NAMES = (
    "login",
    "articles",
    "comments",
    "scan",
    "drafts",
    "send",
    "reply",
    "post",
    "analyze",
    "skipped",
    "doctor",
    "ai-help",
    "paths",
    "ai-reply",
)

COMMAND_EFFECTS = {
    "login": {
        "platform": "认证",
        "local": "更新 xiaohongshu-cli 的本地认证状态",
        "output": "终端文本",
    },
    "articles": {
        "platform": "读取",
        "local": (
            "可能更新 .cache/xsec_index.json 敏感令牌索引；"
            "大列表写入 .cache/articles.json 供分页读取"
        ),
        "output": "终端文本；--json 时为单一 JSON",
    },
    "comments": {
        "platform": "读取",
        "local": (
            "读取本地终态和排除列表；累计写入"
            ".cache/comments.json，保留通知接口已返回且程序未截断的原文"
        ),
        "output": "终端文本；--json 时为单一 JSON",
    },
    "scan": {
        "platform": "读取",
        "local": "写入 scan.json，并可能更新评论缓存和令牌索引",
        "output": "终端文本；--json 时为单一 JSON 摘要",
    },
    "drafts": {
        "platform": "读取并在线核验",
        "local": "写入 drafts.json，并可能更新敏感令牌索引",
        "output": "终端文本，可能进入逐条输入",
    },
    "send": {
        "platform": "发送回复",
        "local": (
            "逐条更新 drafts.json；失败时更新 skipped.json；"
            "在线核验可能更新敏感令牌索引"
        ),
        "output": "终端文本",
    },
    "reply": {
        "platform": "读取、在线核验并发送回复",
        "local": "失败或归档时更新 skipped.json，并可能更新敏感令牌索引",
        "output": "终端文本，smart 策略会逐条输入",
    },
    "post": {
        "platform": "发布笔记；--dry-run 时不发布",
        "local": "仅读取输入 JSON 和图片",
        "output": "终端文本",
    },
    "analyze": {
        "platform": "读取",
        "local": "可能更新评论缓存",
        "output": "终端文本；--json 时为单一 JSON",
    },
    "skipped": {
        "platform": "不访问",
        "local": "读取、移除或清空 skipped.json",
        "output": "终端文本；--clear 会询问确认",
    },
    "doctor": {
        "platform": "不访问",
        "local": "只检查环境和配置",
        "output": "终端文本；--json 时为单一 JSON",
    },
    "ai-help": {
        "platform": "不访问",
        "local": "只读取程序定义和文件清单",
        "output": "始终为单一 JSON",
    },
    "paths": {
        "platform": "不访问",
        "local": "只检查固定工作文件是否存在",
        "output": "始终为单一 JSON",
    },
    "ai-reply": {
        "platform": "prepare/draft 读取并核验；send 发送回复",
        "local": (
            "持有同笔记跨进程锁，读写带批次指纹的固定工作流文件；"
            "失败时更新 skipped.json；在线核验可能更新敏感令牌索引"
        ),
        "output": "始终为单一紧凑 JSON",
    },
}


def add_common_args(parser):
    parser.add_argument("--note-id", help="笔记ID")
    parser.add_argument("--xsec-token", default="", help="笔记的 xsec_token")
    parser.add_argument(
        "--refresh", action="store_true",
        help="强制刷新，忽略缓存重新拉取评论",
    )
    parser.add_argument(
        "--with-subs", action="store_true",
        help="同时拉取楼中楼（默认不拉取，仅检查一级评论）",
    )


def add_limit_args(parser):
    parser.add_argument(
        "--max-pages", type=int, default=None,
        help="最多翻页数（默认翻到底，每页约10篇）",
    )


def build_parser():
    parser = argparse.ArgumentParser(
        description=(
            f"{SYSTEM_NAME}｜面向中文用户与AI助手的发布、评论管理工具"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python3 main.py login
  python3 main.py articles
  python3 main.py comments
  python3 main.py scan --note-id <note_id>
  python3 main.py scan --note-id <note_id> --full-scan --with-subs
  python3 main.py drafts --note-id <note_id>
  python3 main.py send --file .cache/workflows/<note_id>/drafts.json --dry-run
  python3 main.py post --input note.json --dry-run
  python3 main.py doctor
  python3 main.py ai-help
""",
    )
    parser.add_argument(
        "--version", action="version",
        version=f"{SYSTEM_NAME} {APP_VERSION}",
    )
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    subparsers.add_parser("login", help="登录小红书（默认读取 Firefox Cookie）")

    articles = subparsers.add_parser("articles", help="查看最新文章列表")
    articles.add_argument(
        "--limit", type=int, default=10, help="显示文章数量（默认10）"
    )
    articles.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    articles.add_argument(
        "--page", type=int, default=1,
        help="JSON结果页码（默认1）",
    )
    articles.add_argument(
        "--page-size", type=int, default=20,
        help="JSON每页数量（默认20）",
    )
    articles.add_argument(
        "--cache", action="store_true",
        help="从文章缓存读取，不再次访问平台",
    )
    articles.add_argument(
        "--output",
        help="文章缓存文件；默认大列表保存到 .cache/articles.json",
    )

    comments = subparsers.add_parser(
        "comments", help="查看最新评论（只读，不生成回复候选）"
    )
    comments.add_argument("--note-id", help="只查看指定笔记")
    comments.add_argument(
        "--limit", type=int, default=20, help="读取通知数量（默认20）"
    )
    comments.add_argument("--json", action="store_true", help="输出机器可读 JSON")

    scan = subparsers.add_parser(
        "scan", help="扫描未回复评论（默认从通知快速读取）"
    )
    add_common_args(scan)
    add_limit_args(scan)
    scan.add_argument(
        "--full-scan", action="store_true",
        help="全量扫描模式：拉取笔记全部评论逐一比对",
    )
    scan.add_argument(
        "--num-notifications", type=int, default=20,
        help="通知模式下拉取的通知数量（默认20）",
    )
    scan.add_argument("--output", help="扫描结果 JSON 保存路径")
    scan.add_argument("--json", action="store_true", help="仅输出机器可读 JSON")

    drafts = subparsers.add_parser(
        "drafts", help="生成回复草稿（逐条确认后保存）"
    )
    add_common_args(drafts)
    drafts.add_argument("--output", help="草稿文件保存路径（默认固定工作目录）")
    drafts.add_argument(
        "--from-scan", metavar="FILE", help="从已有扫描结果文件加载"
    )
    drafts.add_argument(
        "--batch", metavar="FILE",
        help='批量导入回复映射，格式: {"comment_id":"回复文案"}',
    )
    drafts.add_argument(
        "--allow-unverified", action="store_true",
        help="兼容旧扫描文件；生成草稿前仍会强制在线核验",
    )
    drafts.add_argument(
        "--num-notifications", type=int, default=20,
        help="默认读取的最新评论通知数量（默认20）",
    )
    drafts.add_argument(
        "--full-scan", action="store_true",
        help="处理全部历史评论和楼中楼；默认只处理最新评论",
    )

    send = subparsers.add_parser("send", help="发送已审核的回复草稿")
    send.add_argument("--file", required=True, help="草稿文件路径")
    send.add_argument("--dry-run", action="store_true", help="预览模式，不发送")
    send.add_argument("--confirm", action="store_true", help="发送前二次确认")
    send.add_argument(
        "--resume", action="store_true",
        help="兼容选项，仅改变续发提示；终态和排除过滤始终生效",
    )

    reply = subparsers.add_parser("reply", help="回复评论（兼容旧入口）")
    add_common_args(reply)
    reply.add_argument(
        "--strategy", default="smart", choices=["smart", "generic"],
        help="回复策略: smart=逐条确认, generic=随机话术",
    )
    reply.add_argument("--from-file", help="从JSON文件读取未回复列表")
    reply.add_argument(
        "--num-notifications", type=int, default=20,
        help="默认读取的最新评论通知数量（默认20）",
    )
    reply.add_argument(
        "--full-scan", action="store_true",
        help="处理全部历史评论和楼中楼；默认只处理最新评论",
    )

    skipped = subparsers.add_parser("skipped", help="管理跳过列表")
    skipped.add_argument("--remove", help="移除指定评论ID")
    skipped.add_argument("--clear", action="store_true", help="清空所有跳过记录")

    analyze = subparsers.add_parser("analyze", help="分析评论")
    add_common_args(analyze)
    analyze.add_argument("--note-title", default="", help="笔记标题（可选）")
    analyze.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    analyze.add_argument(
        "--details", action="store_true", help="JSON 中包含全部评论明细"
    )

    post = subparsers.add_parser("post", help="发布小红书笔记")
    post.add_argument("--input", metavar="FILE", help="从 JSON 文件读取笔记")
    post.add_argument("--title", help="笔记标题")
    post.add_argument("--body", help="笔记正文")
    post.add_argument("--images", nargs="+", help="图片路径")
    post.add_argument("--topics", help="话题标签，逗号分隔")
    post.add_argument("--private", action="store_true", help="私密发布")
    post.add_argument("--dry-run", action="store_true", help="只校验和预览")

    doctor = subparsers.add_parser("doctor", help="检查本地环境与配置（不联网）")
    doctor.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    ai_help = subparsers.add_parser("ai-help", help="输出 AI 调用协议（JSON）")
    ai_help_mode = ai_help.add_mutually_exclusive_group()
    ai_help_mode.add_argument(
        "--summary", action="store_true",
        help="仅输出权威项目摘要，减少 AI 上下文用量",
    )
    ai_help_mode.add_argument(
        "--command", dest="command_name", choices=COMMAND_NAMES,
        help="仅输出指定命令的精确参数、副作用和输出约定",
    )
    ai_help_mode.add_argument(
        "--tests", action="store_true",
        help="仅输出当前测试清单；不能据此推断相对上一版本的新增项",
    )
    paths = subparsers.add_parser("paths", help="显示固定工作文件路径")
    paths.add_argument("--note-id", default="all", help="笔记ID（默认 all）")

    ai_reply = subparsers.add_parser(
        "ai-reply", help="AI 专用紧凑回复工作流（仅输出 JSON）"
    )
    ai_reply.add_argument("--note-id", required=True, help="笔记ID")
    ai_reply.add_argument(
        "--action", required=True, choices=["prepare", "draft", "send"],
        help="prepare=扫描，draft=生成预览，send=发送",
    )
    ai_reply.add_argument(
        "--replies", metavar="FILE",
        help="回复映射文件；默认使用固定 reply_map.json",
    )
    ai_reply.add_argument(
        "--confirmed", action="store_true",
        help="确认用户已审核预览，仅 send 动作使用",
    )
    ai_reply.add_argument(
        "--batch-id",
        help="draft返回的批次编号；send必须原样提交",
    )
    ai_reply.add_argument(
        "--preview-hash",
        help="draft返回的预览指纹；send必须原样提交",
    )
    ai_reply.add_argument(
        "--limit", type=int, default=20,
        help="prepare 默认读取的最新评论通知数量（默认20）",
    )
    ai_reply.add_argument(
        "--full-scan", action="store_true",
        help="扫描全部历史评论和楼中楼；默认只处理最新评论",
    )
    if set(subparsers.choices) != set(COMMAND_NAMES):
        raise RuntimeError("参数定义与 COMMAND_NAMES 不一致")
    if set(COMMAND_EFFECTS) != set(COMMAND_NAMES):
        raise RuntimeError("命令副作用清单与 COMMAND_NAMES 不一致")
    return parser


def build_command_contract(command):
    """从 argparse 定义生成单条命令的机器可读协议，避免手工抄写参数。"""
    if command not in COMMAND_NAMES:
        raise ValueError(f"未知命令: {command}")
    parser = build_parser()
    subparsers = next(
        action for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    command_parser = subparsers.choices[command]
    arguments = []
    for action in command_parser._actions:
        if action.dest == "help":
            continue
        argument = {
            "name": action.dest,
            "flags": list(action.option_strings),
            "required": bool(action.required),
            "default": action.default,
            "help": action.help or "",
        }
        if action.choices is not None:
            argument["choices"] = list(action.choices)
        if action.nargs is not None:
            argument["nargs"] = action.nargs
        if isinstance(action, argparse._StoreTrueAction):
            argument["type"] = "boolean"
        elif action.type:
            argument["type"] = getattr(action.type, "__name__", str(action.type))
        else:
            argument["type"] = "string"
        arguments.append(argument)
    return {
        "command": command,
        "usage": command_parser.format_usage().strip(),
        "arguments": arguments,
        "effects": COMMAND_EFFECTS[command],
        "source": "由 lib/cli_parser.py 的 argparse 定义运行时生成",
    }
