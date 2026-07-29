"""命令行参数定义；与业务命令解耦，便于单独测试和扩展。"""

import argparse


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
        description="面向中文用户与 AI 助手的小红书发布、评论管理命令行工具",
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
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    subparsers.add_parser("login", help="登录小红书（默认读取 Firefox Cookie）")

    articles = subparsers.add_parser("articles", help="查看最新文章列表")
    articles.add_argument(
        "--limit", type=int, default=10, help="显示文章数量（默认10）"
    )
    articles.add_argument("--json", action="store_true", help="输出机器可读 JSON")

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
        help="允许使用未核验扫描文件（有重复回复风险）",
    )

    send = subparsers.add_parser("send", help="发送已审核的回复草稿")
    send.add_argument("--file", required=True, help="草稿文件路径")
    send.add_argument("--dry-run", action="store_true", help="预览模式，不发送")
    send.add_argument("--confirm", action="store_true", help="发送前二次确认")
    send.add_argument("--resume", action="store_true", help="断点续发")

    reply = subparsers.add_parser("reply", help="回复评论（兼容旧入口）")
    add_common_args(reply)
    reply.add_argument(
        "--strategy", default="smart", choices=["smart", "generic"],
        help="回复策略: smart=逐条确认, generic=随机话术",
    )
    reply.add_argument("--from-file", help="从JSON文件读取未回复列表")

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
    subparsers.add_parser("ai-help", help="输出 AI 调用协议（JSON）")
    paths = subparsers.add_parser("paths", help="显示固定工作文件路径")
    paths.add_argument("--note-id", default="all", help="笔记ID（默认 all）")
    return parser
