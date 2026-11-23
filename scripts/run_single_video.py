#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Transcribe one local video and translate its subtitles to Chinese entirely offline."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

from local_translation import parse_srt, translate_srt, validate_pair


DEFAULT_WORKSPACE = Path.cwd()
DEFAULT_ASR_MODEL = Path(
    os.environ.get("LOCAL_SUB_ASR_MODEL", str(Path.cwd() / "models" / "faster-whisper-large-v3-turbo"))
)


def resolve_default_fw_exe() -> Path:
    env_exe = os.environ.get("FASTER_WHISPER_EXE")
    if env_exe:
        return Path(env_exe)
    found = shutil.which("faster-whisper-xxl") or shutil.which("faster-whisper-xxl.exe")
    return Path(found) if found else Path("faster-whisper-xxl")


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def unload_translation_model(host: str, model: str) -> None:
    payload = json.dumps({"model": model, "keep_alive": 0}).encode("utf-8")
    request = urllib.request.Request(
        host.rstrip("/") + "/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            response.read()
    except Exception:
        # Ollama may not have the model loaded yet; safe to continue.
        pass


def transcribe_one(video: Path, source_srt: Path, args: argparse.Namespace, log_dir: Path) -> dict:
    if source_srt.exists() and not (args.force or args.force_transcribe):
        cues = parse_srt(source_srt)
        if not cues:
            raise RuntimeError(f"Existing source SRT has no cues: {source_srt}")
        print(f"reuse source subtitle: {source_srt} ({len(cues)} cues)", flush=True)
        return {"elapsed": 0.0, "reused": True, "cue_count": len(cues)}
    if args.skip_transcribe:
        raise RuntimeError(f"Source subtitle is missing: {source_srt}")
    if not (args.fw_exe.exists() or shutil.which(str(args.fw_exe))):
        raise FileNotFoundError(
            f"Faster-Whisper CLI not found ({args.fw_exe}). Pass --fw-exe or set FASTER_WHISPER_EXE."
        )
    if not (args.asr_model_dir / "model.bin").exists():
        raise FileNotFoundError(
            f"Whisper model.bin missing in {args.asr_model_dir}; run scripts/setup_local_models.py."
        )

    unload_translation_model(args.host, args.model)
    started = time.perf_counter()
    temp_root = args.workspace / ".local_subtitles" / "temp"
    temp_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="asr-", dir=temp_root) as temp_name:
        out_dir = Path(temp_name)
        model_name = args.asr_model_dir.name.removeprefix("faster-whisper-")
        command = [
            str(args.fw_exe),
            str(video),
            "--model",
            model_name,
            "--model_dir",
            str(args.asr_model_dir.parent),
            "--device",
            args.device,
            "--compute_type",
            args.compute_type,
            "--output_dir",
            str(out_dir),
            "--output_format",
            "srt",
            "--sentence",
            "--max_line_width",
            str(args.max_line_width),
            "--max_line_count",
            "2",
            "--vad_filter",
            "True",
            "--vad_min_silence_duration_ms",
            str(args.vad_silence_ms),
            "--vad_speech_pad_ms",
            "400",
            "--beep_off",
        ]
        if args.language != "auto":
            command.extend(["--language", args.language])
        if not args.sequential_asr:
            command.extend(["--batched", "--batch_size", str(args.asr_batch_size)])
        print(f"transcribe: {video}", flush=True)
        log_file = log_dir / "transcribe.log"
        with log_file.open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            assert process.stdout is not None
            for line in process.stdout:
                log.write(line)
                log.flush()
                if not line.lstrip().startswith("["):
                    print(line.rstrip(), flush=True)
            code = process.wait()
        candidates = sorted(out_dir.glob("*.srt"), key=lambda p: p.stat().st_size, reverse=True)
        if not candidates or not parse_srt(candidates[0]):
            raise RuntimeError(f"Transcription produced no usable SRT; see {log_file} (exit={code}).")
        if code:
            print(f"warning: transcriber exit={code}, but usable SRT was produced", flush=True)
        temporary_dest = source_srt.with_suffix(source_srt.suffix + ".tmp")
        shutil.copyfile(candidates[0], temporary_dest)
        temporary_dest.replace(source_srt)
    return {
        "elapsed": time.perf_counter() - started,
        "reused": False,
        "cue_count": len(parse_srt(source_srt)),
        "exit_code": code,
        "log": str(log_file),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="100% local Faster-Whisper + Ollama Hy-MT2-1.8B video-to-Chinese subtitle generator."
    )
    parser.add_argument("video", help="Path to local video file (.mp4, .mkv, etc.)")
    parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)
    parser.add_argument("--output-dir", type=Path, help="Output folder; defaults to the video's folder.")
    parser.add_argument("--fw-exe", type=Path, default=resolve_default_fw_exe())
    parser.add_argument("--asr-model-dir", type=Path, default=DEFAULT_ASR_MODEL)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--compute-type", default="int8_float16")
    parser.add_argument("--asr-batch-size", type=int, default=4)
    parser.add_argument("--sequential-asr", action="store_true")
    parser.add_argument("--language", choices=("ja", "en", "auto"), default="ja")
    parser.add_argument("--source", choices=("ja", "en", "auto"))
    parser.add_argument("--target", default="zh")
    parser.add_argument("--model", default="hy-mt2-local")
    parser.add_argument("--host", default="http://localhost:11434")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--num-ctx", type=int, default=4096)
    parser.add_argument("--keep-alive", default="10m")
    parser.add_argument("--vad-silence-ms", type=int, default=500)
    parser.add_argument("--max-line-width", type=int, default=36)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--max-kana-ratio", type=float, default=0.03)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--force-transcribe", action="store_true")
    parser.add_argument("--force-translate", action="store_true")
    parser.add_argument("--skip-transcribe", action="store_true")
    parser.add_argument("--skip-translate", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    configure_stdio()
    args = build_parser().parse_args()
    args.workspace = args.workspace.resolve()
    video = Path(args.video)
    video = (video if video.is_absolute() else args.workspace / video).resolve()
    if not video.is_file():
        raise FileNotFoundError(video)
    if args.batch_size < 1 or args.asr_batch_size < 1:
        raise ValueError("Batch sizes must be positive.")
    args.source = args.source or args.language
    output_dir = (args.output_dir or video.parent).resolve()
    suffix = ".source.srt" if args.language == "auto" else f".{args.language}.srt"
    source_srt = output_dir / (video.stem + suffix)
    target_srt = output_dir / (video.stem + ".zh.srt")
    job_id = hashlib.sha1(str(target_srt).encode("utf-8")).hexdigest()[:12]
    log_dir = args.workspace / ".local_subtitles" / "logs" / job_id

    if args.dry_run:
        print(
            json.dumps(
                {
                    "video": str(video),
                    "source_srt": str(source_srt),
                    "target_srt": str(target_srt),
                    "asr_model": str(args.asr_model_dir),
                    "translation_model": args.model,
                    "cloud": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    output_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    transcription = transcribe_one(video, source_srt, args, log_dir)
    translation = None
    if not args.skip_translate and target_srt.exists() and not (args.force or args.force_translate):
        existing_problems = validate_pair(source_srt, target_srt, args.max_kana_ratio)
        if existing_problems:
            raise RuntimeError(
                "Existing target subtitle is invalid; rerun with --force-translate: "
                + "; ".join(existing_problems)
            )
        translation = {
            "elapsed": 0.0,
            "reused": True,
            "cue_count": len(parse_srt(target_srt)),
            "request_count": 0,
        }
        print(f"reuse target subtitle: {target_srt}", flush=True)
    elif not args.skip_translate:
        checkpoint = log_dir / "translation.checkpoint.json"
        cache = args.workspace / ".local_subtitles" / "logs" / "translation_cache.json"
        if args.force or args.force_translate:
            cache = log_dir / "force-translation-cache.json"
            for path in (checkpoint, cache):
                if path.exists():
                    path.unlink()
        translation = translate_srt(
            source_srt,
            target_srt,
            model=args.model,
            source=args.source,
            target=args.target,
            batch_size=args.batch_size,
            host=args.host,
            cache_path=cache,
            checkpoint_path=checkpoint,
            retries=args.retries,
            num_ctx=args.num_ctx,
            keep_alive=args.keep_alive,
        )

    problems = [] if args.skip_translate else validate_pair(source_srt, target_srt, args.max_kana_ratio)
    result = {
        "video": str(video),
        "source_srt": str(source_srt),
        "target_srt": str(target_srt),
        "ok": not problems,
        "problems": problems,
        "cloud": False,
        "elapsed": time.perf_counter() - started,
        "transcription": transcription,
        "translation": translation,
    }
    report = log_dir / "result.json"
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    print(f"report: {report}", flush=True)
    return 0 if not problems else 2


if __name__ == "__main__":
    raise SystemExit(main())
