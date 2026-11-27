---
name: local-sub-skill
description: '100% local video-to-Chinese subtitle generator and SRT translator powered by Faster-Whisper (large-v3-turbo) and Ollama (Tencent Hy-MT2-1.8B). Zero cloud API keys, zero data upload, with exact SRT timing preservation, checkpoint resume, and kana-residue quality checks.'
---

# Local Sub Skill (100% Offline Video → Chinese Subtitle Pipeline)

Use this skill whenever the user wants to generate or translate Chinese subtitles (`.zh.srt`) for local Japanese or English videos (lectures, documentaries, podcasts, films) completely offline on their own machine.

## One-Time Model Setup

```bash
python scripts/setup_local_models.py --asr-model-dir ./models/faster-whisper-large-v3-turbo
```

This pulls `hf.co/tencent/Hy-MT2-1.8B-GGUF:Q4_K_M`, creates the verified `hy-mt2-local` Ollama model using `scripts/hy-mt2-local.Modelfile`, and downloads `faster-whisper-large-v3-turbo`.

## Workflow 1: Translate an Existing `.srt`, WebVTT (`.vtt`), or ASS/SSA (`.ass`/`.ssa`) File (Zero GPU / Ollama Only)

```bash
python scripts/local_translation.py path/to/lecture.en.vtt --source en --target zh --shift 1.5 --bilingual --ass --notes --glossary glossary.txt
```

- Supports `.srt`, YouTube/yt-dlp WebVTT (`.vtt`, stripping `<c>` / inline timestamp tags and normalizing `MM:SS.mmm` timestamps), and ASS/SSA (`.ass`/`.ssa`, stripping `{...}` override blocks and vector drawings) across `ja`, `en`, `ko`, `fr`, `de`, `es`, `ru`, `pt`, and `auto` -> `zh`, `zh-Hant`, `en`, `ja`, `ko`, `fr`, `de`, `es`, `ru`, or `pt`, plus `--shift <seconds>` for timestamp synchronization.
- Supports custom terminology overrides via `--glossary` (`.json` or `source=target` text) or auto-discovered `.local-sub-glossary.txt` / `glossary.txt`, hashed into cache identity.
- Automatically splits batches and falls back to single-cue translation if the 1.8B model drops an ID.
- Persists model-digest-keyed translation caches and crash-safe checkpoints for instant resume.
- Validates cue counts, non-empty lines, and low Japanese kana residue (`--max-kana-ratio`).
- Pass `--bilingual`, `--ass` (styled `.bilingual.ass` dual-color PotPlayer/VLC/mpv subtitles), and `--notes` (`.bilingual.md` clean reading transcript for Obsidian/Notion) alongside `--bilingual-order zh-first|source-first`.

## Workflow 2: End-to-End Video, Audio, or URL → Source `.srt` → Translated `.srt`

```bash
python scripts/run_single_video.py path/to/movie.mp4 --language ja --target zh --extract-subs --bilingual --ass --mux
python scripts/run_single_video.py path/to/clip.mp4 --language ja --target en --bilingual --ass --burn
python scripts/run_single_video.py path/to/podcast.mp3 --language en --bilingual --notes
python scripts/run_single_video.py "https://www.youtube.com/watch?v=..." --language en --extract-subs --bilingual --notes
```

Supports local video files, audio/podcast files (`.mp3`, `.m4a`, `.wav`, `.flac`, `.ogg`, `.opus`, `.aac`, `.wma`), and media URLs via `yt-dlp` (`--extract-subs` grabs existing YouTube/Bilibili captions without downloading video when `--mux`/`--burn` are off). Pass `--mux` for soft-muxed `.subbed.mkv` or `--burn` for hard-burned `.burned.mp4` (mobile/social-ready). Use `--language ja|en|ko|fr|de|es|ru|pt|auto` and `--target zh|zh-Hant|en|ja|ko|fr|de|es|ru|pt`. Add `--dry-run` to preview output paths without executing models.

## Workflow 3: Batch Process a Folder of Videos, Audio, or `.srt` / `.vtt` Subtitles

```bash
python scripts/run_batch.py path/to/series_folder --language ja --bilingual --continue-on-error
```

Scans video (`.mp4`, `.mkv`, `.mov`, `.avi`, `.webm`, `.m4v`, `.ts`, `.flv`) and audio (`.mp3`, `.m4a`, `.wav`, `.flac`, `.ogg`, `.opus`, `.aac`, `.wma`) files plus standalone `.srt` / `.vtt` subtitle files (use `--subtitles-only` to translate subtitles only, or `--recursive` for nested episodes), automatically reusing valid existing `.srt` / `.zh.srt` files and generating `.bilingual.srt` when requested.

## Workflow 4: Environment Check & Drag-and-Drop Web Studio

```bash
python scripts/doctor.py
python scripts/gui.py --port 8765
```

Runs cross-platform checks (Windows/macOS/Linux; auto-falls back to Python `faster-whisper` when `faster-whisper-xxl` is absent) or starts a zero-dependency local Drag-and-Drop studio at `http://127.0.0.1:8765`.

## Guardrails

- Never overwrite an existing valid `.zh.srt` unless `--force` or `--force-translate` is explicitly passed.
- Always keep source and translated subtitles aligned to the exact video basename (`movie.mp4` -> `movie.zh.srt`) so players like PotPlayer, VLC, IINA, and mpv auto-load them automatically.
