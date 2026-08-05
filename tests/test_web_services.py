"""完整Web控制台的结构化服务与排除列表分页测试。"""

from tests.support import *

from lib import web_services


class WebServiceTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
