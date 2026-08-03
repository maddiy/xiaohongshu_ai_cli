"""跨JSON文件、SQLite和预览指纹共用的规范化编码。"""

import hashlib
import json


def canonical_json(value):
    """对象键递归排序，列表顺序保持不变，生成稳定UTF-8 JSON文本。"""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_json_bytes(value):
    return canonical_json(value).encode("utf-8")


def json_digest(value):
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()
