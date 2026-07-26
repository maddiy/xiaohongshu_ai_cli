"""
配置文件 - 修改此文件以适配你的账号和偏好
运行 xhs whoami 可查看你的 user_id
"""
import os

# 作者信息
AUTHOR_USER_ID = "你的小红书用户ID"  # 运行 xhs whoami 查看

# 请求间隔（秒），避免频率限制
REQUEST_DELAY = 3

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
CACHE_DIR = ".cache"           # 评论缓存目录（相对于项目根目录）
CACHE_TTL_MINUTES = 30         # 缓存有效期（分钟），超时后自动刷新

# 跳过列表（不想回复 / 无法回复的评论存档）
SKIPPED_FILE = os.path.join(CACHE_DIR, "skipped.json")  # 跳过列表存储路径

