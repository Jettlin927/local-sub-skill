"""Behavior checks for local subtitle translation; no model or GPU required."""

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

import local_translation as local


class LocalTranslationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / "电影.en.srt"
        self.target = self.root / "电影.zh.srt"
        self.cache = self.root / "cache.json"
        self.checkpoint = self.root / "checkpoint.json"
        self.cues = [
            local.Cue("007", "00:00:01,234 --> 00:00:03,000", "What are you doing?\nCome here."),
            local.Cue("12", "00:00:03,123 --> 00:00:05,678", "I am going home."),
            local.Cue("19", "00:00:07,000 --> 00:00:09,111", "Good night."),
        ]
        local.write_srt(self.source, self.cues)
        self.digest = "model-digest-one"
        self.requests = []

    def fake_api(self, replies):
        iterator = iter(replies)

        def request(host, endpoint, payload=None):
            if endpoint == "/api/tags":
                return {"models": [{"name": "hy-mt2-local:latest", "digest": self.digest}]}
            self.requests.append(payload)
            response = next(iterator)
            if isinstance(response, Exception):
                raise response
            if isinstance(response, dict):
                return response
            return {"done": True, "done_reason": "stop", "response": response, "prompt_eval_count": 100, "eval_count": 20, "load_duration": 50_000_000, "prompt_eval_duration": 25_000_000, "eval_duration": 100_000_000, "total_duration": 175_000_000}

        return patch.object(local, "_request", side_effect=request)

    def run_translation(self, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return local.translate_srt(self.source, self.target, source=kwargs.pop("source", "en"), cache_path=self.cache, checkpoint_path=self.checkpoint, retries=0, **kwargs)

    def test_batch_preserves_exact_indices_and_timing(self):
        with self.fake_api(["[1] 你在做什么？\n过来。\n[2] 我要回家了。\n[3] 晚安。"]):
            stats = self.run_translation()
        translated = local.parse_srt(self.target)
        self.assertEqual([(c.index, c.timing) for c in self.cues], [(c.index, c.timing) for c in translated])
        self.assertEqual(translated[0].text, "你在做什么？\n过来。")
        self.assertEqual(local.validate_pair(self.source, self.target), [])
        self.assertEqual(stats["request_count"], 1)
        self.assertAlmostEqual(stats["ollama_eval_duration"], 0.1)
        self.assertIsNone(stats["prompt_eval_cached_count"])
        self.assertFalse(stats["prompt_cache_metrics_supported"])

    def test_missing_id_splits_batch_and_plain_single_fallback(self):
        with self.fake_api(["[1] 你在做什么？\n[3] 晚安。", "译文：你在做什么？过来。", "你在做什么？过来。", "[1] 我要回家了。\n[2] 晚安。"]):
            stats = self.run_translation()
        self.assertEqual([c.text for c in local.parse_srt(self.target)], ["你在做什么？过来。", "我要回家了。", "晚安。"])
        self.assertEqual(stats["split_batches"], 1)
        self.assertEqual(stats["fallback_requests"], 1)
        self.assertEqual(stats["request_count"], 4)

    def test_failed_run_preserves_existing_target_then_resumes(self):
        self.target.write_text("existing user subtitle", encoding="utf-8")
        with self.fake_api(["[1] 你在做什么？过来。", urllib.error.URLError("offline")]):
            with self.assertRaises(urllib.error.URLError):
                self.run_translation(batch_size=1)
        self.assertEqual(self.target.read_text(encoding="utf-8"), "existing user subtitle")
        with self.fake_api(["[1] 我要回家了。", "[1] 晚安。"]):
            stats = self.run_translation(batch_size=1)
        self.assertEqual(stats["resumed_cues"], 1)
        self.assertEqual(stats["request_count"], 2)
        with self.fake_api([]):
            again = self.run_translation(batch_size=1)
        self.assertEqual(again["request_count"], 0)
        self.assertEqual(again["resumed_cues"], 3)

    def test_result_cache_and_model_digest_invalidation(self):
        reply = "[1] 你在做什么？过来。\n[2] 我要回家了。\n[3] 晚安。"
        with self.fake_api([reply]):
            self.run_translation()
        self.checkpoint.unlink()
        with self.fake_api([]):
            stats = self.run_translation()
        self.assertEqual(stats["cache_hits"], 3)
        self.assertEqual(stats["request_count"], 0)
        self.digest = "model-digest-two"
        with self.fake_api([reply]):
            updated = self.run_translation()
        self.assertEqual(updated["cache_hits"], 0)
        self.assertEqual(updated["resumed_cues"], 0)
        self.assertEqual(updated["request_count"], 1)

    def test_split_batch_is_fully_cached_on_next_run(self):
        with self.fake_api(["[1] 你在做什么？过来。\n[3] 晚安。", "[1] 你在做什么？过来。", "[1] 我要回家了。\n[2] 晚安。"]):
            first = self.run_translation()
        self.assertEqual(first["request_count"], 3)
        self.assertEqual(first["split_batches"], 1)
        self.checkpoint = self.root / "independent-checkpoint.json"
        with self.fake_api([]):
            second = self.run_translation()
        self.assertEqual(second["resumed_cues"], 0)
        self.assertEqual(second["request_count"], 0)
        self.assertEqual(second["cache_hits"], 3)

    def test_plain_fallback_is_fully_cached_on_next_run(self):
        local.write_srt(self.source, self.cues[:1])
        with self.fake_api(["译文：你在做什么？过来。", "你在做什么？过来。"]):
            first = self.run_translation()
        self.assertEqual(first["request_count"], 2)
        self.checkpoint = self.root / "independent-checkpoint.json"
        with self.fake_api([]):
            second = self.run_translation()
        self.assertEqual(second["resumed_cues"], 0)
        self.assertEqual(second["request_count"], 0)
        self.assertEqual(second["cache_hits"], 1)

    def test_context_change_invalidates_whole_batch_cache(self):
        with self.fake_api(["[1] 你在做什么？过来。\n[2] 我要回家了。\n[3] 晚安。"]):
            self.run_translation()
        self.cues[1].text = "I am staying here."
        local.write_srt(self.source, self.cues)
        with self.fake_api(["[1] 你在做什么？过来。\n[2] 我要待在这里。\n[3] 晚安。"]):
            stats = self.run_translation()
        self.assertEqual(stats["resumed_cues"], 0)
        self.assertEqual(stats["cache_hits"], 0)
        self.assertEqual(stats["request_count"], 1)

    def test_truncation_and_untranslated_errors_never_publish(self):
        local.write_srt(self.source, self.cues[:1])
        truncated = {"done": True, "done_reason": "length", "response": "[1] 你在做什么"}
        with self.fake_api([truncated, "I cannot translate this text."]):
            with self.assertRaisesRegex(RuntimeError, "no Chinese"):
                self.run_translation()
        self.assertFalse(self.target.exists())
        self.assertFalse(self.checkpoint.exists())

    def test_partial_target_is_not_completion_evidence(self):
        local.write_srt(self.target, [local.Cue("007", self.cues[0].timing, "原有的半份字幕。")])
        with self.fake_api(["[1] 你在做什么？过来。\n[2] 我要回家了。\n[3] 晚安。"]):
            stats = self.run_translation()
        self.assertEqual(stats["request_count"], 1)
        self.assertEqual(len(local.parse_srt(self.target)), 3)

    def test_uppercase_dialogue_is_not_an_acronym(self):
        local.write_srt(self.source, [local.Cue("1", self.cues[0].timing, "GET OUT")])
        with self.fake_api(["[1] GET OUT", "GET OUT"]):
            with self.assertRaisesRegex(RuntimeError, "no Chinese"):
                self.run_translation()
        self.assertFalse(self.target.exists())

    def test_tiktok_brand_in_mixed_subtitles_keeps_name_and_punctuation(self):
        cues = [
            local.Cue("67", self.cues[0].timing, "TikTok?"),
            local.Cue("68", self.cues[1].timing, "こんにちは。"),
        ]
        local.write_srt(self.source, cues)
        original_bytes = self.source.read_bytes()
        with self.fake_api(["[1] TikTok？\n[2] 你好。", "[1] TikTok？", "TikTok？"]):
            stats = self.run_translation(source="ja")
        translated = local.parse_srt(self.target)
        self.assertEqual(self.source.read_bytes(), original_bytes)
        self.assertEqual([(c.index, c.timing) for c in translated], [(c.index, c.timing) for c in cues])
        self.assertEqual([c.text for c in translated], ["TikTok？", "你好。"])
        self.assertEqual(stats["request_count"], 1)
        self.assertEqual(stats["fallback_requests"], 0)
        self.assertEqual(local.validate_pair(self.source, self.target), [])

    def test_tiktok_full_name_accepts_case_and_trailing_punctuation(self):
        for original, translated in (
            ("TikTok", "TikTok"),
            ("TikTok?", "TikTok？"),
            ("tiktok!", "TIKTOK！"),
            ("TikTok.", "tiktok。"),
            ("TikTok,;:", "TiKToK，；："),
        ):
            with self.subTest(original=original, translated=translated):
                local._check_translations([original], [translated], "zh")

    def test_tiktok_exemption_does_not_accept_wrong_name_or_english_sentences(self):
        for original, translated in (
            ("TikTok?", "YouTube？"),
            ("TikTok?", "I cannot translate this text."),
            ("TikTok?", "TikTok again!"),
            ("Are you on TikTok?", "Are you on TikTok？"),
            ("Are you on TikTok?", "TikTok？"),
            ("Good morning.", "Good morning."),
        ):
            with self.subTest(original=original, translated=translated):
                with self.assertRaisesRegex(ValueError, "no Chinese"):
                    local._check_translations([original], [translated], "zh")
        local._check_translations(["NASA"], ["NASA"], "zh")

    def test_numeric_japanese_middle_dot_is_preserved_verbatim(self):
        self.cues[0].text = "3・2・"
        local.write_srt(self.source, self.cues)
        with self.fake_api(["3・2・", "3・2・", "[1] 我要回家了。", "[1] 晚安。"]):
            stats = self.run_translation(source="ja", batch_size=1)
        self.assertEqual(local.parse_srt(self.target)[0].text, "3・2・")
        self.assertEqual(stats["fallback_requests"], 1)
        self.assertEqual(local.validate_pair(self.source, self.target), [])

    def test_actual_japanese_text_still_requires_translation(self):
        text = "私は東京へ行く。"
        local.write_srt(self.source, [local.Cue("1", self.cues[0].timing, text)])
        with self.fake_api(["[1] " + text, text]):
            with self.assertRaisesRegex(RuntimeError, "untranslated Japanese"):
                self.run_translation(source="ja")
        self.assertFalse(self.target.exists())

    def test_symbol_only_cue_bypasses_truncated_model_response(self):
        symbols = "〜 " * 36 + "〜"
        cues = [
            local.Cue("17", "00:01:22,320 --> 00:01:22,580", symbols),
            local.Cue("18", "00:01:23,000 --> 00:01:24,000", "こんにちは。"),
        ]
        local.write_srt(self.source, cues)

        def request(host, endpoint, payload=None):
            if endpoint == "/api/tags":
                return {"models": [{"name": "hy-mt2-local:latest", "digest": self.digest}]}
            self.requests.append(payload)
            if symbols in payload["prompt"]:
                return {"done": True, "done_reason": "length", "response": ""}
            return {"done": True, "done_reason": "stop", "response": "[1] 你好。"}

        with patch.object(local, "_request", side_effect=request):
            stats = self.run_translation(source="ja")
        translated = local.parse_srt(self.target)
        self.assertEqual([(c.index, c.timing) for c in translated], [(c.index, c.timing) for c in cues])
        self.assertEqual([c.text for c in translated], [symbols, "你好。"])
        self.assertEqual(stats["passthrough_cues"], 1)
        self.assertEqual(stats["resumed_cues"], 0)
        self.assertEqual(stats["request_count"], 1)
        self.assertEqual(stats["fallback_requests"], 0)
        self.assertNotIn(symbols, self.requests[0]["prompt"])

    def test_dialogue_with_repeated_waves_only_collapses_model_input(self):
        text = "こんにちは。" + "〜 \n" * 67 + "〜"
        cue = local.Cue("1", "00:00:02,960 --> 00:00:14,560", text)
        local.write_srt(self.source, [cue])
        original_bytes = self.source.read_bytes()

        def request(host, endpoint, payload=None):
            if endpoint == "/api/tags":
                return {"models": [{"name": "hy-mt2-local:latest", "digest": self.digest}]}
            self.requests.append(payload)
            if payload["prompt"].count("〜") > 1:
                return {"done": True, "done_reason": "length", "response": ""}
            return {"done": True, "done_reason": "stop", "response": "[1] 你好。"}

        with patch.object(local, "_request", side_effect=request):
            stats = self.run_translation(source="ja")
        self.assertEqual(self.source.read_bytes(), original_bytes)
        translated = local.parse_srt(self.target)
        self.assertEqual([(c.index, c.timing) for c in translated], [(cue.index, cue.timing)])
        self.assertEqual(translated[0].text, "你好。")
        self.assertEqual(stats["request_count"], 1)
        self.assertEqual(stats["fallback_requests"], 0)
        self.assertEqual(stats["passthrough_cues"], 0)
        self.assertTrue(self.requests[0]["prompt"].endswith("[1] こんにちは。〜"))
        self.assertTrue(local._prompt([text], "ja", "zh", False).endswith("こんにちは。〜"))
        self.assertEqual(local.validate_pair(self.source, self.target), [])

    def test_single_wave_and_other_punctuation_keep_model_input(self):
        text = "こんにちは〜。！？——"
        for numbered in (True, False):
            with self.subTest(numbered=numbered):
                self.assertTrue(local._prompt([text], "ja", "zh", numbered).endswith(text))

    def test_long_small_e_emphasis_only_collapses_model_input(self):
        text = "え" + "ぇ" * 35 + "\n" + "ぇ" * 36
        cue = local.Cue("773", "01:46:40,790 --> 01:46:46,770", text)
        local.write_srt(self.source, [cue])
        original_bytes = self.source.read_bytes()

        def request(host, endpoint, payload=None):
            if endpoint == "/api/tags":
                return {"models": [{"name": "hy-mt2-local:latest", "digest": self.digest}]}
            self.requests.append(payload)
            if payload["prompt"].count("ぇ") >= 8:
                return {"done": True, "done_reason": "length", "response": ""}
            return {"done": True, "done_reason": "stop", "response": "[1] 诶。"}

        with patch.object(local, "_request", side_effect=request):
            stats = self.run_translation(source="ja")
        translated = local.parse_srt(self.target)
        self.assertEqual(self.source.read_bytes(), original_bytes)
        self.assertEqual([(c.index, c.timing) for c in translated], [(cue.index, cue.timing)])
        self.assertEqual(translated[0].text, "诶。")
        self.assertEqual(stats["request_count"], 1)
        self.assertEqual(stats["fallback_requests"], 0)
        self.assertEqual(stats["passthrough_cues"], 0)
        self.assertTrue(self.requests[0]["prompt"].endswith("[1] えぇ"))
        self.assertTrue(local._prompt([text], "ja", "zh", False).endswith("えぇ"))
        self.assertTrue(local._prompt(["え" + "ぇ \n" * 8], "ja", "zh", False).endswith("えぇ"))
        self.assertEqual(local.validate_pair(self.source, self.target), [])

    def test_short_small_e_emphasis_and_other_kana_keep_model_input(self):
        for text in ("えぇ", "ぇ" * 3, "ぇ" * 7, "え" + ("ぇ \n" * 7).rstrip(), "え" + "ぁ" * 72):
            for numbered in (True, False):
                with self.subTest(text=text, numbered=numbered):
                    expected = text.replace("\n", " ") if numbered else text
                    self.assertTrue(local._prompt([text], "ja", "zh", numbered).endswith(expected))

    def test_long_vowel_only_cues_bypass_truncated_model_response(self):
        symbols = "ーー " + "ー" * 34 + "\n" + "ー" * 34
        cues = [
            local.Cue("1251", "01:56:08,580 --> 01:56:11,280", symbols),
            local.Cue("1252", self.cues[0].timing, "ー"),
            local.Cue("1253", self.cues[1].timing, "ー ！\n？—"),
            local.Cue("1254", self.cues[2].timing, "コーヒーをお願いします。"),
        ]
        local.write_srt(self.source, cues)
        original_bytes = self.source.read_bytes()

        def request(host, endpoint, payload=None):
            if endpoint == "/api/tags":
                return {"models": [{"name": "hy-mt2-local:latest", "digest": self.digest}]}
            self.requests.append(payload)
            if "ーー" in payload["prompt"]:
                return {"done": True, "done_reason": "length", "response": ""}
            return {"done": True, "done_reason": "stop", "response": "[1] 请给我咖啡。"}

        with patch.object(local, "_request", side_effect=request):
            stats = self.run_translation(source="ja")
        translated = local.parse_srt(self.target)
        self.assertEqual(self.source.read_bytes(), original_bytes)
        self.assertEqual([(c.index, c.timing) for c in translated], [(c.index, c.timing) for c in cues])
        self.assertEqual([c.text for c in translated], [symbols, "ー", "ー ！\n？—", "请给我咖啡。"])
        self.assertEqual(stats["passthrough_cues"], 3)
        self.assertEqual(stats["request_count"], 1)
        self.assertEqual(stats["fallback_requests"], 0)
        self.assertTrue(self.requests[0]["prompt"].endswith("[1] コーヒーをお願いします。"))
        self.assertEqual(local.validate_pair(self.source, self.target), [])

    def test_normal_word_with_long_vowel_still_requires_translation(self):
        local.write_srt(self.source, [local.Cue("1", self.cues[0].timing, "コーヒー")])
        with self.fake_api(["[1] 咖啡。"]):
            stats = self.run_translation(source="ja")
        self.assertEqual(stats["passthrough_cues"], 0)
        self.assertEqual(stats["request_count"], 1)
        self.assertTrue(self.requests[0]["prompt"].endswith("[1] コーヒー"))
        self.assertEqual(local.parse_srt(self.target)[0].text, "咖啡。")

    def test_symbol_passthrough_preserves_sixteen_completed_cues(self):
        symbols = "〜 " * 36 + "〜"
        cues = [local.Cue(str(i), self.cues[0].timing, "こんにちは。") for i in range(1, 17)]
        cues.extend([
            local.Cue("17", "00:01:22,320 --> 00:01:22,580", symbols),
            local.Cue("18", "00:01:23,000 --> 00:01:24,000", "こんにちは。"),
        ])
        local.write_srt(self.source, cues)

        def request(host, endpoint, payload=None):
            if endpoint == "/api/tags":
                return {"models": [{"name": "hy-mt2-local:latest", "digest": self.digest}]}
            texts = payload["prompt"].split("\n\n", 1)[1].splitlines()
            response = "\n".join(line.split(" ", 1)[0] + " " + (symbols if symbols in line else "你好。") for line in texts)
            return {"done": True, "done_reason": "stop", "response": response}

        with patch.object(local, "_request", side_effect=request):
            self.run_translation(source="ja")
        checkpoint = json.loads(self.checkpoint.read_text(encoding="utf-8"))
        checkpoint["completed"] = {key: value for key, value in checkpoint["completed"].items() if int(key) < 16}
        checkpoint["complete"] = False
        self.checkpoint.write_text(json.dumps(checkpoint), encoding="utf-8")
        self.cache = self.root / "empty-cache.json"
        with self.fake_api(["[1] 你好。"]):
            stats = self.run_translation(source="ja")
        self.assertEqual(stats["resumed_cues"], 16)
        self.assertEqual(stats["passthrough_cues"], 1)
        self.assertEqual(stats["request_count"], 1)
        self.assertNotIn(symbols, self.requests[0]["prompt"])
        translated = local.parse_srt(self.target)
        self.assertEqual([(c.index, c.timing) for c in translated], [(c.index, c.timing) for c in cues])
        self.assertEqual(translated[16].text, symbols)
        self.assertTrue(all(c.text == "你好。" for i, c in enumerate(translated) if i != 16))
        with self.fake_api([]):
            again = self.run_translation(source="ja")
        self.assertEqual(again["resumed_cues"], 18)
        self.assertEqual(again["request_count"], 0)

    def test_validation_catches_index_corruption(self):
        translated = [local.Cue(c.index, c.timing, "中文。") for c in self.cues]
        translated[1].index = "99"
        local.write_srt(self.target, translated)
        self.assertIn("index mismatch at cue 2", local.validate_pair(self.source, self.target))

    def test_kv_cache_metrics_are_separate_from_result_cache(self):
        with self.fake_api([{"done": True, "response": "[1] 你在做什么？过来。\n[2] 我要回家了。\n[3] 晚安。", "prompt_eval_count": 100, "prompt_eval_cached_count": 25}]):
            stats = self.run_translation()
        self.assertTrue(stats["prompt_cache_metrics_supported"])
        self.assertEqual(stats["prompt_eval_cached_count"], 25)
        self.assertEqual(stats["cache_hits"], 0)

    def test_bilingual_srt_supports_zh_first_source_first_and_passthrough_dedupe(self):
        cues = [
            local.Cue("1", self.cues[0].timing, "What are you doing?"),
            local.Cue("2", self.cues[1].timing, "..."),
        ]
        local.write_srt(self.source, cues)
        bilingual_zh_first = self.root / "电影.bilingual.srt"
        with self.fake_api(["[1] 你在做什么？"]):
            stats = self.run_translation(bilingual_path=bilingual_zh_first, bilingual_order="zh-first")
        self.assertEqual(stats["bilingual_srt"], str(bilingual_zh_first))
        parsed_zh_first = local.parse_srt(bilingual_zh_first)
        self.assertEqual([c.text for c in parsed_zh_first], ["你在做什么？\nWhat are you doing?", "..."])
        self.assertEqual([(c.index, c.timing) for c in parsed_zh_first], [(c.index, c.timing) for c in cues])

        bilingual_src_first = self.root / "电影.src.bilingual.srt"
        local.write_bilingual_srt(self.source, self.target, bilingual_src_first, order="source-first")
        parsed_src_first = local.parse_srt(bilingual_src_first)
        self.assertEqual([c.text for c in parsed_src_first], ["What are you doing?\n你在做什么？", "..."])
        self.assertEqual(local.default_bilingual_path(self.target), self.root / "电影.bilingual.srt")

    def test_invalid_bilingual_order_fails_before_api_call(self):
        with self.fake_api([]):
            with self.assertRaisesRegex(ValueError, "Unsupported bilingual order"):
                self.run_translation(bilingual_path=self.root / "out.bilingual.srt", bilingual_order="invalid")
        self.assertEqual(self.requests, [])

    def test_webvtt_parses_normalizes_tags_and_translates_to_bilingual_srt(self):
        vtt_path = self.root / "yt_lecture.en.vtt"
        vtt_path.write_text(
            "WEBVTT\nKind: captions\nLanguage: en\n\n"
            "NOTE skip this comment\n\n"
            "STYLE\n::cue { color: white; }\n\n"
            "00:01.200 --> 00:03.400 align:start position:0%\n"
            "<c>Hello</c><00:00:01.500><c> &amp; welcome</c>\n"
            "Hello &amp; welcome\n\n"
            "intro-2\n"
            "01:02:03.000 --> 01:02:05.500\n"
            "<v Host>Let's&nbsp;begin.\n",
            encoding="utf-8",
        )
        parsed = local.parse_subtitles(vtt_path)
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0], local.Cue("1", "00:00:01,200 --> 00:00:03,400", "Hello & welcome"))
        self.assertEqual(parsed[1], local.Cue("2", "01:02:03,000 --> 01:02:05,500", "Let's begin."))

        derived_target = local.derive_target_srt_path(vtt_path)
        self.assertEqual(derived_target, self.root / "yt_lecture.zh.srt")
        bilingual_path = local.default_bilingual_path(derived_target)
        with self.fake_api(["[1] 你好，欢迎。\n[2] 我们开始吧。"]):
            with contextlib.redirect_stdout(io.StringIO()):
                stats = local.translate_srt(
                    vtt_path,
                    derived_target,
                    source="en",
                    cache_path=self.cache,
                    checkpoint_path=self.checkpoint,
                    retries=0,
                    bilingual_path=bilingual_path,
                )
        self.assertEqual(stats["cue_count"], 2)
        self.assertEqual(local.validate_pair(vtt_path, derived_target), [])
        bi_cues = local.parse_srt(bilingual_path)
        self.assertEqual(bi_cues[0].text, "你好，欢迎。\nHello & welcome")
        self.assertEqual(bi_cues[1].timing, "01:02:03,000 --> 01:02:05,500")

    def test_glossary_parsing_prompt_injection_and_cache_invalidation(self):
        parsed = local.parse_glossary_text("# comment\nAgent = 智能体\nRAG => 检索增强生成\nMCP\t模型上下文协议\n")
        self.assertEqual(parsed, {"Agent": "智能体", "RAG": "检索增强生成", "MCP": "模型上下文协议"})
        self.assertEqual(local.parse_glossary_text('{"Agent": "智能体"}'), {"Agent": "智能体"})
        with self.assertRaises(ValueError):
            local.parse_glossary_text("invalid line without separator")

        glossary_file = self.root / "glossary.txt"
        glossary_file.write_text("Agent = 智能体\n", encoding="utf-8")
        with self.fake_api(["[1] 智能体你在做什么？\n过来。\n[2] 我要回家了。\n[3] 晚安。"]):
            first = self.run_translation()
        self.assertEqual(first["glossary_count"], 1)
        self.assertIn("Agent->智能体", self.requests[0]["prompt"])

        # Updating glossary must invalidate prior cache/checkpoint automatically.
        glossary_file.write_text("Agent = 代理\n", encoding="utf-8")
        with self.fake_api(["[1] 代理你在做什么？\n过来。\n[2] 我要回家了。\n[3] 晚安。"]):
            second = self.run_translation()
        self.assertEqual(second["request_count"], 1)
        self.assertEqual(second["cache_hits"], 0)
        self.assertIn("代理你在做什么？", local.parse_srt(self.target)[0].text)

    def test_bilingual_ass_export_formats_styles_and_timings(self):
        cues = [
            local.Cue("1", "00:00:01,234 --> 00:00:03,000", "Hello {world}\nNext line"),
            local.Cue("2", "00:00:04,000 --> 00:00:05,500", "OK"),
        ]
        local.write_srt(self.source, cues)
        ass_path = local.default_ass_path(self.target)
        self.assertEqual(ass_path, self.root / "电影.bilingual.ass")
        with self.fake_api(["[1] 你好世界\n下一行\n[2] 好的"]):
            stats = self.run_translation(ass_path=ass_path, bilingual_order="source-first")
        self.assertEqual(stats["bilingual_ass"], str(ass_path))
        ass_text = ass_path.read_text(encoding="utf-8")
        self.assertIn("Style: PrimaryZh,", ass_text)
        self.assertIn("Style: SecondarySrc,", ass_text)
        self.assertIn(r"Dialogue: 0,0:00:01.23,0:00:03.00,PrimaryZh,,0,0,0,,{\rSecondarySrc}Hello \{world\}\NNext line\N{\rPrimaryZh}你好世界\N下一行", ass_text)
        self.assertIn(r"Dialogue: 0,0:00:04.00,0:00:05.50,PrimaryZh,,0,0,0,,{\rSecondarySrc}OK\N{\rPrimaryZh}好的", ass_text)

    def test_bilingual_markdown_notes_and_traditional_chinese_target(self):
        cues = [
            local.Cue("1", "00:00:01,234 --> 00:00:03,000", "Welcome\nto the lecture"),
            local.Cue("2", "00:00:04,000 --> 00:00:05,500", "AI"),
        ]
        ko_source = self.root / "lecture.ko.srt"
        local.write_srt(ko_source, cues)
        hant_target = local.derive_target_srt_path(ko_source, target="zh-Hant")
        self.assertEqual(hant_target, self.root / "lecture.zh-Hant.srt")
        notes_path = local.default_notes_path(hant_target)
        self.assertEqual(notes_path, self.root / "lecture.bilingual.md")

        with self.fake_api(["[1] 歡迎來到\n這堂課\n[2] AI"]):
            with contextlib.redirect_stdout(io.StringIO()):
                stats = local.translate_srt(
                    ko_source,
                    hant_target,
                    source="ko",
                    target="zh-Hant",
                    cache_path=self.cache,
                    checkpoint_path=self.checkpoint,
                    retries=0,
                    notes_path=notes_path,
                )
        self.assertEqual(stats["bilingual_md"], str(notes_path))
        md_text = notes_path.read_text(encoding="utf-8")
        self.assertIn("# lecture", md_text)
        self.assertIn("- `00:00:01` **歡迎來到 這堂課**  \n  Welcome to the lecture", md_text)
        self.assertIn("- `00:00:04` **AI**", md_text)
        self.assertIn("韩语", local._prompt(["안녕하세요"], "ko", "zh-Hant", False))
        self.assertIn("繁体中文", local._prompt(["안녕하세요"], "ko", "zh-Hant", False))

    def test_non_chinese_target_language_translation_and_validation(self):
        ja_source = self.root / "anime.ja.srt"
        local.write_srt(ja_source, [local.Cue("1", "00:00:01,000 --> 00:00:02,500", "おはようございます")])
        en_target = local.derive_target_srt_path(ja_source, target="en")
        self.assertEqual(en_target, self.root / "anime.en.srt")
        with self.fake_api(["[1] Good morning"]):
            with contextlib.redirect_stdout(io.StringIO()):
                stats = local.translate_srt(
                    ja_source,
                    en_target,
                    source="ja",
                    target="en",
                    cache_path=self.cache,
                    checkpoint_path=self.checkpoint,
                    retries=0,
                )
        self.assertEqual(stats["cue_count"], 1)
        self.assertEqual(local.parse_srt(en_target)[0].text, "Good morning")
        self.assertEqual(local.validate_pair(ja_source, en_target, check_cjk=False), [])

    def test_ass_input_parsing_and_timestamp_shifting(self):
        ass_source = self.root / "episode.ja.ass"
        ass_source.write_text(
            "\n".join(
                [
                    "[Script Info]",
                    "Title: Demo Anime",
                    "[Events]",
                    "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
                    r"Dialogue: 0,0:00:01.20,0:00:03.50,Default,,0,0,0,,{\pos(960,900)\b1}おはようございます\N元気ですか",
                    r"Dialogue: 0,0:00:04.00,0:00:05.00,Signs,,0,0,0,,{\p1}m 0 0 l 100 0 100 100{\p0}",
                    r"Dialogue: 0,0:00:06.00,0:00:08.25,Default,,0,0,0,,すごい,ですね",
                ]
            ),
            encoding="utf-8",
        )
        parsed = local.parse_subtitles(ass_source)
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0].timing, "00:00:01,200 --> 00:00:03,500")
        self.assertEqual(parsed[0].text, "おはようございます\n元気ですか")
        self.assertEqual(parsed[1].text, "すごい,ですね")

        shifted_target = local.derive_target_srt_path(ass_source, target="zh")
        self.assertEqual(shifted_target, self.root / "episode.zh.srt")
        with self.fake_api(["[1] 早上好\n你好吗\n[2] 真厉害啊"]):
            with contextlib.redirect_stdout(io.StringIO()):
                stats = local.translate_srt(
                    ass_source,
                    shifted_target,
                    source="ja",
                    target="zh",
                    cache_path=self.cache,
                    checkpoint_path=self.checkpoint,
                    retries=0,
                    shift_seconds=-1.5,
                )
        self.assertEqual(stats["shift_seconds"], -1.5)
        translated = local.parse_srt(shifted_target)
        # First cue start clamps cleanly at 00:00:00,000 while end shifts to 00:00:02,000.
        self.assertEqual(translated[0].timing, "00:00:00,000 --> 00:00:02,000")
        self.assertEqual(translated[1].timing, "00:00:04,500 --> 00:00:06,750")
        self.assertEqual(local.validate_pair(ass_source, shifted_target, shift_seconds=-1.5), [])

        # Changing shift_seconds must invalidate prior cache/checkpoint automatically.
        with self.fake_api(["[1] 早上好\n你好吗\n[2] 真厉害啊"]):
            with contextlib.redirect_stdout(io.StringIO()):
                resynced = local.translate_srt(
                    ass_source,
                    shifted_target,
                    source="ja",
                    target="zh",
                    cache_path=self.cache,
                    checkpoint_path=self.checkpoint,
                    retries=0,
                    shift_seconds=2.0,
                )
        self.assertEqual(resynced["cache_hits"], 0)
        self.assertEqual(resynced["request_count"], 1)
        self.assertEqual(local.parse_srt(shifted_target)[0].timing, "00:00:03,200 --> 00:00:05,500")


if __name__ == "__main__":
    unittest.main()
