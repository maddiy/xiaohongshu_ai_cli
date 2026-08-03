"""评论树在线核验和楼中楼精确判定测试。"""

from tests.support import *


class OnlineVerificationTests(unittest.TestCase):
    def test_online_reply_index_tracks_direct_nested_reply(self):
        comments = [{
            "id": "root",
            "sub_comments": [
                {
                    "id": "candidate",
                    "user_info": {"user_id": "other"},
                },
                {
                    "id": "author-reply",
                    "user_info": {"user_id": "author-user"},
                    "target_comment": {"id": "candidate"},
                },
            ],
        }]
        existing, replied = CommentScanner(
            author_user_id="author-user"
        )._online_reply_index(comments)
        self.assertEqual(existing, {"root", "candidate", "author-reply"})
        self.assertEqual(replied, {"candidate"})

    def test_online_verification_excludes_replied_and_missing(self):
        client = MagicMock()
        client.get_comments_until_ids.return_value = [{
            "id": "keep",
            "sub_comments": [{
                "id": "author-reply",
                "user_info": {"user_id": "author-user"},
                "target_comment": {"id": "replied"},
            }, {
                "id": "replied",
                "user_info": {"user_id": "other"},
            }],
        }]
        eligible, excluded = CommentScanner(
            client, "author-user"
        ).verify_candidates_online(
            "note",
            [
                {"comment_id": "keep"},
                {"comment_id": "replied"},
                {"comment_id": "missing"},
            ],
        )
        self.assertEqual(eligible, [{"comment_id": "keep"}])
        self.assertEqual(
            excluded,
            [
                {"comment_id": "replied", "reason": "online_replied"},
                {"comment_id": "missing", "reason": "online_missing"},
            ],
        )

    def test_online_verification_fetches_incomplete_candidate_thread(self):
        client = MagicMock()
        client.get_comments_until_ids.return_value = [{
            "id": "root",
            "sub_comment_count": "2",
            "sub_comments": [{
                "id": "candidate",
                "user_info": {"user_id": "other"},
            }],
        }]
        client.get_sub_comments.return_value = [
            {
                "id": "candidate",
                "user_info": {"user_id": "other"},
            },
            {
                "id": "author-reply",
                "user_info": {"user_id": "author-user"},
                "target_comment": {"id": "candidate"},
            },
        ]
        eligible, excluded = CommentScanner(
            client, "author-user"
        ).verify_candidates_online(
            "note", [{"comment_id": "candidate"}]
        )
        self.assertEqual(eligible, [])
        self.assertEqual(
            excluded,
            [{"comment_id": "candidate", "reason": "online_replied"}],
        )

    def test_online_verification_stops_when_search_is_incomplete(self):
        client = MagicMock()
        client.get_comments_until_ids.return_value = ([], False)
        with self.assertRaisesRegex(RuntimeError, "页数上限"):
            CommentScanner(client, "author-user").verify_candidates_online(
                "note", [{"comment_id": "candidate"}]
            )

    def test_online_verification_defers_unresolved_in_partial_mode(self):
        client = MagicMock()
        client.get_comments_until_ids.return_value = ([], False)
        eligible, excluded = CommentScanner(
            client
        ).verify_candidates_online(
            "note",
            [{"comment_id": "candidate"}],
            max_pages=6,
            allow_partial=True,
        )
        self.assertEqual(eligible, [])
        self.assertEqual(
            excluded,
            [{"comment_id": "candidate", "reason": "online_unresolved"}],
        )
        kwargs = client.get_comments_until_ids.call_args.kwargs
        self.assertEqual(kwargs["max_pages"], 6)
        self.assertFalse(kwargs["expand_unresolved"])
        client.get_sub_comments.assert_not_called()

    def test_online_verification_stops_when_sub_comments_are_incomplete(self):
        client = MagicMock()
        client.get_comments_until_ids.return_value = ([{
            "id": "root",
            "sub_comment_count": "2",
            "sub_comments": [{
                "id": "candidate",
                "user_info": {"user_id": "other"},
            }],
        }], True)
        client.get_sub_comments.return_value = [{
            "id": "candidate",
            "user_info": {"user_id": "other"},
        }]
        with self.assertRaisesRegex(RuntimeError, "楼中楼在线数据不完整"):
            CommentScanner(client, "author-user").verify_candidates_online(
                "note", [{"comment_id": "candidate"}]
            )

    def test_online_verification_ignores_unrelated_incomplete_thread(self):
        client = MagicMock()
        client.get_comments_until_ids.return_value = ([{
            "id": "unrelated",
            "sub_comment_count": "2",
            "sub_comments": [],
        }, {
            "id": "candidate",
            "sub_comment_count": "0",
            "sub_comments": [],
        }], True)
        eligible, excluded = CommentScanner(
            client
        ).verify_candidates_online(
            "note", [{"comment_id": "candidate"}]
        )
        self.assertEqual(eligible, [{"comment_id": "candidate"}])
        self.assertEqual(excluded, [])
        client.get_sub_comments.assert_not_called()

    def test_online_verification_defers_unrelated_failure_until_located(self):
        client = MagicMock()
        client.get_comments_until_ids.return_value = ([{
            "id": "unrelated",
            "sub_comment_count": "2",
            "sub_comments": [],
        }, {
            "id": "candidate-thread",
            "sub_comment_count": "1",
            "sub_comments": [],
        }], True)

        def get_sub_comments(
            _note_id, comment_id, _xsec_token, strict=False
        ):
            if comment_id == "unrelated":
                return []
            return [{
                "id": "candidate",
                "user_info": {"user_id": "other"},
            }]

        client.get_sub_comments.side_effect = get_sub_comments
        eligible, excluded = CommentScanner(
            client, "author-user"
        ).verify_candidates_online(
            "note", [{"comment_id": "candidate"}]
        )
        self.assertEqual(eligible, [{"comment_id": "candidate"}])
        self.assertEqual(excluded, [])
        self.assertEqual(client.get_sub_comments.call_count, 2)

    def test_online_verification_uses_target_hint_before_unrelated_thread(
        self
    ):
        client = MagicMock()
        client.get_comments_until_ids.return_value = ([{
            "id": "unrelated",
            "sub_comment_count": "2",
            "sub_comments": [],
        }, {
            "id": "root1",
            "sub_comment_count": "2",
            "sub_comments": [{
                "id": "target1",
                "user_info": {"user_id": "other"},
            }],
        }], True)
        client.get_sub_comments.return_value = [{
            "id": "target1",
            "user_info": {"user_id": "other"},
        }, {
            "id": "candidate",
            "user_info": {"user_id": "other"},
        }]
        eligible, excluded = CommentScanner(
            client, "author-user"
        ).verify_candidates_online(
            "note",
            [{
                "comment_id": "candidate",
                "target_comment_id": "target1",
            }],
        )
        self.assertEqual(
            eligible,
            [{
                "comment_id": "candidate",
                "target_comment_id": "target1",
            }],
        )
        self.assertEqual(excluded, [])
        self.assertEqual(
            client.get_comments_until_ids.call_args.kwargs[
                "target_anchors"
            ],
            [["target1", "candidate"]],
        )
        client.get_sub_comments.assert_called_once_with(
            "note", "root1", "", strict=True
        )

    def test_online_verification_stops_if_unresolved_may_be_in_failed_thread(
        self
    ):
        client = MagicMock()
        client.get_comments_until_ids.return_value = ([{
            "id": "unknown-thread",
            "sub_comment_count": "2",
            "sub_comments": [],
        }], True)
        client.get_sub_comments.return_value = []
        with self.assertRaisesRegex(
            RuntimeError, "候选评论尚未定位"
        ):
            CommentScanner(client, "author-user").verify_candidates_online(
                "note", [{"comment_id": "candidate"}]
            )

    @patch("lib.scanner.time.sleep")
    def test_scan_keeps_unreplied_nested_comment_when_root_was_replied(
        self, _sleep
    ):
        author_id = "author-user"
        client = MagicMock()
        client.get_skipped_ids.return_value = set()
        client.get_comments_cached.return_value = ([{
            "id": "root",
            "content": "一级评论",
            "user_info": {"user_id": "other", "nickname": "甲"},
            "sub_comment_count": "4",
            "sub_comments": [{
                "id": "reply-root",
                "user_info": {"user_id": author_id},
                "target_comment": {"id": "root"},
            }],
        }], False)
        client.get_sub_comments.return_value = [
            {
                "id": "reply-root",
                "user_info": {"user_id": author_id},
                "target_comment": {"id": "root"},
            },
            {
                "id": "nested-replied",
                "content": "已回复的楼中楼",
                "user_info": {"user_id": "other", "nickname": "乙"},
            },
            {
                "id": "reply-nested",
                "user_info": {"user_id": author_id},
                "target_comment": {"id": "nested-replied"},
            },
            {
                "id": "nested-pending",
                "content": "尚未回复的楼中楼",
                "user_info": {"user_id": "other", "nickname": "丙"},
            },
        ]
        result = CommentScanner(client, "author-user").scan_note(
            "note", include_sub_comments=True, verbose=False
        )
        self.assertEqual(result["unreplied_level1"], [])
        self.assertEqual(
            [item["comment_id"] for item in result["unreplied_subs"]],
            ["nested-pending"],
        )
        self.assertTrue(result["reply_status_verified"])

    def test_complete_inline_nested_comments_use_target_specific_reply(self):
        author_id = "author-user"
        client = MagicMock()
        client.get_skipped_ids.return_value = set()
        client.get_comments_cached.return_value = ([{
            "id": "root",
            "content": "一级评论",
            "user_info": {"user_id": "other", "nickname": "甲"},
            "sub_comment_count": "3",
            "sub_comments": [
                {
                    "id": "nested-replied",
                    "content": "已回复",
                    "user_info": {"user_id": "other", "nickname": "乙"},
                },
                {
                    "id": "author-reply",
                    "user_info": {"user_id": author_id},
                    "target_comment": {"id": "nested-replied"},
                },
                {
                    "id": "nested-pending",
                    "content": "未回复",
                    "user_info": {"user_id": "other", "nickname": "丙"},
                },
            ],
        }], False)
        result = CommentScanner(client, "author-user").scan_note(
            "note", include_sub_comments=True, verbose=False
        )
        self.assertEqual(
            [item["comment_id"] for item in result["unreplied_level1"]],
            ["root"],
        )
        self.assertEqual(
            [item["comment_id"] for item in result["unreplied_subs"]],
            ["nested-pending"],
        )

    @patch("lib.scanner.time.sleep")
    def test_scan_marks_incomplete_nested_data_unverified(self, _sleep):
        client = MagicMock()
        client.get_skipped_ids.return_value = set()
        client.get_comments_cached.return_value = ([{
            "id": "root",
            "content": "一级评论",
            "user_info": {"user_id": "other", "nickname": "甲"},
            "sub_comment_count": "2",
            "sub_comments": [],
        }], False)
        client.get_sub_comments.return_value = []
        result = CommentScanner(client, "author-user").scan_note(
            "note", include_sub_comments=True, verbose=False
        )
        self.assertFalse(result["reply_status_verified"])
        self.assertIn("楼中楼数据不完整", result["scan_error"])
        self.assertEqual(result["unreplied_subs"], [])
