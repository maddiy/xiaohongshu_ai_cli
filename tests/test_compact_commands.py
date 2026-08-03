"""文章、扫描和紧凑输出命令测试。"""

from tests.support import *


class CompactCommandTests(unittest.TestCase):
    @patch("lib.xhs_client.XHSClient._run_xhs")
    def test_articles_strict_mode_does_not_hide_failure(self, run_xhs):
        run_xhs.side_effect = RuntimeError("network unavailable")
        with self.assertRaisesRegex(RuntimeError, "network unavailable"):
            XHSClient.list_articles(limit=20)

    @patch("main.XHSClient.list_articles", side_effect=RuntimeError("offline"))
    def test_articles_json_error_remains_valid_json(self, _list_articles):
        args = argparse.Namespace(limit=20, json=True)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            main.cmd_articles(args)
        self.assertEqual(
            json.loads(output.getvalue()),
            {"ok": False, "error": "offline"},
        )

    def test_compact_comment_removes_internal_fields(self):
        source = {
            "comment_id": "c1",
            "nickname": "用户",
            "content": "内容",
            "likes": 2,
            "target_comment_id": "root1",
            "inline_subs": [{"id": "internal"}],
            "inline_subs_count": 1,
            "_replied": False,
        }
        self.assertEqual(
            main.compact_comment(source),
            {
                "comment_id": "c1",
                "nickname": "用户",
                "content": "内容",
                "likes": 2,
                "target_comment_id": "root1",
            },
        )

    def test_compact_scan_keeps_verification_state(self):
        compact = main.compact_scan_result({
            "note_id": "n1",
            "reply_status_verified": True,
            "unreplied_level1": [],
            "unreplied_subs": [],
        })
        self.assertTrue(compact["reply_status_verified"])

    def test_compact_scan_numbers_comment_note_groups(self):
        compact = main.compact_scan_result({
            "note_id": "all",
            "unreplied_level1": [],
            "unreplied_subs": [],
            "per_note": [
                {
                    "note_id": "n1",
                    "unreplied_level1": [],
                    "unreplied_subs": [],
                },
                {
                    "note_id": "n2",
                    "unreplied_level1": [],
                    "unreplied_subs": [],
                },
            ],
        })
        self.assertEqual(
            [item["note_index"] for item in compact["per_note"]],
            [1, 2],
        )

    def test_scan_summary_does_not_repeat_comments(self):
        result = {
            "note_id": "n1",
            "unreplied_level1": [{"comment_id": "c1", "content": "很长的评论"}],
            "unreplied_subs": [],
        }
        summary = main.scan_summary(result)
        self.assertNotIn("很长的评论", json.dumps(summary, ensure_ascii=False))
        self.assertEqual(summary["unreplied_level1"], 1)

    @patch("main.XHSClient.list_articles")
    def test_articles_json_hides_xsec_token(self, list_articles):
        list_articles.return_value = [{
            "id": "n1",
            "title": "标题",
            "comments_count": 3,
            "xsec_token": "secret",
            "time": "2026-07-26 10:00",
        }]
        args = argparse.Namespace(limit=1, json=True)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            main.cmd_articles(args)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["articles"][0]["note_id"], "n1")
        self.assertEqual(
            payload["columns"],
            ["序号", "发布时间", "评论数", "标题", "笔记ID"],
        )
        self.assertEqual(
            payload["column_fields"]["笔记ID"], "note_id"
        )
        self.assertEqual(payload["articles"][0]["title"], "标题")
        self.assertNotIn("xsec_token", payload["articles"][0])
        self.assertNotIn("secret", output.getvalue())

    @patch("main.XHSClient.list_articles")
    def test_articles_large_json_is_cached_and_paginated(
        self, list_articles
    ):
        list_articles.return_value = [{
            "id": f"n{index}",
            "title": f"标题{index}",
            "comments_count": index,
            "time": "2026-07-30 10:00",
        } for index in range(1, 46)]
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = os.path.join(temp_dir, "articles.json")
            first_args = argparse.Namespace(
                limit=45,
                json=True,
                page=1,
                page_size=20,
                cache=False,
                output=cache_path,
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                main.cmd_articles(first_args)
            first = json.loads(output.getvalue())
            self.assertEqual(first["count"], 20)
            self.assertEqual(first["total_count"], 45)
            self.assertEqual(first["articles"][0]["note_id"], "n1")
            self.assertTrue(first["pagination"]["has_more"])
            self.assertIn("--page 2", first["pagination"]["next_command"])
            self.assertEqual(first["cache_path"], cache_path)
            with open(cache_path, encoding="utf-8") as saved:
                self.assertEqual(len(json.load(saved)["articles"]), 45)

            second_args = argparse.Namespace(
                limit=10,
                json=True,
                page=2,
                page_size=20,
                cache=True,
                output=cache_path,
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                main.cmd_articles(second_args)
            second = json.loads(output.getvalue())
            self.assertEqual(second["articles"][0]["index"], 21)
            self.assertEqual(second["articles"][0]["note_id"], "n21")
            self.assertEqual(list_articles.call_count, 1)

    def test_compact_analysis_removes_duplicate_arrays(self):
        result = {
            "note_id": "n1",
            "counts": {"positive": 1},
            "classified": [{"content": "重复内容"}],
            "sorted_by_likes": [{"content": "热门内容", "likes": 2}],
            "active_users": {"用户": 2},
        }
        compact = main.compact_analysis(result)
        self.assertNotIn("classified", compact)
        self.assertEqual(compact["top_comments"][0]["content"], "热门内容")

    @patch("main.CommentScanner.scan_via_notifications")
    def test_scan_with_output_only_prints_summary(self, scan):
        scan.return_value = {
            "note_id": "n1",
            "source": "notifications",
            "unreplied_level1": [{
                "comment_id": "c1",
                "nickname": "用户",
                "content": "不应在终端重复的评论正文",
                "inline_subs": [{"large": "internal"}],
            }],
            "unreplied_subs": [],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = os.path.join(temp_dir, "scan.json")
            args = argparse.Namespace(
                full_scan=False,
                note_id="n1",
                json=True,
                num_notifications=20,
                xsec_token="",
                output=output_path,
                with_subs=False,
                refresh=False,
                max_pages=None,
            )
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                main.cmd_scan(args)
            response = json.loads(stdout.getvalue())
            with open(output_path, encoding="utf-8") as saved_file:
                saved = json.load(saved_file)
            self.assertNotIn("不应在终端重复的评论正文", stdout.getvalue())
            self.assertEqual(response["summary"]["unreplied_level1"], 1)
            self.assertNotIn("inline_subs", saved["unreplied_level1"][0])
