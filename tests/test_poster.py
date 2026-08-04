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
