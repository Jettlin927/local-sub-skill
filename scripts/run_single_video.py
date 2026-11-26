#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Transcribe one local video and translate its subtitles to Chinese entirely offline."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

from local_translation import BILINGUAL_ORDERS, Cue, default_ass_path, parse_srt, translate_srt, validate_pair, write_bilingual_ass, write_bilingual_srt, write_srt


DEFAULT_WORKSPACE = Path.cwd()
DEFAULT_ASR_MODEL = Path(
    os.environ.get("LOCAL_SUB_ASR_MODEL", str(Path.cwd() / "models" / "faster-whisper-large-v3-turbo"))
)
DEFAULT_DEVICE = "cpu" if sys.platform == "darwin" else "cuda"
DEFAULT_COMPUTE_TYPE = "int8" if sys.platform == "darwin" else "int8_float16"


def resolve_default_fw_exe() -> Path:
    env_exe = os.environ.get("FASTER_WHISPER_EXE")
    if env_exe:
        return Path(env_exe)
    found = shutil.which("faster-whisper-xxl") or shutil.which("faster-whisper-xxl.exe")
    return Path(found) if found else Path("faster-whisper-xxl")


def has_python_faster_whisper() -> bool:
    return importlib.util.find_spec("faster_whisper") is not None


def format_srt_timestamp(seconds: float) -> str:
    """Format fractional seconds into an SRT timestamp (HH:MM:SS,mmm)."""
    total_ms = max(0, round(seconds * 1000))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


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


def transcribe_with_python_faster_whisper(video: Path, source_srt: Path, args: argparse.Namespace) -> dict:
    """Fallback ASR using Python faster-whisper (macOS/Linux/CPU-friendly)."""
    from faster_whisper import WhisperModel

    model_ref = (
        str(args.asr_model_dir)
        if (args.asr_model_dir / "model.bin").exists()
        else args.asr_model_dir.name.removeprefix("faster-whisper-")
    )
    started = time.perf_counter()
    model = WhisperModel(model_ref, device=args.device, compute_type=args.compute_type)
    segments, _info = model.transcribe(
        str(video),
        language=None if args.language == "auto" else args.language,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": args.vad_silence_ms, "speech_pad_ms": 400},
    )
    cues: list[Cue] = []
    for index, seg in enumerate(segments, 1):
        text = seg.text.strip()
        if not text:
            continue
        cues.append(Cue(str(index), f"{format_srt_timestamp(seg.start)} --> {format_srt_timestamp(seg.end)}", text))
    if not cues:
        raise RuntimeError(f"Python faster-whisper produced no usable cues for {video}")
    write_srt(source_srt, cues)
    return {
        "elapsed": time.perf_counter() - started,
        "reused": False,
        "backend": "python-faster-whisper",
        "cue_count": len(cues),
    }


