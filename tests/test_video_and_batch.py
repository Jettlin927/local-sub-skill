"""Tests for single-video bilingual output and batch folder discovery/execution."""

from pathlib import Path
import tempfile
import unittest

import local_translation as local
import run_batch
import run_single_video as single


class VideoAndBatchTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_dry_run_includes_bilingual_srt_and_ass_paths(self):
        video = self.root / "lecture.mp4"
        video.write_bytes(b"fake-video")
        args = single.build_parser().parse_args([str(video), "--workspace", str(self.root), "--language", "en", "--bilingual", "--ass", "--dry-run"])
        res = single.run_video_pipeline(args.video, args)
        self.assertTrue(res["ok"])
        self.assertEqual(res["source_srt"], str((self.root / "lecture.en.srt").resolve()))
        self.assertEqual(res["target_srt"], str((self.root / "lecture.zh.srt").resolve()))
        self.assertEqual(res["bilingual_srt"], str((self.root / "lecture.bilingual.srt").resolve()))
        self.assertEqual(res["bilingual_ass"], str((self.root / "lecture.bilingual.ass").resolve()))

    def test_reused_existing_subtitles_materialize_bilingual_srt_and_ass(self):
        video = self.root / "clip.mp4"
        video.write_bytes(b"fake-video")
        src_srt = self.root / "clip.en.srt"
        tgt_srt = self.root / "clip.zh.srt"
        local.write_srt(src_srt, [local.Cue("1", "00:00:01,000 --> 00:00:02,000", "Hello world.")])
        local.write_srt(tgt_srt, [local.Cue("1", "00:00:01,000 --> 00:00:02,000", "你好，世界。")])

        args = single.build_parser().parse_args(
            [str(video), "--workspace", str(self.root), "--language", "en", "--bilingual", "--ass", "--bilingual-order", "source-first"]
        )
        res = single.run_video_pipeline(args.video, args)
        self.assertTrue(res["ok"])
        self.assertTrue(res["transcription"]["reused"])
        self.assertTrue(res["translation"]["reused"])
        bilingual = self.root / "clip.bilingual.srt"
        ass_file = self.root / "clip.bilingual.ass"
        self.assertTrue(bilingual.is_file())
        self.assertTrue(ass_file.is_file())
        self.assertEqual(local.parse_srt(bilingual)[0].text, "Hello world.\n你好，世界。")
        self.assertIn("Dialogue: 0,0:00:01.00,0:00:02.00", ass_file.read_text(encoding="utf-8"))

    def test_batch_discovers_sorted_videos_skips_hidden_and_supports_continue_on_error(self):
        (self.root / "ep02.mkv").write_bytes(b"v2")
        (self.root / "ep01.mp4").write_bytes(b"v1")
        (self.root / "notes.txt").write_text("ignore", encoding="utf-8")
        hidden = self.root / ".local_subtitles"
        hidden.mkdir()
        (hidden / "temp.mp4").write_bytes(b"ignore")
        sub = self.root / "season2"
        sub.mkdir()
        (sub / "ep03.MOV").write_bytes(b"v3")

        flat = run_batch.discover_videos(self.root, recursive=False)
        self.assertEqual([p.name for p in flat], ["ep01.mp4", "ep02.mkv"])

        deep = run_batch.discover_videos(self.root, recursive=True)
        self.assertEqual([p.name for p in deep], ["ep01.mp4", "ep02.mkv", "ep03.MOV"])

        args = run_batch.build_batch_parser().parse_args(
            [str(self.root), "--workspace", str(self.root), "--recursive", "--bilingual", "--dry-run"]
        )
        summary = run_batch.run_batch(args)
        self.assertTrue(summary["ok"])
        self.assertEqual(summary["total_discovered"], 3)
        self.assertEqual(summary["succeeded"], 3)
        self.assertEqual(summary["failed"], 0)
        self.assertEqual(summary["items"][0]["bilingual_srt"], str((self.root / "ep01.bilingual.srt").resolve()))

    def test_batch_discovers_standalone_srt_and_vtt_and_skips_generated_outputs(self):
        (self.root / "ep01.mp4").write_bytes(b"v1")
        (self.root / "ep01.en.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nPaired\n", encoding="utf-8")
        (self.root / "yt_talk.en.vtt").write_text("WEBVTT\n\n00:01.000 --> 00:02.000\nHello\n", encoding="utf-8")
        (self.root / "standalone.ja.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nこんにちは\n", encoding="utf-8")
        (self.root / "already.zh.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\n已有\n", encoding="utf-8")
        (self.root / "already.bilingual.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\n已有\n", encoding="utf-8")

        videos = run_batch.discover_videos(self.root)
        subs = run_batch.discover_standalone_subtitles(self.root, video_paths=videos)
        self.assertEqual([p.name for p in subs], ["standalone.ja.srt", "yt_talk.en.vtt"])

        args = run_batch.build_batch_parser().parse_args(
            [str(self.root), "--workspace", str(self.root), "--subtitles-only", "--bilingual", "--ass", "--dry-run"]
        )
        summary = run_batch.run_batch(args)
        self.assertTrue(summary["ok"])
        self.assertEqual(summary["videos_discovered"], 0)
        self.assertEqual(summary["subtitles_discovered"], 3)
        self.assertEqual(summary["succeeded"], 3)
        vtt_item = next(item for item in summary["items"] if item["source_srt"].endswith("yt_talk.en.vtt"))
        self.assertEqual(vtt_item["target_srt"], str((self.root / "yt_talk.zh.srt").resolve()))
        self.assertEqual(vtt_item["bilingual_srt"], str((self.root / "yt_talk.bilingual.srt").resolve()))
        self.assertEqual(vtt_item["bilingual_ass"], str((self.root / "yt_talk.bilingual.ass").resolve()))


if __name__ == "__main__":
    unittest.main()
