#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Batch-generate local Chinese & bilingual subtitles for a folder of videos."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time

from local_translation import (
    BILINGUAL_ORDERS,
    SOURCE_LANGUAGE_CHOICES,
    TARGET_LANGUAGE_CHOICES,
    default_ass_path,
    default_bilingual_path,
    default_notes_path,
    derive_target_srt_path,
    materialize_bilingual_outputs,
    parse_subtitles,
    strip_subtitle_lang_suffix,
    translate_srt,
    validate_pair,
)
from run_single_video import (
    AUDIO_EXTENSIONS,
    DEFAULT_ASR_MODEL,
    DEFAULT_COMPUTE_TYPE,
    DEFAULT_DEVICE,
    DEFAULT_WORKSPACE,
    configure_stdio,
    resolve_default_fw_exe,
    run_video_pipeline,
)


VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".ts", ".flv"}
MEDIA_EXTENSIONS = VIDEO_EXTENSIONS | AUDIO_EXTENSIONS
SUBTITLE_EXTENSIONS = {".srt", ".vtt", ".ass", ".ssa"}
GENERATED_SUBTITLE_SUFFIXES = (".zh.srt", ".bilingual.srt", ".zh-hant.srt", ".bilingual.ass")


def _discover_by_ext(folder: Path, extensions: set[str], recursive: bool = False) -> list[Path]:
    if not folder.is_dir():
        raise NotADirectoryError(folder)
    iterator = folder.rglob("*") if recursive else folder.iterdir()
    found: list[Path] = []
    for candidate in iterator:
        if not candidate.is_file() or candidate.suffix.lower() not in extensions:
            continue
        rel_parts = candidate.relative_to(folder).parts
        if any(part.startswith(".") for part in rel_parts):
            continue
        found.append(candidate.resolve())
    return sorted(found, key=lambda p: str(p).lower())


def discover_videos(folder: Path, recursive: bool = False) -> list[Path]:
    """Return sorted video & audio files inside folder, skipping hidden/.local_subtitles dirs."""
    return _discover_by_ext(folder, MEDIA_EXTENSIONS, recursive=recursive)


discover_media = discover_videos


def discover_standalone_subtitles(
    folder: Path,
    recursive: bool = False,
    video_paths: list[Path] | None = None,
) -> list[Path]:
    """Return sorted standalone .srt/.vtt files not belonging to discovered videos or generated outputs."""
    video_keys = {(v.parent.resolve(), v.stem.lower()) for v in (video_paths or [])}
    subtitles: list[Path] = []
    for candidate in _discover_by_ext(folder, SUBTITLE_EXTENSIONS, recursive=recursive):
        lower_name = candidate.name.lower()
        if lower_name.endswith(GENERATED_SUBTITLE_SUFFIXES):
            continue
        base_stem = strip_subtitle_lang_suffix(candidate.stem).lower()
        if (candidate.parent.resolve(), base_stem) in video_keys:
            continue
        subtitles.append(candidate)
    return subtitles


