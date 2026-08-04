#!/usr/bin/env python3
"""在一个 xhs 会话内完成评论分页，避免每页重复启动 CLI 和读取 Cookie。"""

import json
import sys
import time

from xhs_cli.client import XhsClient
from xhs_cli.cookies import get_cookies
from xhs_cli.error_codes import error_code_for_exception


def _loaded_ids(comments):
    ids = set()
    for comment in comments:
        comment_id = comment.get("id", "")
        if comment_id:
            ids.add(comment_id)
        ids.update(
            sub.get("id", "")
            for sub in comment.get("sub_comments", [])
            if sub.get("id")
        )
    return ids


def _dedupe_comments(comments):
    """合并定位请求返回的重复一级评论，优先保留楼中楼更完整的版本。"""
    ordered = []
    by_id = {}
    for comment in comments:
        comment_id = comment.get("id", "")
        if not comment_id:
            ordered.append(comment)
            continue
        existing = by_id.get(comment_id)
        if existing is None:
            by_id[comment_id] = comment
            ordered.append(comment)
            continue
        if len(comment.get("sub_comments", [])) > len(
            existing.get("sub_comments", [])
        ):
            existing.update(comment)
    return ordered


def _check_budget(deadline, request_timeout, pages_fetched, time_budget):
    """在新请求前保证helper还能自行返回，不把收敛交给父进程强杀。"""
    if time.monotonic() + request_timeout * 2 + 1 >= deadline:
        raise TimeoutError(
            "评论helper达到主动时间预算"
            f"（{time_budget:g}秒，已读取{pages_fetched}页）"
        )


def _expand_thread(
    client, note_id, comment, xsec_token, ensure_budget=lambda: None
):
    """在当前会话内补全候选楼层，避免再启动一个子进程。"""
    inline_subs = comment.get("sub_comments", [])
    expected = int(comment.get("sub_comment_count", 0) or 0)
    if expected <= len(inline_subs):
        return False
    root_id = comment.get("id", "")
    full_subs = []
    cursor = ""
    seen_cursors = set()
    while True:
        ensure_budget()
        page = client._main_api_get(
            "/api/sns/web/v2/comment/sub/page",
            {
                "note_id": note_id,
                "root_comment_id": root_id,
                "num": 30,
                "cursor": cursor,
                "image_formats": "jpg,webp,avif",
                "xsec_token": xsec_token,
            },
        )
        page_comments = page.get("comments", [])
        full_subs.extend(page_comments)
        next_cursor = str(page.get("cursor", "") or "")
        if not next_cursor or not page_comments:
            break
        if next_cursor in seen_cursors:
            raise RuntimeError("楼中楼返回了重复游标")
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    if len(full_subs) < expected:
        raise RuntimeError(
            f"楼中楼数据不完整: {root_id} "
            f"期望{expected}条，实际{len(full_subs)}条"
        )
    comment["sub_comments"] = full_subs
    return True


