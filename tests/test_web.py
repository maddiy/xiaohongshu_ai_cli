"""本地Web控制台的绑定范围、安全头和监控管理测试。"""

from tests.support import *

import inspect
import threading

from lib.web_app import (
    WatchManager,
    _handler_class,
    _load_articles,
    _load_comments,
    _prefetch_missing_note_details,
    create_web_server,
)


class _ManagedFakeWatcher:
    def __init__(self, note_id="", user="", auto_reply=False,
                 confirmed=False, reply_text="", interval=60, limit=50):
        self.note_id = note_id
        self.user = user
        self.auto_reply = auto_reply
        self.confirmed = confirmed
        self.reply_text = reply_text
        self.interval = interval
        self.limit = limit
        self.profile = {"note_id": note_id, "user": user}
        self.profile_id = f"profile-{note_id}-{user}"
        self.mode = "note" if note_id else ("user" if user else "all")

    def run(self, stop_event, reset=False, on_event=None):
        on_event({
            "ok": True,
            "event": "baseline",
            "baseline_count": 0,
            "items": [],
            "active": True,
        })
        stop_event.wait(2)


class WebAppTests(unittest.TestCase):
    @patch("lib.web_app.XHSClient._merge_xsec_index")
    @patch("lib.web_app.XHSClient.get_note_detail_cached")
    @patch("lib.web_app.read_json_state")
    def test_missing_comment_note_is_fetched_once_and_cached_for_prompts(
        self, read_state, get_detail, merge_tokens
    ):
        def state_for(path, default=None):
            if path.endswith("articles.json"):
                return {"articles": [{"note_id": "known-note"}]}
            if path.endswith("note_details.json"):
                return {}
            return default

        read_state.side_effect = state_for
        get_detail.return_value = {
            "note_id": "missing-note",
            "title": "补齐后的标题",
            "desc": "完整笔记正文",
            "read_at": "2026-08-08T10:00:00+08:00",
        }
        notifications = [{
            "item_info": {
                "id": "missing-note",
                "xsec_token": "sensitive-token",
            },
        }, {
            "item_info": {
                "id": "missing-note",
                "xsec_token": "sensitive-token",
            },
        }]
        groups = [{
            "note_id": "missing-note",
            "note_title": "无标题",
            "comments": [],
        }]

        result = _prefetch_missing_note_details(notifications, groups)

        self.assertEqual(result["fetched"], 1)
        get_detail.assert_called_once_with(
            "missing-note",
            xsec_token="sensitive-token",
            force_refresh=False,
        )
        merge_tokens.assert_called_once_with({
            "missing-note": "sensitive-token",
        })
        self.assertEqual(groups[0]["note_title"], "补齐后的标题")

    @patch("lib.web_app.XHSClient._merge_xsec_index")
    @patch("lib.web_app.XHSClient.get_note_detail_cached")
    @patch("lib.web_app.read_json_state")
    def test_existing_note_detail_is_reused_without_platform_request(
        self, read_state, get_detail, _merge_tokens
    ):
        cached = {
            "note_id": "missing-note",
            "title": "缓存标题",
            "desc": "缓存正文",
        }
        read_state.side_effect = lambda path, default=None: (
            {"articles": []} if path.endswith("articles.json")
            else {"missing-note": cached}
        )
        groups = [{
            "note_id": "missing-note",
            "note_title": "无标题",
            "comments": [],
        }]

        result = _prefetch_missing_note_details([], groups)

        self.assertEqual(result["already_cached"], 1)
        get_detail.assert_not_called()
        self.assertEqual(groups[0]["note_title"], "缓存标题")

    def test_watch_manager_starts_and_stops_manual_monitor(self):
        manager = WatchManager(watcher_factory=_ManagedFakeWatcher)
        started = manager.start({
            "note_id": "n1",
            "auto_reply": True,
            "confirmed": True,
        })
        self.assertEqual(started["profile_id"], "profile-n1-")
        self.assertTrue(started["active"])
        stopped = manager.stop(started["profile_id"])
        self.assertFalse(stopped["active"])
        manager.shutdown()

    @patch("lib.web_app.ThreadingHTTPServer")
    def test_server_only_binds_localhost_and_sets_security_headers(
        self, server_class
    ):
        manager = WatchManager(watcher_factory=_ManagedFakeWatcher)
        server_class.return_value.server_address = ("127.0.0.1", 8765)
        server, token, manager = create_web_server(port=0, manager=manager)
        address = server_class.call_args.args[0]
        self.assertEqual(address, ("127.0.0.1", 0))
        self.assertEqual(len(token), 48)
        handler = server_class.call_args.args[1]
        instance = object.__new__(handler)
        instance.send_header = MagicMock()
        instance._security_headers()
        header_values = [call.args for call in instance.send_header.call_args_list]
        self.assertTrue(any(
            name == "Content-Security-Policy"
            and "frame-ancestors 'none'" in value
            for name, value in header_values
        ))
        self.assertIn("X-CSRF-Token", inspect.getsource(handler.do_POST))
        self.assertIs(server, server_class.return_value)

    def test_web_assets_exist_and_do_not_load_remote_scripts(self):
        for filename in ("index.html", "app.js", "styles.css", "logo.svg"):
            path = os.path.join(PROJECT_ROOT, "web", filename)
            self.assertTrue(os.path.isfile(path))
        with open(
            os.path.join(PROJECT_ROOT, "web", "index.html"),
            encoding="utf-8",
        ) as file:
            html_source = file.read()
        self.assertIn("__CSRF_TOKEN__", html_source)
        self.assertIn('href="/logo.svg"', html_source)
        self.assertIn('class="brand-mark"', html_source)
        self.assertIn('viewBox="0 0 64 64"', html_source)
        self.assertNotIn("https://", html_source)
        handler_source = inspect.getsource(_handler_class)
        self.assertIn('parsed.path == "/logo.svg"', handler_source)
        self.assertIn('"image/svg+xml; charset=utf-8"', handler_source)

    def test_daily_bar_chart_values_follow_bar_height(self):
        with open(
            os.path.join(PROJECT_ROOT, "web", "app.js"),
            encoding="utf-8",
        ) as file:
            app_source = file.read()
        with open(
            os.path.join(PROJECT_ROOT, "web", "styles.css"),
            encoding="utf-8",
        ) as file:
            style_source = file.read()

        self.assertIn('track.style.setProperty("--bar-height"', app_source)
        self.assertIn("bar.hidden = safeHeight === 0", app_source)
        self.assertIn("height: var(--bar-height)", style_source)
        self.assertIn(
            "bottom: calc(var(--bar-height) + .32rem)", style_source
        )
        self.assertIn(
            ".bar-track .bar[hidden] { display: none; }", style_source
        )

    def test_console_is_split_into_pages_and_has_no_post_ui(self):
        with open(
            os.path.join(PROJECT_ROOT, "web", "index.html"),
            encoding="utf-8",
        ) as file:
            html_source = file.read()
        for page in (
            "dashboard", "articles", "comments", "analyze",
            "monitor", "skipped",
        ):
            self.assertIn(f'data-page="{page}"', html_source)
        self.assertNotIn('data-page="content"', html_source)
        self.assertNotIn('data-page="system"', html_source)
        self.assertIn('id="articlePagination"', html_source)
        self.assertIn('id="commentPagination"', html_source)
        self.assertIn('id="skippedSearchForm"', html_source)
        self.assertIn('id="skippedSearchStatus"', html_source)
        self.assertIn('id="submitSkippedSearch"', html_source)
        self.assertIn('id="skippedPagination"', html_source)
        self.assertIn('<option value="15" selected>15</option>', html_source)
        self.assertNotIn('id="toolDoctor"', html_source)
        self.assertNotIn('id="toolPaths"', html_source)
        self.assertNotIn('id="toolProtocol"', html_source)
        self.assertNotIn('data-page="post"', html_source)
        self.assertNotIn('id="postForm"', html_source)
        self.assertNotIn('data-page="reply"', html_source)
        self.assertNotIn('id="replyPromptDialog"', html_source)
        self.assertNotIn('id="replyPromptText"', html_source)
        self.assertNotIn('id="copyReplyPrompt"', html_source)
        self.assertNotIn('id="replySendButton"', html_source)
        self.assertIn('id="replyDraftDialog"', html_source)
        self.assertIn('id="replyDraftText"', html_source)
        self.assertIn('id="sendReplyDraft"', html_source)
        self.assertIn('class="skip-link" href="#mainContent"', html_source)
        self.assertIn('id="mainContent" tabindex="-1"', html_source)
        self.assertIn("提示词按钮不会发送", html_source)
        self.assertNotIn("网页只生成 AI 提示词，不直接发送", html_source)

        with open(
            os.path.join(PROJECT_ROOT, "web", "app.js"),
            encoding="utf-8",
        ) as file:
            javascript = file.read()
        self.assertIn('"⧉", "copy-button icon-copy"', javascript)
        self.assertNotIn('"复制", "copy-button"', javascript)
        self.assertIn('openPage("analyze")', javascript)
        self.assertIn("function buildAllCommentsReplyPrompt", javascript)
        self.assertIn("function buildCommentReplyPrompt", javascript)
        self.assertNotIn("function showReplyPrompt", javascript)
        self.assertIn('"该文章全部评论的 AI 回复提示词已复制"', javascript)
        self.assertIn('"该条评论的 AI 回复提示词已复制"', javascript)
        self.assertIn("await copyText(", javascript)
        self.assertIn("--action prepare --full-scan", javascript)
        self.assertIn("笔记 ID：${id}", javascript)
        self.assertIn("目标评论 ID：${commentId}", javascript)
        self.assertIn("function untrustedPlatformData", javascript)
        self.assertIn("<UNTRUSTED_PLATFORM_DATA_JSON>", javascript)
        self.assertIn("不得添加 --full-scan", javascript)
        self.assertIn("只有目标评论可根据审查结果设为 send", javascript)
        self.assertIn("不要先遍历全部源码", javascript)
        self.assertIn("没有外部事实主张时明确写“无需外部核查”", javascript)
        self.assertIn("最多使用 1 个合适的 emoji", javascript)
        self.assertIn("只有 send 返回 status=sent 才能报告发送成功", javascript)
        self.assertIn("(newReply) => { row.reply = newReply; }", javascript)
        self.assertIn("/^https?:\\/\\//i.test(sourceUrl)", javascript)
        self.assertIn('link.setAttribute("aria-current", "page")', javascript)
        self.assertIn('action: "ignore"', javascript)
        self.assertIn('"人工忽略"', javascript)
        self.assertIn('textNode("button", "草稿", "use-button small")', javascript)
        self.assertIn("function openReplyDraft", javascript)
        self.assertIn("function sendCurrentReplyDraft", javascript)
        self.assertIn('api("/api/reply/send"', javascript)
        self.assertGreaterEqual(
            javascript.count('textNode("button", "评论", "use-button small")'),
            2,
        )
        self.assertIn('["序号", "时间", "用户", "评论", "状态", "操作"]', javascript)
        self.assertNotIn('openPage("reply")', javascript)
        self.assertNotIn("function sendReplies", javascript)
        self.assertNotIn("/api/post", javascript)
        self.assertIn("function applySkippedSearch", javascript)
        self.assertIn("setTimeout(applySkippedSearch, 300)", javascript)
        self.assertIn("requestId !== state.skipped.requestId", javascript)
        self.assertIn("pagination.page_size || 15", javascript)

    @patch("lib.web_app.read_json_state")
    @patch("lib.web_app.json_state_exists", return_value=True)
    def test_articles_are_paginated_from_complete_snapshot(
        self, _exists, read_state
    ):
        read_state.return_value = {
            "articles": [{
                "index": index,
                "note_id": f"n{index}",
                "title": f"文章{index}",
            } for index in range(1, 24)]
        }
        result = _load_articles(page=2, page_size=10)
        self.assertEqual(result["total_count"], 23)
        self.assertEqual(result["articles"][0]["note_id"], "n11")
        self.assertTrue(result["pagination"]["has_previous"])
        self.assertTrue(result["pagination"]["has_next"])

    @patch("lib.web_app.XHSClient.load_skipped")
    @patch("lib.web_app.read_json_state")
    @patch("lib.web_app.json_state_exists", return_value=True)
    def test_comments_are_paginated_across_archived_notes(
        self, _exists, read_state, load_skipped
    ):
        load_skipped.return_value = {
            "c4": {"reason": "人工忽略"},
        }
        read_state.return_value = {
            "content_untruncated_locally": True,
            "groups": [{
                "note_id": "n1",
                "note_title": "文章一",
                "comments": [{
                    "comment_id": f"c{index}",
                    "time": f"2026-08-04 10:{index:02d}",
                    "nickname": "用户",
                    "content": f"完整评论{index}",
                    "status": "正常",
                } for index in range(15)],
            }],
        }
        result = _load_comments(page=2, page_size=10)
        self.assertEqual(result["total_count"], 15)
        self.assertEqual(result["count"], 5)
        self.assertTrue(result["pagination"]["has_previous"])
        self.assertFalse(result["pagination"]["has_next"])
        self.assertEqual(
            result["columns"], ["序号", "时间", "用户", "评论", "状态", "操作"]
        )
        self.assertEqual(result["display"]["column_fields"]["操作"], "action_data")
        self.assertIn(
            "action_data.comment_id",
            result["display"]["internal_fields_hidden_from_table"],
        )
        first = result["groups"][0]["comments"][0]
        self.assertEqual(first["action_data"]["comment_id"], "c4")
        self.assertEqual(first["action_data"]["content"], "完整评论4")
        self.assertTrue(first["action_data"]["ignored"])
        self.assertEqual(first["status"], "人工忽略")

    def test_complete_console_routes_are_csrf_protected(self):
        handler = _handler_class(
            WatchManager(watcher_factory=_ManagedFakeWatcher), "token"
        )
        source = inspect.getsource(handler.do_POST)
        for route in (
            "/api/analyze",
            "/api/skipped",
            "/api/doctor",
            "/api/paths",
            "/api/protocol",
            "/api/watch/start",
            "/api/watch/stop",
            "/api/reply/send",
        ):
            self.assertIn(route, source)
        self.assertNotIn("/api/post", source)
        self.assertIn("X-CSRF-Token", source)
