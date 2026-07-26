import argparse
import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import main


class CompactOutputTests(unittest.TestCase):
    def test_compact_comment_removes_internal_fields(self):
        source = {
            "comment_id": "c1",
            "nickname": "用户",
            "content": "内容",
            "likes": 2,
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
            },
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
        self.assertEqual(payload["articles"][0]["id"], "n1")
        self.assertNotIn("xsec_token", payload["articles"][0])
        self.assertNotIn("secret", output.getvalue())

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


if __name__ == "__main__":
    unittest.main()
