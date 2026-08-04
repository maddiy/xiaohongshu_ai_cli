"""使用模拟平台覆盖prepare→map→draft→send完整链路。"""

from tests.support import *


class AIReplyEndToEndTests(unittest.TestCase):
    def test_mock_platform_full_reply_workflow(self):
        candidate = {
            "comment_id": "c1", "nickname": "用户", "content": "评论",
        }

        class FakeSendReplier(Replier):
            def send_drafts(self, drafts, **_kwargs):
                for item in drafts["drafts"]:
                    if item.get("comment_id") == "c1":
                        item["send_status"] = "sent"
                return {"success": 1, "fail": 0, "skip": 0}

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = {
                "directory": temp_dir,
                "scan": os.path.join(temp_dir, "scan.json"),
                "reply_map": os.path.join(temp_dir, "reply_map.json"),
                "drafts": os.path.join(temp_dir, "drafts.json"),
                "audit": os.path.join(temp_dir, "audit.json"),
            }
            scanner = MagicMock()
            scanner.scan_via_notifications.return_value = {
                "note_id": "n1", "note_title": "测试笔记",
                "reply_status_verified": True,
                "unreplied_level1": [candidate], "unreplied_subs": [],
            }
            scanner.verify_candidates_online.return_value = ([candidate], [])

            def run(arguments):
                args = build_parser().parse_args(arguments)
                output = io.StringIO()
                with (
                    patch("lib.cli_ai.workflow_paths", return_value=paths),
                    patch("lib.cli_ai.CommentScanner", return_value=scanner),
                    contextlib.redirect_stdout(output),
                ):
                    cli_ai.cmd_ai_reply(args)
                return json.loads(output.getvalue())

            prepared = run([
                "ai-reply", "--note-id", "n1", "--action", "prepare",
            ])
            mapped = run([
                "ai-reply", "--note-id", "n1", "--action", "map",
                "--candidate-index", "1", "--decision", "send",
                "--reply-text", '保留"英文引号"也能安全写入',
                "--logic-verdict", "partly_sound",
                "--logic-reason", "存在可讨论观点",
                "--fact-verdict", "not_applicable",
                "--fact-reason", "没有外部事实主张",
                "--boast-verdict", "none",
                "--boast-reason", "没有自我夸大",
            ])
            drafted = run([
                "ai-reply", "--note-id", "n1", "--action", "draft",
            ])
            with patch("lib.cli_ai.Replier", FakeSendReplier):
                sent = run([
                    "ai-reply", "--note-id", "n1", "--action", "send",
                    "--confirmed", "--batch-id", drafted["batch_id"],
                    "--preview-hash", drafted["preview_hash"],
                ])

        self.assertTrue(prepared["ok"])
        self.assertEqual(prepared["candidates"][0]["candidate_index"], 1)
        self.assertTrue(mapped["ok"])
        self.assertEqual(mapped["remaining_count"], 0)
        self.assertTrue(drafted["new_confirmation_required"])
        self.assertTrue(sent["ok"])
        self.assertEqual(sent["sent"], 1)
