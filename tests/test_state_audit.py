"""工作路径、审计、草稿合并和发送状态测试。"""

from tests.support import *


class StateAuditTests(unittest.TestCase):
    def test_workflow_paths_are_stable(self):
        first = main.workflow_paths("note-1")
        second = main.workflow_paths("note-1")
        self.assertEqual(first, second)
        self.assertTrue(os.path.isabs(CACHE_DIR))
        self.assertTrue(CACHE_DIR.startswith(PROJECT_ROOT))
        self.assertTrue(first["scan"].endswith(
            ".cache/workflows/note-1/scan.json"
        ))
        self.assertTrue(first["audit"].endswith(
            ".cache/workflows/note-1/audit.json"
        ))

    def test_ai_reply_records_started_and_failed_events_without_content(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = {
                "directory": temp_dir,
                "scan": os.path.join(temp_dir, "scan.json"),
                "reply_map": os.path.join(temp_dir, "reply_map.json"),
                "drafts": os.path.join(temp_dir, "drafts.json"),
                "audit": os.path.join(temp_dir, "audit.json"),
            }
            args = argparse.Namespace(
                note_id="n1", action="send", replies=None,
                confirmed=False, batch_id="", preview_hash="",
            )
            output = io.StringIO()
            with patch("lib.cli_ai.workflow_paths", return_value=paths):
                with contextlib.redirect_stdout(output):
                    cli_ai.cmd_ai_reply(args)
            payload = json.loads(output.getvalue())
            with open(paths["audit"], encoding="utf-8") as file:
                audit = json.load(file)
            mode = stat.S_IMODE(os.stat(paths["audit"]).st_mode)
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["audit"]["recorded"])
        self.assertEqual(audit["schema_version"], 2)
        self.assertEqual(
            [event["phase"] for event in audit["events"]],
            ["started", "failed"],
        )
        self.assertEqual(
            audit["events"][0]["command_id"],
            audit["events"][1]["command_id"],
        )
        self.assertEqual(
            audit["events"][0]["workflow_id"],
            audit["events"][1]["workflow_id"],
        )
        self.assertEqual(
            payload["audit"]["workflow_id"],
            audit["current_workflow_id"],
        )
        self.assertNotIn("comment_id", json.dumps(audit, ensure_ascii=False))
        self.assertEqual(mode, 0o600)

    def test_audit_workflow_id_groups_retries_until_next_prepare(self):
        def successful_handler(args, paths):
            cli_ai.print_json({"ok": True, "action": args.action})

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = {
                "directory": temp_dir,
                "scan": os.path.join(temp_dir, "scan.json"),
                "reply_map": os.path.join(temp_dir, "reply_map.json"),
                "drafts": os.path.join(temp_dir, "drafts.json"),
                "audit": os.path.join(temp_dir, "audit.json"),
            }
            actions = ("prepare", "draft", "draft", "send", "prepare")
            for action in actions:
                args = argparse.Namespace(
                    note_id="n1", action=action, full_scan=False, limit=20,
                    replies=None, confirmed=True, batch_id="b",
                    preview_hash="h",
                )
                with contextlib.redirect_stdout(io.StringIO()):
                    cli_ai._run_audited_action(
                        successful_handler, args, paths
                    )
            with open(paths["audit"], encoding="utf-8") as file:
                audit = json.load(file)
        starts = [
            event for event in audit["events"]
            if event["phase"] == "started"
        ]
        first_workflow = starts[0]["workflow_id"]
        self.assertTrue(all(
            event["workflow_id"] == first_workflow
            for event in starts[:4]
        ))
        self.assertNotEqual(starts[4]["workflow_id"], first_workflow)
        self.assertEqual(
            audit["current_workflow_id"], starts[4]["workflow_id"]
        )

    def test_paths_returns_recent_workflow_audit_events(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = {
                "directory": temp_dir,
                "scan": os.path.join(temp_dir, "scan.json"),
                "reply_map": os.path.join(temp_dir, "reply_map.json"),
                "drafts": os.path.join(temp_dir, "drafts.json"),
                "audit": os.path.join(temp_dir, "audit.json"),
            }
            with open(paths["audit"], "w", encoding="utf-8") as file:
                json.dump({
                    "schema_version": 1,
                    "retained_limit": 500,
                    "events": [{
                        "event_id": "e1", "command_id": "c1",
                        "action": "send", "phase": "failed",
                    }],
                }, file)
            output = io.StringIO()
            with patch(
                "lib.cli_admin.workflow_paths", return_value=paths
            ):
                with contextlib.redirect_stdout(output):
                    main.cmd_paths(argparse.Namespace(
                        note_id="n1", audit_limit=20
                    ))
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["workflow_audit"]["events_total"], 1)
        self.assertEqual(
            payload["workflow_audit"]["events"][0]["event_id"], "e1"
        )

    def test_paths_reports_legacy_batch_that_requires_redraft(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = {
                "directory": temp_dir,
                "scan": os.path.join(temp_dir, "scan.json"),
                "reply_map": os.path.join(temp_dir, "reply_map.json"),
                "drafts": os.path.join(temp_dir, "drafts.json"),
            }
            with open(paths["drafts"], "w", encoding="utf-8") as file:
                json.dump({
                    "active_comment_ids": ["c1"],
                    "drafts": [],
                }, file)
            output = io.StringIO()
            with patch(
                "lib.cli_admin.workflow_paths", return_value=paths
            ):
                with contextlib.redirect_stdout(output):
                    main.cmd_paths(argparse.Namespace(note_id="n1"))
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["workflow_state"]["active_count"], 1)
        self.assertTrue(
            payload["workflow_state"]["legacy_requires_redraft"]
        )

    def test_file_lock_rejects_second_writer(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = os.path.join(temp_dir, "state.lock")
            with file_lock(lock_path, timeout=0):
                with self.assertRaises(StateLockTimeout):
                    with file_lock(lock_path, timeout=0):
                        pass

    def test_drafts_reject_unverified_scan(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            scan_file = os.path.join(temp_dir, "scan.json")
            with open(scan_file, "w", encoding="utf-8") as f:
                json.dump({
                    "note_id": "n1",
                    "reply_status_verified": False,
                    "unreplied_level1": [{"comment_id": "c1"}],
                    "unreplied_subs": [],
                }, f)
            args = argparse.Namespace(
                note_id="n1",
                from_scan=scan_file,
                allow_unverified=False,
                xsec_token="",
                with_subs=False,
                refresh=False,
                batch=None,
                output=None,
            )
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                main.cmd_drafts(args)
            self.assertIn("未确认评论回复状态", stdout.getvalue())

    def test_merge_drafts_preserves_sent_history(self):
        existing = {
            "send_attempts": [{"attempt_id": "attempt-1"}],
            "drafts": [{
                "comment_id": "c1",
                "reply": "已发送回复",
                "action": "send",
                "send_status": "sent",
            }],
        }
        new = {
            "note_id": "n1",
            "drafts": [
                {"comment_id": "c1", "reply": "不应覆盖", "action": "send"},
                {"comment_id": "c2", "reply": "新回复", "action": "send"},
            ],
        }
        merged = main.merge_draft_history(existing, new)
        self.assertEqual(merged["drafts"][0]["reply"], "已发送回复")
        self.assertEqual(merged["drafts"][1]["comment_id"], "c2")
        self.assertEqual(
            merged["send_attempts"], [{"attempt_id": "attempt-1"}]
        )

    def test_merge_drafts_keeps_old_items_outside_new_batch(self):
        existing = {
            "drafts": [{
                "comment_id": "old-pending",
                "reply": "旧草稿",
                "action": "send",
            }],
        }
        new = {
            "note_id": "n1",
            "active_comment_ids": ["new"],
            "drafts": [{
                "comment_id": "new",
                "reply": "新草稿",
                "action": "send",
            }],
        }
        merged = main.merge_draft_history(existing, new)
        self.assertEqual(
            [item["comment_id"] for item in merged["drafts"]],
            ["old-pending", "new"],
        )
        self.assertEqual(merged["active_comment_ids"], ["new"])

    def test_merge_drafts_never_overwrites_uncertain_inflight_item(self):
        existing = {
            "drafts": [{
                "comment_id": "c1",
                "reply": "可能已发送",
                "action": "send",
                "send_status": "sending",
            }],
        }
        new = {
            "active_comment_ids": ["c1"],
            "drafts": [{
                "comment_id": "c1",
                "reply": "不应覆盖",
                "action": "send",
            }],
        }
        merged = main.merge_draft_history(existing, new)
        self.assertEqual(merged["drafts"][0]["reply"], "可能已发送")
        self.assertEqual(merged["drafts"][0]["send_status"], "sending")

    @patch("lib.replier.time.sleep")
    def test_send_state_is_persisted_and_not_resent(self, _sleep):
        client = MagicMock()
        client.reply.return_value = (True, "", "")
        client.is_skipped.return_value = False
        replier = Replier(client)
        drafts = {
            "note_id": "n1",
            "drafts": [{
                "comment_id": "c1",
                "nickname": "用户",
                "content": "评论",
                "reply": "回复",
                "action": "send",
            }],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = os.path.join(temp_dir, "drafts.json")
            replier.send_drafts(drafts, state_file=state_file)
            with open(state_file, encoding="utf-8") as saved_file:
                saved = json.load(saved_file)
            self.assertEqual(saved["drafts"][0]["send_status"], "sent")

            second = Replier(client)
            second.send_drafts(saved, state_file=state_file)
            self.assertEqual(client.reply.call_count, 1)

    @patch("lib.replier.time.sleep")
    def test_send_writes_inflight_state_before_platform_request(self, _sleep):
        client = MagicMock()
        client.is_skipped.return_value = False
        drafts = {
            "note_id": "n1",
            "drafts": [{
                "comment_id": "c1",
                "nickname": "用户",
                "content": "评论",
                "reply": "回复",
                "action": "send",
            }],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = os.path.join(temp_dir, "drafts.json")

            def reply(*_args):
                with open(state_file, encoding="utf-8") as file:
                    during_request = json.load(file)
                self.assertEqual(
                    during_request["drafts"][0]["send_status"],
                    "sending",
                )
                return True, "", ""

            client.reply.side_effect = reply
            Replier(client).send_drafts(drafts, state_file=state_file)
        self.assertEqual(drafts["drafts"][0]["send_status"], "sent")

    def test_inflight_item_is_never_automatically_resent(self):
        client = MagicMock()
        client.is_skipped.return_value = False
        drafts = {
            "note_id": "n1",
            "drafts": [{
                "comment_id": "c1",
                "nickname": "用户",
                "content": "评论",
                "reply": "回复",
                "action": "send",
                "send_status": "sending",
            }],
        }
        Replier(client).send_drafts(drafts)
        client.reply.assert_not_called()

    @patch("lib.replier.time.sleep")
    def test_all_failed_replies_are_added_to_skipped_list(self, _sleep):
        for error_type in XHSClient.REPLY_ERROR_TYPES:
            with self.subTest(error_type=error_type):
                client = MagicMock()
                client.reply.return_value = (False, "发送失败", error_type)
                client.is_skipped.return_value = False
                drafts = {
                    "note_id": "n1",
                    "drafts": [{
                        "comment_id": f"c-{error_type}",
                        "nickname": "用户",
                        "content": "评论",
                        "reply": "回复",
                        "action": "send",
                    }],
                }
                Replier(client).send_drafts(drafts)
                client.add_skipped.assert_called_once()
                self.assertEqual(
                    drafts["drafts"][0]["send_status"], "failed"
                )
