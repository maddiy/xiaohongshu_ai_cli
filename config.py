"""
配置文件 - 修改此文件以适配你的账号和偏好
运行 xhs whoami 可查看你的 user_id
"""
import os

# 项目根目录。所有本地状态都基于该绝对路径，避免AI从其他工作目录运行时
# 产生第二套.cache。
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

# 唯一正式名称（APP_NAME 保留为旧代码兼容别名）
SYSTEM_NAME = "小红书AI智能运营系统"
APP_NAME = SYSTEM_NAME
APP_VERSION = "4.0.0"
AI_SCHEMA_VERSION = "4"

# 作者信息
AUTHOR_USER_ID = "6321167e0000000023038acd"  # 运行 xhs whoami 查看

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

# 只读分页间隔。发送回复仍使用上面的请求间隔。
READ_PAGE_DELAY = 0.1

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
CACHE_TTL_MINUTES = 30         # 缓存有效期（分钟），超时后自动刷新
WORK_DIR = os.path.join(CACHE_DIR, "workflows")  # AI 之间共享的临时工作目录
# 累计保存comments从通知接口读取到且程序未截断的原始正文。
COMMENTS_FILE = os.path.join(CACHE_DIR, "comments.json")

# 跳过列表（不想回复 / 无法回复的评论存档）
SKIPPED_FILE = os.path.join(CACHE_DIR, "skipped.json")  # 跳过列表存储路径
