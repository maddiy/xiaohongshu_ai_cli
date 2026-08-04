"""命令清单和AI协议测试。"""

from tests.support import *
from lib.state_db import DB_SCHEMA_VERSION


class ProtocolTests(unittest.TestCase):
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
        self.assertEqual(len(COMMAND_NAMES), 16)

    def test_ai_help_reports_authoritative_program_facts(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            main.cmd_ai_help(argparse.Namespace())
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["command_count"], 16)
        self.assertEqual(payload["commands"], list(COMMAND_NAMES))
        self.assertEqual(payload["app_name"], "小红书AI智能运营系统")
        self.assertEqual(payload["system_name"], "小红书AI智能运营系统")
        self.assertNotIn("cli_name", payload)
        self.assertEqual(payload["app_version"], APP_VERSION)
        self.assertEqual(payload["schema_version"], AI_SCHEMA_VERSION)
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
        self.assertGreater(
            payload["defaults"]["persistent_helper_response_timeout_seconds"],
            0,
        )
        self.assertGreater(
            payload["defaults"]["comment_helper_max_seconds"],
            payload["defaults"]["comment_helper_request_timeout_seconds"],
        )
        self.assertEqual(
            payload["defaults"]["state_db_schema_version"],
            DB_SCHEMA_VERSION,
        )
        self.assertIn(
            f"DB schema {DB_SCHEMA_VERSION}",
            payload["storage"]["database_schema"],
        )
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
            + len(payload["project_inventory"]["test_support"])
            + len(payload["project_inventory"]["documentation"])
            + len(payload["project_inventory"]["tooling"])
            + len(payload["project_inventory"]["web_assets"]),
        )
        self.assertIn("lib/state_io.py", payload["source_inventory"]["files"])
        self.assertIn(
            "tests/test_protocol.py",
            payload["project_inventory"]["tests"],
        )
        self.assertEqual(
            payload["project_inventory"]["web_assets"],
            ["web/index.html", "web/app.js", "web/styles.css"],
        )
        self.assertNotIn(
            "tests/support.py",
            payload["project_inventory"]["tests"],
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
            "reply_map_review_required",
            payload["output_contract"],
        )
        review_contract = payload["reply_decision"]["pre_reply_review"]
        self.assertEqual(
            review_contract["dimensions"],
            ["逻辑分析", "事实核查", "吹牛判定"],
        )
        self.assertIn(
            "reviews",
            review_contract["draft_output"],
        )
        self.assertIn(
            "http(s)",
            payload["output_contract"]["fact_check_sources"],
        )
        self.assertIn(
            "batch_id",
            payload["output_contract"]["draft_confirmation"],
        )
        self.assertIn(
            "不要求AI直接编辑reply_map.json",
            payload["output_contract"]["structured_mapping"],
        )
        self.assertIn(
            "force_refresh=true",
            payload["online_verification_stages"]["prepare"],
        )
        self.assertIn(
            "不访问平台",
            payload["online_verification_stages"]["map"],
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
            "AI编辑的JSON快照优先",
            payload["storage"]["read_authority"]["reply_map.json"],
        )
        self.assertIn(
            "SQLite优先",
            payload["storage"]["read_authority"]["drafts.json"],
        )
        self.assertIn(
            "对象键顺序无关",
            payload["storage"]["canonical_hash"],
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
            "binding_reused=true",
            payload["common_misunderstandings"]["draft_regeneration"],
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
        self.assertTrue(payload["safety"]["watch_is_manual_foreground_only"])
        self.assertTrue(payload["safety"]["watch_first_poll_is_baseline_only"])
        self.assertEqual(payload["safety"]["web_bind"], "127.0.0.1")
        self.assertFalse(payload["safety"]["web_remote_access"])
        self.assertIn("--confirmed", payload["workflows"]["watch"][3])
        self.assertIn("127.0.0.1", payload["workflows"]["web"][1])

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
            "test_protocol.ProtocolTests."
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
        self.assertEqual(payload["app_version"], APP_VERSION)
        self.assertEqual(payload["schema_version"], AI_SCHEMA_VERSION)
        self.assertIn("--batch-id", payload["reply_workflow"]["send"])
        self.assertIn("--preview-hash", payload["reply_workflow"]["send"])
        self.assertIn("--action map", payload["reply_workflow"]["map"])
        self.assertIn("--candidate-index", payload["reply_workflow"]["map"])
        self.assertIn("--action status", payload["reply_workflow"]["resume"])
        self.assertIn("--retry-authorized", payload["reply_workflow"]["retry"])
        self.assertIn("workflow_busy", payload["error_actions"])
        self.assertIn("uncertain_send_state", payload["error_actions"])
        self.assertIn("batch_id", payload["state"]["batch_fields"])
        self.assertIn("对象键顺序无关", payload["state"]["canonical_hash"])
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

    def test_ai_help_inventory_includes_short_guides_and_state_module(self):
        args = argparse.Namespace(
            summary=False, command_name=None, tests=False,
        )
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            from lib.cli_protocol import cmd_ai_help
            cmd_ai_help(args)
        payload = json.loads(output.getvalue())
        self.assertIn(
            "QUICKSTART.md", payload["project_inventory"]["documentation"]
        )
        self.assertIn(
            "TROUBLESHOOTING.md",
            payload["project_inventory"]["documentation"],
        )
        self.assertIn("lib/cli_ai_state.py", payload["source_inventory"]["files"])
