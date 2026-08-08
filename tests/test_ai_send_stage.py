"""直接覆盖AI发送阶段的确认绑定、在线核验和状态保存。"""

from tests.support import *

from lib import cli_ai_send


class AISendStageTests(unittest.TestCase):
    @staticmethod
    def _paths(temp_dir):
        return {
            "directory": temp_dir,
            "scan": os.path.join(temp_dir, "scan.json"),
            "reply_map": os.path.join(temp_dir, "reply_map.json"),
            "drafts": os.path.join(temp_dir, "drafts.json"),
            "audit": os.path.join(temp_dir, "audit.json"),
        }

    def test_send_stage_rejects_missing_confirmation_before_reading_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                cli_ai_send._send(argparse.Namespace(
                    confirmed=False, note_id="n1",
                    batch_id=None, preview_hash=None,
                ), self._paths(temp_dir))
        payload = json.loads(output.getvalue())
        self.assertFalse(payload["ok"])
        self.assertIn("--confirmed", payload["error"])

    def test_send_stage_rejects_stale_preview_and_records_attempt(self):
        item = {
            "comment_id": "c1", "nickname": "用户", "content": "评论",
            "reply": "回复", "action": "send",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = self._paths(temp_dir)
            with open(paths["drafts"], "w", encoding="utf-8") as file:
                json.dump({
                    "active_comment_ids": ["c1"],
                    "drafts": [item],
                    "active_batch": {
                        "batch_id": "current",
                        "preview_hash": cli_ai.preview_hash([item]),
                        "status": "previewed", "revision": 1,
                    },
                }, file)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                cli_ai_send._send(argparse.Namespace(
                    confirmed=True, note_id="n1",
                    batch_id="old", preview_hash="old-hash",
                ), paths)
            payload = json.loads(output.getvalue())
            with open(paths["drafts"], encoding="utf-8") as file:
                saved = json.load(file)
        self.assertEqual(payload["error_type"], "stale_preview")
        self.assertEqual(saved["send_attempts"][-1]["outcome"], "rejected")

    @patch("lib.cli_ai_send.Replier")
    @patch("lib.cli_ai_send.CommentScanner")
    def test_send_stage_rechecks_and_sends_active_item(
        self, scanner_class, replier_class
    ):
        item = {
            "comment_id": "c1", "nickname": "用户", "content": "评论",
            "reply": "回复", "action": "send",
        }
        scanner_class.return_value.verify_candidates_online.return_value = (
            [item], []
        )

        def send_drafts(drafts, **_kwargs):
            drafts["drafts"][0]["send_status"] = "sent"
            return {"success": 1, "fail": 0, "skip": 0}

        replier_class.return_value.send_drafts.side_effect = send_drafts
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = self._paths(temp_dir)
            digest = cli_ai.preview_hash([item])
            with open(paths["drafts"], "w", encoding="utf-8") as file:
                json.dump({
                    "active_comment_ids": ["c1"],
                    "drafts": [item],
                    "active_batch": {
                        "batch_id": "batch", "preview_hash": digest,
                        "status": "previewed", "revision": 1,
                    },
                }, file)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                cli_ai_send._send(argparse.Namespace(
                    confirmed=True, note_id="n1",
                    batch_id="batch", preview_hash=digest,
                ), paths)
            payload = json.loads(output.getvalue())
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["sent"], 1)
        scanner_class.return_value.verify_candidates_online.assert_called_once()
        replier_class.return_value.send_drafts.assert_called_once()
