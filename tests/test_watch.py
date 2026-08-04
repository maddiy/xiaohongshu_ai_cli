"""新评论监控、过滤、检查点和自动回复测试。"""

from tests.support import *

from lib.cli_watch import watch_event_payload
from lib.comment_watcher import CommentWatchError, CommentWatcher


def _notification(comment_id, nickname="用户", user_id="u1",
                  note_id="n1", timestamp=1):
    return {
        "note_id": note_id,
        "note_title": f"文章{note_id}",
        "note_xsec_token": "sensitive-token",
        "new_comments": [{
            "comment_id": comment_id,
            "nickname": nickname,
            "user_id": user_id,
            "content": f"评论{comment_id}",
            "time": timestamp,
            "target_comment_id": "",
            "deleted": False,
        }],
    }


class _FakeSession:
    process = object()

    def __init__(self, result=(True, "", "")):
        self.result = result
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def reply(self, comment_id, content):
        self.calls.append((comment_id, content))
        return self.result


class _FakeClient:
    def __init__(self, batches, reply_result=(True, "", "")):
        self.batches = list(batches)
        self.session = _FakeSession(reply_result)
        self.skipped = set()
        self.skipped_records = []

    def get_new_comment_notifications(self, num=50):
        if len(self.batches) > 1:
            return self.batches.pop(0)
        return self.batches[0] if self.batches else []

    def get_skipped_ids(self):
        return set(self.skipped)

    def reply_session(self, _note_id):
        return self.session

    def add_skipped(self, comment_id, *_args, **kwargs):
        self.skipped.add(comment_id)
        self.skipped_records.append((comment_id, kwargs))


class _FakeScanner:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def verify_candidates_online(self, note_id, candidates, token):
        self.calls.append((note_id, candidates, token))
        if self.error:
            raise self.error
        return candidates, []


class _UnresolvedScanner(_FakeScanner):
    def verify_candidates_online(self, note_id, candidates, token):
        self.calls.append((note_id, candidates, token))
        return [], []


def _no_lock(*_args, **_kwargs):
    return contextlib.nullcontext()