def transcribe_one(video: Path, source_srt: Path, args: argparse.Namespace, log_dir: Path) -> dict:
    if source_srt.exists() and not (args.force or args.force_transcribe):
        cues = parse_srt(source_srt)
        if not cues:
            raise RuntimeError(f"Existing source SRT has no cues: {source_srt}")
        print(f"reuse source subtitle: {source_srt} ({len(cues)} cues)", flush=True)
        return {"elapsed": 0.0, "reused": True, "cue_count": len(cues)}
    if args.skip_transcribe:
        raise RuntimeError(f"Source subtitle is missing: {source_srt}")

    has_fw_exe = args.fw_exe.exists() or bool(shutil.which(str(args.fw_exe)))
    has_local_model = (args.asr_model_dir / "model.bin").exists()
    if not has_fw_exe:
        if has_python_faster_whisper():
            unload_translation_model(args.host, args.model)
            return transcribe_with_python_faster_whisper(video, source_srt, args)
        raise FileNotFoundError(
            f"Faster-Whisper CLI ({args.fw_exe}) not found and Python `faster-whisper` is not installed. "
            "On macOS/Linux/CPU run `pip install faster-whisper`, or pass `--fw-exe` / set FASTER_WHISPER_EXE."
        )
    if not has_local_model:
        if has_python_faster_whisper():
            unload_translation_model(args.host, args.model)
            return transcribe_with_python_faster_whisper(video, source_srt, args)
        raise FileNotFoundError(
            f"Whisper model.bin missing in {args.asr_model_dir}; run `npx local-sub-skill setup`."
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
    parser.add_argument("--device", default=DEFAULT_DEVICE)
    parser.add_argument("--compute-type", default=DEFAULT_COMPUTE_TYPE)
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
    parser.add_argument("--bilingual", action="store_true", help="Also write a .bilingual.srt file.")
    parser.add_argument("--bilingual-order", choices=BILINGUAL_ORDERS, default="zh-first")
    parser.add_argument("--ass", action="store_true", help="Also write a styled dual-language .bilingual.ass file.")
    parser.add_argument("--glossary", type=Path, help="Optional JSON or text glossary (`source=target`) file.")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def run_video_pipeline(video_arg: str | Path, args: argparse.Namespace) -> dict:
    """Run the local ASR -> translation -> bilingual SRT pipeline for a single video."""
    workspace = args.workspace.resolve()
    video = Path(video_arg)
    video = (video if video.is_absolute() else workspace / video).resolve()
    if not video.is_file():
        raise FileNotFoundError(video)
    if args.batch_size < 1 or args.asr_batch_size < 1:
        raise ValueError("Batch sizes must be positive.")
    source_lang = args.source or args.language
    output_dir = (args.output_dir or video.parent).resolve()
    suffix = ".source.srt" if args.language == "auto" else f".{args.language}.srt"
    source_srt = output_dir / (video.stem + suffix)
    target_srt = output_dir / (video.stem + ".zh.srt")
    bilingual_srt = output_dir / (video.stem + ".bilingual.srt") if (args.bilingual and not args.skip_translate) else None
    bilingual_ass = default_ass_path(target_srt) if (getattr(args, "ass", False) and not args.skip_translate) else None
    glossary_arg = getattr(args, "glossary", None)
    job_id = hashlib.sha1(str(target_srt).encode("utf-8")).hexdigest()[:12]
    log_dir = workspace / ".local_subtitles" / "logs" / job_id

    if args.dry_run:
        return {
            "video": str(video),
            "source_srt": str(source_srt),
            "target_srt": str(target_srt),
            "bilingual_srt": str(bilingual_srt) if bilingual_srt else None,
            "bilingual_ass": str(bilingual_ass) if bilingual_ass else None,
            "bilingual_order": args.bilingual_order if (bilingual_srt or bilingual_ass) else None,
            "glossary": str(glossary_arg) if glossary_arg else None,
            "asr_model": str(args.asr_model_dir),
            "translation_model": args.model,
            "cloud": False,
            "dry_run": True,
            "ok": True,
        }

    # Copy args with resolved workspace/source so transcribe_one sees consistent paths
    run_args = argparse.Namespace(**vars(args))
    run_args.workspace = workspace
    run_args.source = source_lang

    output_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    transcription = transcribe_one(video, source_srt, run_args, log_dir)
    translation = None
    if not run_args.skip_translate and target_srt.exists() and not (run_args.force or run_args.force_transcribe or run_args.force_translate):
        existing_problems = validate_pair(source_srt, target_srt, run_args.max_kana_ratio)
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
        if bilingual_srt is not None and (not bilingual_srt.exists() or run_args.force or run_args.force_translate):
            write_bilingual_srt(source_srt, target_srt, bilingual_srt, order=run_args.bilingual_order)
        if bilingual_srt is not None:
            translation["bilingual_srt"] = str(bilingual_srt)
        if bilingual_ass is not None and (not bilingual_ass.exists() or run_args.force or run_args.force_translate):
            write_bilingual_ass(source_srt, target_srt, bilingual_ass, order=run_args.bilingual_order)
        if bilingual_ass is not None:
            translation["bilingual_ass"] = str(bilingual_ass)
        print(f"reuse target subtitle: {target_srt}", flush=True)
    elif not run_args.skip_translate:
        checkpoint = log_dir / "translation.checkpoint.json"
        cache = workspace / ".local_subtitles" / "logs" / "translation_cache.json"
        if run_args.force or run_args.force_translate:
            cache = log_dir / "force-translation-cache.json"
            for path in (checkpoint, cache):
                if path.exists():
                    path.unlink()
        translation = translate_srt(
            source_srt,
            target_srt,
            model=run_args.model,
            source=source_lang,
            target=run_args.target,
            batch_size=run_args.batch_size,
            host=run_args.host,
            cache_path=cache,
            checkpoint_path=checkpoint,
            retries=run_args.retries,
            num_ctx=run_args.num_ctx,
            keep_alive=run_args.keep_alive,
            bilingual_path=bilingual_srt,
            bilingual_order=run_args.bilingual_order,
            ass_path=bilingual_ass,
            glossary=glossary_arg,
        )

    problems = [] if run_args.skip_translate else validate_pair(source_srt, target_srt, run_args.max_kana_ratio)
    result = {
        "video": str(video),
        "source_srt": str(source_srt),
        "target_srt": str(target_srt),
        "bilingual_srt": str(bilingual_srt) if bilingual_srt else None,
        "bilingual_ass": str(bilingual_ass) if bilingual_ass else None,
        "ok": not problems,
        "problems": problems,
        "cloud": False,
        "elapsed": time.perf_counter() - started,
        "transcription": transcription,
        "translation": translation,
    }
    report = log_dir / "result.json"
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    result["report"] = str(report)
    return result


def main() -> int:
    configure_stdio()
    args = build_parser().parse_args()
    result = run_video_pipeline(args.video, args)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    if result.get("report"):
        print(f"report: {result['report']}", flush=True)
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
