"""评论归档、完整正文和自适应展示测试。"""

from tests.support import *


class CommentDisplayTests(unittest.TestCase):
    @patch("main.XHSClient.get_skipped_ids", return_value=set())
    def test_comment_groups_are_numbered_and_hide_credentials(self, _skipped):
        groups = main.build_comment_groups([
            {
                "time": 1785300000,
                "title": "评论了你的笔记",
                "user_info": {"nickname": "用户", "xsec_token": "secret"},
                "item_info": {
                    "id": "n1",
                    "content": "文章",
                    "xsec_token": "note-secret",
                },
                "comment_info": {
                    "id": "c1",
                    "content": "评论",
                    "illegal_info": {"illegal_status": "NORMAL"},
                },
            },
        ])
        self.assertEqual(groups[0]["note_index"], 1)
        self.assertEqual(groups[0]["comments"][0]["status"], "正常")
        self.assertNotIn("secret", json.dumps(groups, ensure_ascii=False))

    @patch(
        "lib.cli_view.save_comment_archive",
        return_value={
            "path": "/cache/comments.json",
            "notes": 0,
            "comments": 0,
            "content_complete": True,
        },
    )
    @patch("main.XHSClient.get_notifications", return_value=[])
    def test_comments_json_declares_user_visible_columns(
        self, _notifications, save_archive
    ):
        args = argparse.Namespace(limit=20, note_id=None, json=True)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            main.cmd_comments(args)
        payload = json.loads(output.getvalue())
        self.assertEqual(
            payload["columns"],
            ["序号", "时间", "用户", "评论", "状态"],
        )
        self.assertTrue(payload["archive"]["content_complete"])
        self.assertTrue(payload["display"]["adaptive_width"])
        self.assertEqual(
            payload["display"]["comment_column"]["soft_break_tag"],
            "<wbr>",
        )
        self.assertEqual(
            payload["display"]["layout"],
            "comment_flexible_other_columns_compact",
        )
        save_archive.assert_called_once_with([])

    @patch(
        "lib.cli_view.save_comment_archive",
        return_value={
            "path": "/cache/comments.json",
            "notes": 1,
            "comments": 1,
            "content_complete": True,
            "content_complete_scope": "notification_payload",
            "content_untruncated_locally": True,
            "platform_tree_verified": False,
        },
    )
    @patch("main.XHSClient.get_skipped_ids", return_value=set())
    @patch("main.XHSClient.get_notifications")
    def test_comments_json_returns_display_ready_safe_groups(
        self, get_notifications, _skipped, _archive
    ):
        get_notifications.return_value = [{
            "time": 1785556800,
            "title": "评论了你的笔记",
            "user_info": {"nickname": "非常非常非常长的用户名"},
            "item_info": {"id": "n1", "content": "文章<table>"},
            "comment_info": {
                "id": "c1",
                "content": "正文|<script>\n" + "长" * 45,
                "illegal_info": {"illegal_status": "NORMAL"},
            },
        }]
        output = io.StringIO()
        args = argparse.Namespace(limit=20, note_id=None, json=True)
        with contextlib.redirect_stdout(output):
            main.cmd_comments(args)
        payload = json.loads(output.getvalue())
        group = payload["groups"][0]
        comment = group["comments"][0]
        self.assertIn("&lt;table&gt;", group["note_title"])
        self.assertEqual(comment["index"], 1)
        self.assertNotIn("comment_id", comment)
        self.assertIn("&#124;", comment["content"])
        self.assertIn("&lt;script&gt;", comment["content"])
        self.assertIn("<br>", comment["content"])
        self.assertIn("<wbr>", comment["content"])
        self.assertTrue(payload["display"]["groups_are_display_ready"])

    @patch(
        "lib.cli_view.save_comment_archive",
        side_effect=RuntimeError(
            "评论归档无法解析，已停止写入以保护历史数据"
        ),
    )
    @patch("main.XHSClient.get_skipped_ids", return_value=set())
    @patch("main.XHSClient.get_notifications")
    def test_comments_json_still_returns_rows_when_archive_is_invalid(
        self, get_notifications, _skipped, _archive
    ):
        get_notifications.return_value = [{
            "time": 1785556800,
            "title": "评论了你的笔记",
            "user_info": {"nickname": "用户"},
            "item_info": {"id": "n1", "content": "文章"},
            "comment_info": {
                "id": "c1",
                "content": "评论",
                "illegal_info": {"illegal_status": "NORMAL"},
            },
        }]
        output = io.StringIO()
        args = argparse.Namespace(limit=20, note_id=None, json=True)
        with contextlib.redirect_stdout(output):
            main.cmd_comments(args)
        payload = json.loads(output.getvalue())
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["comments"], 1)
        self.assertFalse(payload["archive"]["ok"])
        self.assertTrue(payload["archive"]["write_skipped"])
        self.assertEqual(len(payload["warnings"]), 1)

    def test_comment_archive_preserves_full_content_and_quotes(self):
        original = (
            '他说：“中文引号”，又写了 "AI已通过测试"\n'
            '\\path\\end ' + "长评论" * 100
        )
        groups = [{
            "note_index": 1,
            "note_id": "n1",
            "note_title": "文章",
            "comments": [{
                "comment_id": "c1",
                "time": "2026-08-01 12:00",
                "nickname": "用户",
                "content": original,
                "status": "正常",
            }],
        }]
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "comments.json")
            summary = save_comment_archive(groups, path=path)
            with open(path, encoding="utf-8") as file:
                raw = file.read()
            saved = json.loads(raw)
            content = saved["groups"][0]["comments"][0]["content"]
            self.assertEqual(content, original)
            self.assertIn('\\"AI已通过测试\\"', raw)
            self.assertTrue(saved["content_complete"])
            self.assertTrue(summary["content_complete"])
            self.assertEqual(
                saved["content_complete_scope"], "notification_payload"
            )
            self.assertTrue(saved["content_untruncated_locally"])
            self.assertFalse(saved["platform_tree_verified"])
            self.assertEqual(
                summary["content_complete_scope"], "notification_payload"
            )
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)

    def test_comment_archive_refuses_to_overwrite_invalid_json(self):
        broken = b'{"groups":['
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "comments.json")
            with open(path, "wb") as file:
                file.write(broken)
            with self.assertRaisesRegex(RuntimeError, "保护历史数据"):
                save_comment_archive([], path=path)
            with open(path, "rb") as file:
                self.assertEqual(file.read(), broken)

    def test_comment_archive_renumbers_groups_after_incremental_merge(self):
        def group(note_id, comment_id):
            return {
                "note_index": 1,
                "note_id": note_id,
                "note_title": note_id,
                "comments": [{
                    "comment_id": comment_id,
                    "time": "2026-08-01 12:00",
                    "nickname": "用户",
                    "content": "评论",
                    "status": "正常",
                }],
            }

        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "comments.json")
            save_comment_archive([group("n1", "c1")], path=path)
            save_comment_archive([group("n2", "c2")], path=path)
            with open(path, encoding="utf-8") as file:
                saved = json.load(file)
        self.assertEqual(
            [item["note_index"] for item in saved["groups"]],
            [1, 2],
        )
        self.assertEqual(
            [item["note_id"] for item in saved["groups"]],
            ["n2", "n1"],
        )

    def test_comment_display_groups_are_safe_complete_and_ready(self):
        original = '<table>|\\| "引号"\n' + "长" * 45
        groups = [{
            "note_index": 1,
            "note_id": "n1",
            "note_title": "文章",
            "comments": [{
                "comment_id": "c1",
                "time": "2026-08-01 12:00",
                "nickname": "非常非常非常长的用户名",
                "content": original,
                "status": "回复了你的评论",
            }],
        }]
        display_item = build_comment_display_groups(groups)[0]["comments"][0]
        self.assertNotIn("comment_id", display_item)
        self.assertEqual(display_item["index"], 1)
        self.assertEqual(display_item["time"], "2026-08-01<br>12:00")
        self.assertIn("<wbr>", display_item["nickname"])
        self.assertEqual(display_item["status"], "回复了<wbr>你的评论")
        self.assertIn("&lt;table&gt;", display_item["content"])
        self.assertIn("&#124;", display_item["content"])
        self.assertIn("<br>", display_item["content"])
        self.assertIn("<wbr>", display_item["content"])
        restored = html.unescape(
            display_item["content"].replace("<br>", "\n").replace(
                "<wbr>", ""
            )
        )
        self.assertEqual(restored, original)

    @patch(
        "lib.cli_view.save_comment_archive",
        return_value={
            "path": "/cache/comments.json",
            "notes": 1,
            "comments": 1,
            "content_complete": True,
        },
    )
    @patch("main.XHSClient.get_skipped_ids", return_value=set())
    @patch("main.XHSClient.get_notifications")
    def test_comments_terminal_displays_full_comment_content(
        self, get_notifications, _skipped, _archive
    ):
        content = "评论正文" * 100 + '结尾 "引号"'
        get_notifications.return_value = [{
            "time": 1785556800,
            "title": "评论了你的笔记",
            "user_info": {"nickname": "用户"},
            "item_info": {"id": "n1", "content": "文章"},
            "comment_info": {
                "id": "c1",
                "content": content,
                "illegal_info": {"illegal_status": "NORMAL"},
            },
        }]
        output = io.StringIO()
        args = argparse.Namespace(limit=20, note_id=None, json=False)
        with contextlib.redirect_stdout(output):
            main.cmd_comments(args)
        self.assertIn(content, output.getvalue())

    def test_drafts_preserve_full_original_comment_content(self):
        content = "原评论" * 100 + "完整结尾"
        drafts = Replier(MagicMock()).generate_drafts_from_mapping(
            [{
                "comment_id": "c1",
                "nickname": "用户",
                "content": content,
            }],
            {"c1": "回复"},
            note_id="n1",
        )
        self.assertEqual(drafts["drafts"][0]["content"], content)

    def test_analysis_preserves_full_comment_content(self):
        content = "分析评论" * 100 + "完整结尾"
        result = _classify_sentiment([{
            "content": content,
            "user_info": {"user_id": "u1", "nickname": "用户"},
            "like_count": 0,
        }], author_id="author")
        self.assertEqual(result[0]["content"], content)

    def test_skipped_archive_does_not_truncate_comment_content(self):
        content = '引号 "AI" ' + "完整正文" * 100
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "skipped.json")
            with patch(
                "lib.xhs_client.XHSClient._skipped_path",
                return_value=path,
            ):
                XHSClient._skipped_cache = None
                XHSClient._skipped_mtime = 0
                XHSClient.add_skipped("c1", content=content, note_id="n1")
                with open(path, encoding="utf-8") as file:
                    saved = json.load(file)
                self.assertEqual(saved["c1"]["content"], content)
            XHSClient._skipped_cache = None
            XHSClient._skipped_mtime = 0
