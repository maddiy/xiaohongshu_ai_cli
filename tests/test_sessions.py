"""持久登录会话和批量请求测试。"""

from tests.support import *
from config import COMMENT_HELPER_REQUEST_TIMEOUT


class PersistentSessionTests(unittest.TestCase):
    @patch("lib.xhs_client.subprocess.Popen")
    @patch("lib.xhs_client.os.path.exists", return_value=True)
    @patch(
        "lib.xhs_client.XHSClient._find_xhs_tool_python",
        return_value="/tool/python",
    )
    def test_persistent_reply_session_reuses_one_process(
        self, _tool_python, _exists, popen
    ):
        process = popen.return_value
        process.stdout.readline.side_effect = [
            '{"ok":true,"comment_id":"c1"}\n',
            '{"ok":true,"comment_id":"c2"}\n',
        ]
        process.wait.return_value = 0
        client = XHSClient()
        with client.reply_session("n1") as session:
            self.assertEqual(session.reply("c1", "回复一"), (True, "", ""))
            self.assertEqual(session.reply("c2", "回复二"), (True, "", ""))
        popen.assert_called_once()
        self.assertEqual(process.stdin.write.call_count, 2)

    @patch("lib.xhs_client.subprocess.Popen")
    @patch("lib.xhs_client.os.path.exists", return_value=True)
    @patch(
        "lib.xhs_client.XHSClient._find_xhs_tool_python",
        return_value="/tool/python",
    )
    def test_persistent_session_can_post_top_level_comment(
        self, _tool_python, _exists, popen
    ):
        process = popen.return_value
        process.stdout.readline.return_value = (
            '{"ok":true,"action":"comment","sequence":1,'
            '"comment_id":"new-comment"}\n'
        )
        process.wait.return_value = 0
        client = XHSClient()
        with client.reply_session("n1") as session:
            self.assertEqual(
                session.comment("顶层评论", sequence=1),
                (True, "", "", "new-comment"),
            )
        payload = json.loads(
            process.stdin.write.call_args.args[0].strip()
        )
        self.assertEqual(payload["action"], "comment")
        self.assertEqual(payload["sequence"], 1)
        self.assertEqual(payload["content"], "顶层评论")

    @patch(
        "lib.xhs_client.XHSClient._find_xhs_tool_python",
        return_value="/tool/python",
    )
    @patch("lib.xhs_client.XHSClient._run_xhs")
    def test_full_comment_read_uses_one_session_and_expands_subs(
        self, run_xhs, _tool_python
    ):
        run_xhs.return_value = {
            "ok": True,
            "data": {"comments": [{"id": "root"}]},
        }
        result = XHSClient.get_all_comments(
            "n1", "token", include_sub_comments=True
        )
        self.assertEqual(result, [{"id": "root"}])
        command = run_xhs.call_args.args[0]
        self.assertTrue(command[1].endswith("xhs_comments_helper.py"))
        self.assertEqual(command[9], "1")
        helper_budget = float(command[11])
        request_timeout = float(command[12])
        parent_timeout = run_xhs.call_args.kwargs["timeout"]
        self.assertEqual(request_timeout, COMMENT_HELPER_REQUEST_TIMEOUT)
        self.assertGreater(
            parent_timeout, helper_budget + request_timeout * 2
        )

    @patch("lib.xhs_client.select.select", return_value=([], [], []))
    @patch("lib.xhs_client.subprocess.Popen")
    @patch("lib.xhs_client.os.path.exists", return_value=True)
    @patch(
        "lib.xhs_client.XHSClient._find_xhs_tool_python",
        return_value="/tool/python",
    )
    def test_persistent_session_times_out_and_terminates_helper(
        self, _tool_python, _exists, popen, _select
    ):
        process = popen.return_value
        process.poll.return_value = None
        process.wait.return_value = 0
        client = XHSClient()
        with client.reply_session("n1") as session:
            session.response_timeout = 0.1
            result = session.reply("c1", "回复")
        self.assertFalse(result[0])
        self.assertEqual(result[2], "session_error")
        self.assertIn("响应超时", result[1])
        process.terminate.assert_called()
        process.stdout.readline.assert_not_called()

    @patch("lib.xhs_client.select.select")
    @patch("lib.xhs_client.subprocess.Popen")
    @patch("lib.xhs_client.os.path.exists", return_value=True)
    @patch(
        "lib.xhs_client.XHSClient._find_xhs_tool_python",
        return_value="/tool/python",
    )
    def test_persistent_session_returns_redacted_stderr_tail(
        self, _tool_python, _exists, popen, select_mock
    ):
        process = popen.return_value
        process.stdout.readline.return_value = ""
        process.wait.return_value = 0
        select_mock.return_value = ([process.stdout], [], [])
        client = XHSClient()
        with client.reply_session("n1") as session:
            session.stderr_file.write("cookie=secret-value")
            result = session.reply("c1", "回复")
        self.assertIn("已隐藏", result[1])
        self.assertNotIn("secret-value", result[1])

    @patch("lib.replier.time.sleep")
    def test_batch_stops_after_account_level_error(self, _sleep):
        client = MagicMock()
        client.is_skipped.return_value = False
        client.reply.return_value = (
            False, "verification_required", "verification_required"
        )
        drafts = {
            "note_id": "n1",
            "drafts": [
                {
                    "comment_id": "c1", "nickname": "甲",
                    "content": "评论一", "reply": "回复一", "action": "send",
                },
                {
                    "comment_id": "c2", "nickname": "乙",
                    "content": "评论二", "reply": "回复二", "action": "send",
                },
            ],
        }
        stats = Replier(client).send_drafts(drafts)
        self.assertTrue(stats["stopped"])
        self.assertEqual(stats["remaining"], 1)
        client.reply.assert_called_once()
        self.assertEqual(drafts["drafts"][0]["send_status"], "failed")
        self.assertNotIn("send_status", drafts["drafts"][1])

    def test_scan_requests_complete_subs_in_shared_read_session(self):
        client = MagicMock()
        client.get_comments_cached.return_value = ([], False)
        client.get_skipped_ids.return_value = set()
        CommentScanner(client, "author-user").scan_note(
            "n1", include_sub_comments=True, verbose=False
        )
        self.assertTrue(
            client.get_comments_cached.call_args.kwargs[
                "include_sub_comments"
            ]
        )

    def test_known_complete_parent_makes_missing_sub_conclusive(self):
        client = MagicMock()
        client.find_note_xsec.return_value = ""
        client.get_comments_until_ids.return_value = ([{
            "id": "root",
            "sub_comment_count": 0,
            "sub_comments": [],
        }, {
            "id": "unrelated",
            "sub_comment_count": 3,
            "sub_comments": [],
        }], False)
        eligible, excluded = CommentScanner(
            client
        ).verify_candidates_online(
            "n1",
            [{
                "comment_id": "missing-sub",
                "parent_comment_id": "root",
            }],
        )
        self.assertEqual(eligible, [])
        self.assertEqual(
            excluded,
            [{"comment_id": "missing-sub", "reason": "online_missing"}],
        )
        client.get_sub_comments.assert_not_called()
