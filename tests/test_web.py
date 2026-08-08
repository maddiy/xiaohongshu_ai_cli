"""本地Web控制台的绑定范围、安全头和监控管理测试。"""

from tests.support import *

import inspect
import threading

from lib.web_app import (
    _handler_class,
    _load_articles,
    _load_comments,
    _merge_articles,
    _prefetch_missing_note_details,
    create_web_server,
)


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

    @patch("lib.web_app.ThreadingHTTPServer")
    def test_server_only_binds_localhost_and_sets_security_headers(
        self, server_class
    ):
        server_class.return_value.server_address = ("127.0.0.1", 8765)
        server, token = create_web_server(port=0)
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
            "skipped", "drafts",
        ):
            self.assertIn(f'data-page="{page}"', html_source)
        self.assertNotIn('data-page="monitor"', html_source)
        self.assertNotIn('id="watchForm"', html_source)
        self.assertNotIn('data-page="content"', html_source)
        self.assertNotIn('data-page="system"', html_source)
        self.assertIn('id="articlePagination"', html_source)
        self.assertIn('id="commentPagination"', html_source)
        self.assertIn('id="articleSearchForm"', html_source)
        self.assertIn('id="articleSearchStatus"', html_source)
        self.assertIn('id="commentSearchForm"', html_source)
        self.assertIn('id="commentSearchStatus"', html_source)
        self.assertNotIn('id="resetArticleSearch"', html_source)
        self.assertNotIn('id="resetCommentSearch"', html_source)
        self.assertNotIn('id="resetSkippedSearch"', html_source)
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
        self.assertIn('id="deleteAllDrafts"', html_source)
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
        self.assertIn('api("/api/drafts/delete-all"', javascript)
        self.assertIn("function deleteAllDrafts", javascript)
        self.assertGreaterEqual(
            javascript.count('textNode("button", "评论", "use-button small")'),
            2,
        )
        self.assertIn('["序号", "时间", "用户", "评论", "状态", "操作"]', javascript)
        self.assertNotIn('openPage("reply")', javascript)
        self.assertNotIn("function sendReplies", javascript)
        self.assertNotIn("/api/post", javascript)
        self.assertIn("function applySkippedSearch", javascript)
        self.assertIn("function applyArticleSearch", javascript)
        self.assertIn("function applyCommentSearch", javascript)
        self.assertIn('"正文缓存"', javascript)
        self.assertIn("body_cache_status", javascript)
        self.assertIn("cache-status", javascript)
        self.assertIn("setTimeout(applyArticleSearch, 300)", javascript)
        self.assertIn("setTimeout(applyCommentSearch, 300)", javascript)
        self.assertIn("setTimeout(applySkippedSearch, 300)", javascript)
        self.assertIn("requestId !== state.skipped.requestId", javascript)
        self.assertIn("pagination.page_size || 15", javascript)
        self.assertIn('if (name === "comments") {', javascript)
        self.assertIn("refreshComments(1, state.autoRefresh.comments);", javascript)
        self.assertIn('if (name === "articles") {', javascript)
        self.assertIn(
            '"#refreshArticles").addEventListener("click", () => refreshArticles(1, true))',
            javascript,
        )
        self.assertIn(
            'if (name === "articles") {\n    state.loadedPages.add(name);',
            javascript,
        )
        self.assertIn("refreshArticles(1, state.autoRefresh.articles);", javascript)
        self.assertIn("AUTO_REFRESH_STORAGE_KEY", javascript)
        self.assertIn('id="autoRefreshArticles"', html_source)
        self.assertIn('id="autoRefreshComments"', html_source)

    def test_web_comment_refresh_uses_default_twenty_notifications(self):
        from lib.web_app import WEB_COMMENT_FETCH_LIMIT
        self.assertEqual(WEB_COMMENT_FETCH_LIMIT, 20)

    @patch("lib.web_app.XHSClient.load_skipped", return_value={})
    @patch("lib.web_app._schedule_note_detail_prefetch")
    @patch("lib.web_app.save_comment_archive")
    @patch("lib.web_app.build_comment_groups")
    @patch("lib.web_app.XHSClient.get_notifications")
    @patch("lib.web_app.read_json_state")
    @patch("lib.web_app.json_state_exists", return_value=True)
    def test_comment_refresh_archives_before_background_note_prefetch(
        self, _exists, read_state, get_notifications, build_groups,
        save_archive, schedule_prefetch, _load_skipped
    ):
        notifications = [{"item_info": {"id": "n1"}}]
        groups = [{"note_id": "n1", "note_title": "文章", "comments": []}]
        get_notifications.return_value = notifications
        build_groups.return_value = groups
        save_archive.return_value = {"ok": True}
        schedule_prefetch.return_value = {
            "source": "background_scheduled", "scheduled": True,
        }
        read_state.return_value = {"groups": []}

        result = _load_comments(refresh=True)

        get_notifications.assert_called_once_with(
            num=20, notification_type="mentions", strict=True
        )
        save_archive.assert_called_once_with(groups)
        schedule_prefetch.assert_called_once_with(notifications, groups)
        self.assertTrue(result["note_content_cache"]["scheduled"])

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

    def test_fresh_first_page_replaces_cached_article_metrics(self):
        merged = _merge_articles(
            [{
                "index": 1,
                "note_id": "n1",
                "title": "文章一",
                "comments_count": 18,
                "view_count": 3200,
            }],
            [{
                "index": 1,
                "note_id": "n1",
                "title": "文章一",
                "comments_count": 3,
                "view_count": 900,
            }, {
                "index": 2,
                "note_id": "n2",
                "title": "历史文章",
                "comments_count": 7,
                "view_count": 700,
            }],
        )
        self.assertEqual(merged[0]["comments_count"], 18)
        self.assertEqual(merged[0]["view_count"], 3200)
        self.assertEqual(merged[1]["note_id"], "n2")

    @patch("lib.web_app.write_json")
    @patch("lib.web_app.XHSClient.get_my_notes")
    @patch("lib.web_app.read_json_state")
    @patch("lib.web_app.json_state_exists", return_value=True)
    def test_web_refresh_requests_cached_first_page_items_for_new_metrics(
        self, _exists, read_state, get_notes, _write
    ):
        read_state.return_value = {"articles": []}
        get_notes.return_value = []

        _load_articles(refresh=True)

        get_notes.assert_called_once_with(
            max_pages=1, strict=True, include_cached=True
        )

    @patch("lib.web_app.read_json_state")
    @patch("lib.web_app.json_state_exists", return_value=True)
    def test_articles_search_matches_title_and_note_id_before_pagination(
        self, _exists, read_state
    ):
        read_state.return_value = {
            "articles": [
                {"index": 1, "note_id": "abc-001", "title": "杭州台风"},
                {"index": 2, "note_id": "abc-002", "title": "美国经济"},
                {"index": 3, "note_id": "xyz-003", "title": "杭州旅行"},
            ],
        }
        by_title = _load_articles(search="杭州", page=1, page_size=10)
        self.assertEqual(by_title["total_count"], 2)
        self.assertEqual(by_title["search"], "杭州")
        by_terms = _load_articles(search="美国 abc", page=1, page_size=10)
        self.assertEqual(by_terms["total_count"], 1)
        self.assertEqual(by_terms["articles"][0]["note_id"], "abc-002")

    @patch("lib.web_app.read_json_state")
    @patch("lib.web_app.json_state_exists", return_value=True)
    def test_articles_report_local_body_cache_without_platform_reads(
        self, _exists, read_state
    ):
        def state_for(path, default=None):
            if path.endswith("articles.json"):
                return {"articles": [
                    {"index": 1, "note_id": "cached", "title": "已缓存文章"},
                    {"index": 2, "note_id": "missing", "title": "未缓存文章"},
                ]}
            if path.endswith("note_details.json"):
                return {"cached": {
                    "title": "已缓存文章",
                    "desc": "正文",
                    "read_at": "2026-08-08T20:00:00+08:00",
                }}
            return default

        read_state.side_effect = state_for
        result = _load_articles(page=1, page_size=10)

        self.assertIn("正文缓存", result["columns"])
        self.assertTrue(result["articles"][0]["body_cached"])
        self.assertEqual(
            result["articles"][0]["body_cache_status"], "已缓存"
        )
        self.assertFalse(result["articles"][1]["body_cached"])
        self.assertEqual(
            result["articles"][1]["body_cache_status"], "未缓存"
        )
        searched = _load_articles(search="已缓存", page=1, page_size=10)
        self.assertEqual(searched["total_count"], 1)

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

    @patch("lib.web_app.XHSClient.load_skipped", return_value={})
    @patch("lib.web_app.read_json_state")
    @patch("lib.web_app.json_state_exists", return_value=True)
    def test_comments_search_matches_article_user_content_and_status(
        self, _exists, read_state, _load_skipped
    ):
        read_state.return_value = {
            "groups": [{
                "note_id": "n1",
                "note_title": "杭州见闻",
                "comments": [
                    {"comment_id": "c1", "nickname": "小明", "content": "台风来了", "status": "正常", "time": "2026-08-08"},
                    {"comment_id": "c2", "nickname": "Daniel", "content": "天气不错", "status": "已回复", "time": "2026-08-07"},
                ],
            }],
        }
        result = _load_comments(search="杭州 小明 台风")
        self.assertEqual(result["total_count"], 1)
        self.assertEqual(result["search"], "杭州 小明 台风")
        self.assertEqual(
            result["groups"][0]["comments"][0]["action_data"]["nickname"],
            "小明",
        )

    def test_complete_console_routes_are_csrf_protected(self):
        handler = _handler_class("token")
        source = inspect.getsource(handler.do_POST)
        for route in (
            "/api/analyze",
            "/api/skipped",
            "/api/doctor",
            "/api/paths",
            "/api/protocol",
            "/api/reply/send",
            "/api/drafts/delete-all",
        ):
            self.assertIn(route, source)
        self.assertNotIn("/api/post", source)
        self.assertNotIn("/api/watch/", source)
        self.assertIn("X-CSRF-Token", source)
