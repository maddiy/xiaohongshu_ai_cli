"""通知、评论分页、令牌和楼中楼传输测试。"""

from tests.support import *
from types import SimpleNamespace


class XHSClientTests(unittest.TestCase):
    @patch("lib.xhs_client_content.XHSClient._merge_xsec_index")
    @patch("lib.xhs_client_content.XHSClient._run_xhs")
    @patch("lib.xhs_client_content.read_json_state")
    @patch("lib.xhs_client_content.json_state_exists", return_value=True)
    @patch("lib.xhs_client_content.time.sleep")
    def test_my_notes_can_include_cached_items_to_refresh_metrics(
        self, _sleep, _exists, read_state, run_xhs, _merge_index
    ):
        read_state.return_value = {
            "articles": [{"note_id": "n1", "comments_count": 2}],
        }
        run_xhs.return_value = {
            "ok": True,
            "data": {"notes": [{
                "id": "n1",
                "display_title": "文章",
                "comments_count": 19,
                "view_count": 2500,
                "time": "2026-08-08",
            }]},
        }

        notes = XHSClient.get_my_notes(
            max_pages=1, strict=True, include_cached=True
        )

        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]["comments_count"], 19)
        self.assertEqual(notes[0]["view_count"], 2500)

    def test_notification_scan_online_verifies_candidates(self):
        client = MagicMock()
        client.get_new_comment_notifications.return_value = [{
            "note_id": "n1",
            "note_title": "文章",
            "note_xsec_token": "token",
            "new_comments": [{
                "comment_id": "c1",
                "nickname": "用户",
                "content": "评论",
                "target_comment_id": "root1",
                "deleted": False,
            }],
        }]
        client.get_skipped_ids.return_value = set()
        client.get_comments_until_ids.return_value = [{
            "id": "c1",
            "sub_comment_count": "0",
            "sub_comments": [],
        }]
        result = CommentScanner(client, "author-user").scan_via_notifications(
            note_id="n1", verbose=False
        )
        self.assertTrue(result["reply_status_verified"])
        self.assertEqual(result["unreplied_level1"][0]["comment_id"], "c1")
        self.assertEqual(
            result["unreplied_level1"][0]["target_comment_id"],
            "root1",
        )

    def test_notification_scan_filters_deleted_before_online_check(self):
        client = MagicMock()
        client.get_new_comment_notifications.return_value = [{
            "note_id": "n1",
            "note_title": "文章",
            "note_xsec_token": "token",
            "new_comments": [{
                "comment_id": "c1",
                "nickname": "用户",
                "content": "评论",
                "deleted": True,
            }],
        }]
        client.get_skipped_ids.return_value = set()
        result = CommentScanner(client, "author-user").scan_via_notifications(
            note_id="n1", verbose=False
        )
        self.assertTrue(result["reply_status_verified"])
        self.assertEqual(result["unreplied_level1"], [])
        self.assertEqual(result["filtered_deleted"], 1)
        client.get_comments_until_ids.assert_not_called()

    def test_notification_scan_filters_local_terminal_before_online_check(self):
        client = MagicMock()
        client.get_new_comment_notifications.return_value = [{
            "note_id": "n1",
            "note_title": "文章",
            "note_xsec_token": "token",
            "new_comments": [{
                "comment_id": "sent",
                "nickname": "旧用户",
                "content": "已处理",
                "deleted": False,
            }, {
                "comment_id": "new",
                "nickname": "新用户",
                "content": "新评论",
                "deleted": False,
            }],
        }]
        client.get_skipped_ids.return_value = set()
        client.get_comments_until_ids.return_value = [{
            "id": "new",
            "sub_comment_count": "0",
            "sub_comments": [],
        }]
        result = CommentScanner(client, "author-user").scan_via_notifications(
            note_id="n1",
            verbose=False,
            excluded_comment_ids={"sent"},
        )
        self.assertEqual(result["filtered_local"], 1)
        self.assertEqual(
            [item["comment_id"] for item in result["unreplied_level1"]],
            ["new"],
        )
        requested = client.get_comments_until_ids.call_args.args[1]
        self.assertNotIn("sent", requested)

    def test_notification_scan_reports_network_failure(self):
        client = MagicMock()
        client.get_new_comment_notifications.side_effect = RuntimeError(
            "network unavailable"
        )
        result = CommentScanner(client, "author-user").scan_via_notifications(verbose=False)
        self.assertFalse(result["reply_status_verified"])
        self.assertIn("network unavailable", result["scan_error"])

    def test_cli_error_marks_dns_failure_with_actionable_message(self):
        result = SimpleNamespace(
            stdout="",
            stderr=(
                "api_error: Request failed after 3 retries: [Errno 8] "
                "nodename nor servname provided, or not known"
            ),
        )
        message = XHSClient._cli_error_message(result)
        self.assertIn("network_dns_failure", message)
        self.assertIn("DNS", message)

    @patch("lib.xhs_client.XHSClient._run_xhs")
    def test_notifications_strict_mode_does_not_hide_failure(self, run_xhs):
        run_xhs.side_effect = RuntimeError("network unavailable")
        with self.assertRaises(RuntimeError):
            XHSClient.get_notifications(strict=True)

    @patch("lib.xhs_client.XHSClient.get_notifications")
    def test_comment_notifications_fallback_to_id_and_deduplicate(
        self, notifications
    ):
        item = {
            "type": "comment",
            "item_info": {"id": "n1", "content": "文章", "link": ""},
            "comment_info": {
                "id": "c1",
                "content": "评论",
                "target_comment": {"id": "root1"},
                "illegal_info": {"illegal_status": "NORMAL"},
            },
            "user_info": {"nickname": "用户", "user_id": "u1"},
            "time": 1,
        }
        notifications.return_value = [item, dict(item)]
        result = XHSClient.get_new_comment_notifications(num=20)
        self.assertEqual(len(result[0]["new_comments"]), 1)
        self.assertEqual(result[0]["new_comments"][0]["comment_id"], "c1")
        self.assertEqual(
            result[0]["new_comments"][0]["target_comment_id"],
            "root1",
        )
        self.assertEqual(result[0]["new_comments"][0]["user_id"], "u1")

    @patch("lib.xhs_client.XHSClient._merge_xsec_index")
    @patch("lib.xhs_client.XHSClient.get_notifications")
    def test_comment_notifications_persist_token_for_later_steps(
        self, notifications, merge_index
    ):
        notifications.return_value = [{
            "type": "comment",
            "item_info": {
                "id": "n1",
                "content": "文章",
                "xsec_token": "secret-token",
            },
            "comment_info": {
                "id": "c1",
                "content": "评论",
                "illegal_info": {"illegal_status": "NORMAL"},
            },
            "user_info": {"nickname": "用户"},
        }]
        result = XHSClient.get_new_comment_notifications(num=20)
        self.assertEqual(
            result[0]["note_xsec_token"], "secret-token"
        )
        merge_index.assert_called_once_with({"n1": "secret-token"})

    @patch("lib.xhs_client.time.sleep")
    @patch("lib.xhs_client.XHSClient._run_xhs")
    def test_comment_lookup_stops_when_candidate_context_is_found(
        self, run_xhs, _sleep
    ):
        run_xhs.return_value = {
            "ok": True,
            "data": {
                "comments": [{
                    "id": "root1",
                    "sub_comments": [],
                }],
                "has_more": True,
                "cursor": "next",
            },
        }
        comments, complete = XHSClient.get_comments_until_ids(
            "note",
            {"candidate"},
            with_status=True,
            target_groups=[{"candidate", "root1"}],
        )
        self.assertTrue(complete)
        self.assertEqual(comments[0]["id"], "root1")
        run_xhs.assert_called_once()

    @patch(
        "lib.xhs_client.XHSClient._find_xhs_tool_python",
        return_value="/tool/python",
    )
    @patch("lib.xhs_client.XHSClient._run_xhs")
    def test_comment_lookup_uses_one_session_for_pagination(
        self, run_xhs, _tool_python
    ):
        run_xhs.return_value = {
            "ok": True,
            "data": {
                "comments": [{"id": "root1", "sub_comments": []}],
                "search_complete": True,
                "pages_fetched": 4,
            },
        }
        comments, complete = XHSClient.get_comments_until_ids(
            "note",
            {"candidate", "root1"},
            xsec_token="secret-token",
            with_status=True,
            target_groups=[{"candidate", "root1"}],
            target_anchors=[["root1", "candidate"]],
        )
        self.assertTrue(complete)
        self.assertEqual(comments[0]["id"], "root1")
        run_xhs.assert_called_once()
        command = run_xhs.call_args.args[0]
        self.assertTrue(command[1].endswith("xhs_comments_helper.py"))
        self.assertEqual(command[2], "note")
        self.assertEqual(json.loads(command[8]), [["root1", "candidate"]])
        self.assertEqual(command[9], "1")

    @patch("lib.xhs_client.subprocess.run")
    def test_run_xhs_timeout_does_not_leak_command_secrets(self, run):
        run.side_effect = subprocess.TimeoutExpired(
            ["helper", "--xsec-token", "secret-token"], 12
        )
        with self.assertRaisesRegex(RuntimeError, "请求超时") as raised:
            XHSClient._run_xhs(
                ["helper", "--xsec-token", "secret-token"],
                timeout=12,
            )
        self.assertNotIn("secret-token", str(raised.exception))

    def test_xsec_index_is_atomic_and_private(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "xsec_index.json")
            with patch(
                "lib.xhs_client.XHSClient._xsec_index_path",
                return_value=path,
            ):
                with patch(
                    "lib.xhs_client.XHSClient._ensure_cache_dir"
                ):
                    XHSClient._merge_xsec_index({"n1": "token"})
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            with open(path, encoding="utf-8") as file:
                self.assertEqual(json.load(file), {"n1": "token"})

    @patch("lib.xhs_client.os.path.exists", return_value=True)
    @patch("lib.xhs_client.XHSClient._run_xhs")
    def test_sub_comments_use_xsec_compatibility_helper(
        self, run_xhs, _exists
    ):
        run_xhs.return_value = {
            "ok": True,
            "data": {"comments": [{"id": "sub"}], "cursor": ""},
        }
        result = XHSClient.get_sub_comments(
            "note", "root", xsec_token="secret-token"
        )
        self.assertEqual(result, [{"id": "sub"}])
        command = run_xhs.call_args.args[0]
        self.assertTrue(command[1].endswith("xhs_subcomments_helper.py"))
        self.assertEqual(command[2:4], ["note", "root"])
        self.assertIn("secret-token", command)

    @patch(
        "lib.xhs_client.XHSClient._find_xhs_tool_python",
        return_value="/tool/python",
    )
    @patch("lib.xhs_client.XHSClient._run_xhs")
    def test_sub_comments_fall_back_to_native_after_helper_failure(
        self, run_xhs, _tool_python
    ):
        run_xhs.side_effect = [
            RuntimeError("helper failed"),
            {
                "ok": True,
                "data": {
                    "comments": [{"id": "sub"}],
                    "cursor": "",
                },
            },
        ]
        result = XHSClient.get_sub_comments(
            "note", "root", xsec_token="token", strict=True
        )
        self.assertEqual(result, [{"id": "sub"}])
        self.assertEqual(run_xhs.call_count, 2)
        self.assertEqual(run_xhs.call_args_list[1].args[0][0], "xhs")

    @patch("lib.xhs_client.subprocess.run")
    def test_run_xhs_preserves_structured_verification_error(
        self, run
    ):
        run.return_value = MagicMock(
            returncode=1,
            stdout=json.dumps({
                "ok": False,
                "error": {
                    "code": "verification_required",
                    "message": "Captcha required.",
                },
            }),
            stderr="WARNING: captcha cooling down",
        )
        with self.assertRaisesRegex(
            RuntimeError,
            "verification_required: Captcha required",
        ):
            XHSClient._run_xhs(["xhs", "sub-comments"])

    @patch(
        "lib.xhs_client.XHSClient._find_xhs_tool_python",
        return_value="/tool/python",
    )
    @patch(
        "lib.xhs_client.XHSClient._run_xhs",
        side_effect=RuntimeError("verification_required"),
    )
    def test_sub_comments_strict_mode_preserves_failure(
        self, run_xhs, _tool_python
    ):
        with self.assertRaisesRegex(RuntimeError, "verification_required"):
            XHSClient.get_sub_comments(
                "note", "root", xsec_token="token", strict=True
            )
        run_xhs.assert_called_once()

    @patch(
        "lib.xhs_client.XHSClient._find_xhs_tool_python",
        return_value="/tool/python",
    )
    @patch("lib.xhs_client.XHSClient._run_xhs")
    def test_sub_comments_does_not_fallback_on_structured_verification(
        self, run_xhs, _tool_python
    ):
        run_xhs.return_value = {
            "ok": False,
            "error": {
                "code": "verification_required",
                "message": "Captcha required: type=unknown, uuid=unknown",
            },
        }
        with self.assertRaisesRegex(RuntimeError, "verification_required"):
            XHSClient.get_sub_comments(
                "note", "root", xsec_token="token", strict=True
            )
        run_xhs.assert_called_once()
