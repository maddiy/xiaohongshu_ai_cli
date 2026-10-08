"""发帖模块 — 小红书写笔记发布

本模块不包含 AI 生成能力。AI 内容生成由 AI 编程助手（CodeBuddy、Cursor、Copilot 等）直接完成，
本模块仅负责将生成好的标题、正文通过 xhs CLI 发布出去。
"""

import os
import json
import re
import subprocess
from typing import List, Optional


def validate_note(title: str, body: str, images: List[str]) -> None:
    """校验发布参数，错误时抛出适合直接展示给中文用户的异常。"""
    if not title.strip():
        raise ValueError("标题不能为空")
    if not body.strip():
        raise ValueError("正文不能为空")
    if len(title) > 20:
        raise ValueError(f"标题共 {len(title)} 个字符，建议不超过 20 个字符")
    if len(body) > 1000:
        raise ValueError(f"正文共 {len(body)} 个字符，不能超过1000个字符（含话题）")
    if not images:
        raise ValueError("至少需要一张图片")
    for img in images:
        if not os.path.isfile(img):
            raise FileNotFoundError(f"图片不存在: {img}")


def build_command(
    title: str,
    body: str,
    images: List[str],
    topics: Optional[List[str]] = None,
    private: bool = False,
) -> List[str]:
    """构造 xhs 命令，供 CLI、AI 工具和测试共同复用。"""
    validate_note(title, body, images)
    full_body = body
    if topics:
        normalized_topics = [
            str(topic).strip().lstrip("#")
            for topic in topics if str(topic).strip()
        ]
        if normalized_topics:
            full_body = f"{body}\n\n{' '.join('#' + t for t in normalized_topics)}"

    validate_note(title, full_body, images)
    cmd = ["xhs", "post", "--title", title, "--body", full_body, "--json"]
    for image in images:
        cmd.extend(["--images", image])
    if private:
        cmd.append("--private")
    return cmd


def publish(
    title: str,
    body: str,
    images: List[str],
    topics: Optional[List[str]] = None,
    private: bool = False,
    dry_run: bool = False,
) -> bool:
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

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=180
        )
    except subprocess.TimeoutExpired:
        print("发布结果未知: 平台请求超过180秒，请先到客户端核对，禁止自动重发")
        return False
    except OSError as error:
        print(f"发布失败: 无法启动xhs命令: {error}")
        return False
    output = result.stdout + result.stderr
    if result.returncode != 0:
        print(f"发布失败: {output[:200]}")
        return False
    try:
        response = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        response = {}
    if not isinstance(response, dict):
        response = {}
    if response.get("ok") is True:
        print("发布成功！")
        data = response.get("data")
        note_id = data.get("id") if isinstance(data, dict) else None
        if note_id:
            print(f"笔记链接: https://www.xiaohongshu.com/explore/{note_id}")
        return True
    print(f"结果未知: {output[:200]}")
    return False


def delete_note(note_id: str, *, confirmed: bool = False, dry_run: bool = False) -> dict:
    """单篇删除入口。依赖可能不支持删除；未知结果绝不自动重试。"""
    if not re.fullmatch(r"[0-9a-f]{24}", note_id or ""):
        return {"ok": False, "error_type": "invalid_note_id", "error": "必须提供完整的24位笔记ID"}
    if dry_run:
        return {"ok": True, "action": "delete", "note_id": note_id, "dry_run": True,
                "next": "核对笔记后明确确认，再添加--confirmed；当前依赖接口可能不支持删除"}
    if not confirmed:
        return {"ok": False, "error_type": "confirmation_required", "error": "删除不可撤销，需要用户明确确认后添加--confirmed"}
    try:
        result = subprocess.run(["xhs", "delete", note_id, "--yes", "--json"],
                                capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error_type": "uncertain_delete_state", "error": "删除请求超时，结果未知；请在客户端核对", "automatic_retry": False}
    except OSError:
        return {"ok": False, "error_type": "delete_unavailable", "error": "无法启动xhs删除命令", "automatic_retry": False}
    try:
        response = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        response = {}
    if not isinstance(response, dict):
        response = {}
    if result.returncode == 0 and response.get("ok") is True:
        return {"ok": True, "action": "delete", "note_id": note_id, "deleted": True,
                "local_history_preserved": True}
    error = response.get("error") or {}
    code = error.get("code", "uncertain_delete_state") if isinstance(error, dict) else "uncertain_delete_state"
    return {"ok": False, "action": "delete", "note_id": note_id, "error_type": code,
            "error": "当前依赖的公开网页接口不支持删除笔记，请在小红书客户端手动删除" if code == "unsupported_operation" else "删除未确认成功，请在客户端核对；不要自动重试",
            "automatic_retry": False, "local_history_preserved": True}
