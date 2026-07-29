import argparse
import contextlib
import io
import json
import os
import tempfile
import unittest
import stat
from unittest.mock import MagicMock, patch

import main
from lib.replier import Replier
from lib.scanner import CommentScanner
from lib.xhs_client import XHSClient
from lib import cli_ai
from lib.cli_parser import (
    COMMAND_EFFECTS,
    COMMAND_NAMES,
    build_command_contract,
    build_parser,
)
from lib.cli_support import TERMINAL_SEND_STATUSES


class CompactOutputTests(unittest.TestCase):
    def test_command_manifest_matches_parser(self):
        parser = build_parser()
        subparsers = next(
            action for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)
        )
        self.assertEqual(set(subparsers.choices), set(COMMAND_NAMES))
        self.assertEqual(set(main.COMMAND_HANDLERS), set(COMMAND_NAMES))
        self.assertEqual(set(COMMAND_EFFECTS), set(COMMAND_NAMES))
        self.assertEqual(len(COMMAND_NAMES), 14)

    def test_ai_help_reports_authoritative_program_facts(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            main.cmd_ai_help(argparse.Namespace())
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["command_count"], 14)
        self.assertEqual(payload["commands"], list(COMMAND_NAMES))
        self.assertEqual(payload["app_name"], "小红书AI智能运营系统")
        self.assertEqual(payload["cli_name"], "小红书AI智能运营系统")
        self.assertEqual(
            payload["output_contract"]["ai_reply"],
            "始终为单一紧凑JSON",
        )
        self.assertEqual(payload["defaults"]["cache_ttl_minutes"], 30)
        self.assertIn("以本次命令运行结果为准", payload["verification"]["test_count"])
        self.assertIn("lib/__init__.py", payload["source_inventory"]["files"])
        self.assertEqual(
            payload["source_inventory"]["count"],
            len(payload["source_inventory"]["files"]),
        )
        self.assertEqual(payload["project_inventory"]["count"], 19)
        self.assertEqual(
            payload["project_inventory"]["tests"],
            ["tests/test_compact_output.py"],
        )
        self.assertTrue(
            payload["workflows"]["traditional_reply"]["send_online_recheck"]
        )
        self.assertEqual(
            payload["reply_decision"]["terminal_statuses"],
            list(TERMINAL_SEND_STATUSES),
        )
        self.assertIn(
            "不代表可回复",
            payload["reply_decision"]["status_precedence"][-1],
        )
        self.assertIn(
            "reply_map_string_shorthand",
            payload["output_contract"],
        )
        self.assertIn(
            "force_refresh=true",
            payload["online_verification_stages"]["prepare"],
        )
        self.assertIn(
            "停用旧active_comment_ids",
            payload["online_verification_stages"]["draft"],
        )
        self.assertIn(
            "sub_comment_count",
            payload["online_verification_stages"]["incomplete_sub_comments"],
        )
        self.assertGreater(payload["test_inventory"]["count"], 0)
        self.assertIn(
            "不能证明",
            payload["test_inventory"]["history_rule"],
        )

    def test_ai_help_can_report_one_exact_command(self):
        args = build_parser().parse_args([
            "ai-help", "--command", "send",
        ])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            main.cmd_ai_help(args)
        payload = json.loads(output.getvalue())
        contract = payload["command_contract"]
        self.assertEqual(contract["command"], "send")
        arguments = {
            item["name"]: item for item in contract["arguments"]
        }
        self.assertTrue(arguments["file"]["required"])
        self.assertEqual(arguments["confirm"]["type"], "boolean")
        self.assertIn("仅改变续发提示", arguments["resume"]["help"])
        self.assertEqual(contract["effects"]["platform"], "发送回复")
        self.assertEqual(contract, build_command_contract("send"))

    def test_ai_help_can_report_current_tests_without_claiming_history(self):
        args = build_parser().parse_args(["ai-help", "--tests"])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            main.cmd_ai_help(args)
        payload = json.loads(output.getvalue())
        inventory = payload["test_inventory"]
        self.assertEqual(inventory["count"], len(inventory["tests"]))
        self.assertIn(
            "test_compact_output.CompactOutputTests."
            "test_ai_help_can_report_current_tests_without_claiming_history",
            inventory["tests"],
        )
        self.assertIn("不能证明", inventory["history_rule"])

    def test_ai_help_summary_is_short_and_canonical(self):
        args = build_parser().parse_args(["ai-help", "--summary"])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            main.cmd_ai_help(args)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["system_name"], "小红书AI智能运营系统")
        self.assertEqual(payload["cli_name"], "小红书AI智能运营系统")
        self.assertIn(
            "全量prepare使用scan_note",
            payload["canonical_facts"]["ai_reply"],
        )
        self.assertIn(
            "之后仍强制在线核验",
            payload["canonical_facts"]["unverified_scan"],
        )
        self.assertIn("不能称为只读", payload["canonical_facts"]["login"])
        self.assertEqual(
            payload["canonical_facts"]["failure_types"]["current"],
            list(XHSClient.REPLY_ERROR_TYPES),
        )
        self.assertIn(
            "不等于可回复",
            payload["canonical_facts"]["candidate_eligibility"],
        )
        self.assertIn(
            "硬停止且不归档",
            payload["canonical_facts"]["online_outcomes"],
        )
        self.assertIn(
            "全部终态",
            payload["canonical_facts"]["history_merge"],
        )
        self.assertIn(
            "缺少映射时默认skip",
            payload["canonical_facts"]["reply_map"],
        )
        self.assertIn(
            "force_refresh=true",
            payload["canonical_facts"]["full_prepare"],
        )
        self.assertIn(
            "prepare和draft都会清空",
            payload["canonical_facts"]["batch_reset"],
        )
        self.assertIn(
            "--resume只改变",
            payload["canonical_facts"]["traditional_send"],
        )
        self.assertIn(
            "没有旧快照",
            payload["canonical_facts"]["tests"],
        )
        self.assertIn(
            "不能称为只读",
            payload["canonical_facts"]["module_mutability"],
        )
        self.assertNotIn("architecture", payload)
        self.assertEqual(
            payload["exact_command_protocol"],
            "python3 main.py ai-help --command <command>",
        )
        self.assertEqual(
            payload["test_inventory"]["command"],
            "python3 main.py ai-help --tests",
        )
        self.assertEqual(payload["full_protocol"], "python3 main.py ai-help")

    def test_reply_map_validation_rejects_unsafe_send_entries(self):
        errors = cli_ai._validate_reply_map({
            "c1": {"reply": "", "action": "send"},
            "c2": {"reply": "回复", "action": "sent"},
        }, ["c1", "c2"])
        self.assertEqual(len(errors), 2)
        self.assertIn("reply 不能为空", errors[0])
        self.assertIn("action 必须", errors[1])

    def test_reply_map_allows_empty_skip_and_archive(self):
        errors = cli_ai._validate_reply_map({
            "c1": {"reply": "", "action": "skip"},
            "c2": {"reply": "", "action": "archive"},
        }, ["c1", "c2"])
        self.assertEqual(errors, [])

    def test_clear_active_batch_preserves_history(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "drafts.json")
            original = {
                "active_comment_ids": ["c1"],
                "drafts": [{
                    "comment_id": "c1",
                    "send_status": "sent",
                }],
            }
            with open(path, "w", encoding="utf-8") as file:
                json.dump(original, file)
            cli_ai._clear_active_batch(path)
            with open(path, encoding="utf-8") as file:
                updated = json.load(file)
        self.assertEqual(updated["active_comment_ids"], [])
        self.assertEqual(updated["drafts"], original["drafts"])

    def test_reply_error_markers_match_current_error_types(self):
        cases = {
            "评论已删除": "comment_deleted",
            "操作太快，请稍后": "rate_limited",
            "API error -9126": "content_rejected",
            "其他回复失败": "unknown_error",
        }
        for output, expected in cases.items():
            with self.subTest(output=output):
                self.assertEqual(
                    XHSClient._classify_reply_error("", output),
                    expected,
                )
        self.assertNotIn(
            "-1",
            XHSClient.REPLY_ERROR_MARKERS["comment_deleted"],
        )

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

    def test_ai_reply_send_requires_user_confirmation(self):
        args = argparse.Namespace(
            note_id="n1", action="send", replies=None, confirmed=False
        )
        output = io.StringIO()
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
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["candidates"][0]["comment_id"], "c1")
        scanner_class.return_value.scan_note.assert_not_called()

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
        kwargs = scanner_class.return_value.scan_note.call_args.kwargs
        self.assertTrue(kwargs["include_sub_comments"])
        self.assertTrue(kwargs["force_refresh"])
        scanner_class.return_value.scan_via_notifications.assert_not_called()

    @patch("main.XHSClient.get_skipped_ids", return_value=set())
    def test_comment_groups_are_numbered_and_hide_credentials(self, _skipped):
        groups = main.build_comment_groups([
            {
                "time": 1785300000,
                "title": "评论了你的笔记",
                "user_info": {"nickname": "用户", "xsec_token": "secret"},
                "item_info": {
                    "id": "n1",
                    "content": "文章",
                    "xsec_token": "note-secret",
                },
                "comment_info": {
                    "id": "c1",
                    "content": "评论",
                    "illegal_info": {"illegal_status": "NORMAL"},
                },
            },
        ])
        self.assertEqual(groups[0]["note_index"], 1)
        self.assertEqual(groups[0]["comments"][0]["status"], "正常")
        self.assertNotIn("secret", json.dumps(groups, ensure_ascii=False))

    @patch("main.XHSClient.get_notifications", return_value=[])
    def test_comments_json_declares_user_visible_columns(self, _notifications):
        args = argparse.Namespace(limit=20, note_id=None, json=True)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            main.cmd_comments(args)
        payload = json.loads(output.getvalue())
        self.assertEqual(
            payload["columns"],
            ["序号", "时间", "用户", "评论", "状态"],
        )

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
                "deleted": False,
            }],
        }]
        client.get_skipped_ids.return_value = set()
        client.get_comments_until_ids.return_value = [{
            "id": "c1",
            "sub_comment_count": "0",
            "sub_comments": [],
        }]
        result = CommentScanner(client).scan_via_notifications(
            note_id="n1", verbose=False
        )
        self.assertTrue(result["reply_status_verified"])
        self.assertEqual(result["unreplied_level1"][0]["comment_id"], "c1")

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
        result = CommentScanner(client).scan_via_notifications(
            note_id="n1", verbose=False
        )
        self.assertTrue(result["reply_status_verified"])
        self.assertEqual(result["unreplied_level1"], [])
        self.assertEqual(result["filtered_deleted"], 1)
        client.get_comments_until_ids.assert_not_called()

    def test_notification_scan_reports_network_failure(self):
        client = MagicMock()
        client.get_new_comment_notifications.side_effect = RuntimeError(
            "network unavailable"
        )
        result = CommentScanner(client).scan_via_notifications(verbose=False)
        self.assertFalse(result["reply_status_verified"])
        self.assertIn("network unavailable", result["scan_error"])

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
                "illegal_info": {"illegal_status": "NORMAL"},
            },
            "user_info": {"nickname": "用户"},
            "time": 1,
        }
        notifications.return_value = [item, dict(item)]
        result = XHSClient.get_new_comment_notifications(num=20)
        self.assertEqual(len(result[0]["new_comments"]), 1)
        self.assertEqual(result[0]["new_comments"][0]["comment_id"], "c1")

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

    def test_online_reply_index_tracks_direct_nested_reply(self):
        comments = [{
            "id": "root",
            "sub_comments": [
                {
                    "id": "candidate",
                    "user_info": {"user_id": "other"},
                },
                {
                    "id": "author-reply",
                    "user_info": {"user_id": "6321167e0000000023038acd"},
                    "target_comment": {"id": "candidate"},
                },
            ],
        }]
        existing, replied = CommentScanner._online_reply_index(comments)
        self.assertEqual(existing, {"root", "candidate", "author-reply"})
        self.assertEqual(replied, {"candidate"})

    def test_online_verification_excludes_replied_and_missing(self):
        client = MagicMock()
        client.get_comments_until_ids.return_value = [{
            "id": "keep",
            "sub_comments": [{
                "id": "author-reply",
                "user_info": {"user_id": "6321167e0000000023038acd"},
                "target_comment": {"id": "replied"},
            }, {
                "id": "replied",
                "user_info": {"user_id": "other"},
            }],
        }]
        eligible, excluded = CommentScanner(client).verify_candidates_online(
            "note",
            [
                {"comment_id": "keep"},
                {"comment_id": "replied"},
                {"comment_id": "missing"},
            ],
        )
        self.assertEqual(eligible, [{"comment_id": "keep"}])
        self.assertEqual(
            excluded,
            [
                {"comment_id": "replied", "reason": "online_replied"},
                {"comment_id": "missing", "reason": "online_missing"},
            ],
        )

    def test_online_verification_fetches_incomplete_candidate_thread(self):
        client = MagicMock()
        client.get_comments_until_ids.return_value = [{
            "id": "root",
            "sub_comment_count": "2",
            "sub_comments": [{
                "id": "candidate",
                "user_info": {"user_id": "other"},
            }],
        }]
        client.get_sub_comments.return_value = [
            {
                "id": "candidate",
                "user_info": {"user_id": "other"},
            },
            {
                "id": "author-reply",
                "user_info": {"user_id": "6321167e0000000023038acd"},
                "target_comment": {"id": "candidate"},
            },
        ]
        eligible, excluded = CommentScanner(client).verify_candidates_online(
            "note", [{"comment_id": "candidate"}]
        )
        self.assertEqual(eligible, [])
        self.assertEqual(
            excluded,
            [{"comment_id": "candidate", "reason": "online_replied"}],
        )

    def test_online_verification_stops_when_search_is_incomplete(self):
        client = MagicMock()
        client.get_comments_until_ids.return_value = ([], False)
        with self.assertRaisesRegex(RuntimeError, "页数上限"):
            CommentScanner(client).verify_candidates_online(
                "note", [{"comment_id": "candidate"}]
            )

    def test_online_verification_stops_when_sub_comments_are_incomplete(self):
        client = MagicMock()
        client.get_comments_until_ids.return_value = ([{
            "id": "root",
            "sub_comment_count": "2",
            "sub_comments": [{
                "id": "candidate",
                "user_info": {"user_id": "other"},
            }],
        }], True)
        client.get_sub_comments.return_value = [{
            "id": "candidate",
            "user_info": {"user_id": "other"},
        }]
        with self.assertRaisesRegex(RuntimeError, "楼中楼在线数据不完整"):
            CommentScanner(client).verify_candidates_online(
                "note", [{"comment_id": "candidate"}]
            )

    @patch("lib.scanner.time.sleep")
    def test_scan_keeps_unreplied_nested_comment_when_root_was_replied(
        self, _sleep
    ):
        author_id = "6321167e0000000023038acd"
        client = MagicMock()
        client.get_skipped_ids.return_value = set()
        client.get_comments_cached.return_value = ([{
            "id": "root",
            "content": "一级评论",
            "user_info": {"user_id": "other", "nickname": "甲"},
            "sub_comment_count": "4",
            "sub_comments": [{
                "id": "reply-root",
                "user_info": {"user_id": author_id},
                "target_comment": {"id": "root"},
            }],
        }], False)
        client.get_sub_comments.return_value = [
            {
                "id": "reply-root",
                "user_info": {"user_id": author_id},
                "target_comment": {"id": "root"},
            },
            {
                "id": "nested-replied",
                "content": "已回复的楼中楼",
                "user_info": {"user_id": "other", "nickname": "乙"},
            },
            {
                "id": "reply-nested",
                "user_info": {"user_id": author_id},
                "target_comment": {"id": "nested-replied"},
            },
            {
                "id": "nested-pending",
                "content": "尚未回复的楼中楼",
                "user_info": {"user_id": "other", "nickname": "丙"},
            },
        ]
        result = CommentScanner(client).scan_note(
            "note", include_sub_comments=True, verbose=False
        )
        self.assertEqual(result["unreplied_level1"], [])
        self.assertEqual(
            [item["comment_id"] for item in result["unreplied_subs"]],
            ["nested-pending"],
        )
        self.assertTrue(result["reply_status_verified"])

    def test_complete_inline_nested_comments_use_target_specific_reply(self):
        author_id = "6321167e0000000023038acd"
        client = MagicMock()
        client.get_skipped_ids.return_value = set()
        client.get_comments_cached.return_value = ([{
            "id": "root",
            "content": "一级评论",
            "user_info": {"user_id": "other", "nickname": "甲"},
            "sub_comment_count": "3",
            "sub_comments": [
                {
                    "id": "nested-replied",
                    "content": "已回复",
                    "user_info": {"user_id": "other", "nickname": "乙"},
                },
                {
                    "id": "author-reply",
                    "user_info": {"user_id": author_id},
                    "target_comment": {"id": "nested-replied"},
                },
                {
                    "id": "nested-pending",
                    "content": "未回复",
                    "user_info": {"user_id": "other", "nickname": "丙"},
                },
            ],
        }], False)
        result = CommentScanner(client).scan_note(
            "note", include_sub_comments=True, verbose=False
        )
        self.assertEqual(
            [item["comment_id"] for item in result["unreplied_level1"]],
            ["root"],
        )
        self.assertEqual(
            [item["comment_id"] for item in result["unreplied_subs"]],
            ["nested-pending"],
        )

    @patch("lib.scanner.time.sleep")
    def test_scan_marks_incomplete_nested_data_unverified(self, _sleep):
        client = MagicMock()
        client.get_skipped_ids.return_value = set()
        client.get_comments_cached.return_value = ([{
            "id": "root",
            "content": "一级评论",
            "user_info": {"user_id": "other", "nickname": "甲"},
            "sub_comment_count": "2",
            "sub_comments": [],
        }], False)
        client.get_sub_comments.return_value = []
        result = CommentScanner(client).scan_note(
            "note", include_sub_comments=True, verbose=False
        )
        self.assertFalse(result["reply_status_verified"])
        self.assertIn("楼中楼数据不完整", result["scan_error"])
        self.assertEqual(result["unreplied_subs"], [])

    @patch("lib.xhs_client.XHSClient._run_xhs")
    def test_articles_strict_mode_does_not_hide_failure(self, run_xhs):
        run_xhs.side_effect = RuntimeError("network unavailable")
        with self.assertRaisesRegex(RuntimeError, "network unavailable"):
            XHSClient.list_articles(limit=20)

    @patch("main.XHSClient.list_articles", side_effect=RuntimeError("offline"))
    def test_articles_json_error_remains_valid_json(self, _list_articles):
        args = argparse.Namespace(limit=20, json=True)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            main.cmd_articles(args)
        self.assertEqual(
            json.loads(output.getvalue()),
            {"ok": False, "error": "offline"},
        )

    def test_compact_comment_removes_internal_fields(self):
        source = {
            "comment_id": "c1",
            "nickname": "用户",
            "content": "内容",
            "likes": 2,
            "inline_subs": [{"id": "internal"}],
            "inline_subs_count": 1,
            "_replied": False,
        }
        self.assertEqual(
            main.compact_comment(source),
            {
                "comment_id": "c1",
                "nickname": "用户",
                "content": "内容",
                "likes": 2,
            },
        )

    def test_compact_scan_keeps_verification_state(self):
        compact = main.compact_scan_result({
            "note_id": "n1",
            "reply_status_verified": True,
            "unreplied_level1": [],
            "unreplied_subs": [],
        })
        self.assertTrue(compact["reply_status_verified"])

    def test_compact_scan_numbers_comment_note_groups(self):
        compact = main.compact_scan_result({
            "note_id": "all",
            "unreplied_level1": [],
            "unreplied_subs": [],
            "per_note": [
                {
                    "note_id": "n1",
                    "unreplied_level1": [],
                    "unreplied_subs": [],
                },
                {
                    "note_id": "n2",
                    "unreplied_level1": [],
                    "unreplied_subs": [],
                },
            ],
        })
        self.assertEqual(
            [item["note_index"] for item in compact["per_note"]],
            [1, 2],
        )

    def test_scan_summary_does_not_repeat_comments(self):
        result = {
            "note_id": "n1",
            "unreplied_level1": [{"comment_id": "c1", "content": "很长的评论"}],
            "unreplied_subs": [],
        }
        summary = main.scan_summary(result)
        self.assertNotIn("很长的评论", json.dumps(summary, ensure_ascii=False))
        self.assertEqual(summary["unreplied_level1"], 1)

    @patch("main.XHSClient.list_articles")
    def test_articles_json_hides_xsec_token(self, list_articles):
        list_articles.return_value = [{
            "id": "n1",
            "title": "标题",
            "comments_count": 3,
            "xsec_token": "secret",
            "time": "2026-07-26 10:00",
        }]
        args = argparse.Namespace(limit=1, json=True)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            main.cmd_articles(args)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["articles"][0]["id"], "n1")
        self.assertEqual(
            payload["columns"],
            ["序号", "发布时间", "评论数", "标题", "笔记ID"],
        )
        self.assertEqual(payload["articles"][0]["title"], "标题")
        self.assertNotIn("xsec_token", payload["articles"][0])
        self.assertNotIn("secret", output.getvalue())

    def test_compact_analysis_removes_duplicate_arrays(self):
        result = {
            "note_id": "n1",
            "counts": {"positive": 1},
            "classified": [{"content": "重复内容"}],
            "sorted_by_likes": [{"content": "热门内容", "likes": 2}],
            "active_users": {"用户": 2},
        }
        compact = main.compact_analysis(result)
        self.assertNotIn("classified", compact)
        self.assertEqual(compact["top_comments"][0]["content"], "热门内容")

    @patch("main.CommentScanner.scan_via_notifications")
    def test_scan_with_output_only_prints_summary(self, scan):
        scan.return_value = {
            "note_id": "n1",
            "source": "notifications",
            "unreplied_level1": [{
                "comment_id": "c1",
                "nickname": "用户",
                "content": "不应在终端重复的评论正文",
                "inline_subs": [{"large": "internal"}],
            }],
            "unreplied_subs": [],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = os.path.join(temp_dir, "scan.json")
            args = argparse.Namespace(
                full_scan=False,
                note_id="n1",
                json=True,
                num_notifications=20,
                xsec_token="",
                output=output_path,
                with_subs=False,
                refresh=False,
                max_pages=None,
            )
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                main.cmd_scan(args)
            response = json.loads(stdout.getvalue())
            with open(output_path, encoding="utf-8") as saved_file:
                saved = json.load(saved_file)
            self.assertNotIn("不应在终端重复的评论正文", stdout.getvalue())
            self.assertEqual(response["summary"]["unreplied_level1"], 1)
            self.assertNotIn("inline_subs", saved["unreplied_level1"][0])

    def test_workflow_paths_are_stable(self):
        first = main.workflow_paths("note-1")
        second = main.workflow_paths("note-1")
        self.assertEqual(first, second)
        self.assertTrue(first["scan"].endswith(
            ".cache/workflows/note-1/scan.json"
        ))

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


if __name__ == "__main__":
    unittest.main()
