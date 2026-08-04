"""AI回复批次、确认绑定和三阶段工作流测试。"""

from tests.support import *


class AIReplyWorkflowTests(unittest.TestCase):
    def test_ai_pending_only_uses_active_batch(self):
        drafts = {
            "active_comment_ids": ["c2"],
            "drafts": [
                {"comment_id": "c1", "action": "send"},
                {"comment_id": "c2", "action": "send"},
            ],
        }
        self.assertEqual(
            [item["comment_id"] for item in cli_ai._pending(drafts)],
            ["c2"],
        )

    @patch("lib.replier.time.sleep")
    def test_replier_only_executes_ai_active_batch(self, _sleep):
        client = MagicMock()
        client.reply.return_value = (True, "", "")
        client.is_skipped.return_value = False
        drafts = {
            "note_id": "n1",
            "active_comment_ids": ["c2"],
            "drafts": [
                {
                    "comment_id": "c1", "nickname": "旧用户",
                    "content": "旧评论", "reply": "旧回复", "action": "send",
                },
                {
                    "comment_id": "c2", "nickname": "新用户",
                    "content": "新评论", "reply": "新回复", "action": "send",
                },
            ],
        }
        Replier(client).send_drafts(drafts)
        client.reply.assert_called_once_with("n1", "c2", "新回复")
        self.assertNotIn("send_status", drafts["drafts"][0])
        self.assertEqual(drafts["drafts"][1]["send_status"], "sent")

    @patch("main.Replier")
    @patch("main.CommentScanner")
    def test_legacy_send_rechecks_only_active_batch(
        self, scanner_class, replier_class
    ):
        scanner_class.return_value.verify_candidates_online.return_value = (
            [{
                "comment_id": "c2",
                "nickname": "新用户",
                "content": "新评论",
            }],
            [],
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            draft_file = os.path.join(temp_dir, "drafts.json")
            with open(draft_file, "w", encoding="utf-8") as file:
                json.dump({
                    "note_id": "n1",
                    "active_comment_ids": ["c2"],
                    "drafts": [
                        {
                            "comment_id": "c1", "nickname": "旧用户",
                            "content": "旧评论", "reply": "旧回复",
                            "action": "send",
                        },
                        {
                            "comment_id": "c2", "nickname": "新用户",
                            "content": "新评论", "reply": "新回复",
                            "action": "send",
                        },
                    ],
                }, file, ensure_ascii=False)
            args = argparse.Namespace(
                file=draft_file,
                dry_run=False,
                confirm=False,
                resume=False,
            )
            with contextlib.redirect_stdout(io.StringIO()):
                main.cmd_send(args)

        verified = (
            scanner_class.return_value
            .verify_candidates_online.call_args.args[1]
        )
        self.assertEqual(
            [item["comment_id"] for item in verified],
            ["c2"],
        )
        replier_class.return_value.send_drafts.assert_called_once()

    @patch("main.Replier")
    @patch("main.CommentScanner")
    def test_legacy_send_stops_when_online_recheck_fails(
        self, scanner_class, replier_class
    ):
        scanner_class.return_value.verify_candidates_online.side_effect = (
            RuntimeError("楼中楼数据不完整")
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            draft_file = os.path.join(temp_dir, "drafts.json")
            with open(draft_file, "w", encoding="utf-8") as file:
                json.dump({
                    "note_id": "n1",
                    "drafts": [{
                        "comment_id": "c1", "nickname": "用户",
                        "content": "评论", "reply": "回复",
                        "action": "send",
                    }],
                }, file, ensure_ascii=False)
            args = argparse.Namespace(
                file=draft_file,
                dry_run=False,
                confirm=False,
                resume=False,
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                main.cmd_send(args)

        self.assertIn("在线核验失败，已停止发送", output.getvalue())
        replier_class.assert_not_called()

    def test_ai_reply_parser_requires_explicit_action(self):
        args = build_parser().parse_args([
            "ai-reply", "--note-id", "n1", "--action", "prepare"
        ])
        self.assertEqual((args.command, args.action), ("ai-reply", "prepare"))
        self.assertFalse(args.confirmed)
        self.assertFalse(args.full_scan)
        self.assertEqual(args.limit, 20)
        self.assertIsNone(args.batch_id)
        self.assertIsNone(args.preview_hash)

    def test_ai_reply_parser_exposes_structured_map_fields(self):
        args = build_parser().parse_args([
            "ai-reply", "--note-id", "n1", "--action", "map",
            "--comment-id", "c1", "--decision", "send",
            "--reply-text", '包含"引号"的回复',
            "--logic-verdict", "partly_sound",
            "--logic-reason", "依据",
            "--fact-verdict", "supported",
            "--fact-reason", "资料支持",
            "--fact-source", "来源一", "https://example.com/one",
            "--fact-source", "来源二", "https://example.com/two",
            "--boast-verdict", "none",
            "--boast-reason", "没有夸大",
        ])
        self.assertEqual(args.action, "map")
        self.assertEqual(args.comment_id, "c1")
        self.assertEqual(args.reply_text, '包含"引号"的回复')
        self.assertEqual(len(args.fact_source), 2)

    def test_ai_reply_parser_exposes_status_retry_and_candidate_index(self):
        map_args = build_parser().parse_args([
            "ai-reply", "--note-id", "n1", "--action", "map",
            "--candidate-index", "2",
        ])
        retry_args = build_parser().parse_args([
            "ai-reply", "--note-id", "n1", "--action", "retry",
            "--comment-id", "c1", "--retry-authorized",
        ])
        status_args = build_parser().parse_args([
            "ai-reply", "--note-id", "n1", "--action", "status",
        ])
        self.assertEqual(map_args.candidate_index, 2)
        self.assertTrue(retry_args.retry_authorized)
        self.assertEqual(status_args.action, "status")

    @patch("lib.cli_ai.CommentScanner")
    def test_ai_draft_creates_confirmation_bound_batch(
        self, scanner_class
    ):
        candidate = {
            "comment_id": "c1",
            "nickname": "用户",
            "content": "评论",
        }
        scanner_class.return_value.verify_candidates_online.return_value = (
            [candidate],
            [],
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = {
                "directory": temp_dir,
                "scan": os.path.join(temp_dir, "scan.json"),
                "reply_map": os.path.join(temp_dir, "reply_map.json"),
                "drafts": os.path.join(temp_dir, "drafts.json"),
            }
            with open(paths["scan"], "w", encoding="utf-8") as file:
                json.dump({
                    "note_id": "n1",
                    "reply_status_verified": True,
                    "unreplied_level1": [candidate],
                    "unreplied_subs": [],
                }, file)
            with open(
                paths["reply_map"], "w", encoding="utf-8"
            ) as file:
                json.dump({
                    "c1": {
                        "reply": "回复",
                        "action": "send",
                        "review": _valid_review(),
                    },
                }, file)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                cli_ai._draft(
                    argparse.Namespace(note_id="n1", replies=None),
                    paths,
                )
            payload = json.loads(output.getvalue())
            with open(paths["drafts"], encoding="utf-8") as file:
                saved = json.load(file)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["batch_id"])
        self.assertEqual(len(payload["preview_hash"]), 64)
        self.assertEqual(
            saved["active_batch"]["batch_id"], payload["batch_id"]
        )
        self.assertEqual(
            saved["active_batch"]["preview_hash"],
            payload["preview_hash"],
        )
        self.assertIn("--batch-id", payload["next"])
        self.assertIn("--preview-hash", payload["next"])
        self.assertEqual(payload["review_columns"], [
            "序号", "用户", "逻辑分析", "事实核查", "吹牛判定",
        ])
        self.assertEqual(payload["reviews_total"], 1)
        self.assertEqual(
            payload["reviews"][0]["logic_analysis"]["verdict"],
            "partly_sound",
        )
        self.assertEqual(
            payload["reviews"][0]["logic_analysis"]["label"],
            "部分成立",
        )
        self.assertFalse(payload["review_display"]["comment_id_visible"])
        self.assertEqual(
            saved["drafts"][0]["review"]["boast_check"]["verdict"],
            "none",
        )

    @patch("lib.cli_ai.CommentScanner")
    def test_ai_draft_reuses_binding_only_when_preview_is_unchanged(
        self, scanner_class
    ):
        candidate = {
            "comment_id": "c1", "nickname": "用户", "content": "评论",
        }
        scanner_class.return_value.verify_candidates_online.return_value = (
            [candidate], []
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = {
                "directory": temp_dir,
                "scan": os.path.join(temp_dir, "scan.json"),
                "reply_map": os.path.join(temp_dir, "reply_map.json"),
                "drafts": os.path.join(temp_dir, "drafts.json"),
            }
            with open(paths["scan"], "w", encoding="utf-8") as file:
                json.dump({
                    "note_id": "n1",
                    "reply_status_verified": True,
                    "unreplied_level1": [candidate],
                    "unreplied_subs": [],
                }, file)
            mapping = {
                "c1": {
                    "reply": "回复", "action": "send",
                    "review": _valid_review(),
                },
            }
            with open(paths["reply_map"], "w", encoding="utf-8") as file:
                json.dump(mapping, file)
            args = argparse.Namespace(note_id="n1", replies=None)

            first_output = io.StringIO()
            with contextlib.redirect_stdout(first_output):
                cli_ai._draft(args, paths)
            first = json.loads(first_output.getvalue())

            second_output = io.StringIO()
            with contextlib.redirect_stdout(second_output):
                cli_ai._draft(args, paths)
            second = json.loads(second_output.getvalue())
            self.assertTrue(second["binding_reused"])
            self.assertFalse(second["new_confirmation_required"])
            self.assertEqual(second["batch_id"], first["batch_id"])
            self.assertEqual(second["preview_hash"], first["preview_hash"])
            self.assertEqual(second["revision"], first["revision"])

            mapping["c1"]["reply"] = "修改后的回复"
            with open(paths["reply_map"], "w", encoding="utf-8") as file:
                json.dump(mapping, file)
            third_output = io.StringIO()
            with contextlib.redirect_stdout(third_output):
                cli_ai._draft(args, paths)
            third = json.loads(third_output.getvalue())
        self.assertFalse(third["binding_reused"])
        self.assertTrue(third["new_confirmation_required"])
        self.assertNotEqual(third["batch_id"], second["batch_id"])
        self.assertNotEqual(third["preview_hash"], second["preview_hash"])
        self.assertEqual(third["revision"], second["revision"] + 1)

    @patch("lib.cli_ai.CommentScanner")
    def test_ai_send_rejects_confirmation_for_old_batch(
        self, scanner_class
    ):
        item = {
            "comment_id": "c1",
            "nickname": "用户",
            "content": "评论",
            "reply": "回复",
            "action": "send",
        }
        current_hash = cli_ai.preview_hash([item])
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = {
                "directory": temp_dir,
                "scan": os.path.join(temp_dir, "scan.json"),
                "reply_map": os.path.join(temp_dir, "reply_map.json"),
                "drafts": os.path.join(temp_dir, "drafts.json"),
            }
            with open(paths["drafts"], "w", encoding="utf-8") as file:
                json.dump({
                    "note_id": "n1",
                    "active_comment_ids": ["c1"],
                    "active_batch": {
                        "batch_id": "new-batch",
                        "preview_hash": current_hash,
                        "status": "previewed",
                    },
                    "drafts": [item],
                }, file)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                cli_ai._send(
                    argparse.Namespace(
                        note_id="n1",
                        confirmed=True,
                        batch_id="old-batch",
                        preview_hash=current_hash,
                    ),
                    paths,
                )
            with open(paths["drafts"], encoding="utf-8") as file:
                saved = json.load(file)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["error_type"], "stale_preview")
        self.assertEqual(payload["mismatch"], {
            "batch_id": True,
            "preview_hash": False,
        })
        self.assertTrue(payload["requires_user_confirmation"])
        self.assertNotIn("preview_hash", payload)
        self.assertNotIn("batch_id", payload)
        self.assertEqual(
            saved["send_attempts"][-1]["attempt_id"],
            payload["attempt_id"],
        )
        self.assertEqual(
            saved["send_attempts"][-1]["error_type"],
            "stale_preview",
        )
        scanner_class.assert_not_called()

    @patch("lib.cli_ai.CommentScanner")
    def test_ai_send_reports_preview_hash_mismatch_without_new_binding(
        self, scanner_class
    ):
        item = {
            "comment_id": "c1",
            "nickname": "用户",
            "content": "评论",
            "reply": "回复",
            "action": "send",
        }
        current_hash = cli_ai.preview_hash([item])
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = {
                "directory": temp_dir,
                "scan": os.path.join(temp_dir, "scan.json"),
                "reply_map": os.path.join(temp_dir, "reply_map.json"),
                "drafts": os.path.join(temp_dir, "drafts.json"),
            }
            with open(paths["drafts"], "w", encoding="utf-8") as file:
                json.dump({
                    "note_id": "n1",
                    "active_comment_ids": ["c1"],
                    "active_batch": {
                        "batch_id": "batch-1",
                        "revision": 3,
                        "preview_hash": current_hash,
                        "status": "previewed",
                    },
                    "drafts": [item],
                }, file)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                cli_ai._send(
                    argparse.Namespace(
                        note_id="n1",
                        confirmed=True,
                        batch_id="batch-1",
                        preview_hash="old-hash",
                    ),
                    paths,
                )
            with open(paths["drafts"], encoding="utf-8") as file:
                saved = json.load(file)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["mismatch"], {
            "batch_id": False,
            "preview_hash": True,
        })
        self.assertEqual(payload["current_revision"], 3)
        self.assertNotIn("expected_preview_hash", payload)
        self.assertEqual(
            saved["send_attempts"][-1]["mismatch"],
            {"batch_id": False, "preview_hash": True},
        )
        scanner_class.assert_not_called()

    @patch("lib.replier.time.sleep")
    @patch("lib.replier.XHSClient.is_skipped", return_value=False)
    @patch(
        "lib.replier.XHSClient.reply",
        return_value=(True, "", ""),
    )
    @patch("lib.cli_ai.CommentScanner")
    def test_ai_send_accepts_exact_preview_binding(
        self, scanner_class, _reply, _is_skipped, _sleep
    ):
        item = {
            "comment_id": "c1",
            "nickname": "用户",
            "content": "评论",
            "reply": "回复",
            "action": "send",
        }
        current_hash = cli_ai.preview_hash([item])
        scanner_class.return_value.verify_candidates_online.return_value = (
            [item.copy()],
            [],
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = {
                "directory": temp_dir,
                "scan": os.path.join(temp_dir, "scan.json"),
                "reply_map": os.path.join(temp_dir, "reply_map.json"),
                "drafts": os.path.join(temp_dir, "drafts.json"),
            }
            with open(paths["drafts"], "w", encoding="utf-8") as file:
                json.dump({
                    "note_id": "n1",
                    "active_comment_ids": ["c1"],
                    "active_batch": {
                        "batch_id": "batch-1",
                        "preview_hash": current_hash,
                        "status": "previewed",
                    },
                    "drafts": [item],
                }, file)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                cli_ai._send(
                    argparse.Namespace(
                        note_id="n1",
                        confirmed=True,
                        batch_id="batch-1",
                        preview_hash=current_hash,
                    ),
                    paths,
                )
            payload = json.loads(output.getvalue())
            with open(paths["drafts"], encoding="utf-8") as file:
                saved = json.load(file)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["sent"], 1)
        self.assertEqual(
            saved["active_batch"]["status"], "completed"
        )
        self.assertEqual(saved["drafts"][0]["send_status"], "sent")

    @patch("lib.cli_ai.Replier")
    @patch("lib.cli_ai.CommentScanner")
    def test_ai_send_never_retries_uncertain_inflight_item(
        self, scanner_class, replier_class
    ):
        item = {
            "comment_id": "c1",
            "nickname": "用户",
            "content": "评论",
            "reply": "回复",
            "action": "send",
            "send_status": "sending",
        }
        current_hash = cli_ai.preview_hash([item])
        scanner_class.return_value.verify_candidates_online.return_value = (
            [item.copy()],
            [],
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = {
                "directory": temp_dir,
                "scan": os.path.join(temp_dir, "scan.json"),
                "reply_map": os.path.join(temp_dir, "reply_map.json"),
                "drafts": os.path.join(temp_dir, "drafts.json"),
            }
            with open(paths["drafts"], "w", encoding="utf-8") as file:
                json.dump({
                    "note_id": "n1",
                    "active_comment_ids": ["c1"],
                    "active_batch": {
                        "batch_id": "batch-1",
                        "preview_hash": current_hash,
                        "status": "sending",
                    },
                    "drafts": [item],
                }, file)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                cli_ai._send(
                    argparse.Namespace(
                        note_id="n1",
                        confirmed=True,
                        batch_id="batch-1",
                        preview_hash=current_hash,
                    ),
                    paths,
                )
        payload = json.loads(output.getvalue())
        self.assertEqual(
            payload["error_type"], "uncertain_send_state"
        )
        self.assertFalse(payload["automatic_retry"])
        replier_class.assert_not_called()

    def test_ai_reply_send_requires_user_confirmation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = {
                "directory": temp_dir,
                "scan": os.path.join(temp_dir, "scan.json"),
                "reply_map": os.path.join(temp_dir, "reply_map.json"),
                "drafts": os.path.join(temp_dir, "drafts.json"),
            }
            args = argparse.Namespace(
                note_id="n1", action="send", replies=None, confirmed=False
            )
            output = io.StringIO()
            with patch("lib.cli_ai.workflow_paths", return_value=paths):
                with contextlib.redirect_stdout(output):
                    cli_ai.cmd_ai_reply(args)
        payload = json.loads(output.getvalue())
        self.assertFalse(payload["ok"])
        self.assertIn("--confirmed", payload["error"])

    @patch("lib.cli_ai.CommentScanner")
    def test_ai_reply_prepare_returns_inline_candidates(self, scanner_class):
        scanner_class.return_value.scan_via_notifications.return_value = {
            "note_id": "n1",
            "source": "notifications",
            "reply_status_verified": True,
            "unreplied_level1": [{
                "comment_id": "c1",
                "nickname": "用户",
                "content": "评论",
            }],
            "unreplied_subs": [],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = {
                "directory": temp_dir,
                "scan": os.path.join(temp_dir, "scan.json"),
                "reply_map": os.path.join(temp_dir, "reply_map.json"),
                "drafts": os.path.join(temp_dir, "drafts.json"),
            }
            args = argparse.Namespace(
                note_id="n1", action="prepare",
                replies=None, confirmed=False,
                full_scan=False, limit=20,
            )
            output = io.StringIO()
            with patch("lib.cli_ai.workflow_paths", return_value=paths):
                with contextlib.redirect_stdout(output):
                    cli_ai.cmd_ai_reply(args)
            payload = json.loads(output.getvalue())
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["scope"], "latest")
        self.assertEqual(
            payload["scan_method"], "scan_via_notifications"
        )
        self.assertEqual(
            payload["verification_mode"], "candidate_online_recheck"
        )
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["deferred_count"], 0)
        self.assertEqual(payload["candidates"][0]["comment_id"], "c1")
        scanner_class.return_value.scan_note.assert_not_called()
        scan_kwargs = (
            scanner_class.return_value.scan_via_notifications
            .call_args.kwargs
        )
        self.assertEqual(scan_kwargs["verification_max_pages"], 6)
        self.assertTrue(scan_kwargs["allow_partial_verification"])

    @patch(
        "lib.cli_ai.load_local_comment_states",
        return_value={
            "sent-id": "sent",
            "failed-id": "failed",
            "sending-id": "sending",
            "pending-id": "",
        },
    )
    @patch("lib.cli_ai.CommentScanner")
    def test_ai_reply_prepare_prefilters_non_resend_local_states(
        self, scanner_class, _local_states
    ):
        scanner_class.return_value.scan_via_notifications.return_value = {
            "note_id": "n1",
            "source": "notifications",
            "reply_status_verified": True,
            "unreplied_level1": [],
            "unreplied_subs": [],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = {
                "directory": temp_dir,
                "scan": os.path.join(temp_dir, "scan.json"),
                "reply_map": os.path.join(temp_dir, "reply_map.json"),
                "drafts": os.path.join(temp_dir, "drafts.json"),
            }
            args = argparse.Namespace(
                note_id="n1", action="prepare",
                replies=None, confirmed=False,
                full_scan=False, limit=20,
            )
            output = io.StringIO()
            with patch("lib.cli_ai.workflow_paths", return_value=paths):
                with contextlib.redirect_stdout(output):
                    cli_ai.cmd_ai_reply(args)
        excluded = (
            scanner_class.return_value.scan_via_notifications
            .call_args.kwargs["excluded_comment_ids"]
        )
        self.assertEqual(
            excluded,
            {"sent-id", "failed-id", "sending-id"},
        )

    @patch("lib.cli_ai.CommentScanner")
    def test_ai_reply_full_prepare_bypasses_cache(self, scanner_class):
        scanner_class.return_value.scan_note.return_value = {
            "note_id": "n1",
            "source": "full_scan",
            "reply_status_verified": True,
            "unreplied_level1": [],
            "unreplied_subs": [],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = {
                "directory": temp_dir,
                "scan": os.path.join(temp_dir, "scan.json"),
                "reply_map": os.path.join(temp_dir, "reply_map.json"),
                "drafts": os.path.join(temp_dir, "drafts.json"),
            }
            args = argparse.Namespace(
                note_id="n1", action="prepare",
                replies=None, confirmed=False,
                full_scan=True, limit=20,
            )
            output = io.StringIO()
            with patch("lib.cli_ai.workflow_paths", return_value=paths):
                with contextlib.redirect_stdout(output):
                    cli_ai.cmd_ai_reply(args)
            payload = json.loads(output.getvalue())
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["scope"], "full")
        self.assertEqual(payload["scan_method"], "scan_note")
        self.assertEqual(payload["verification_mode"], "full_tree_scan")
        kwargs = scanner_class.return_value.scan_note.call_args.kwargs
        self.assertTrue(kwargs["include_sub_comments"])
        self.assertTrue(kwargs["force_refresh"])
        scanner_class.return_value.scan_via_notifications.assert_not_called()
