"""AI回复映射、审查结构和错误分类测试。"""

from tests.support import *


class AIReplyMappingTests(unittest.TestCase):
    def test_reply_map_validation_rejects_unsafe_send_entries(self):
        errors = cli_ai._validate_reply_map({
            "c1": {
                "reply": "", "action": "send", "review": _valid_review(),
            },
            "c2": {
                "reply": "回复", "action": "sent", "review": _valid_review(),
            },
        }, ["c1", "c2"])
        self.assertEqual(len(errors), 2)
        self.assertIn("reply 不能为空", errors[0])
        self.assertIn("action 必须", errors[1])

    def test_reply_map_allows_empty_skip_and_archive(self):
        errors = cli_ai._validate_reply_map({
            "c1": {
                "reply": "", "action": "skip", "review": _valid_review(),
            },
            "c2": {
                "reply": "", "action": "archive", "review": _valid_review(),
            },
        }, ["c1", "c2"])
        self.assertEqual(errors, [])

    def test_reply_map_requires_review_for_every_candidate(self):
        errors = cli_ai._validate_reply_map({
            "c1": {"reply": "回复", "action": "send"},
        }, ["c1", "c2"])
        self.assertTrue(any("review 必须是对象" in item for item in errors))
        self.assertTrue(any("缺少映射" in item for item in errors))

    def test_fact_check_verdict_requires_traceable_source(self):
        review = _valid_review(fact_verdict="contradicted")
        errors = cli_ai._validate_reply_map({
            "c1": {
                "reply": "回复", "action": "send", "review": review,
            },
        }, ["c1"])
        self.assertTrue(any("至少需要一个可核对来源" in item for item in errors))

    def test_fact_check_accepts_traceable_source(self):
        review = _valid_review(
            fact_verdict="supported",
            sources=[{
                "title": "权威来源",
                "url": "https://example.com/source",
            }],
        )
        errors = cli_ai._validate_reply_map({
            "c1": {
                "reply": "回复", "action": "send", "review": review,
            },
        }, ["c1"])
        self.assertEqual(errors, [])

    def test_review_change_invalidates_preview_hash(self):
        first_review = _valid_review()
        second_review = json.loads(json.dumps(first_review))
        second_review["logic"]["reason"] = "新的逻辑分析"
        base = {
            "comment_id": "c1",
            "nickname": "用户",
            "content": "评论",
            "reply": "回复",
            "action": "send",
        }
        self.assertNotEqual(
            cli_ai.preview_hash([{**base, "review": first_review}]),
            cli_ai.preview_hash([{**base, "review": second_review}]),
        )

    def test_preview_hash_ignores_nested_object_key_order(self):
        first_review = _valid_review()
        second_review = {
            "boast_check": dict(reversed(list(
                first_review["boast_check"].items()
            ))),
            "fact_check": dict(reversed(list(
                first_review["fact_check"].items()
            ))),
            "logic": dict(reversed(list(first_review["logic"].items()))),
        }
        base = {
            "comment_id": "c1",
            "nickname": "用户",
            "content": "评论",
            "reply": "回复",
            "action": "send",
        }
        self.assertEqual(
            cli_ai.preview_hash([{**base, "review": first_review}]),
            cli_ai.preview_hash([{**base, "review": second_review}]),
        )

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
                file.write(
                    '{"c1":{"reply":"他说"在美华人也存钱"",'
                    '"action":"send"}}'
                )
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
        self.assertEqual(payload["error_location"]["line"], 1)
        self.assertGreater(payload["error_location"]["column"], 1)
        self.assertIn("中文引号", payload["quote_policy"]["text_values"])
        self.assertIn("必须保留", payload["quote_policy"]["json_structure"])
        self.assertIn("禁止全文件", payload["quote_policy"]["forbidden"])
        audited = cli_ai._audit_result(payload)
        self.assertEqual(
            audited["error_location"], payload["error_location"]
        )
        self.assertIn("quote_policy", audited)
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
    def test_ai_draft_repairs_unescaped_quotes_in_ai_text_only(
        self, scanner_class
    ):
        candidate = {
            "comment_id": "c1",
            "nickname": "用户",
            "content": "评论原文保持不变",
        }
        scanner_class.return_value.verify_candidates_online.return_value = (
            [candidate.copy()], []
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
                file.write(
                    '{\n'
                    '  "c1": {\n'
                    '    "reply": "他说"在美华人也存钱"。",\n'
                    '    "action": "send",\n'
                    '    "review": {\n'
                    '      "logic": {\n'
                    '        "verdict": "partly_sound",\n'
                    '        "reason": "用"单一例子"推导整体，论据不足"\n'
                    '      },\n'
                    '      "fact_check": {\n'
                    '        "verdict": "unverifiable",\n'
                    '        "reason": "缺少独立数据",\n'
                    '        "sources": []\n'
                    '      },\n'
                    '      "boast_check": {\n'
                    '        "verdict": "none",\n'
                    '        "reason": "没有自我夸大"\n'
                    '      }\n'
                    '    }\n'
                    '  }\n'
                    '}\n'
                )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                cli_ai._draft(
                    argparse.Namespace(note_id="n1", replies=None), paths
                )
            payload = json.loads(output.getvalue())
            with open(paths["reply_map"], encoding="utf-8") as file:
                repaired = json.load(file)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["reply_map_repaired"])
        self.assertEqual(payload["quote_replacements"], 4)
        self.assertEqual(
            repaired["c1"]["reply"], "他说“在美华人也存钱”。"
        )
        self.assertIn(
            "“单一例子”", repaired["c1"]["review"]["logic"]["reason"]
        )
        self.assertEqual(candidate["content"], "评论原文保持不变")

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
                    "c1": {
                        "reply": "回复一", "action": "send",
                        "review": _valid_review(),
                    },
                    "c2": {
                        "reply": "回复二", "action": "send",
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
