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

from local_translation import (
    BILINGUAL_ORDERS,
    SOURCE_LANGUAGE_CHOICES,
    TARGET_LANGUAGE_CHOICES,
    Cue,
    default_ass_path,
    default_notes_path,
    materialize_bilingual_outputs,
    parse_srt,
    parse_subtitles,
    strip_subtitle_lang_suffix,
    translate_srt,
    validate_pair,
    write_srt,
)


DEFAULT_WORKSPACE = Path.cwd()
DEFAULT_ASR_MODEL = Path(
    os.environ.get("LOCAL_SUB_ASR_MODEL", str(Path.cwd() / "models" / "faster-whisper-large-v3-turbo"))
)
DEFAULT_DEVICE = "cpu" if sys.platform == "darwin" else "cuda"
DEFAULT_COMPUTE_TYPE = "int8" if sys.platform == "darwin" else "int8_float16"
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".wav", ".flac", ".ogg", ".opus", ".aac", ".wma"}
TEXT_SUBTITLE_CODECS = {"subrip", "srt", "ass", "ssa", "webvtt", "mov_text", "text"}
ISO639_ALIASES = {
    "ja": {"ja", "jpn"},
    "en": {"en", "eng"},
    "ko": {"ko", "kor"},
    "fr": {"fr", "fre", "fra"},
    "de": {"de", "ger", "deu"},
    "es": {"es", "spa"},
    "ru": {"ru", "rus"},
    "pt": {"pt", "por"},
}


def resolve_bin(name: str) -> str | None:
    return shutil.which(name) or shutil.which(f"{name}.exe")


def resolve_ffmpeg_bin(name: str = "ffmpeg") -> str | None:
    return resolve_bin(name)


def resolve_yt_dlp_bin() -> str | None:
    return resolve_bin("yt-dlp")


def is_audio_file(path: Path) -> bool:
    return path.suffix.lower() in AUDIO_EXTENSIONS


def is_media_url(value: str | Path) -> bool:
    raw = str(value).strip().lower()
    return raw.startswith("http://") or raw.startswith("https://")


def _yt_dlp_sub_langs(language: str) -> str:
    if language != "auto":
        return f"{language}.*,{language}"
    return "en.*,ja.*,ko.*,zh.*,.*-orig"


