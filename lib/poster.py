"""发帖模块 — 小红书写笔记发布

本模块不包含 AI 生成能力。AI 内容生成由 AI 编程助手（CodeBuddy、Cursor、Copilot 等）直接完成，
本模块仅负责将生成好的标题、正文通过 xhs CLI 发布出去。
"""

import os
import subprocess
from typing import Optional


def validate_note(title: str, body: str, images: list) -> None:
    """校验发布参数，错误时抛出适合直接展示给中文用户的异常。"""
    if not title.strip():
        raise ValueError("标题不能为空")
    if not body.strip():
        raise ValueError("正文不能为空")
    if len(title) > 20:
        raise ValueError(f"标题共 {len(title)} 个字符，建议不超过 20 个字符")
    if not images:
        raise ValueError("至少需要一张图片")
    for img in images:
        if not os.path.isfile(img):
            raise FileNotFoundError(f"图片不存在: {img}")


def build_command(title: str, body: str, images: list,
                  topics: Optional[list] = None,
                  private: bool = False) -> list:
    """构造 xhs 命令，供 CLI、AI 工具和测试共同复用。"""
    validate_note(title, body, images)
    full_body = body
    if topics:
        normalized_topics = [str(t).strip().lstrip("#") for t in topics if str(t).strip()]
        if normalized_topics:
            full_body = f"{body}\n\n{' '.join('#' + t for t in normalized_topics)}"

    cmd = ["xhs", "post", "--title", title, "--body", full_body, "--images", *images]
    if private:
        cmd.append("--private")
    return cmd


def publish(title: str, body: str, images: list, topics: Optional[list] = None,
            private: bool = False, dry_run: bool = False) -> bool:
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
    cmd = build_command(title, body, images, topics, private)

    print("预览笔记..." if dry_run else "发布笔记...")
    print(f"   标题: {title}")
    print(f"   正文: {body[:100]}{'…' if len(body) > 100 else ''}")
    print(f"   图片: {len(images)} 张")
    print(f"   话题: {topics or ['无']}")
    print(f"   可见性: {'仅自己可见' if private else '公开'}")

    if dry_run:
        print("预览完成，未发布。")
        return True

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
