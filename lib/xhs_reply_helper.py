#!/usr/bin/env python3
"""在一个已登录会话内连续发布或回复评论，并逐条确认结果。"""

import json
import sys

from xhs_cli.client import XhsClient
from xhs_cli.cookies import get_cookies
from xhs_cli.error_codes import error_code_for_exception


def _emit(payload):
    print(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        flush=True,
    )


def main():
    if len(sys.argv) != 4:
        raise SystemExit(
            "usage: helper NOTE_ID COOKIE_SOURCE REQUEST_DELAY"
        )
    note_id, cookie_source, request_delay = sys.argv[1:]
    try:
        _, cookies = get_cookies(cookie_source)
        client = XhsClient(
            cookies,
            timeout=20.0,
            request_delay=float(request_delay),
            max_retries=1,
        )
    except Exception as error:
        _emit({
            "ok": False,
            "fatal": True,
            "error": {
                "code": error_code_for_exception(error),
                "message": str(error),
            },
        })
        raise SystemExit(1)

    with client:
        for line in sys.stdin:
            request = None
            try:
                request = json.loads(line)
                action = str(request.get("action", "reply"))
                content = str(request["content"])
                if action == "comment":
                    result = client.post_comment(note_id, content)
                    comment = (
                        result.get("data", {}).get("comment", {})
                        if isinstance(result, dict) else {}
                    )
                    _emit({
                        "ok": True,
                        "action": action,
                        "sequence": request.get("sequence"),
                        "comment_id": str(comment.get("id", "") or ""),
                    })
                    continue
                if action != "reply":
                    raise ValueError(f"不支持的操作: {action}")
                comment_id = str(request["comment_id"])
                client.reply_comment(note_id, comment_id, content)
                _emit({
                    "ok": True,
                    "action": action,
                    "comment_id": comment_id,
                })
            except Exception as error:
                _emit({
                    "ok": False,
                    "action": (
                        str(request.get("action", "reply"))
                        if isinstance(request, dict) else ""
                    ),
                    "sequence": (
                        request.get("sequence")
                        if isinstance(request, dict) else None
                    ),
                    "comment_id": (
                        str(request.get("comment_id", ""))
                        if isinstance(request, dict) else ""
                    ),
                    "error": {
                        "code": error_code_for_exception(error),
                        "message": str(error),
                    },
                })


if __name__ == "__main__":
    main()
