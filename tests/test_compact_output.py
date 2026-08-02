import argparse
import contextlib
import html
import io
import json
import os
import subprocess
import tempfile
import unittest
import stat
from unittest.mock import MagicMock, patch

import main
from config import CACHE_DIR, PROJECT_ROOT
from lib.replier import Replier
from lib.scanner import CommentScanner
from lib.analyzer import _classify_sentiment
from lib.state_io import file_lock, StateLockTimeout
from lib.xhs_client import XHSClient
from lib import cli_ai
from lib.cli_parser import (
    COMMAND_EFFECTS,
    COMMAND_NAMES,
    build_command_contract,
    build_parser,
)
from lib.cli_support import (
    build_comment_display_groups,
    save_comment_archive,
    TERMINAL_SEND_STATUSES,
)


class CompactOutputTests(unittest.TestCase):
    def test_ai_large_rows_are_capped_without_losing_total_source(self):
        rows = [{"index": index} for index in range(25)]
        self.assertEqual(len(cli_ai._inline_rows(rows)), 20)
        self.assertEqual(cli_ai._inline_rows(rows)[-1]["index"], 19)

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
        self.assertEqual(payload["system_name"], "小红书AI智能运营系统")
        self.assertNotIn("cli_name", payload)
        self.assertEqual(payload["app_version"], "4.0.0")
        self.assertEqual(payload["schema_version"], "4")
        self.assertEqual(
            payload["output_contract"]["ai_reply"],
            "始终为单一紧凑JSON",
        )
        self.assertIn(
            "不得截断",
            payload["output_contract"]["comment_list"],
        )
        display = payload["output_contract"]["comment_display"]
        self.assertTrue(display["adaptive_width"])
        self.assertTrue(display["groups_are_display_ready"])
        self.assertEqual(display["raw_content_source"], "archive.path")
        self.assertEqual(
            display["comment_column"]["soft_break_tag"], "<wbr>"
        )
        columns = display["columns"]
        self.assertEqual(columns["评论"]["role"], "flexible")
        for name in ("序号", "时间", "用户", "状态"):
            self.assertEqual(columns[name]["role"], "compact")
        self.assertEqual(
            columns["时间"]["format"], "YYYY-MM-DD<br>HH:mm"
        )
        self.assertGreater(
            columns["评论"]["preferred_display_width"],
            columns["用户"]["preferred_display_width"],
        )
        self.assertEqual(payload["defaults"]["cache_ttl_minutes"], 30)
        self.assertIn("以本次命令运行结果为准", payload["verification"]["test_count"])
        self.assertIn("lib/__init__.py", payload["source_inventory"]["files"])
        self.assertEqual(
            payload["source_inventory"]["count"],
            len(payload["source_inventory"]["files"]),
        )
        self.assertEqual(
            payload["project_inventory"]["count"],
            payload["project_inventory"]["production_python_count"]
            + len(payload["project_inventory"]["tests"])
            + len(payload["project_inventory"]["documentation"]),
        )
        self.assertIn("lib/state_io.py", payload["source_inventory"]["files"])
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
            "batch_id",
            payload["output_contract"]["draft_confirmation"],
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
        self.assertIn(
            "只严格展开候选所在楼层",
            payload["online_verification_stages"]["expansion_scope"],
        )
        self.assertIn(
            "automatic_retry=false",
            payload["online_verification_stages"]["verification_required"],
        )
        self.assertIn(
            "禁止用第二种传输重复请求",
            payload["online_verification_stages"]["sub_comment_transport"],
        )
        self.assertIn(
            "供后续draft/send接续",
            payload["storage"]["notification_token_handoff"],
        )
        self.assertIn(
            "scan_via_notifications",
            payload["common_misunderstandings"]["notification_entrypoint"],
        )
        self.assertIn(
            "只严格补全候选相关楼层",
            payload["common_misunderstandings"]["expansion_scope"],
        )
        self.assertIn(
            "完全不写状态",
            payload["common_misunderstandings"]["hard_stop_state"],
        )
        self.assertIn(
            "固定三次",
            payload["common_misunderstandings"]["fixed_three_stages"],
        )
        self.assertIn(
            "保留全部旧条目",
            payload["common_misunderstandings"]["history_merge"],
        )
        self.assertIn(
            "只校验本次scan候选",
            payload["common_misunderstandings"]["mapping_validation_scope"],
        )
        self.assertIn(
            "verification_mode",
            payload["output_contract"]["prepare_metadata"],
        )
        self.assertIn(
            "duplicate_send_mapping",
            payload["output_contract"]["duplicate_send_error"],
        )
        self.assertIn(
            "最多回复一次",
            payload["reply_decision"]["duplicate_policy"],
        )
        self.assertIn(
            "不得显示comment_id",
            payload["reply_decision"]["user_visible_ids"],
        )
        self.assertIn(
            "本次候选映射语义",
            payload["online_verification_stages"]["draft"],
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
        self.assertEqual(payload["app_name"], "小红书AI智能运营系统")
        self.assertEqual(payload["app_version"], "4.0.0")
        self.assertEqual(payload["schema_version"], "4")
        self.assertIn("--batch-id", payload["reply_workflow"]["send"])
        self.assertIn("--preview-hash", payload["reply_workflow"]["send"])
        self.assertIn(
            "workflow_busy",
            payload["error_actions"],
        )
        self.assertIn(
            "uncertain_send_state",
            payload["error_actions"],
        )
        self.assertIn(
            "batch_id",
            payload["state"]["batch_fields"],
        )
        self.assertLess(len(output.getvalue().encode("utf-8")), 4000)
        self.assertNotIn("architecture", payload)
        self.assertEqual(
            payload["entrypoints"]["command"],
            "python3 main.py ai-help --command <command>",
        )
        self.assertEqual(
            payload["entrypoints"]["tests"],
            "python3 main.py ai-help --tests",
        )
        self.assertEqual(
            payload["entrypoints"]["full"],
            "python3 main.py ai-help",
        )

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

    def test_workflow_error_marks_captcha_as_user_action(self):
        payload = cli_ai._workflow_error(
            "draft",
            RuntimeError(
                "verification_required: Captcha required. "
                "Please complete verification."
            ),
            prefix="在线复核失败",
        )
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error_type"], "verification_required")
        self.assertFalse(payload["automatic_retry"])
        self.assertTrue(payload["requires_user_action"])
        self.assertIn("Firefox", payload["next"])
        self.assertIn("重新运行当前 action", payload["next"])

    def test_workflow_error_distinguishes_api_risk_control(self):
        payload = cli_ai._workflow_error(
            "draft",
            RuntimeError(
                "verification_required: Captcha required: "
                "type=unknown, uuid=unknown"
            ),
        )
        self.assertEqual(
            payload["verification_context"], "api_risk_control"
        )
        self.assertIn("浏览器页面正常也可能发生", payload["next"])
        self.assertIn("重新导入Firefox Cookie", payload["next"])

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

    @patch("lib.cli_ai.CommentScanner")
    def test_ai_draft_rejects_invalid_mapping_before_online_check(
        self, scanner_class
    ):
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
                    "unreplied_level1": [{
                        "comment_id": "c1",
                        "nickname": "用户",
                        "content": "评论",
                    }],
                    "unreplied_subs": [],
                }, file)
            with open(
                paths["reply_map"], "w", encoding="utf-8"
            ) as file:
                file.write('{"c1":{"reply":"未闭合}')
            args = argparse.Namespace(note_id="n1", replies=None)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                cli_ai._draft(args, paths)
        payload = json.loads(output.getvalue())
        self.assertFalse(payload["ok"])
        self.assertIn("不是有效 JSON", payload["error"])
        self.assertEqual(
            payload["error_type"], "invalid_reply_map_json"
        )
        self.assertFalse(payload["automatic_retry"])
        self.assertTrue(payload["requires_file_fix"])
        self.assertIn("英文半角双引号", payload["next"])
        scanner_class.assert_not_called()

    @patch("lib.cli_ai.CommentScanner")
    def test_ai_draft_rejects_invalid_mapping_semantics_before_online_check(
        self, scanner_class
    ):
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
                    "unreplied_level1": [{
                        "comment_id": "c1",
                        "nickname": "用户",
                        "content": "评论",
                    }],
                    "unreplied_subs": [],
                }, file)
            with open(
                paths["reply_map"], "w", encoding="utf-8"
            ) as file:
                json.dump({
                    "c1": {"reply": "", "action": "send"},
                }, file)
            args = argparse.Namespace(note_id="n1", replies=None)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                cli_ai._draft(args, paths)
        payload = json.loads(output.getvalue())
        self.assertFalse(payload["ok"])
        self.assertEqual(
            payload["error_type"], "invalid_reply_map_mapping"
        )
        self.assertTrue(payload["requires_file_fix"])
        self.assertIn("reply 不能为空", payload["details"][0])
        scanner_class.assert_not_called()

    @patch("lib.cli_ai.CommentScanner")
    def test_ai_draft_rejects_duplicate_send_mapping_before_online_check(
        self, scanner_class
    ):
        candidates = [
            {
                "comment_id": "c1",
                "nickname": "同一用户",
                "content": "相同评论",
            },
            {
                "comment_id": "c2",
                "nickname": "同一用户",
                "content": "相同评论",
            },
        ]
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
                    "unreplied_level1": candidates,
                    "unreplied_subs": [],
                }, file)
            with open(
                paths["reply_map"], "w", encoding="utf-8"
            ) as file:
                json.dump({
                    "c1": {"reply": "回复一", "action": "send"},
                    "c2": {"reply": "回复二", "action": "send"},
                }, file)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                cli_ai._draft(
                    argparse.Namespace(note_id="n1", replies=None),
                    paths,
                )
        payload = json.loads(output.getvalue())
        self.assertFalse(payload["ok"])
        self.assertEqual(
            payload["error_type"], "duplicate_send_mapping"
        )
        self.assertIn("其余改为skip", payload["next"])
        scanner_class.assert_not_called()

    def test_reply_error_markers_match_current_error_types(self):
        cases = {
            "评论已删除": "comment_deleted",
            "操作太快，请稍后": "rate_limited",
            "API error -9126": "content_rejected",
            "API error -9131，由于对方设置，你无法发表评论": (
                "permission_denied"
            ),
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

    def test_nested_api_error_preserves_deleted_message(self):
        output = json.dumps({
            "ok": False,
            "error": {
                "code": "api_error",
                "message": (
                    "API error: "
                    + json.dumps({
                        "success": False,
                        "msg": "回复失败，评论已删除",
                        "code": -9128,
                    }, ensure_ascii=False)
                ),
            },
        }, ensure_ascii=False)
        message, _ = XHSClient._extract_error_from_output(output)
        self.assertIn("评论已删除", message)
        self.assertEqual(
            XHSClient._classify_reply_error(message, output),
            "comment_deleted",
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
        self.assertIsNone(args.batch_id)
        self.assertIsNone(args.preview_hash)

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
                json.dump({"c1": {"reply": "回复", "action": "send"}}, file)
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
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["error_type"], "stale_preview")
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

    @patch(
        "lib.cli_view.save_comment_archive",
        return_value={
            "path": "/cache/comments.json",
            "notes": 0,
            "comments": 0,
            "content_complete": True,
        },
    )
    @patch("main.XHSClient.get_notifications", return_value=[])
    def test_comments_json_declares_user_visible_columns(
        self, _notifications, save_archive
    ):
        args = argparse.Namespace(limit=20, note_id=None, json=True)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            main.cmd_comments(args)
        payload = json.loads(output.getvalue())
        self.assertEqual(
            payload["columns"],
            ["序号", "时间", "用户", "评论", "状态"],
        )
        self.assertTrue(payload["archive"]["content_complete"])
        self.assertTrue(payload["display"]["adaptive_width"])
        self.assertEqual(
            payload["display"]["comment_column"]["soft_break_tag"],
            "<wbr>",
        )
        self.assertEqual(
            payload["display"]["layout"],
            "comment_flexible_other_columns_compact",
        )
        save_archive.assert_called_once_with([])

    @patch(
        "lib.cli_view.save_comment_archive",
        return_value={
            "path": "/cache/comments.json",
            "notes": 1,
            "comments": 1,
            "content_complete": True,
            "content_complete_scope": "notification_payload",
            "content_untruncated_locally": True,
            "platform_tree_verified": False,
        },
    )
    @patch("main.XHSClient.get_skipped_ids", return_value=set())
    @patch("main.XHSClient.get_notifications")
    def test_comments_json_returns_display_ready_safe_groups(
        self, get_notifications, _skipped, _archive
    ):
        get_notifications.return_value = [{
            "time": 1785556800,
            "title": "评论了你的笔记",
            "user_info": {"nickname": "非常非常非常长的用户名"},
            "item_info": {"id": "n1", "content": "文章<table>"},
            "comment_info": {
                "id": "c1",
                "content": "正文|<script>\n" + "长" * 45,
                "illegal_info": {"illegal_status": "NORMAL"},
            },
        }]
        output = io.StringIO()
        args = argparse.Namespace(limit=20, note_id=None, json=True)
        with contextlib.redirect_stdout(output):
            main.cmd_comments(args)
        payload = json.loads(output.getvalue())
        group = payload["groups"][0]
        comment = group["comments"][0]
        self.assertIn("&lt;table&gt;", group["note_title"])
        self.assertEqual(comment["index"], 1)
        self.assertNotIn("comment_id", comment)
        self.assertIn("&#124;", comment["content"])
        self.assertIn("&lt;script&gt;", comment["content"])
        self.assertIn("<br>", comment["content"])
        self.assertIn("<wbr>", comment["content"])
        self.assertTrue(payload["display"]["groups_are_display_ready"])

    @patch(
        "lib.cli_view.save_comment_archive",
        side_effect=RuntimeError(
            "评论归档无法解析，已停止写入以保护历史数据"
        ),
    )
    @patch("main.XHSClient.get_skipped_ids", return_value=set())
    @patch("main.XHSClient.get_notifications")
    def test_comments_json_still_returns_rows_when_archive_is_invalid(
        self, get_notifications, _skipped, _archive
    ):
        get_notifications.return_value = [{
            "time": 1785556800,
            "title": "评论了你的笔记",
            "user_info": {"nickname": "用户"},
            "item_info": {"id": "n1", "content": "文章"},
            "comment_info": {
                "id": "c1",
                "content": "评论",
                "illegal_info": {"illegal_status": "NORMAL"},
            },
        }]
        output = io.StringIO()
        args = argparse.Namespace(limit=20, note_id=None, json=True)
        with contextlib.redirect_stdout(output):
            main.cmd_comments(args)
        payload = json.loads(output.getvalue())
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["comments"], 1)
        self.assertFalse(payload["archive"]["ok"])
        self.assertTrue(payload["archive"]["write_skipped"])
        self.assertEqual(len(payload["warnings"]), 1)

    def test_comment_archive_preserves_full_content_and_quotes(self):
        original = (
            '他说：“中文引号”，又写了 "AI已通过测试"\n'
            '\\path\\end ' + "长评论" * 100
        )
        groups = [{
            "note_index": 1,
            "note_id": "n1",
            "note_title": "文章",
            "comments": [{
                "comment_id": "c1",
                "time": "2026-08-01 12:00",
                "nickname": "用户",
                "content": original,
                "status": "正常",
            }],
        }]
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "comments.json")
            summary = save_comment_archive(groups, path=path)
            with open(path, encoding="utf-8") as file:
                raw = file.read()
            saved = json.loads(raw)
            content = saved["groups"][0]["comments"][0]["content"]
            self.assertEqual(content, original)
            self.assertIn('\\"AI已通过测试\\"', raw)
            self.assertTrue(saved["content_complete"])
            self.assertTrue(summary["content_complete"])
            self.assertEqual(
                saved["content_complete_scope"], "notification_payload"
            )
            self.assertTrue(saved["content_untruncated_locally"])
            self.assertFalse(saved["platform_tree_verified"])
            self.assertEqual(
                summary["content_complete_scope"], "notification_payload"
            )
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)

    def test_comment_archive_refuses_to_overwrite_invalid_json(self):
        broken = b'{"groups":['
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "comments.json")
            with open(path, "wb") as file:
                file.write(broken)
            with self.assertRaisesRegex(RuntimeError, "保护历史数据"):
                save_comment_archive([], path=path)
            with open(path, "rb") as file:
                self.assertEqual(file.read(), broken)

    def test_comment_archive_renumbers_groups_after_incremental_merge(self):
        def group(note_id, comment_id):
            return {
                "note_index": 1,
                "note_id": note_id,
                "note_title": note_id,
                "comments": [{
                    "comment_id": comment_id,
                    "time": "2026-08-01 12:00",
                    "nickname": "用户",
                    "content": "评论",
                    "status": "正常",
                }],
            }

        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "comments.json")
            save_comment_archive([group("n1", "c1")], path=path)
            save_comment_archive([group("n2", "c2")], path=path)
            with open(path, encoding="utf-8") as file:
                saved = json.load(file)
        self.assertEqual(
            [item["note_index"] for item in saved["groups"]],
            [1, 2],
        )
        self.assertEqual(
            [item["note_id"] for item in saved["groups"]],
            ["n2", "n1"],
        )

    def test_comment_display_groups_are_safe_complete_and_ready(self):
        original = '<table>|\\| "引号"\n' + "长" * 45
        groups = [{
            "note_index": 1,
            "note_id": "n1",
            "note_title": "文章",
            "comments": [{
                "comment_id": "c1",
                "time": "2026-08-01 12:00",
                "nickname": "非常非常非常长的用户名",
                "content": original,
                "status": "回复了你的评论",
            }],
        }]
        display_item = build_comment_display_groups(groups)[0]["comments"][0]
        self.assertNotIn("comment_id", display_item)
        self.assertEqual(display_item["index"], 1)
        self.assertEqual(display_item["time"], "2026-08-01<br>12:00")
        self.assertIn("<wbr>", display_item["nickname"])
        self.assertEqual(display_item["status"], "回复了<wbr>你的评论")
        self.assertIn("&lt;table&gt;", display_item["content"])
        self.assertIn("&#124;", display_item["content"])
        self.assertIn("<br>", display_item["content"])
        self.assertIn("<wbr>", display_item["content"])
        restored = html.unescape(
            display_item["content"].replace("<br>", "\n").replace(
                "<wbr>", ""
            )
        )
        self.assertEqual(restored, original)

    @patch(
        "lib.cli_view.save_comment_archive",
        return_value={
            "path": "/cache/comments.json",
            "notes": 1,
            "comments": 1,
            "content_complete": True,
        },
    )
    @patch("main.XHSClient.get_skipped_ids", return_value=set())
    @patch("main.XHSClient.get_notifications")
    def test_comments_terminal_displays_full_comment_content(
        self, get_notifications, _skipped, _archive
    ):
        content = "评论正文" * 100 + '结尾 "引号"'
        get_notifications.return_value = [{
            "time": 1785556800,
            "title": "评论了你的笔记",
            "user_info": {"nickname": "用户"},
            "item_info": {"id": "n1", "content": "文章"},
            "comment_info": {
                "id": "c1",
                "content": content,
                "illegal_info": {"illegal_status": "NORMAL"},
            },
        }]
        output = io.StringIO()
        args = argparse.Namespace(limit=20, note_id=None, json=False)
        with contextlib.redirect_stdout(output):
            main.cmd_comments(args)
        self.assertIn(content, output.getvalue())

    def test_drafts_preserve_full_original_comment_content(self):
        content = "原评论" * 100 + "完整结尾"
        drafts = Replier(MagicMock()).generate_drafts_from_mapping(
            [{
                "comment_id": "c1",
                "nickname": "用户",
                "content": content,
            }],
            {"c1": "回复"},
            note_id="n1",
        )
        self.assertEqual(drafts["drafts"][0]["content"], content)

    def test_analysis_preserves_full_comment_content(self):
        content = "分析评论" * 100 + "完整结尾"
        result = _classify_sentiment([{
            "content": content,
            "user_info": {"user_id": "u1", "nickname": "用户"},
            "like_count": 0,
        }], author_id="author")
        self.assertEqual(result[0]["content"], content)

    def test_skipped_archive_does_not_truncate_comment_content(self):
        content = '引号 "AI" ' + "完整正文" * 100
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "skipped.json")
            with patch(
                "lib.xhs_client.XHSClient._skipped_path",
                return_value=path,
            ):
                XHSClient._skipped_cache = None
                XHSClient._skipped_mtime = 0
                XHSClient.add_skipped("c1", content=content, note_id="n1")
                with open(path, encoding="utf-8") as file:
                    saved = json.load(file)
                self.assertEqual(saved["c1"]["content"], content)
            XHSClient._skipped_cache = None
            XHSClient._skipped_mtime = 0

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
                "target_comment_id": "root1",
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
        self.assertEqual(
            result["unreplied_level1"][0]["target_comment_id"],
            "root1",
        )

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

    def test_notification_scan_filters_local_terminal_before_online_check(self):
        client = MagicMock()
        client.get_new_comment_notifications.return_value = [{
            "note_id": "n1",
            "note_title": "文章",
            "note_xsec_token": "token",
            "new_comments": [{
                "comment_id": "sent",
                "nickname": "旧用户",
                "content": "已处理",
                "deleted": False,
            }, {
                "comment_id": "new",
                "nickname": "新用户",
                "content": "新评论",
                "deleted": False,
            }],
        }]
        client.get_skipped_ids.return_value = set()
        client.get_comments_until_ids.return_value = [{
            "id": "new",
            "sub_comment_count": "0",
            "sub_comments": [],
        }]
        result = CommentScanner(client).scan_via_notifications(
            note_id="n1",
            verbose=False,
            excluded_comment_ids={"sent"},
        )
        self.assertEqual(result["filtered_local"], 1)
        self.assertEqual(
            [item["comment_id"] for item in result["unreplied_level1"]],
            ["new"],
        )
        requested = client.get_comments_until_ids.call_args.args[1]
        self.assertNotIn("sent", requested)

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
                "target_comment": {"id": "root1"},
                "illegal_info": {"illegal_status": "NORMAL"},
            },
            "user_info": {"nickname": "用户"},
            "time": 1,
        }
        notifications.return_value = [item, dict(item)]
        result = XHSClient.get_new_comment_notifications(num=20)
        self.assertEqual(len(result[0]["new_comments"]), 1)
        self.assertEqual(result[0]["new_comments"][0]["comment_id"], "c1")
        self.assertEqual(
            result[0]["new_comments"][0]["target_comment_id"],
            "root1",
        )

    @patch("lib.xhs_client.XHSClient._merge_xsec_index")
    @patch("lib.xhs_client.XHSClient.get_notifications")
    def test_comment_notifications_persist_token_for_later_steps(
        self, notifications, merge_index
    ):
        notifications.return_value = [{
            "type": "comment",
            "item_info": {
                "id": "n1",
                "content": "文章",
                "xsec_token": "secret-token",
            },
            "comment_info": {
                "id": "c1",
                "content": "评论",
                "illegal_info": {"illegal_status": "NORMAL"},
            },
            "user_info": {"nickname": "用户"},
        }]
        result = XHSClient.get_new_comment_notifications(num=20)
        self.assertEqual(
            result[0]["note_xsec_token"], "secret-token"
        )
        merge_index.assert_called_once_with({"n1": "secret-token"})

    @patch("lib.xhs_client.time.sleep")
    @patch("lib.xhs_client.XHSClient._run_xhs")
    def test_comment_lookup_stops_when_candidate_context_is_found(
        self, run_xhs, _sleep
    ):
        run_xhs.return_value = {
            "ok": True,
            "data": {
                "comments": [{
                    "id": "root1",
                    "sub_comments": [],
                }],
                "has_more": True,
                "cursor": "next",
            },
        }
        comments, complete = XHSClient.get_comments_until_ids(
            "note",
            {"candidate"},
            with_status=True,
            target_groups=[{"candidate", "root1"}],
        )
        self.assertTrue(complete)
        self.assertEqual(comments[0]["id"], "root1")
        run_xhs.assert_called_once()

    @patch(
        "lib.xhs_client.XHSClient._find_xhs_tool_python",
        return_value="/tool/python",
    )
    @patch("lib.xhs_client.XHSClient._run_xhs")
    def test_comment_lookup_uses_one_session_for_pagination(
        self, run_xhs, _tool_python
    ):
        run_xhs.return_value = {
            "ok": True,
            "data": {
                "comments": [{"id": "root1", "sub_comments": []}],
                "search_complete": True,
                "pages_fetched": 4,
            },
        }
        comments, complete = XHSClient.get_comments_until_ids(
            "note",
            {"candidate", "root1"},
            xsec_token="secret-token",
            with_status=True,
            target_groups=[{"candidate", "root1"}],
            target_anchors=[["root1", "candidate"]],
        )
        self.assertTrue(complete)
        self.assertEqual(comments[0]["id"], "root1")
        run_xhs.assert_called_once()
        command = run_xhs.call_args.args[0]
        self.assertTrue(command[1].endswith("xhs_comments_helper.py"))
        self.assertEqual(command[2], "note")
        self.assertEqual(json.loads(command[8]), [["root1", "candidate"]])
        self.assertEqual(command[9], "1")

    @patch("lib.xhs_client.subprocess.run")
    def test_run_xhs_timeout_does_not_leak_command_secrets(self, run):
        run.side_effect = subprocess.TimeoutExpired(
            ["helper", "--xsec-token", "secret-token"], 12
        )
        with self.assertRaisesRegex(RuntimeError, "请求超时") as raised:
            XHSClient._run_xhs(
                ["helper", "--xsec-token", "secret-token"],
                timeout=12,
            )
        self.assertNotIn("secret-token", str(raised.exception))

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

    @patch(
        "lib.xhs_client.XHSClient._find_xhs_tool_python",
        return_value="/tool/python",
    )
    @patch("lib.xhs_client.XHSClient._run_xhs")
    def test_sub_comments_fall_back_to_native_after_helper_failure(
        self, run_xhs, _tool_python
    ):
        run_xhs.side_effect = [
            RuntimeError("helper failed"),
            {
                "ok": True,
                "data": {
                    "comments": [{"id": "sub"}],
                    "cursor": "",
                },
            },
        ]
        result = XHSClient.get_sub_comments(
            "note", "root", xsec_token="token", strict=True
        )
        self.assertEqual(result, [{"id": "sub"}])
        self.assertEqual(run_xhs.call_count, 2)
        self.assertEqual(run_xhs.call_args_list[1].args[0][0], "xhs")

    @patch("lib.xhs_client.subprocess.run")
    def test_run_xhs_preserves_structured_verification_error(
        self, run
    ):
        run.return_value = MagicMock(
            returncode=1,
            stdout=json.dumps({
                "ok": False,
                "error": {
                    "code": "verification_required",
                    "message": "Captcha required.",
                },
            }),
            stderr="WARNING: captcha cooling down",
        )
        with self.assertRaisesRegex(
            RuntimeError,
            "verification_required: Captcha required",
        ):
            XHSClient._run_xhs(["xhs", "sub-comments"])

    @patch(
        "lib.xhs_client.XHSClient._find_xhs_tool_python",
        return_value="/tool/python",
    )
    @patch(
        "lib.xhs_client.XHSClient._run_xhs",
        side_effect=RuntimeError("verification_required"),
    )
    def test_sub_comments_strict_mode_preserves_failure(
        self, run_xhs, _tool_python
    ):
        with self.assertRaisesRegex(RuntimeError, "verification_required"):
            XHSClient.get_sub_comments(
                "note", "root", xsec_token="token", strict=True
            )
        run_xhs.assert_called_once()

    @patch(
        "lib.xhs_client.XHSClient._find_xhs_tool_python",
        return_value="/tool/python",
    )
    @patch("lib.xhs_client.XHSClient._run_xhs")
    def test_sub_comments_does_not_fallback_on_structured_verification(
        self, run_xhs, _tool_python
    ):
        run_xhs.return_value = {
            "ok": False,
            "error": {
                "code": "verification_required",
                "message": "Captcha required: type=unknown, uuid=unknown",
            },
        }
        with self.assertRaisesRegex(RuntimeError, "verification_required"):
            XHSClient.get_sub_comments(
                "note", "root", xsec_token="token", strict=True
            )
        run_xhs.assert_called_once()

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

    def test_online_verification_defers_unresolved_in_partial_mode(self):
        client = MagicMock()
        client.get_comments_until_ids.return_value = ([], False)
        eligible, excluded = CommentScanner(
            client
        ).verify_candidates_online(
            "note",
            [{"comment_id": "candidate"}],
            max_pages=6,
            allow_partial=True,
        )
        self.assertEqual(eligible, [])
        self.assertEqual(
            excluded,
            [{"comment_id": "candidate", "reason": "online_unresolved"}],
        )
        kwargs = client.get_comments_until_ids.call_args.kwargs
        self.assertEqual(kwargs["max_pages"], 6)
        self.assertFalse(kwargs["expand_unresolved"])
        client.get_sub_comments.assert_not_called()

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

    def test_online_verification_ignores_unrelated_incomplete_thread(self):
        client = MagicMock()
        client.get_comments_until_ids.return_value = ([{
            "id": "unrelated",
            "sub_comment_count": "2",
            "sub_comments": [],
        }, {
            "id": "candidate",
            "sub_comment_count": "0",
            "sub_comments": [],
        }], True)
        eligible, excluded = CommentScanner(
            client
        ).verify_candidates_online(
            "note", [{"comment_id": "candidate"}]
        )
        self.assertEqual(eligible, [{"comment_id": "candidate"}])
        self.assertEqual(excluded, [])
        client.get_sub_comments.assert_not_called()

    def test_online_verification_defers_unrelated_failure_until_located(self):
        client = MagicMock()
        client.get_comments_until_ids.return_value = ([{
            "id": "unrelated",
            "sub_comment_count": "2",
            "sub_comments": [],
        }, {
            "id": "candidate-thread",
            "sub_comment_count": "1",
            "sub_comments": [],
        }], True)

        def get_sub_comments(
            _note_id, comment_id, _xsec_token, strict=False
        ):
            if comment_id == "unrelated":
                return []
            return [{
                "id": "candidate",
                "user_info": {"user_id": "other"},
            }]

        client.get_sub_comments.side_effect = get_sub_comments
        eligible, excluded = CommentScanner(
            client
        ).verify_candidates_online(
            "note", [{"comment_id": "candidate"}]
        )
        self.assertEqual(eligible, [{"comment_id": "candidate"}])
        self.assertEqual(excluded, [])
        self.assertEqual(client.get_sub_comments.call_count, 2)

    def test_online_verification_uses_target_hint_before_unrelated_thread(
        self
    ):
        client = MagicMock()
        client.get_comments_until_ids.return_value = ([{
            "id": "unrelated",
            "sub_comment_count": "2",
            "sub_comments": [],
        }, {
            "id": "root1",
            "sub_comment_count": "2",
            "sub_comments": [{
                "id": "target1",
                "user_info": {"user_id": "other"},
            }],
        }], True)
        client.get_sub_comments.return_value = [{
            "id": "target1",
            "user_info": {"user_id": "other"},
        }, {
            "id": "candidate",
            "user_info": {"user_id": "other"},
        }]
        eligible, excluded = CommentScanner(
            client
        ).verify_candidates_online(
            "note",
            [{
                "comment_id": "candidate",
                "target_comment_id": "target1",
            }],
        )
        self.assertEqual(
            eligible,
            [{
                "comment_id": "candidate",
                "target_comment_id": "target1",
            }],
        )
        self.assertEqual(excluded, [])
        self.assertEqual(
            client.get_comments_until_ids.call_args.kwargs[
                "target_anchors"
            ],
            [["target1", "candidate"]],
        )
        client.get_sub_comments.assert_called_once_with(
            "note", "root1", "", strict=True
        )

    def test_online_verification_stops_if_unresolved_may_be_in_failed_thread(
        self
    ):
        client = MagicMock()
        client.get_comments_until_ids.return_value = ([{
            "id": "unknown-thread",
            "sub_comment_count": "2",
            "sub_comments": [],
        }], True)
        client.get_sub_comments.return_value = []
        with self.assertRaisesRegex(
            RuntimeError, "候选评论尚未定位"
        ):
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
            "target_comment_id": "root1",
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
                "target_comment_id": "root1",
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
        self.assertEqual(payload["articles"][0]["note_id"], "n1")
        self.assertEqual(
            payload["columns"],
            ["序号", "发布时间", "评论数", "标题", "笔记ID"],
        )
        self.assertEqual(
            payload["column_fields"]["笔记ID"], "note_id"
        )
        self.assertEqual(payload["articles"][0]["title"], "标题")
        self.assertNotIn("xsec_token", payload["articles"][0])
        self.assertNotIn("secret", output.getvalue())

    @patch("main.XHSClient.list_articles")
    def test_articles_large_json_is_cached_and_paginated(
        self, list_articles
    ):
        list_articles.return_value = [{
            "id": f"n{index}",
            "title": f"标题{index}",
            "comments_count": index,
            "time": "2026-07-30 10:00",
        } for index in range(1, 46)]
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = os.path.join(temp_dir, "articles.json")
            first_args = argparse.Namespace(
                limit=45,
                json=True,
                page=1,
                page_size=20,
                cache=False,
                output=cache_path,
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                main.cmd_articles(first_args)
            first = json.loads(output.getvalue())
            self.assertEqual(first["count"], 20)
            self.assertEqual(first["total_count"], 45)
            self.assertEqual(first["articles"][0]["note_id"], "n1")
            self.assertTrue(first["pagination"]["has_more"])
            self.assertIn("--page 2", first["pagination"]["next_command"])
            self.assertEqual(first["cache_path"], cache_path)
            with open(cache_path, encoding="utf-8") as saved:
                self.assertEqual(len(json.load(saved)["articles"]), 45)

            second_args = argparse.Namespace(
                limit=10,
                json=True,
                page=2,
                page_size=20,
                cache=True,
                output=cache_path,
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                main.cmd_articles(second_args)
            second = json.loads(output.getvalue())
            self.assertEqual(second["articles"][0]["index"], 21)
            self.assertEqual(second["articles"][0]["note_id"], "n21")
            self.assertEqual(list_articles.call_count, 1)

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
        self.assertTrue(os.path.isabs(CACHE_DIR))
        self.assertTrue(CACHE_DIR.startswith(PROJECT_ROOT))
        self.assertTrue(first["scan"].endswith(
            ".cache/workflows/note-1/scan.json"
        ))

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
        self.assertEqual(run_xhs.call_args.kwargs["timeout"], 300)

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
        CommentScanner(client).scan_note(
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


if __name__ == "__main__":
    unittest.main()
