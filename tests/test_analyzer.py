"""增强评论分析测试。"""

from tests.support import *

from lib.analyzer import (
    CommentAnalyzer,
    _build_reply_priorities,
    _extract_keywords,
    _is_question,
)


class AnalyzerTests(unittest.TestCase):
    def test_question_detection_supports_chinese_and_punctuation(self):
        self.assertTrue(_is_question("为什么会这样"))
        self.assertTrue(_is_question("这是事实吗？"))
        self.assertFalse(_is_question("这是一个陈述"))

    def test_keywords_use_cross_comment_frequency(self):
        result = _extract_keywords([
            {"content": "人工智能正在改变产业"},
            {"content": "人工智能需要更多数据"},
            {"content": "天气很好"},
        ])
        self.assertTrue(any("人工智能" in item["term"] for item in result))

    def test_reply_priority_excludes_author_replied_comments(self):
        comments = [
            {
                "content": "为什么？", "like_count": 2,
                "sub_comment_count": 1,
                "user_info": {"user_id": "u1", "nickname": "甲"},
                "sub_comments": [],
            },
            {
                "content": "已回复", "like_count": 9,
                "sub_comment_count": 1,
                "user_info": {"user_id": "u2", "nickname": "乙"},
                "sub_comments": [{"user_info": {"user_id": "author"}}],
            },
        ]
        priorities = _build_reply_priorities(comments, "author")
        self.assertEqual([item["nick"] for item in priorities], ["甲"])
        self.assertTrue(priorities[0]["is_question"])

    @patch("lib.xhs_client.XHSClient.get_author_user_id", return_value="author")
    @patch("lib.xhs_client.XHSClient.get_comments_cached")
    def test_analysis_returns_operational_insights(self, get_comments, _author):
        get_comments.return_value = ([
            {
                "content": "为什么人工智能这么重要？", "like_count": 3,
                "sub_comment_count": 0, "create_time": 1786118400,
                "user_info": {"user_id": "u1", "nickname": "甲"},
                "sub_comments": [],
            },
            {
                "content": "人工智能确实有道理", "like_count": 1,
                "sub_comment_count": 1, "create_time": 1786118400,
                "user_info": {"user_id": "u2", "nickname": "乙"},
                "sub_comments": [{"user_info": {"user_id": "author"}}],
            },
        ], False)
        result = CommentAnalyzer.analyze("n1")
        self.assertEqual(result["reply_rate"], 50.0)
        self.assertEqual(result["question_count"], 1)
        self.assertEqual(result["engagement_total"], 5)
        self.assertTrue(result["keywords"])
        self.assertTrue(result["timeline"])
        self.assertTrue(result["reply_priorities"])
        self.assertTrue(result["recommendations"])