class CommentWatcherTests(unittest.TestCase):
    def _watcher(self, temp_dir, client, scanner=None, **kwargs):
        archive_writer = kwargs.pop(
            "archive_writer",
            lambda groups: {
                "ok": True,
                "comments": sum(
                    len(group["comments"]) for group in groups
                ),
            },
        )
        return CommentWatcher(
            client=client,
            scanner=scanner or _FakeScanner(),
            database=StateDB(os.path.join(temp_dir, "state.sqlite3")),
            lock_factory=_no_lock,
            archive_writer=archive_writer,
            **kwargs,
        )

    def test_first_poll_only_establishes_baseline_then_detects_new(self):
        batches = [
            [_notification("c1")],
            [_notification("c1"), _notification("c2", timestamp=2)],
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            watcher = self._watcher(temp_dir, _FakeClient(batches))
            baseline = watcher.poll()
            event = watcher.poll()
        self.assertEqual(baseline["event"], "baseline")
        self.assertEqual(baseline["baseline_count"], 1)
        self.assertEqual(baseline["new_count"], 0)
        self.assertEqual(event["detected_count"], 1)
        self.assertEqual(event["items"][0]["comment_id"], "c2")
        self.assertEqual(event["items"][0]["status"], "发现新评论")

    def test_note_and_user_filters_are_exact(self):
        batch = [
            _notification("c1", nickname="甲", user_id="u1", note_id="n1"),
            _notification("c2", nickname="乙", user_id="u2", note_id="n1"),
            _notification("c3", nickname="甲", user_id="u1", note_id="n2"),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            watcher = self._watcher(
                temp_dir,
                _FakeClient([batch]),
                note_id="n1",
                user="甲",
            )
            event = watcher.poll()
        self.assertEqual(event["baseline_count"], 1)
        self.assertEqual(watcher.mode, "note_and_user")

    def test_poll_archives_all_fetched_notification_content_before_filter(self):
        captured = []

        def archive(groups):
            captured.extend(groups)
            return {"ok": True}

        batch = [
            _notification("c1", note_id="n1"),
            _notification("c2", note_id="n2"),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            watcher = self._watcher(
                temp_dir,
                _FakeClient([batch]),
                note_id="n1",
                archive_writer=archive,
            )
            event = watcher.poll()
        self.assertEqual(event["baseline_count"], 1)
        self.assertEqual(
            {group["note_id"] for group in captured}, {"n1", "n2"}
        )
        self.assertEqual(
            sum(len(group["comments"]) for group in captured), 2
        )

    def test_archive_failure_does_not_hide_fetched_comments(self):
        def archive(_groups):
            raise RuntimeError("归档不可用")

        with tempfile.TemporaryDirectory() as temp_dir:
            watcher = self._watcher(
                temp_dir,
                _FakeClient([[_notification("c1")]]),
                archive_writer=archive,
            )
            event = watcher.poll()
        self.assertTrue(event["ok"])
        self.assertFalse(event["archive"]["ok"])
        self.assertIn("归档不可用", event["archive"]["error"])

    def test_auto_reply_requires_explicit_confirmation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(CommentWatchError, "明确确认"):
                self._watcher(
                    temp_dir, _FakeClient([[]]), auto_reply=True
                )

    @patch("lib.comment_watcher.load_local_comment_states", return_value={})
    def test_auto_reply_online_verifies_and_persists_sent_state(self, _states):
        batches = [
            [_notification("c1")],
            [_notification("c1"), _notification("c2", timestamp=2)],
        ]
        client = _FakeClient(batches)
        scanner = _FakeScanner()
        with tempfile.TemporaryDirectory() as temp_dir:
            watcher = self._watcher(
                temp_dir,
                client,
                scanner=scanner,
                auto_reply=True,
                confirmed=True,
                reply_text='谢谢“你的留言”',
            )
            watcher.poll()
            event = watcher.poll()
            status = watcher.status()
        self.assertTrue(event["ok"])
        self.assertEqual(event["items"][0]["status"], "已自动回复")
        self.assertEqual(client.session.calls, [("c2", '谢谢“你的留言”')])
        self.assertEqual(status["reply_status_counts"], {"sent": 1})
        self.assertEqual(scanner.calls[0][2], "sensitive-token")

    @patch("lib.comment_watcher.load_local_comment_states", return_value={})
    def test_verification_failure_stops_and_keeps_comment_pending(self, _states):
        batches = [
            [],
            [_notification("c2", timestamp=2)],
        ]
        scanner = _FakeScanner(RuntimeError("verification_required"))
        with tempfile.TemporaryDirectory() as temp_dir:
            watcher = self._watcher(
                temp_dir,
                _FakeClient(batches),
                scanner=scanner,
                auto_reply=True,
                confirmed=True,
            )
            watcher.poll()
            event = watcher.poll()
            checkpoint = watcher.database.get_document(watcher.state_key)
        self.assertFalse(event["ok"])
        self.assertTrue(event["stop_required"])
        self.assertEqual(event["deferred_count"], 1)
        self.assertNotIn("c2", checkpoint["seen_comment_ids"])
        self.assertIn("在线核验失败", event["items"][0]["status"])

    @patch("lib.comment_watcher.load_local_comment_states", return_value={})
    def test_unpartitioned_online_result_stops_instead_of_dropping(self, _states):
        batches = [[], [_notification("c2", timestamp=2)]]
        with tempfile.TemporaryDirectory() as temp_dir:
            watcher = self._watcher(
                temp_dir,
                _FakeClient(batches),
                scanner=_UnresolvedScanner(),
                auto_reply=True,
                confirmed=True,
            )
            watcher.poll()
            event = watcher.poll()
            checkpoint = watcher.database.get_document(watcher.state_key)
        self.assertEqual(event["stop_reason"], "verification_failed")
        self.assertEqual(event["deferred_count"], 1)
        self.assertNotIn("c2", checkpoint["seen_comment_ids"])

    @patch("lib.comment_watcher.load_local_comment_states", return_value={})
    def test_session_error_stays_uncertain_and_is_not_auto_retried(self, _states):
        batches = [
            [],
            [_notification("c2", timestamp=2)],
        ]
        client = _FakeClient(
            batches, reply_result=(False, "响应超时", "session_error")
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            watcher = self._watcher(
                temp_dir,
                client,
                auto_reply=True,
                confirmed=True,
            )
            watcher.poll()
            event = watcher.poll()
            checkpoint = watcher.database.get_document(watcher.state_key)
        self.assertEqual(checkpoint["replies"]["c2"]["status"], "sending")
        self.assertIn("c2", checkpoint["seen_comment_ids"])
        self.assertEqual(client.skipped_records[0][0], "c2")
        self.assertTrue(event["stop_required"])

    def test_public_event_never_exposes_internal_ids_or_token(self):
        payload = watch_event_payload({
            "ok": True,
            "event": "new_comments",
            "items": [{
                "comment_id": "secret-comment-id",
                "user_id": "secret-user-id",
                "_xsec_token": "secret-token",
                "note_id": "n1",
                "note_title": "文章",
                "nickname": "用户",
                "content": '<b>|完整评论 "引号"',
                "status": "发现新评论",
            }],
        })
        raw = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("secret-comment-id", raw)
        self.assertNotIn("secret-user-id", raw)
        self.assertNotIn("secret-token", raw)
        self.assertIn("&lt;b&gt;", payload["groups"][0]["comments"][0]["content"])
        self.assertIn("&#124;", payload["groups"][0]["comments"][0]["content"])
