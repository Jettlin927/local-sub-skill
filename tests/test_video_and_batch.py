"""Tests for single-video bilingual output and batch folder discovery/execution."""

from pathlib import Path
import json
import subprocess
import tempfile
import unittest
from unittest import mock

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
        args = single.build_parser().parse_args(
            [str(video), "--workspace", str(self.root), "--language", "ko", "--target", "zh-Hant", "--bilingual", "--ass", "--notes", "--extract-subs", "--mux", "--dry-run"]
        )
        res = single.run_video_pipeline(args.video, args)
        self.assertTrue(res["ok"])
        self.assertEqual(res["source_srt"], str((self.root / "lecture.ko.srt").resolve()))
        self.assertEqual(res["target_srt"], str((self.root / "lecture.zh-Hant.srt").resolve()))
        self.assertEqual(res["bilingual_srt"], str((self.root / "lecture.bilingual.srt").resolve()))
        self.assertEqual(res["bilingual_ass"], str((self.root / "lecture.bilingual.ass").resolve()))
        self.assertEqual(res["bilingual_md"], str((self.root / "lecture.bilingual.md").resolve()))
        self.assertEqual(res["muxed_video"], str((self.root / "lecture.subbed.mkv").resolve()))
        self.assertTrue(res["extract_subs"])

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
        (self.root / "anime_ep.ja.ass").write_text("[Events]\nDialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,こんにちは\n", encoding="utf-8")
        (self.root / "standalone.ja.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nこんにちは\n", encoding="utf-8")
        (self.root / "already.zh.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\n已有\n", encoding="utf-8")
        (self.root / "already.bilingual.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\n已有\n", encoding="utf-8")
        (self.root / "already.bilingual.ass").write_text("[Events]\nDialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,已有\n", encoding="utf-8")

        videos = run_batch.discover_videos(self.root)
        subs = run_batch.discover_standalone_subtitles(self.root, video_paths=videos)
        self.assertEqual([p.name for p in subs], ["anime_ep.ja.ass", "standalone.ja.srt", "yt_talk.en.vtt"])

        args = run_batch.build_batch_parser().parse_args(
            [str(self.root), "--workspace", str(self.root), "--subtitles-only", "--shift", "1.25", "--bilingual", "--ass", "--dry-run"]
        )
        summary = run_batch.run_batch(args)
        self.assertTrue(summary["ok"])
        self.assertEqual(summary["videos_discovered"], 0)
        self.assertEqual(summary["subtitles_discovered"], 4)
        self.assertEqual(summary["succeeded"], 4)
        ass_item = next(item for item in summary["items"] if item["source_srt"].endswith("anime_ep.ja.ass"))
        self.assertEqual(ass_item["shift_seconds"], 1.25)
        self.assertEqual(ass_item["target_srt"], str((self.root / "anime_ep.zh.srt").resolve()))
        vtt_item = next(item for item in summary["items"] if item["source_srt"].endswith("yt_talk.en.vtt"))
        self.assertEqual(vtt_item["target_srt"], str((self.root / "yt_talk.zh.srt").resolve()))
        self.assertEqual(vtt_item["bilingual_srt"], str((self.root / "yt_talk.bilingual.srt").resolve()))
        self.assertEqual(vtt_item["bilingual_ass"], str((self.root / "yt_talk.bilingual.ass").resolve()))

    def test_extract_embedded_subtitle_and_mux_subtitles_with_mock_ffmpeg(self):
        video = self.root / "anime.mkv"
        video.write_bytes(b"fake-mkv")
        src_srt = self.root / "anime.ja.srt"
        tgt_srt = self.root / "anime.zh.srt"
        local.write_srt(tgt_srt, [local.Cue("1", "00:00:01,000 --> 00:00:02,000", "你好。")])

        ffprobe_json = json.dumps({
            "streams": [
                {"index": 2, "codec_name": "hdmv_pgs_subtitle", "tags": {"language": "jpn"}},
                {"index": 3, "codec_name": "ass", "tags": {"language": "eng"}},
                {"index": 4, "codec_name": "subrip", "tags": {"language": "jpn"}},
            ]
        })

        def fake_run(cmd, **_kwargs):
            exe = Path(cmd[0]).name.lower()
            if "ffprobe" in exe:
                return subprocess.CompletedProcess(cmd, 0, stdout=ffprobe_json, stderr="")
            if "-c:s" in cmd and "srt" in cmd:
                out_file = Path(cmd[-1])
                local.write_srt(out_file, [local.Cue("1", "00:00:01,000 --> 00:00:02,000", "こんにちは。")])
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            out_mkv = Path(cmd[-1])
            out_mkv.write_bytes(b"muxed-mkv")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with mock.patch("run_single_video.resolve_ffmpeg_bin", side_effect=lambda name="ffmpeg": f"/usr/bin/{name}"):
            with mock.patch("run_single_video.subprocess.run", side_effect=fake_run) as run_mock:
                extracted = single.extract_embedded_subtitle(video, src_srt, language="ja")
                self.assertIsNotNone(extracted)
                self.assertEqual(extracted["stream_index"], 4)
                self.assertEqual(extracted["backend"], "ffmpeg-extract")
                self.assertEqual(local.parse_srt(src_srt)[0].text, "こんにちは。")

                args = single.build_parser().parse_args(
                    [str(video), "--workspace", str(self.root), "--language", "ja", "--bilingual", "--notes", "--mux"]
                )
                res = single.run_video_pipeline(args.video, args)
                self.assertTrue(res["ok"])
                self.assertTrue(Path(res["bilingual_md"]).is_file())
                self.assertEqual(res["muxed_video"], str((self.root / "anime.subbed.mkv").resolve()))
                self.assertTrue(Path(res["muxed_video"]).is_file())
                mux_cmd = run_mock.call_args_list[-1].args[0]
                self.assertIn("-c", mux_cmd)
                self.assertIn("copy", mux_cmd)

    def test_audio_file_discovery_and_url_yt_dlp_extract_subs(self):
        audio = self.root / "podcast.mp3"
        audio.write_bytes(b"fake-mp3")
        discovered = run_batch.discover_videos(self.root)
        self.assertEqual([p.name for p in discovered], ["podcast.mp3"])

        audio_args = single.build_parser().parse_args(
            [str(audio), "--workspace", str(self.root), "--language", "en", "--bilingual", "--notes", "--extract-subs", "--mux", "--dry-run"]
        )
        audio_res = single.run_video_pipeline(audio_args.video, audio_args)
        self.assertTrue(audio_res["ok"])
        self.assertEqual(audio_res["media_type"], "audio")
        self.assertIsNone(audio_res["muxed_video"])
        self.assertFalse(audio_res["extract_subs"])

        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        url_dry = single.build_parser().parse_args([url, "--workspace", str(self.root), "--bilingual", "--dry-run"])
        url_dry_res = single.run_video_pipeline(url_dry.video, url_dry)
        self.assertTrue(url_dry_res["ok"])
        self.assertEqual(url_dry_res["url"], url)
        self.assertTrue(url_dry_res["dry_run"])

        def fake_ytdlp(cmd, **_kwargs):
            out_dir = Path(cmd[cmd.index("-P") + 1])
            (out_dir / "Demo_Talk-dQw4w9WgXcQ.en.vtt").write_text(
                "WEBVTT\n\n00:01.000 --> 00:02.000\n<c>Hello URL.</c>\n",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        fake_tags = mock.MagicMock()
        fake_tags.__enter__.return_value.read.return_value = json.dumps({"models": [{"name": "hy-mt2-local", "digest": "sha256:test"}]}).encode("utf-8")
        fake_gen = mock.MagicMock()
        fake_gen.__enter__.return_value.read.return_value = json.dumps({"done": True, "done_reason": "stop", "response": "[1] 你好链接。"}).encode("utf-8")

        url_args = single.build_parser().parse_args(
            [url, "--workspace", str(self.root), "--language", "en", "--extract-subs", "--bilingual", "--notes"]
        )
        with mock.patch("run_single_video.resolve_yt_dlp_bin", return_value="/usr/bin/yt-dlp"):
            with mock.patch("run_single_video.subprocess.run", side_effect=fake_ytdlp):
                with mock.patch("local_translation.urllib.request.urlopen", side_effect=[fake_tags, fake_gen]):
                    url_res = single.run_video_pipeline(url_args.video, url_args)

        self.assertTrue(url_res["ok"])
        self.assertEqual(url_res["url"], url)
        self.assertEqual(url_res["transcription"]["backend"], "yt-dlp-subs")
        self.assertTrue(Path(url_res["target_srt"]).is_file())
        self.assertTrue(Path(url_res["bilingual_srt"]).is_file())
        self.assertTrue(Path(url_res["bilingual_md"]).is_file())

    def test_burn_subtitles_escapes_filter_path_and_produces_mp4(self):
        escaped = single._escape_ffmpeg_filter_path(Path("D:/Videos/[2026] O'Connor/clip.bilingual.ass"))
        self.assertIn(r"\:", escaped)
        self.assertIn(r"\'", escaped)
        self.assertIn(r"\[2026\]", escaped)
        self.assertNotIn("\\/", escaped)

        video = self.root / "social.mp4"
        video.write_bytes(b"fake-mp4")
        local.write_srt(self.root / "social.ja.srt", [local.Cue("1", "00:00:01,000 --> 00:00:02,000", "こんにちは")])
        local.write_srt(self.root / "social.en.srt", [local.Cue("1", "00:00:01,000 --> 00:00:02,000", "Hello")])

        def fake_burn(cmd, **_kwargs):
            out_mp4 = Path(cmd[-1])
            out_mp4.write_bytes(b"burned-mp4")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        dry_args = single.build_parser().parse_args(
            [str(video), "--workspace", str(self.root), "--language", "ja", "--target", "en", "--bilingual", "--ass", "--burn", "--dry-run"]
        )
        dry_res = single.run_video_pipeline(dry_args.video, dry_args)
        self.assertEqual(dry_res["burned_video"], str((self.root / "social.burned.mp4").resolve()))

        run_args = single.build_parser().parse_args(
            [str(video), "--workspace", str(self.root), "--language", "ja", "--target", "en", "--bilingual", "--ass", "--burn"]
        )
        with mock.patch("run_single_video.resolve_ffmpeg_bin", return_value="/usr/bin/ffmpeg"):
            with mock.patch("run_single_video.subprocess.run", side_effect=fake_burn) as burn_mock:
                res = single.run_video_pipeline(run_args.video, run_args)
        self.assertTrue(res["ok"])
        self.assertEqual(res["burned_video"], str((self.root / "social.burned.mp4").resolve()))
        self.assertTrue(Path(res["burned_video"]).is_file())
        burn_cmd = burn_mock.call_args.args[0]
        self.assertIn("-vf", burn_cmd)
        self.assertIn("ass='", burn_cmd[burn_cmd.index("-vf") + 1])


if __name__ == "__main__":
    unittest.main()
