"""笔记文字图片识别和缓存正文测试。"""

from tests.support import *

from types import SimpleNamespace

from lib.note_ocr import enrich_note_with_image_text, note_needs_image_ocr


class NoteOCRTests(unittest.TestCase):
    @patch("lib.note_ocr.subprocess.run")
    def test_text_description_skips_ocr_and_becomes_content_text(self, run):
        note = {"desc": "平台文字正文", "images": [{"url": "https://img"}]}
        enriched, changed = enrich_note_with_image_text(note)
        self.assertTrue(changed)
        self.assertEqual(enriched["content_text"], "平台文字正文")
        self.assertEqual(enriched["ocr_status"], "not_needed")
        run.assert_not_called()

    @patch("lib.note_ocr._ensure_ocr_binary", return_value="/tmp/note_ocr")
    @patch("lib.note_ocr.subprocess.run")
    def test_only_text_dominant_images_are_merged_as_cached_body(
        self, run, _binary
    ):
        run.return_value = SimpleNamespace(
            returncode=0,
            stderr="",
            stdout=json.dumps({"images": [
                {"index": 0, "text": "第一张图片中的长篇正文", "line_count": 4,
                 "char_count": 30, "text_area_ratio": 0.2,
                 "is_text_image": True, "error": None},
                {"index": 1, "text": "照片角落水印", "line_count": 1,
                 "char_count": 6, "text_area_ratio": 0.01,
                 "is_text_image": False, "error": None},
            ]}, ensure_ascii=False),
        )
        note = {
            "desc": "",
            "images": [{"url": "https://img/1"}, {"url": "https://img/2"}],
        }
        self.assertTrue(note_needs_image_ocr(note))
        enriched, changed = enrich_note_with_image_text(note)
        self.assertTrue(changed)
        self.assertEqual(enriched["ocr_status"], "completed")
        self.assertEqual(enriched["ocr_text_image_count"], 1)
        self.assertEqual(enriched["content_text"], "第一张图片中的长篇正文")
        self.assertNotIn("照片角落水印", enriched["content_text"])
        self.assertFalse(note_needs_image_ocr(enriched))
