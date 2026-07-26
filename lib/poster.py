"""发帖模块 — 小红书写笔记发布

本模块不包含 AI 生成能力。AI 内容生成由 AI 编程助手（CodeBuddy、Cursor、Copilot 等）直接完成，
本模块仅负责将生成好的标题、正文通过 xhs CLI 发布出去。
"""

import os
import subprocess
from typing import Optional


def publish(title: str, body: str, images: list, topics: Optional[list] = None, private: bool = False) -> bool:
    """发布小红书笔记

    Args:
        title:   笔记标题
        body:    笔记正文（纯文本）
        images:  图片路径列表，至少1张
        topics:  话题标签列表（可选），如 ["读书", "成长"]
        private: 是否私密发布

    Returns:
        发布成功返回 True，失败返回 False
    """
    if not images:
        raise ValueError("至少需要一张图片")
    for img in images:
        if not os.path.isfile(img):
            raise FileNotFoundError(f"图片不存在: {img}")

    full_body = body
    if topics:
        full_body = f"{body}\n\n{' '.join('#' + t for t in topics)}"

    cmd = ["xhs", "post", "--title", title, "--body", full_body, "--images", *images]
    if private:
        cmd.append("--private")

    print(f"发布笔记...")
    print(f"   标题: {title}")
    print(f"   图片: {len(images)} 张")
    print(f"   话题: {topics or ['无']}")

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    output = result.stdout + result.stderr
    if result.returncode != 0:
        print(f"发布失败: {output[:200]}")
        return False
    if "ok" in output.lower() or "成功" in output:
        print("发布成功！")
        return True
    print(f"结果未知: {output[:200]}")
    return False