def main():
    if len(sys.argv) != 12:
        raise SystemExit(
            "usage: helper NOTE_ID XSEC_TOKEN COOKIE_SOURCE "
            "MAX_PAGES TARGET_GROUPS_JSON TARGET_IDS_JSON "
            "TARGET_ANCHORS_JSON EXPAND_UNRESOLVED START_CURSOR "
            "TIME_BUDGET_SECONDS REQUEST_TIMEOUT_SECONDS"
        )
    note_id, xsec_token, cookie_source = sys.argv[1:4]
    max_pages = int(sys.argv[4])
    target_groups = [
        set(group) for group in json.loads(sys.argv[5]) if group
    ]
    target_ids = set(json.loads(sys.argv[6]))
    target_anchors = json.loads(sys.argv[7])
    expand_unresolved = sys.argv[8] == "1"
    cursor = sys.argv[9]
    time_budget = max(float(sys.argv[10]), 1.0)
    request_timeout = max(float(sys.argv[11]), 1.0)
    deadline = time.monotonic() + time_budget
    comments = []
    seen_cursors = set()
    search_complete = False
    pages_fetched = 0
    expanded_threads = 0

    def ensure_budget():
        # max_retries=1时给当前请求预留两次request_timeout；这样helper会在
        # 父进程上限前主动输出结构化错误，而不是被父进程直接杀死。
        _check_budget(
            deadline, request_timeout, pages_fetched, time_budget
        )

    try:
        _, cookies = get_cookies(cookie_source)
        # 默认客户端单次网络请求可等待30秒并重试3次，少量候选也可能因此
        # 卡住数分钟。核验助手采用短超时、失败即返回，由上层安全停止。
        with XhsClient(
            cookies,
            timeout=request_timeout,
            request_delay=0.2,
            max_retries=1,
        ) as client:
            # 通知通常携带一级评论或目标评论ID。先用top_comment_id直达
            # 对应楼层，避免为了少量最新评论从头翻几十页历史评论。候选很多
            # 时逐条直达会产生数百次请求；此时直接顺序翻页只需几十次请求。
            direct_lookup = len(target_groups) <= 20
            if direct_lookup:
                for index, group in enumerate(target_groups):
                    # 多条楼中楼候选可能属于同一个一级楼层。前一次请求已经
                    # 加载该楼层时不再重复请求。
                    if group & _loaded_ids(comments):
                        continue
                    anchors = (
                        target_anchors[index]
                        if index < len(target_anchors) else []
                    )
                    for anchor in anchors:
                        ensure_budget()
                        page = client.get_comments(
                            note_id,
                            xsec_token=xsec_token,
                            top_comment_id=anchor,
                        )
                        if isinstance(page, dict):
                            comments.extend(page.get("comments", []))
                            pages_fetched += 1
                        if group & _loaded_ids(comments):
                            break

            loaded_ids = _loaded_ids(comments)
            contexts_found = (
                bool(target_groups)
                and all(group & loaded_ids for group in target_groups)
            )
            if contexts_found:
                search_complete = True
            else:
                for _ in range(max_pages):
                    ensure_budget()
                    page = client.get_comments(
                        note_id,
                        cursor=cursor,
                        xsec_token=xsec_token,
                    )
                    if not isinstance(page, dict):
                        break
                    page_comments = page.get("comments", [])
                    comments.extend(page_comments)
                    pages_fetched += 1
                    loaded_ids = _loaded_ids(comments)
                    if target_groups and all(
                        group & loaded_ids for group in target_groups
                    ):
                        search_complete = True
                        break
                    next_cursor = str(page.get("cursor", "") or "")
                    if (
                        not page.get("has_more", False)
                        or not page_comments
                        or not next_cursor
                    ):
                        search_complete = True
                        break
                    if next_cursor in seen_cursors:
                        raise RuntimeError("平台返回了重复游标")
                    seen_cursors.add(next_cursor)
                    cursor = next_cursor
            comments = _dedupe_comments(comments)
            context_ids = (
                set().union(*target_groups) if target_groups else set()
            )
            checked_threads = set()
            completed_thread_ids = []
            for comment in comments:
                if not (_loaded_ids([comment]) & context_ids):
                    continue
                if _expand_thread(
                    client, note_id, comment, xsec_token, ensure_budget
                ):
                    expanded_threads += 1
                checked_threads.add(id(comment))
                completed_thread_ids.append(_loaded_ids([comment]))

            # 部分通知只有楼中楼评论ID，没有可靠的一级楼层提示。候选仍未
            # 定位时才检查其他不完整楼层，并继续复用当前登录会话。
            loaded_ids = _loaded_ids(comments)
            context_resolved_missing = set()
            for index, anchors in enumerate(target_anchors):
                if not anchors:
                    continue
                candidate_id = str(anchors[-1])
                hints = set(str(item) for item in anchors[:-1] if item)
                if (
                    candidate_id
                    and candidate_id not in loaded_ids
                    and hints
                    and any(hints & ids for ids in completed_thread_ids)
                ):
                    context_resolved_missing.add(candidate_id)
            unresolved_ids = (
                target_ids - loaded_ids - context_resolved_missing
            )
            deferred_errors = []
            expand_all = expand_unresolved and not target_ids
            if (unresolved_ids or expand_all) and expand_unresolved:
                for comment in comments:
                    if id(comment) in checked_threads:
                        continue
                    expected = int(
                        comment.get("sub_comment_count", 0) or 0
                    )
                    if expected <= len(comment.get("sub_comments", [])):
                        continue
                    try:
                        if _expand_thread(
                            client, note_id, comment, xsec_token,
                            ensure_budget,
                        ):
                            expanded_threads += 1
                    except Exception as error:
                        deferred_errors.append(str(error))
                        continue
                    if not expand_all:
                        unresolved_ids = (
                            target_ids
                            - _loaded_ids(comments)
                            - context_resolved_missing
                        )
                    if not expand_all and not unresolved_ids:
                        break
            if expand_all and deferred_errors:
                raise RuntimeError(
                    "部分楼中楼在线数据不完整: "
                    + "；".join(deferred_errors)
                )
            if unresolved_ids and deferred_errors:
                raise RuntimeError(
                    "候选评论尚未定位，且部分楼中楼在线数据不完整: "
                    + "；".join(deferred_errors)
                )
        print(json.dumps({
            "ok": True,
            "data": {
                "comments": comments,
                "search_complete": search_complete,
                "pages_fetched": pages_fetched,
                "expanded_threads": expanded_threads,
            },
        }, ensure_ascii=False))
    except Exception as error:
        print(json.dumps({
            "ok": False,
            "error": {
                "code": error_code_for_exception(error),
                "message": str(error),
            },
            "progress": {
                "pages_fetched": pages_fetched,
                "expanded_threads": expanded_threads,
                "time_budget_seconds": time_budget,
            },
        }, ensure_ascii=False))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