def fetch_url_media(
    url: str,
    output_dir: Path,
    language: str = "auto",
    extract_subs: bool = False,
    mux: bool = False,
) -> dict:
    """Fetch embedded/auto subtitles or download media from a URL via yt-dlp."""
    yt_dlp = resolve_yt_dlp_bin()
    if not yt_dlp:
        raise FileNotFoundError(
            "yt-dlp is required for URL inputs; install via `pip install yt-dlp` or `winget install yt-dlp.yt-dlp`."
        )
    output_dir.mkdir(parents=True, exist_ok=True)

    if extract_subs and not mux:
        before = {p.resolve() for p in output_dir.iterdir() if p.is_file()}
        sub_cmd = [
            yt_dlp,
            "--no-playlist",
            "--restrict-filenames",
            "--skip-download",
            "--write-subs",
            "--write-auto-subs",
            "--sub-format",
            "srt/vtt",
            "--sub-langs",
            _yt_dlp_sub_langs(language),
            "-P",
            str(output_dir),
            "-o",
            "%(title).100B-%(id)s.%(ext)s",
            url,
        ]
        sub_proc = subprocess.run(sub_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if sub_proc.returncode == 0:
            created = sorted(
                (
                    p.resolve()
                    for p in output_dir.iterdir()
                    if p.is_file() and p.resolve() not in before and p.suffix.lower() in {".srt", ".vtt"}
                ),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for candidate in created:
                try:
                    cues = parse_subtitles(candidate)
                except ValueError:
                    continue
                if cues:
                    base_stem = strip_subtitle_lang_suffix(candidate.stem)
                    return {
                        "mode": "subtitle",
                        "subtitle_path": candidate,
                        "cues": cues,
                        "stem": base_stem,
                    }

    media_cmd = [
        yt_dlp,
        "--no-playlist",
        "--restrict-filenames",
        "-f",
        "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b",
        "--merge-output-format",
        "mp4",
        "--print",
        "after_move:filepath",
        "-P",
        str(output_dir),
        "-o",
        "%(title).100B-%(id)s.%(ext)s",
        url,
    ]
    proc = subprocess.run(media_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise RuntimeError(f"yt-dlp failed for {url}: {detail}")
    lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError(f"yt-dlp did not output a downloaded filepath for {url}")
    media_path = Path(lines[-1]).expanduser().resolve()
    if not media_path.is_file():
        raise FileNotFoundError(f"Downloaded media not found: {media_path}")
    return {"mode": "media", "media_path": media_path}


def probe_text_subtitle_stream(video: Path, preferred_lang: str = "auto") -> dict | None:
    """Return best embedded text-based subtitle stream via ffprobe, or None."""
    ffprobe = resolve_ffmpeg_bin("ffprobe")
    if not ffprobe:
        return None
    proc = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "s", "-show_entries", "stream=index,codec_name:stream_tags=language,title", "-of", "json", str(video)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        return None
    try:
        streams = json.loads(proc.stdout or "{}").get("streams", [])
    except ValueError:
        return None
    text_streams = [
        s for s in streams
        if isinstance(s, dict) and str(s.get("codec_name", "")).lower() in TEXT_SUBTITLE_CODECS and "index" in s
    ]
    if not text_streams:
        return None
    wanted = ISO639_ALIASES.get(preferred_lang, {preferred_lang}) if preferred_lang != "auto" else set()
    if wanted:
        for stream in text_streams:
            lang = str((stream.get("tags") or {}).get("language", "")).strip().lower()
            if lang in wanted:
                return stream
    return text_streams[0]


def extract_embedded_subtitle(video: Path, source_srt: Path, language: str = "auto") -> dict | None:
    """Extract an embedded text subtitle stream to source_srt via ffmpeg, or return None."""
    ffmpeg = resolve_ffmpeg_bin("ffmpeg")
    if not ffmpeg:
        return None
    stream = probe_text_subtitle_stream(video, preferred_lang=language)
    if not stream:
        return None
    started = time.perf_counter()
    source_srt.parent.mkdir(parents=True, exist_ok=True)
    tmp_srt = source_srt.with_suffix(source_srt.suffix + ".tmp.srt")
    try:
        proc = subprocess.run(
            [ffmpeg, "-y", "-i", str(video), "-map", f"0:{stream['index']}", "-c:s", "srt", str(tmp_srt)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode != 0 or not tmp_srt.is_file():
            return None
        cues = parse_srt(tmp_srt)
        if not cues:
            return None
        tmp_srt.replace(source_srt)
    finally:
        tmp_srt.unlink(missing_ok=True)
    return {
        "elapsed": time.perf_counter() - started,
        "reused": False,
        "extracted": True,
        "backend": "ffmpeg-extract",
        "stream_index": stream["index"],
        "codec": stream.get("codec_name"),
        "cue_count": len(cues),
    }


def mux_subtitles(
    video: Path,
    subtitle_files: list[tuple[Path, str]],
    output_video: Path | None = None,
) -> Path:
    """Stream-copy video/audio and embed translated subtitles into a self-contained .subbed.mkv."""
    ffmpeg = resolve_ffmpeg_bin("ffmpeg")
    if not ffmpeg:
        raise FileNotFoundError("ffmpeg is required for --mux; install ffmpeg on PATH.")
    valid_subs = [(Path(p), title) for p, title in subtitle_files if p and Path(p).is_file()]
    if not valid_subs:
        raise ValueError("No subtitle files available to mux.")
    out_path = (output_video or video.with_name(f"{video.stem}.subbed.mkv")).resolve()
    tmp_out = out_path.with_name(f"{out_path.stem}.tmp{out_path.suffix}")
    command = [ffmpeg, "-y", "-i", str(video)]
    for sub_path, _title in valid_subs:
        command.extend(["-i", str(sub_path)])
    command.extend(["-map", "0:v?", "-map", "0:a?"])
    for idx in range(len(valid_subs)):
        command.extend(["-map", f"{idx + 1}:0"])
    command.extend(["-c", "copy"])
    for idx, (_sub_path, title) in enumerate(valid_subs):
        command.extend([f"-metadata:s:s:{idx}", "language=chi", f"-metadata:s:s:{idx}", f"title={title}"])
    command.extend(["-disposition:s:0", "default", str(tmp_out)])
    try:
        proc = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if proc.returncode != 0 or not tmp_out.is_file():
            raise RuntimeError(f"ffmpeg mux failed: {(proc.stderr or proc.stdout).strip()}")
        tmp_out.replace(out_path)
    finally:
        tmp_out.unlink(missing_ok=True)
    return out_path


def _escape_ffmpeg_filter_path(path: Path) -> str:
    """Escape a filesystem path for use inside an FFmpeg subtitles/ass filter argument."""
    resolved = str(path.resolve()).replace("\\", "/")
    for src, dst in ((":", r"\:"), ("'", r"\'"), ("[", r"\["), ("]", r"\]")):
        resolved = resolved.replace(src, dst)
    return resolved


def burn_subtitles(
    video: Path,
    subtitle_file: Path,
    output_video: Path | None = None,
) -> Path:
    """Hard-burn subtitles (.ass or .srt) into an H.264 MP4 for mobile and social playback."""
    ffmpeg = resolve_ffmpeg_bin("ffmpeg")
    if not ffmpeg:
        raise FileNotFoundError("ffmpeg is required for --burn; install ffmpeg on PATH.")
    sub_path = Path(subtitle_file).resolve()
    if not sub_path.is_file():
        raise FileNotFoundError(f"Subtitle file not found for --burn: {sub_path}")
    out_path = (output_video or video.with_name(f"{video.stem}.burned.mp4")).resolve()
    tmp_out = out_path.with_name(f"{out_path.stem}.tmp{out_path.suffix}")
    filter_name = "ass" if sub_path.suffix.lower() == ".ass" else "subtitles"
    vf_arg = f"{filter_name}='{_escape_ffmpeg_filter_path(sub_path)}'"
    command = [
        ffmpeg,
        "-y",
        "-i",
        str(video),
        "-vf",
        vf_arg,
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        str(tmp_out),
    ]
    try:
        proc = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if proc.returncode != 0 or not tmp_out.is_file():
            raise RuntimeError(f"ffmpeg burn failed: {(proc.stderr or proc.stdout).strip()}")
        tmp_out.replace(out_path)
    finally:
        tmp_out.unlink(missing_ok=True)
    return out_path


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
    if getattr(args, "extract_subs", False) and not is_audio_file(video):
        extracted = extract_embedded_subtitle(video, source_srt, language=args.language)
        if extracted is not None:
            print(f"extracted embedded subtitle stream #{extracted['stream_index']} -> {source_srt} ({extracted['cue_count']} cues)", flush=True)
            return extracted

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
        description="100% local Faster-Whisper + Ollama Hy-MT2-1.8B video/audio/URL-to-Chinese subtitle generator."
    )
    parser.add_argument("video", help="Path to local video/audio file (.mp4, .mkv, .mp3, .m4a, .wav, etc.) or media URL (YouTube/Bilibili via yt-dlp)")
    parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)
    parser.add_argument("--output-dir", type=Path, help="Output folder; defaults to the video's folder.")
    parser.add_argument("--fw-exe", type=Path, default=resolve_default_fw_exe())
    parser.add_argument("--asr-model-dir", type=Path, default=DEFAULT_ASR_MODEL)
    parser.add_argument("--device", default=DEFAULT_DEVICE)
    parser.add_argument("--compute-type", default=DEFAULT_COMPUTE_TYPE)
    parser.add_argument("--asr-batch-size", type=int, default=4)
    parser.add_argument("--sequential-asr", action="store_true")
    parser.add_argument("--language", choices=SOURCE_LANGUAGE_CHOICES, default="ja")
    parser.add_argument("--source", choices=SOURCE_LANGUAGE_CHOICES)
    parser.add_argument("--target", choices=TARGET_LANGUAGE_CHOICES, default="zh", help="Target subtitle code (zh, zh-Hant, en, ja, ko, ...).")
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
    parser.add_argument("--notes", action="store_true", help="Also write bilingual Markdown reading notes (.bilingual.md).")
    parser.add_argument("--extract-subs", action="store_true", help="Try extracting an embedded text subtitle via ffmpeg before running Whisper ASR.")
    parser.add_argument("--mux", action="store_true", help="Soft-mux generated subtitles into <video>.subbed.mkv via ffmpeg stream copy.")
    parser.add_argument("--burn", action="store_true", help="Hard-burn generated subtitles into <video>.burned.mp4 via ffmpeg for mobile/social playback.")
    parser.add_argument("--glossary", type=Path, help="Optional JSON or text glossary (`source=target`) file.")
    parser.add_argument("--shift", dest="shift_seconds", type=float, default=0.0, help="Shift output subtitle timestamps by seconds (e.g. 1.5 or -0.8).")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def _run_translation_stage(
    source_srt: Path,
    target_srt: Path,
    bilingual_srt: Path | None,
    bilingual_ass: Path | None,
    bilingual_md: Path | None,
    run_args: argparse.Namespace,
    source_lang: str,
    log_dir: Path,
    workspace: Path,
) -> tuple[dict | None, list[str]]:
    glossary_arg = getattr(run_args, "glossary", None)
    shift_val = float(getattr(run_args, "shift_seconds", 0.0) or 0.0)
    translation = None
    if not run_args.skip_translate and target_srt.exists() and not (run_args.force or run_args.force_transcribe or run_args.force_translate):
        existing_problems = validate_pair(source_srt, target_srt, run_args.max_kana_ratio, check_cjk=run_args.target.startswith("zh"), shift_seconds=shift_val)
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
        translation.update(
            materialize_bilingual_outputs(
                source_srt,
                target_srt,
                bilingual_path=bilingual_srt,
                ass_path=bilingual_ass,
                notes_path=bilingual_md,
                order=run_args.bilingual_order,
                shift_seconds=shift_val,
            )
        )
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
            notes_path=bilingual_md,
            glossary=glossary_arg,
            shift_seconds=shift_val,
        )

    problems = [] if run_args.skip_translate else validate_pair(source_srt, target_srt, run_args.max_kana_ratio, check_cjk=run_args.target.startswith("zh"), shift_seconds=shift_val)
    return translation, problems


def _build_output_paths(
    stem: str,
    output_dir: Path,
    args: argparse.Namespace,
    allow_video_outputs: bool,
) -> tuple[Path, Path, Path | None, Path | None, Path | None, Path | None, Path | None]:
    suffix = ".source.srt" if args.language == "auto" else f".{args.language}.srt"
    source_srt = output_dir / (stem + suffix)
    target_srt = output_dir / f"{stem}.{args.target}.srt"
    bilingual_srt = output_dir / (stem + ".bilingual.srt") if (args.bilingual and not args.skip_translate) else None
    bilingual_ass = default_ass_path(target_srt) if (getattr(args, "ass", False) and not args.skip_translate) else None
    bilingual_md = default_notes_path(target_srt) if (getattr(args, "notes", False) and not args.skip_translate) else None
    mux_output = (output_dir / f"{stem}.subbed.mkv") if (allow_video_outputs and getattr(args, "mux", False) and not args.skip_translate) else None
    burn_output = (output_dir / f"{stem}.burned.mp4") if (allow_video_outputs and getattr(args, "burn", False) and not args.skip_translate) else None
    return source_srt, target_srt, bilingual_srt, bilingual_ass, bilingual_md, mux_output, burn_output


def run_video_pipeline(video_arg: str | Path, args: argparse.Namespace) -> dict:
    """Run the local ASR -> translation -> bilingual SRT pipeline for a local video, audio file, or URL."""
    if args.batch_size < 1 or args.asr_batch_size < 1:
        raise ValueError("Batch sizes must be positive.")
    workspace = args.workspace.resolve()
    source_lang = args.source or args.language
    glossary_arg = getattr(args, "glossary", None)
    shift_val = float(getattr(args, "shift_seconds", 0.0) or 0.0)

    if is_media_url(video_arg):
        url = str(video_arg).strip()
        output_dir = (args.output_dir or (workspace / "downloads")).resolve()
        if args.dry_run:
            return {
                "url": url,
                "output_dir": str(output_dir),
                "yt_dlp_found": resolve_yt_dlp_bin() is not None,
                "extract_subs": bool(getattr(args, "extract_subs", False)),
                "bilingual": bool(args.bilingual),
                "ass": bool(getattr(args, "ass", False)),
                "notes": bool(getattr(args, "notes", False)),
                "mux": bool(getattr(args, "mux", False)),
                "burn": bool(getattr(args, "burn", False)),
                "glossary": str(glossary_arg) if glossary_arg else None,
                "shift_seconds": shift_val,
                "asr_model": str(args.asr_model_dir),
                "translation_model": args.model,
                "cloud": False,
                "dry_run": True,
                "ok": True,
            }
        started = time.perf_counter()
        fetched = fetch_url_media(
            url,
            output_dir,
            language=args.language,
            extract_subs=bool(getattr(args, "extract_subs", False)),
            mux=bool(getattr(args, "mux", False) or getattr(args, "burn", False)),
        )
        if fetched["mode"] == "media":
            result = run_video_pipeline(fetched["media_path"], args)
            result["url"] = url
            return result
        source_srt, target_srt, bilingual_srt, bilingual_ass, bilingual_md, _mux, _burn = _build_output_paths(
            fetched["stem"], output_dir, args, allow_video_outputs=False
        )
        write_srt(source_srt, fetched["cues"])
        job_id = hashlib.sha1(str(target_srt).encode("utf-8")).hexdigest()[:12]
        log_dir = workspace / ".local_subtitles" / "logs" / job_id
        output_dir.mkdir(parents=True, exist_ok=True)
        log_dir.mkdir(parents=True, exist_ok=True)
        run_args = argparse.Namespace(**vars(args))
        run_args.workspace = workspace
        run_args.source = source_lang
        translation, problems = _run_translation_stage(
            source_srt, target_srt, bilingual_srt, bilingual_ass, bilingual_md,
            run_args, source_lang, log_dir, workspace,
        )
        result = {
            "url": url,
            "video": str(fetched["subtitle_path"]),
            "source_srt": str(source_srt),
            "target_srt": str(target_srt),
            "bilingual_srt": str(bilingual_srt) if bilingual_srt else None,
            "bilingual_ass": str(bilingual_ass) if bilingual_ass else None,
            "bilingual_md": str(bilingual_md) if bilingual_md else None,
            "muxed_video": None,
            "burned_video": None,
            "ok": not problems,
            "problems": problems,
            "cloud": False,
            "elapsed": time.perf_counter() - started,
            "transcription": {
                "elapsed": time.perf_counter() - started,
                "reused": False,
                "extracted": True,
                "backend": "yt-dlp-subs",
                "cue_count": len(fetched["cues"]),
            },
            "translation": translation,
        }
        report = log_dir / "result.json"
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        result["report"] = str(report)
        return result

    video = Path(video_arg)
    video = (video if video.is_absolute() else workspace / video).resolve()
    if not video.is_file():
        raise FileNotFoundError(video)
    audio_only = is_audio_file(video)
    output_dir = (args.output_dir or video.parent).resolve()
    source_srt, target_srt, bilingual_srt, bilingual_ass, bilingual_md, mux_output, burn_output = _build_output_paths(
        video.stem, output_dir, args, allow_video_outputs=not audio_only
    )
    job_id = hashlib.sha1(str(target_srt).encode("utf-8")).hexdigest()[:12]
    log_dir = workspace / ".local_subtitles" / "logs" / job_id

    if args.dry_run:
        return {
            "video": str(video),
            "media_type": "audio" if audio_only else "video",
            "source_srt": str(source_srt),
            "target_srt": str(target_srt),
            "bilingual_srt": str(bilingual_srt) if bilingual_srt else None,
            "bilingual_ass": str(bilingual_ass) if bilingual_ass else None,
            "bilingual_md": str(bilingual_md) if bilingual_md else None,
            "muxed_video": str(mux_output) if mux_output else None,
            "burned_video": str(burn_output) if burn_output else None,
            "extract_subs": bool(getattr(args, "extract_subs", False)) and not audio_only,
            "bilingual_order": args.bilingual_order if (bilingual_srt or bilingual_ass or bilingual_md) else None,
            "glossary": str(glossary_arg) if glossary_arg else None,
            "shift_seconds": shift_val,
            "asr_model": str(args.asr_model_dir),
            "translation_model": args.model,
            "cloud": False,
            "dry_run": True,
            "ok": True,
        }

    run_args = argparse.Namespace(**vars(args))
    run_args.workspace = workspace
    run_args.source = source_lang

    output_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    transcription = transcribe_one(video, source_srt, run_args, log_dir)
    translation, problems = _run_translation_stage(
        source_srt, target_srt, bilingual_srt, bilingual_ass, bilingual_md,
        run_args, source_lang, log_dir, workspace,
    )
    muxed_path = None
    if mux_output is not None and not problems:
        sub_inputs = [
            (bilingual_ass, "Bilingual ASS"),
            (bilingual_srt, "Bilingual SRT"),
            (target_srt, f"Translated ({run_args.target})"),
        ]
        muxed_path = mux_subtitles(video, [(p, t) for p, t in sub_inputs if p is not None], output_video=mux_output)
    burned_path = None
    if burn_output is not None and not problems:
        preferred_sub = next((p for p in (bilingual_ass, bilingual_srt, target_srt) if p is not None and p.is_file()), None)
        if preferred_sub is not None:
            burned_path = burn_subtitles(video, preferred_sub, output_video=burn_output)
    result = {
        "video": str(video),
        "media_type": "audio" if audio_only else "video",
        "source_srt": str(source_srt),
        "target_srt": str(target_srt),
        "bilingual_srt": str(bilingual_srt) if bilingual_srt else None,
        "bilingual_ass": str(bilingual_ass) if bilingual_ass else None,
        "bilingual_md": str(bilingual_md) if bilingual_md else None,
        "muxed_video": str(muxed_path) if muxed_path else None,
        "burned_video": str(burned_path) if burned_path else None,
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
