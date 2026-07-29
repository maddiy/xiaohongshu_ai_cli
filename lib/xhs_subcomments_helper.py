#!/usr/bin/env python3
"""兼容 xiaohongshu-cli 0.6.4：为楼中楼请求补充 xsec_token。"""

import json
import sys

from xhs_cli.client import XhsClient
from xhs_cli.cookies import get_cookies
from xhs_cli.error_codes import error_code_for_exception


def main():
    if len(sys.argv) != 6:
        raise SystemExit(
            "usage: helper NOTE_ID COMMENT_ID CURSOR XSEC_TOKEN COOKIE_SOURCE"
        )
    note_id, comment_id, cursor, xsec_token, cookie_source = sys.argv[1:]
    try:
        _, cookies = get_cookies(cookie_source)
        with XhsClient(cookies) as client:
            data = client._main_api_get(
                "/api/sns/web/v2/comment/sub/page",
                {
                    "note_id": note_id,
                    "root_comment_id": comment_id,
                    "num": 30,
                    "cursor": cursor,
                    "image_formats": "jpg,webp,avif",
                    "xsec_token": xsec_token,
                },
            )
        print(json.dumps({"ok": True, "data": data}, ensure_ascii=False))
    except Exception as error:
        print(json.dumps({
            "ok": False,
            "error": {
                "code": error_code_for_exception(error),
                "message": str(error),
            },
        }, ensure_ascii=False))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
