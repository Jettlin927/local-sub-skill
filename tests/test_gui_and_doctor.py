"""Tests for doctor diagnostics, Drag-and-Drop Web GUI, ASR fallback, and Windows bundle zip."""

from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest import mock
import urllib.error
import urllib.request
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packaging"))

import doctor
import gui
import local_translation as local
import package_windows_bundle as packager
import run_single_video as single


class FakeOllamaHTTPResponse:
    def __init__(self, payload: dict):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self.payload


class DoctorGuiAndBundleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_doctor_collects_ready_and_missing_states(self):
        model_dir = self.root / "faster-whisper-large-v3-turbo"
        model_dir.mkdir()
        (model_dir / "model.bin").write_bytes(b"weights")
        fake_fw = self.root / "faster-whisper-xxl"
        fake_fw.write_text("bin", encoding="utf-8")

        ready = doctor.collect_diagnostics(
            asr_model_dir=model_dir,
            fw_exe=fake_fw,
            ollama_probe=lambda _h, _m: {"running": True, "model_ready": True, "models": ["hy-mt2-local:latest"], "error": None},
        )
        self.assertTrue(ready["ok"])
        self.assertTrue(ready["srt_ready"])
        self.assertTrue(ready["asr_ready"])
        self.assertIn("Ready for .srt -> .zh.srt: YES", doctor.format_report(ready))

        missing = doctor.collect_diagnostics(
            asr_model_dir=self.root / "missing",
            fw_exe=self.root / "missing-fw",
            ollama_probe=lambda _h, _m: {"running": False, "model_ready": False, "models": [], "error": "refused"},
        )
        self.assertFalse(missing["ok"])
        self.assertFalse(missing["srt_ready"])
        self.assertTrue(len(missing["next_actions"]) >= 1)

    def test_gui_server_serves_html_doctor_translate_srt_and_run_path(self):
        sample_video = self.root / "demo.mp4"
        sample_video.write_bytes(b"video")

        def fake_doctor():
            return {"ok": True, "srt_ready": True, "asr_ready": False, "next_actions": []}

        server = gui.create_server(host="127.0.0.1", port=0, doctor_fn=fake_doctor)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), thread.join(timeout=2)))

        base_url = f"http://127.0.0.1:{port}"
        with urllib.request.urlopen(base_url + "/") as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode("utf-8")
            self.assertIn("local-sub-skill Studio", html)
            self.assertIn('accept=".srt,.vtt,text/plain" multiple', html)

        with urllib.request.urlopen(base_url + "/api/doctor") as resp:
            self.assertEqual(resp.status, 200)
            doc = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(doc["srt_ready"])

        # Test /api/run-path dry-run for video file
        run_req = urllib.request.Request(
            base_url + "/api/run-path",
            data=json.dumps({"path": str(sample_video), "language": "ja", "bilingual": True, "ass": True, "dry_run": True}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(run_req) as resp:
            self.assertEqual(resp.status, 200)
            run_data = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(run_data["ok"])
            self.assertTrue(run_data["dry_run"])
            self.assertEqual(run_data["bilingual_srt"], str((self.root / "demo.bilingual.srt").resolve()))
            self.assertEqual(run_data["bilingual_ass"], str((self.root / "demo.bilingual.ass").resolve()))

        # Test /api/translate-srt with mocked Ollama (delegate test server HTTP calls to real urlopen)
        real_urlopen = urllib.request.urlopen

        def fake_urlopen(req, timeout=600):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            if url.startswith(base_url):
                return real_urlopen(req, timeout=timeout)
            if url.endswith("/api/tags"):
                return FakeOllamaHTTPResponse({"models": [{"name": "hy-mt2-local", "digest": "sha256:test"}]})
            return FakeOllamaHTTPResponse({"done": True, "done_reason": "stop", "response": "[1] 你好，世界。"})

        srt_input = "1\n00:00:01,000 --> 00:00:02,000\nHello world.\n"
        with mock.patch("local_translation.urllib.request.urlopen", side_effect=fake_urlopen):
            tr_req = urllib.request.Request(
                base_url + "/api/translate-srt",
                data=json.dumps({
                    "filename": "talk.en.srt",
                    "srt_text": srt_input,
                    "source": "en",
                    "bilingual": True,
                    "ass": True,
                    "bilingual_order": "source-first",
                    "glossary_text": "Agent=智能体\nworld=世界",
                }).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(tr_req) as resp:
                self.assertEqual(resp.status, 200)
                tr_data = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(tr_data["ok"])
                self.assertEqual(tr_data["filename_zh"], "talk.zh.srt")
                self.assertEqual(tr_data["filename_bilingual"], "talk.bilingual.srt")
                self.assertEqual(tr_data["filename_ass"], "talk.bilingual.ass")
                self.assertEqual(tr_data["stats"]["glossary_count"], 2)
                self.assertIn("你好，世界。", tr_data["zh_srt"])
                self.assertIn("Hello world.\n你好，世界。", tr_data["bilingual_srt"])
                self.assertIn("Dialogue: 0,0:00:01.00,0:00:02.00", tr_data["bilingual_ass"])

            vtt_req = urllib.request.Request(
                base_url + "/api/translate-srt",
                data=json.dumps({
                    "filename": "youtube_talk.en.vtt",
                    "srt_text": "WEBVTT\n\n00:01.000 --> 00:02.000\n<c>Hello world.</c>\n",
                    "source": "en",
                    "bilingual": True,
                }).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(vtt_req) as resp:
                self.assertEqual(resp.status, 200)
                vtt_data = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(vtt_data["ok"])
                self.assertEqual(vtt_data["filename_zh"], "youtube_talk.zh.srt")
                self.assertEqual(vtt_data["filename_bilingual"], "youtube_talk.bilingual.srt")
                self.assertIn("00:00:01,000 --> 00:00:02,000", vtt_data["zh_srt"])

    def test_format_srt_timestamp_and_windows_bundle_zip(self):
        self.assertEqual(single.format_srt_timestamp(0.0), "00:00:00,000")
        self.assertEqual(single.format_srt_timestamp(3661.25), "01:01:01,250")

        zip_path = packager.build_bundle(self.root / "dist")
        self.assertTrue(zip_path.is_file())
        with zipfile.ZipFile(zip_path, "r") as zf:
            self.assertIsNone(zf.testzip())
            names = set(zf.namelist())
            self.assertIn("DropVideoOrSrtHere.bat", names)
            self.assertIn("LaunchGUI.bat", names)
            self.assertIn("README_ZH.md", names)
            self.assertIn("scripts/gui.py", names)
            self.assertIn("scripts/doctor.py", names)
            self.assertNotIn("scripts/package_windows_bundle.py", names)


if __name__ == "__main__":
    unittest.main()
