"""系统配置；账号身份由程序首次运行时自动识别并保存。"""
import os

# 项目根目录。所有本地状态都基于该绝对路径，避免AI从其他工作目录运行时
# 产生第二套.cache。
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

# 唯一正式名称（APP_NAME 保留为旧代码兼容别名）
SYSTEM_NAME = "小红书AI智能运营系统"
APP_NAME = SYSTEM_NAME
APP_VERSION = "6.13.0"
AI_SCHEMA_VERSION = "28"
XHS_CLI_VERSION = "0.6.4"

# 登录时默认读取的浏览器 Cookie
LOGIN_COOKIE_SOURCE = "firefox"

# 兼容单次 xhs 进程发送时的请求间隔（秒）。
# 推荐批量发送会复用一个登录会话，由 xhs 客户端自身执行下面的动态间隔。
REQUEST_DELAY = 3

# 持久会话批量回复的最小间隔。xiaohongshu-cli 会在此基础上加入随机抖动，
# 程序还会周期性短暂停顿；仍比“每条重启进程+固定等待3秒”更快、更稳定。
BATCH_REPLY_DELAY = float(os.environ.get("XHS_BATCH_REPLY_DELAY", "2.0"))
BATCH_REPLY_PAUSE_EVERY = int(
    os.environ.get("XHS_BATCH_REPLY_PAUSE_EVERY", "50")
)
BATCH_REPLY_PAUSE_SECONDS = float(
    os.environ.get("XHS_BATCH_REPLY_PAUSE_SECONDS", "8")
)

# 持久helper可靠性预算。父进程等待单条回复时必须有上限；评论分页helper
# 自己先在预算内停止，父进程只保留单次在途请求和退出清理的宽限时间。
PERSISTENT_HELPER_RESPONSE_TIMEOUT = float(
    os.environ.get("XHS_HELPER_RESPONSE_TIMEOUT", "45")
)
COMMENT_HELPER_REQUEST_TIMEOUT = float(
    os.environ.get("XHS_COMMENT_HELPER_REQUEST_TIMEOUT", "8")
)
COMMENT_HELPER_REQUEST_DELAY = float(
    os.environ.get("XHS_COMMENT_HELPER_REQUEST_DELAY", "0.08")
)
COMMENT_HELPER_MAX_SECONDS = float(
    os.environ.get("XHS_COMMENT_HELPER_MAX_SECONDS", "260")
)
COMMENT_HELPER_PARENT_GRACE_SECONDS = float(
    os.environ.get("XHS_COMMENT_HELPER_PARENT_GRACE_SECONDS", "15")
)
COMMENT_LOOKUP_MAX_PAGES = int(
    os.environ.get("XHS_COMMENT_LOOKUP_MAX_PAGES", "50")
)

# 手动开启的新评论监控。监控仅在watch命令或本地Web控制台运行期间工作；
# 首次启动只建立基线，避免把历史通知误当成刚发现的新评论。
WATCH_POLL_INTERVAL_SECONDS = float(
    os.environ.get("XHS_WATCH_POLL_INTERVAL", "60")
)
WATCH_NOTIFICATION_LIMIT = int(
    os.environ.get("XHS_WATCH_NOTIFICATION_LIMIT", "50")
)
WATCH_SEEN_LIMIT = int(os.environ.get("XHS_WATCH_SEEN_LIMIT", "2000"))
WEB_HOST = "127.0.0.1"
WEB_PORT = int(os.environ.get("XHS_WEB_PORT", "8765"))

# 只读分页间隔。发送回复仍使用上面的请求间隔。
READ_PAGE_DELAY = float(os.environ.get("XHS_READ_PAGE_DELAY", "0.03"))

# 回复策略
REPLY_STRATEGY = "smart"  # "smart" 逐条手动确认 | "auto" 自动生成回复 | "generic" 使用通用话术

# 通用回复话术池（REPLY_STRATEGY = "generic" 时使用）
GENERIC_REPLIES = [
    "感谢你的评论！",
    "谢谢分享观点！",
    "感谢关注！",
    "谢谢支持！",
    "理解你的想法，握手🤝",
]

# 缓存配置
CACHE_DIR = os.path.join(PROJECT_ROOT, ".cache")
STATE_DB_FILE = os.path.join(CACHE_DIR, "state.sqlite3")
CACHE_TTL_MINUTES = 30         # 缓存有效期（分钟），超时后自动刷新
WORK_DIR = os.path.join(CACHE_DIR, "workflows")  # AI 之间共享的临时工作目录
WATCH_DIR = os.path.join(CACHE_DIR, "watch")
# 以下JSON路径是兼容快照/AI交换入口；权威状态保存在STATE_DB_FILE。
# 累计保存comments从通知接口读取到且程序未截断的原始正文。
COMMENTS_FILE = os.path.join(CACHE_DIR, "comments.json")

# 跳过列表（不想回复 / 无法回复的评论存档）
SKIPPED_FILE = os.path.join(CACHE_DIR, "skipped.json")  # 跳过列表存储路径

# 笔记详情缓存（标题+正文desc+图片数等），供回复提示词结合笔记正文使用。
# 0600权限；属于本地只读缓存，不是回复工作流状态。
NOTE_DETAILS_FILE = os.path.join(CACHE_DIR, "note_details.json")
