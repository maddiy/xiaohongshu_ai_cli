"""使用macOS Vision识别小红书文字图片，并生成可缓存的笔记正文。"""

import datetime
import json
import os
import shutil
import subprocess
import tempfile

from config import PROJECT_ROOT


OCR_SCRIPT = os.path.join(PROJECT_ROOT, "scripts", "note_ocr.swift")
OCR_MODULE_CACHE = os.path.join(tempfile.gettempdir(), "xhs-note-ocr-modules")
OCR_BINARY = os.path.join(PROJECT_ROOT, ".cache", "bin", "note_ocr")
OCR_TERMINAL_STATUSES = {"completed", "not_needed", "unavailable", "failed"}


def _ensure_ocr_binary() -> str:
    """按需编译Vision辅助程序；源码未变化时直接复用本地二进制。"""
    if (
        os.path.isfile(OCR_BINARY)
        and os.path.getmtime(OCR_BINARY) >= os.path.getmtime(OCR_SCRIPT)
    ):
        return OCR_BINARY
    if not shutil.which("swiftc"):
        raise RuntimeError("当前环境没有可用的macOS Vision OCR运行时")
    os.makedirs(OCR_MODULE_CACHE, mode=0o700, exist_ok=True)
    os.makedirs(os.path.dirname(OCR_BINARY), mode=0o700, exist_ok=True)
    temporary = f"{OCR_BINARY}.{os.getpid()}.tmp"
    compiled = subprocess.run(
        [
            "swiftc", "-module-cache-path", OCR_MODULE_CACHE,
            OCR_SCRIPT, "-o", temporary,
        ],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if compiled.returncode != 0:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise RuntimeError(
            (compiled.stderr or compiled.stdout or "OCR辅助程序编译失败")[-1000:]
        )
    os.chmod(temporary, 0o700)
    os.replace(temporary, OCR_BINARY)
    return OCR_BINARY


def note_needs_image_ocr(note: dict) -> bool:
    """文字正文为空、有图片且尚未尝试OCR时返回True。"""
    if not isinstance(note, dict):
        return False
    if str(note.get("desc", "") or "").strip():
        return False
    images = note.get("images", []) or []
    if not any(
        isinstance(item, dict) and str(item.get("url", "") or "").strip()
        for item in images
    ):
        return False
    return str(note.get("ocr_status", "") or "") not in OCR_TERMINAL_STATUSES


def enrich_note_with_image_text(note: dict, force: bool = False) -> tuple[dict, bool]:
    """按需OCR图片，返回(更新后的副本, 是否发生变化)。"""
    result = dict(note or {})
    desc = str(result.get("desc", "") or "").strip()
    images = result.get("images", []) or []
    urls = [
        str(item.get("url", "") or "").strip()
        for item in images if isinstance(item, dict) and item.get("url")
    ]
    if desc:
        if not result.get("content_text"):
            result.update({
                "content_text": desc,
                "ocr_status": "not_needed",
                "ocr_text_image_count": 0,
            })
            return result, True
        return result, False
    if not urls:
        return result, False
    if not force and not note_needs_image_ocr(result):
        return result, False

    attempted_at = datetime.datetime.now().astimezone().isoformat(
        timespec="seconds"
    )
    if not os.path.isfile(OCR_SCRIPT):
        result.update({
            "ocr_status": "unavailable",
            "ocr_attempted_at": attempted_at,
            "ocr_error": "当前环境没有可用的macOS Vision OCR运行时",
            "content_text": "",
        })
        return result, True
    try:
        executable = _ensure_ocr_binary()
        completed = subprocess.run(
            [executable, *urls],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                (completed.stderr or completed.stdout or "OCR执行失败")[-1000:]
            )
        payload = json.loads(completed.stdout or "{}")
        image_results = payload.get("images", [])
        if not isinstance(image_results, list):
            raise RuntimeError("OCR返回格式错误")
        text_images = [
            item for item in image_results
            if isinstance(item, dict) and item.get("is_text_image")
            and str(item.get("text", "") or "").strip()
        ]
        image_text = "\n\n".join(
            str(item.get("text", "") or "").strip() for item in text_images
        )
        result.update({
            "ocr_status": "completed",
            "ocr_attempted_at": attempted_at,
            "ocr_engine": "macos_vision",
            "ocr_images": image_results,
            "ocr_text_image_count": len(text_images),
            "image_text": image_text,
            "content_text": image_text,
        })
        result.pop("ocr_error", None)
    except Exception as error:
        result.update({
            "ocr_status": "failed",
            "ocr_attempted_at": attempted_at,
            "ocr_error": str(error),
            "content_text": "",
        })
    return result, True
