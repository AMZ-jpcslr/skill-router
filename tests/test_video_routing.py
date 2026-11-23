"""Offline routing regression tests; synthetic metadata, no user files required."""
import runpy
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "plugins/skill-router/skills/skill-router-settings"
INDEX = runpy.run_path(str(CORE / "local_index.py"))
ENGINE = INDEX["OPEN"]
ROWS = [
    {"name": "synthetic-motion", "path": "synthetic-motion/SKILL.md",
     "description": "Best practices for video creation in React. Render title animations and motion graphics."},
    {"name": "synthetic-video-reader", "path": "synthetic-video-reader/SKILL.md",
     "description": "Extract key frames from MP4 videos, run OCR and generate timestamped reports."},
]

class VideoRouting(unittest.TestCase):
    def test_selection(self):
        cases = [
            ("2秒の紹介動画を作って。文字をフェードインさせてMP4で書き出してください。", {"video-create"}),
            ("タイトルがゆっくり出る映像を1本用意してください。", {"video-create"}),
            ("タイトルカードを滑らかに表示する2秒の無音MP4に仕上げてください。", {"video-create"}),
            ("Make a two-second title video and export it as MP4.", {"video-create"}),
            ("添付MP4から数秒おきに静止画を切り出し、画面の文字をOCRで抽出してください。", {"video-frames-ocr"}),
            ("収録済みの動画の中から、フレームに映った文字を抜き出して一覧にしてください。", {"video-frames-ocr"}),
            ("Extract key frames and run OCR on visible text from this existing video.", {"video-frames-ocr"}),
            ("手元の動画からフレームを切り出してください。", {"video-extract-frames"}),
            ("2秒の紹介動画をこれから作る想定で、文字をフェードインさせてMP4にする手順を2行で説明してください。今回は手順の説明だけで、ファイルの作成・変更や書き出しはしないでください。", set()),
            ("文字がふわっと現れる動画の作り方を教えて。実際の書き出しは不要です。", set()),
            ("動画からフレームを抽出する方法だけ教えてください。", set()),
            ("Explain how to create a fade-in title video. Do not render a file.", set()),
            ("動画のことは置いておいて、今日は挨拶だけお願いします。", set()),
            ("動画をMP4で書き出す手順だけ教えて。別途、添付動画から画面の文字をOCRで抜き出してください。", {"video-frames-ocr"}),
            ("2秒の紹介動画を作って。別途、添付動画から画面の文字をOCRで抜き出してください。", {"video-create", "video-frames-ocr"}),
        ]
        for prompt, expect in cases:
            with self.subTest(prompt=prompt):
                why = {}
                hits = INDEX["rank"](prompt, ROWS, [], why)
                self.assertEqual({why[row["path"]]["operation"] for _, row in hits}, expect)
                for _, row in hits:
                    operation = why[row["path"]]["operation"]
                    self.assertEqual(row["name"], "synthetic-motion" if operation == "video-create" else "synthetic-video-reader")

    def test_no_weak_fallback(self):
        for prompt in ("動画を作ってください。", "MP4から文字をOCRで抽出してください。"):
            with self.subTest(prompt=prompt):
                wrong = ROWS[1:] if "作って" in prompt else ROWS[:1]
                self.assertEqual(INDEX["rank"](prompt, wrong, []), [])

    def test_audio_role_without_candidate(self):
        prompt = "添付のMP4から音声を抜き出して保存してください。"
        self.assertEqual(ENGINE["video_operation"](ENGINE["normalize"](prompt)), "video-extract-audio")
        self.assertEqual(INDEX["rank"](prompt, ROWS, []), [])

if __name__ == "__main__":
    unittest.main()
