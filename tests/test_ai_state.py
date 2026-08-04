"""AI回复状态摘要和显式失败重试测试。"""

from tests.support import *


class AIReplyStateTests(unittest.TestCase):
    @staticmethod
    def _paths(temp_dir):
        return {
            "directory": temp_dir,
            "scan": os.path.join(temp_dir, "scan.json"),
            "reply_map": os.path.join(temp_dir, "reply_map.json"),
            "drafts": os.path.join(temp_dir, "drafts.json"),
            "audit": os.path.join(temp_dir, "audit.json"),
        }

    def test_status_summarizes_state_without_confirmation_binding(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = self._paths(temp_dir)
            with open(paths["scan"], "w", encoding="utf-8") as file:
                json.dump({
                    "reply_status_verified": True,
                    "unreplied_level1": [
                        {"comment_id": "c1"}, {"comment_id": "c2"},
                    ],
                    "unreplied_subs": [],
                }, file)
            with open(paths["reply_map"], "w", encoding="utf-8") as file:
                json.dump({
                    "c1": {
                        "reply": "回复", "action": "send",
                        "review": _valid_review(),
                    },
                }, file)
            with open(paths["drafts"], "w", encoding="utf-8") as file:
                json.dump({
                    "workflow_revision": 3,
                    "active_comment_ids": ["c1"],
                    "active_batch": {
                        "status": "previewed", "batch_id": "secret-batch",
                        "preview_hash": "secret-hash",
                    },
                    "drafts": [
                        {"comment_id": "c1"},
                        {"comment_id": "old", "send_status": "failed"},
                    ],
                }, file)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                cli_ai._status(argparse.Namespace(note_id="n1"), paths)
            payload = json.loads(output.getvalue())
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["candidate_count"], 2)
        self.assertEqual(payload["mapped_count"], 1)
        self.assertEqual(payload["failed_count"], 1)
        self.assertFalse(payload["confirmation_binding_exposed"])
        self.assertNotIn("secret-batch", output.getvalue())
        self.assertNotIn("secret-hash", output.getvalue())

    @patch("lib.cli_ai_state.XHSClient.remove_skipped", return_value=True)
    def test_retry_requires_authorization_and_resets_failed_state(
        self, remove_skipped
    ):
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = self._paths(temp_dir)
            with open(paths["drafts"], "w", encoding="utf-8") as file:
                json.dump({
                    "active_comment_ids": ["c1"],
                    "active_batch": {"status": "completed_with_failures"},
                    "drafts": [{
                        "comment_id": "c1", "action": "send",
                        "send_status": "failed", "error_type": "rate_limited",
                        "last_error": "失败",
                    }],
                }, file)
            denied_output = io.StringIO()
            with contextlib.redirect_stdout(denied_output):
                cli_ai._retry(argparse.Namespace(
                    note_id="n1", comment_id="c1", retry_authorized=False,
                ), paths)
            denied = json.loads(denied_output.getvalue())
            self.assertEqual(
                denied["error_type"], "retry_confirmation_required"
            )

            allowed_output = io.StringIO()
            with contextlib.redirect_stdout(allowed_output):
                cli_ai._retry(argparse.Namespace(
                    note_id="n1", comment_id="c1", retry_authorized=True,
                ), paths)
            allowed = json.loads(allowed_output.getvalue())
            with open(paths["drafts"], encoding="utf-8") as file:
                drafts = json.load(file)
        self.assertTrue(allowed["state_reset"])
        self.assertTrue(allowed["requires_redraft"])
        self.assertEqual(drafts["active_comment_ids"], [])
        self.assertNotIn("send_status", drafts["drafts"][0])
        self.assertNotIn("last_error", drafts["drafts"][0])
        remove_skipped.assert_called_once_with("c1")
