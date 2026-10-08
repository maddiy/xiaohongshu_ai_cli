"""笔记发布参数、预览和平台调用测试。"""

from tests.support import *

from lib import poster


class PosterTests(unittest.TestCase):
    def test_validate_note_rejects_missing_content_and_image(self):
        with self.assertRaisesRegex(ValueError, "标题不能为空"):
            poster.validate_note("", "正文", ["image.jpg"])
        with self.assertRaisesRegex(ValueError, "正文不能为空"):
            poster.validate_note("标题", "", ["image.jpg"])
        with self.assertRaisesRegex(ValueError, "至少需要一张图片"):
            poster.validate_note("标题", "正文", [])

    def test_build_command_normalizes_topics_and_private_flag(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image = os.path.join(temp_dir, "image.jpg")
            with open(image, "wb") as file:
                file.write(b"image")
            command = poster.build_command(
                "标题", "正文", [image], ["#话题", " 测试 "], private=True
            )
        self.assertIn("正文\n\n#话题 #测试", command)
        self.assertIn("--private", command)
        self.assertEqual(command[-2], image)

    def test_multi_images_have_individual_options_and_json_output(self):
        with patch("lib.poster.os.path.isfile", return_value=True):
            command = poster.build_command("标题", "正文", ["a.png", "b.png"])
        self.assertEqual(command.count("--images"), 2)
        self.assertIn("--json", command)
        self.assertEqual(command[-4:], ["--images", "a.png", "--images", "b.png"])

    def test_length_limit_includes_topics(self):
        with patch("lib.poster.os.path.isfile", return_value=True):
            with self.assertRaisesRegex(ValueError, "1000"):
                poster.build_command("标题", "文" * 998, ["a.png"], ["话题"])
            poster.build_command("标题", "文" * 1000, ["a.png"])

    @patch("lib.poster.subprocess.run")
    def test_publish_does_not_treat_false_ok_as_success(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, stdout='{"ok":false}', stderr="")
        with patch("lib.poster.os.path.isfile", return_value=True):
            self.assertFalse(poster.publish("标题", "正文", ["a.png"]))

    @patch("lib.poster.subprocess.run")
    def test_delete_requires_valid_target_and_confirmation(self, run):
        self.assertFalse(poster.delete_note("bad", confirmed=True)["ok"])
        target = "a" * 24
        self.assertEqual(poster.delete_note(target)["error_type"], "confirmation_required")
        self.assertTrue(poster.delete_note(target, dry_run=True)["ok"])
        run.assert_not_called()

    @patch("lib.poster.subprocess.run")
    def test_delete_reports_unsupported_and_preserves_history(self, run):
        run.return_value = subprocess.CompletedProcess([], 1, stdout='{"ok":false,"error":{"code":"unsupported_operation"}}', stderr="")
        result = poster.delete_note("a" * 24, confirmed=True)
        self.assertEqual(result["error_type"], "unsupported_operation")
        self.assertFalse(result["automatic_retry"])
        self.assertTrue(result["local_history_preserved"])
        self.assertEqual(run.call_args.args[0], ["xhs", "delete", "a" * 24, "--yes", "--json"])

    @patch("lib.poster.subprocess.run")
    def test_delete_success_and_unknown_timeout(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, stdout='{"ok":true}', stderr="")
        self.assertTrue(poster.delete_note("a" * 24, confirmed=True)["deleted"])
        run.side_effect = subprocess.TimeoutExpired([], 60)
        self.assertEqual(poster.delete_note("a" * 24, confirmed=True)["error_type"], "uncertain_delete_state")

    @patch("lib.poster.subprocess.run")
    def test_malformed_platform_response_does_not_claim_success(self, run):
        for output in ("[]", "null", "not JSON"):
            run.return_value = subprocess.CompletedProcess([], 0, stdout=output, stderr="")
            self.assertEqual(poster.delete_note("a" * 24, confirmed=True)["error_type"], "uncertain_delete_state")
            with patch("lib.poster.os.path.isfile", return_value=True):
                self.assertFalse(poster.publish("标题", "正文", ["a.png"]))

    @patch("lib.poster.subprocess.run")
    def test_publish_dry_run_never_writes_platform(self, run):
        with tempfile.TemporaryDirectory() as temp_dir:
            image = os.path.join(temp_dir, "image.jpg")
            with open(image, "wb") as file:
                file.write(b"image")
            self.assertTrue(poster.publish(
                "标题", "正文", [image], dry_run=True
            ))
        run.assert_not_called()

    @patch("lib.poster.subprocess.run")
    def test_publish_success_and_timeout_are_conclusive(self, run):
        with tempfile.TemporaryDirectory() as temp_dir:
            image = os.path.join(temp_dir, "image.jpg")
            with open(image, "wb") as file:
                file.write(b"image")
            run.return_value = subprocess.CompletedProcess(
                [], 0, stdout='{"ok":true}', stderr=""
            )
            self.assertTrue(poster.publish("标题", "正文", [image]))
            run.side_effect = subprocess.TimeoutExpired(["xhs", "post"], 60)
            self.assertFalse(poster.publish("标题", "正文", [image]))