def build_batch_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Batch-process a folder of local videos into .zh.srt (and optional .bilingual.srt)."
    )
    parser.add_argument("folder", type=Path, help="Folder containing video files.")
    parser.add_argument("--recursive", action="store_true", help="Scan subfolders recursively.")
    parser.add_argument("--continue-on-error", action="store_true", help="Keep processing remaining files if one fails.")
    parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)
    parser.add_argument("--output-dir", type=Path, help="Output folder; defaults to each video's folder.")
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
    parser.add_argument("--bilingual", action="store_true", help="Also write a .bilingual.srt per video.")
    parser.add_argument("--bilingual-order", choices=BILINGUAL_ORDERS, default="zh-first")
    parser.add_argument("--ass", action="store_true", help="Also write a styled dual-language .bilingual.ass per item.")
    parser.add_argument("--notes", action="store_true", help="Also write bilingual Markdown reading notes (.bilingual.md).")
    parser.add_argument("--extract-subs", action="store_true", help="Try extracting embedded text subtitles via ffmpeg before Whisper ASR.")
    parser.add_argument("--mux", action="store_true", help="Soft-mux generated subtitles into <video>.subbed.mkv via ffmpeg.")
    parser.add_argument("--burn", action="store_true", help="Hard-burn generated subtitles into <video>.burned.mp4 via ffmpeg.")
    parser.add_argument("--glossary", type=Path, help="Optional JSON or text glossary (`source=target`) file.")
    parser.add_argument("--shift", dest="shift_seconds", type=float, default=0.0, help="Shift subtitle timestamps by seconds (e.g. 1.5 or -0.8).")
    parser.add_argument("--subtitles-only", action="store_true", help="Only batch-translate standalone .srt/.vtt/.ass/.ssa files.")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def run_subtitle_job(subtitle_arg: str | Path, args: argparse.Namespace) -> dict:
    """Translate a standalone .srt, .vtt, or .ass/.ssa subtitle file with batch/checkpoint guardrails."""
    workspace = args.workspace.resolve()
    source_sub = Path(subtitle_arg)
    source_sub = (source_sub if source_sub.is_absolute() else workspace / source_sub).resolve()
    if not source_sub.is_file():
        raise FileNotFoundError(source_sub)
    source_lang = args.source or args.language
    output_dir = (args.output_dir or source_sub.parent).resolve()
    target_srt = output_dir / derive_target_srt_path(source_sub, target=args.target).name
    bilingual_srt = default_bilingual_path(target_srt) if (args.bilingual and not args.skip_translate) else None
    bilingual_ass = default_ass_path(target_srt) if (getattr(args, "ass", False) and not args.skip_translate) else None
    bilingual_md = default_notes_path(target_srt) if (getattr(args, "notes", False) and not args.skip_translate) else None
    glossary_arg = getattr(args, "glossary", None)
    shift_val = float(getattr(args, "shift_seconds", 0.0) or 0.0)

    if args.dry_run:
        return {
            "subtitle": str(source_sub),
            "source_srt": str(source_sub),
            "target_srt": str(target_srt),
            "bilingual_srt": str(bilingual_srt) if bilingual_srt else None,
            "bilingual_ass": str(bilingual_ass) if bilingual_ass else None,
            "bilingual_md": str(bilingual_md) if bilingual_md else None,
            "bilingual_order": args.bilingual_order if (bilingual_srt or bilingual_ass or bilingual_md) else None,
            "glossary": str(glossary_arg) if glossary_arg else None,
            "shift_seconds": shift_val,
            "translation_model": args.model,
            "cloud": False,
            "dry_run": True,
            "ok": True,
        }

    started = time.perf_counter()
    output_dir.mkdir(parents=True, exist_ok=True)
    job_id = hashlib.sha1(str(target_srt).encode("utf-8")).hexdigest()[:12]
    log_dir = workspace / ".local_subtitles" / "logs" / job_id
    log_dir.mkdir(parents=True, exist_ok=True)

    translation = None
    if not args.skip_translate and target_srt.exists() and not (args.force or args.force_translate):
        existing_problems = validate_pair(source_sub, target_srt, args.max_kana_ratio, check_cjk=args.target.startswith("zh"), shift_seconds=shift_val)
        if existing_problems:
            raise RuntimeError(
                "Existing target subtitle is invalid; rerun with --force-translate: "
                + "; ".join(existing_problems)
            )
        translation = {
            "elapsed": 0.0,
            "reused": True,
            "cue_count": len(parse_subtitles(target_srt)),
            "request_count": 0,
        }
        translation.update(
            materialize_bilingual_outputs(
                source_sub,
                target_srt,
                bilingual_path=bilingual_srt,
                ass_path=bilingual_ass,
                notes_path=bilingual_md,
                order=args.bilingual_order,
                force=bool(args.force or args.force_translate),
            )
        )
    elif not args.skip_translate:
        checkpoint = log_dir / "translation.checkpoint.json"
        cache = workspace / ".local_subtitles" / "logs" / "translation_cache.json"
        if args.force or args.force_translate:
            cache = log_dir / "force-translation-cache.json"
            for path in (checkpoint, cache):
                if path.exists():
                    path.unlink()
        translation = translate_srt(
            source_sub,
            target_srt,
            model=args.model,
            source=source_lang,
            target=args.target,
            batch_size=args.batch_size,
            host=args.host,
            cache_path=cache,
            checkpoint_path=checkpoint,
            retries=args.retries,
            num_ctx=args.num_ctx,
            keep_alive=args.keep_alive,
            bilingual_path=bilingual_srt,
            bilingual_order=args.bilingual_order,
            ass_path=bilingual_ass,
            notes_path=bilingual_md,
            glossary=glossary_arg,
            shift_seconds=shift_val,
        )

    problems = [] if args.skip_translate else validate_pair(source_sub, target_srt, args.max_kana_ratio, check_cjk=args.target.startswith("zh"), shift_seconds=shift_val)
    return {
        "subtitle": str(source_sub),
        "source_srt": str(source_sub),
        "target_srt": str(target_srt),
        "bilingual_srt": str(bilingual_srt) if bilingual_srt else None,
        "bilingual_ass": str(bilingual_ass) if bilingual_ass else None,
        "bilingual_md": str(bilingual_md) if bilingual_md else None,
        "ok": not problems,
        "problems": problems,
        "cloud": False,
        "elapsed": time.perf_counter() - started,
        "translation": translation,
    }


def run_batch(args: argparse.Namespace) -> dict:
    """Discover and process all videos and standalone .srt/.vtt subtitles in args.folder."""
    folder = Path(args.folder).resolve()
    videos = [] if getattr(args, "subtitles_only", False) else discover_videos(folder, recursive=args.recursive)
    subtitles = discover_standalone_subtitles(folder, recursive=args.recursive, video_paths=videos)
    jobs: list[tuple[str, Path]] = [("video", v) for v in videos] + [("subtitle", s) for s in subtitles]
    items: list[dict] = []
    succeeded = 0
    failed = 0
    for idx, (kind, target_path) in enumerate(jobs, 1):
        print(f"[{idx}/{len(jobs)}] ({kind}) {target_path.name}", flush=True)
        try:
            res = run_video_pipeline(target_path, args) if kind == "video" else run_subtitle_job(target_path, args)
            items.append(res)
            if res.get("ok"):
                succeeded += 1
            else:
                failed += 1
        except Exception as error:
            failed += 1
            items.append({"path": str(target_path), "kind": kind, "ok": False, "error": str(error)})
            if not args.continue_on_error:
                break
    return {
        "folder": str(folder),
        "recursive": bool(args.recursive),
        "videos_discovered": len(videos),
        "subtitles_discovered": len(subtitles),
        "total_discovered": len(jobs),
        "processed": len(items),
        "succeeded": succeeded,
        "failed": failed,
        "cloud": False,
        "ok": failed == 0 and len(jobs) > 0,
        "items": items,
    }


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    args = build_batch_parser().parse_args(argv)
    summary = run_batch(args)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0 if summary["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
