"""完整Web控制台的结构化服务与排除列表分页测试。"""

from tests.support import *

from contextlib import nullcontext

from lib import web_services


class WebServiceTests(unittest.TestCase):
    @patch("lib.web_services.XHSClient.get_note_detail_cached")
    @patch("lib.web_services._cached_note_detail")
    def test_note_detail_prompt_context_always_reuses_local_cache(
        self, cached_detail, get_detail
    ):
        cached_detail.return_value = {
            "note_id": "note-1",
            "title": "本地标题",
            "desc": "本地正文",
            "read_at": "很久以前",
        }

        result = web_services.note_detail({"note_id": "note-1"})

        self.assertTrue(result["cached"])
        self.assertEqual(result["note"]["desc"], "本地正文")
        get_detail.assert_not_called()

    @patch("lib.web_services._capture_json")
    def test_doctor_report_can_show_failed_checks_without_http_failure(
        self, capture
    ):
        capture.return_value = {
            "ok": False,
            "checks": {"repository_release": {"ok": False}},
        }
        payload = web_services.run_doctor()
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["healthy"])

    @patch("lib.web_services.XHSClient.load_skipped")
    def test_skipped_records_support_search_and_pagination(self, load):
        load.return_value = {
            f"c{index}": {
                "nickname": "目标用户" if index == 7 else f"用户{index}",
                "content": (
                    "包含  多个\n空格的评论" if index == 8
                    else f"评论正文{index}"
                ),
                "reason": "发送失败",
                "skipped_at": "2026-08-04 10:00:00",
                "note_id": "note-1",
                "error_type": (
                    "permission_denied" if index == 7 else ""
                ),
                "error_code": "-9131" if index == 7 else "",
            }
            for index in range(1, 26)
        }
        page = web_services.skipped_records(page=2, page_size=10)
        self.assertEqual(page["total_count"], 25)
        self.assertEqual(page["records"][0]["index"], 11)
        self.assertTrue(page["pagination"]["has_previous"])
        self.assertTrue(page["pagination"]["has_next"])
        searched = web_services.skipped_records(
            search="目标用户", page=1, page_size=10
        )
        self.assertEqual(searched["total_count"], 1)
        self.assertEqual(searched["records"][0]["nickname"], "目标用户")
        structured = web_services.skipped_records(
            search="permission_denied", page=1, page_size=10
        )
        self.assertEqual(structured["total_count"], 1)
        normalized = web_services.skipped_records(
            search="包含 多个 空格", page=1, page_size=10
        )
        self.assertEqual(normalized["total_count"], 1)
        default_page = web_services.skipped_records()
        self.assertEqual(default_page["pagination"]["page_size"], 15)
        self.assertEqual(default_page["count"], 15)
        self.assertEqual(default_page["pagination"]["first_index"], 1)
        self.assertEqual(default_page["pagination"]["last_index"], 15)

    @patch("lib.web_services.XHSClient.add_skipped")
    @patch("lib.web_services.XHSClient.load_skipped", return_value={})
    @patch("lib.web_services.read_json_state")
    def test_comment_can_be_manually_ignored_from_canonical_archive(
        self, read_state, _load_skipped, add_skipped
    ):
        read_state.return_value = {
            "groups": [{
                "note_id": "note-1",
                "comments": [{
                    "comment_id": "comment-1",
                    "nickname": "用户甲",
                    "content": "保留“引号”和\n完整正文",
                }],
            }],
        }
        result = web_services.update_skipped({
            "action": "ignore",
            "note_id": "note-1",
            "comment_id": "comment-1",
        })
        self.assertTrue(result["ignored"])
        self.assertFalse(result["already_ignored"])
        self.assertEqual(result["reason"], "人工忽略")
        add_skipped.assert_called_once_with(
            "comment-1",
            nickname="用户甲",
            content="保留“引号”和\n完整正文",
            reason="人工忽略",
            note_id="note-1",
        )

    @patch("lib.web_services.XHSClient.add_skipped")
    @patch("lib.web_services.XHSClient.load_skipped")
    def test_manual_ignore_preserves_existing_exclusion_reason(
        self, load_skipped, add_skipped
    ):
        load_skipped.return_value = {
            "comment-1": {"reason": "发送失败：权限不足"},
        }
        result = web_services.update_skipped({
            "action": "ignore",
            "note_id": "note-1",
            "comment_id": "comment-1",
        })
        self.assertTrue(result["already_ignored"])
        self.assertEqual(result["reason"], "发送失败：权限不足")
        add_skipped.assert_not_called()

    @patch("lib.web_services.XHSClient.load_skipped", return_value={})
    @patch("lib.web_services.json_state_exists", return_value=False)
    @patch("lib.web_services._find_archived_comment")
    def test_reply_draft_is_editable_and_has_safe_generic_fallback(
        self, find_comment, _exists, _load_skipped
    ):
        find_comment.return_value = {
            "comment_id": "comment-1",
            "note_id": "note-1",
            "note_title": "文章",
            "nickname": "用户甲",
            "content": "完整评论正文",
        }
        result = web_services.reply_draft("note-1", "comment-1")
        self.assertTrue(result["can_send"])
        self.assertEqual(result["draft_source"], "generic")
        self.assertTrue(result["reply"])
        self.assertEqual(result["content"], "完整评论正文")
        self.assertEqual(result["max_reply_length"], 1000)

    @patch("lib.web_services.XHSClient.load_skipped")
    @patch("lib.web_services.json_state_exists", return_value=False)
    @patch("lib.web_services._find_archived_comment")
    def test_reply_draft_blocks_excluded_comment(
        self, find_comment, _exists, load_skipped
    ):
        find_comment.return_value = {
            "comment_id": "comment-1",
            "note_id": "note-1",
            "nickname": "用户甲",
            "content": "评论",
        }
        load_skipped.return_value = {
            "comment-1": {"reason": "人工忽略"},
        }
        result = web_services.reply_draft("note-1", "comment-1")
        self.assertFalse(result["can_send"])
        self.assertIn("排除列表", result["blocked_reason"])

    @patch("lib.web_services.Replier.send_drafts")
    @patch("lib.web_services.CommentScanner.verify_candidates_online")
    @patch("lib.web_services.XHSClient.load_skipped", return_value={})
    def test_web_draft_send_online_verifies_and_persists_result(
        self, _load_skipped, verify, send_drafts
    ):
        comment = {
            "comment_id": "comment-1",
            "note_id": "note-1",
            "note_title": "文章",
            "nickname": "用户甲",
            "content": "评论正文",
            "target_comment_id": "root-1",
        }
        verify.return_value = ([{
            "comment_id": "comment-1",
            "nickname": "用户甲",
            "content": "评论正文",
        }], [])

        def send_success(state, state_file=None):
            state["drafts"][0]["send_status"] = "sent"
            return {
                "success": 1, "fail": 0, "skip": 0,
                "stopped": False,
            }

        send_drafts.side_effect = send_success
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = {
                "directory": temp_dir,
                "scan": os.path.join(temp_dir, "scan.json"),
                "reply_map": os.path.join(temp_dir, "reply_map.json"),
                "drafts": os.path.join(temp_dir, "drafts.json"),
                "audit": os.path.join(temp_dir, "audit.json"),
            }
            with patch("lib.web_services.workflow_paths", return_value=paths):
                result = web_services._send_reply_draft_locked(
                    "note-1", "comment-1", "编辑后的回复", comment
                )
            with open(paths["drafts"], encoding="utf-8") as file:
                saved = json.load(file)
        self.assertTrue(result["ok"])
        self.assertEqual(result["sent"], 1)
        self.assertEqual(saved["drafts"][0]["reply"], "编辑后的回复")
        self.assertEqual(saved["drafts"][0]["send_status"], "sent")
        self.assertEqual(saved["active_batch"]["source"], "web_manual_edit")
        verify.assert_called_once()

    @patch("lib.web_services.write_json")
    @patch("lib.web_services.read_workflow_state")
    @patch("lib.web_services.json_state_exists", return_value=True)
    @patch("lib.web_services.workflow_lock")
    def test_draft_edit_uses_lock_updates_text_and_invalidates_binding(
        self, lock, _exists, read_state, write_json
    ):
        lock.return_value = nullcontext()
        state = {
            "drafts": [{
                "comment_id": "comment-1", "reply": "旧回复",
                "action": "send", "send_status": "",
            }],
            "active_comment_ids": ["comment-1"],
            "active_batch": {"status": "active"},
        }
        read_state.return_value = state
        result = web_services.update_draft_reply({
            "note_id": "note-1",
            "comment_id": "comment-1",
            "reply": "修改后的回复",
        })
        self.assertTrue(result["updated"])
        self.assertTrue(result["batch_invalidated"])
        self.assertEqual(state["drafts"][0]["reply"], "修改后的回复")
        self.assertEqual(state["active_comment_ids"], [])
        self.assertEqual(state["active_batch"]["status"], "superseded")
        write_json.assert_called_once()

    @patch("lib.web_services.read_workflow_state")
    @patch("lib.web_services.json_state_exists", return_value=True)
    @patch("lib.web_services.workflow_lock")
    def test_draft_edit_and_delete_refuse_terminal_records(
        self, lock, _exists, read_state
    ):
        lock.return_value = nullcontext()
        read_state.return_value = {
            "drafts": [{
                "comment_id": "comment-1", "reply": "已发送",
                "action": "send", "send_status": "sent",
            }],
            "active_comment_ids": [],
        }
        for operation, payload in (
            (web_services.update_draft_reply, {
                "note_id": "note-1", "comment_id": "comment-1",
                "reply": "不允许修改",
            }),
            (web_services.delete_draft, {
                "note_id": "note-1", "comment_id": "comment-1",
            }),
        ):
            with self.assertRaises(web_services.WebServiceError) as caught:
                operation(payload)
            self.assertEqual(caught.exception.error_type, "terminal_reply_state")

    @patch("lib.web_services.write_json")
    @patch("lib.web_services.read_workflow_state")
    @patch("lib.web_services.json_state_exists", return_value=True)
    @patch("lib.web_services.workflow_lock", return_value=nullcontext())
    @patch("lib.web_services.workflow_paths", return_value={
        "drafts": "/tmp/note-1-drafts.json",
    })
    @patch("lib.web_services.list_all_drafts")
    def test_delete_all_drafts_removes_pending_and_preserves_protected(
        self, list_drafts, _paths, _lock, _exists, read_state, write_json
    ):
        list_drafts.side_effect = [{
            "drafts": [
                {"note_id": "note-1", "comment_id": "pending", "send_status": ""},
                {"note_id": "note-1", "comment_id": "sending", "send_status": "sending"},
            ],
            "total_count": 2,
        }, {"drafts": [], "total_count": 1}]
        state = {
            "drafts": [
                {"comment_id": "pending", "send_status": "", "action": "send"},
                {"comment_id": "sending", "send_status": "sending", "action": "send"},
                {"comment_id": "sent", "send_status": "sent", "action": "send"},
            ],
            "active_comment_ids": ["pending"],
            "active_batch": {"status": "previewed"},
        }
        read_state.return_value = state

        result = web_services.delete_all_drafts({"confirmed": True})

        self.assertEqual(result["deleted"], 1)
        self.assertEqual(result["protected"], 1)
        self.assertEqual(
            [item["comment_id"] for item in state["drafts"]],
            ["sending", "sent"],
        )
        self.assertEqual(state["active_comment_ids"], [])
        write_json.assert_called_once()

    def test_delete_all_drafts_requires_explicit_confirmation(self):
        with self.assertRaises(web_services.WebServiceError) as caught:
            web_services.delete_all_drafts({"confirmed": False})
        self.assertEqual(caught.exception.error_type, "confirmation_required")

    @patch("lib.web_services.send_reply_draft")
    @patch("lib.web_services.list_all_drafts")
    def test_send_all_stops_before_writes_for_uncertain_item(
        self, list_drafts, send_reply
    ):
        list_drafts.return_value = {"drafts": [{
            "note_id": "note-1", "comment_id": "comment-1",
            "nickname": "用户", "reply": "回复",
            "send_status": "sending", "in_skipped": False,
        }]}
        with self.assertRaises(web_services.WebServiceError) as caught:
            web_services.send_all_drafts({"confirmed": True})
        self.assertEqual(caught.exception.error_type, "uncertain_send_state")
        send_reply.assert_not_called()

    @patch("lib.web_services.send_reply_draft")
    @patch("lib.web_services.list_all_drafts")
    def test_send_all_pauses_remaining_on_account_level_error(
        self, list_drafts, send_reply
    ):
        list_drafts.return_value = {"drafts": [
            {
                "note_id": "note-1", "comment_id": "comment-1",
                "nickname": "用户1", "reply": "回复1",
                "send_status": "", "in_skipped": False,
            },
            {
                "note_id": "note-2", "comment_id": "comment-2",
                "nickname": "用户2", "reply": "回复2",
                "send_status": "", "in_skipped": False,
            },
        ]}
        send_reply.return_value = {
            "ok": False, "sent": 0, "failed": 1,
            "error_type": "rate_limited", "error": "操作太快",
        }
        result = web_services.send_all_drafts({"confirmed": True})
        self.assertTrue(result["paused"])
        self.assertEqual(result["pause_reason"], "rate_limited")
        self.assertEqual(result["remaining"], 1)
        send_reply.assert_called_once()


if __name__ == "__main__":
    unittest.main()
