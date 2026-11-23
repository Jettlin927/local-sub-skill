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

## Workflow 1: Translate an Existing `.srt` File (Zero GPU / Ollama Only)

```bash
python scripts/local_translation.py path/to/lecture.en.srt path/to/lecture.zh.srt --source en --target zh
```

- Preserves exact SRT indices and timestamps (`00:00:01,234 --> 00:00:03,000`).
- Automatically splits batches and falls back to single-cue translation if the 1.8B model drops an ID.
- Persists model-digest-keyed translation caches and crash-safe checkpoints for instant resume.
- Validates cue counts, non-empty lines, and low Japanese kana residue (`--max-kana-ratio`).

## Workflow 2: End-to-End Video → Source `.srt` → Chinese `.zh.srt`

```bash
python scripts/run_single_video.py path/to/movie.mp4 --language ja
```

Use `--language en` for English or `--language auto` when unknown. Add `--dry-run` to preview output paths without executing models.

## Guardrails

- Never overwrite an existing valid `.zh.srt` unless `--force` or `--force-translate` is explicitly passed.
- Always keep source and translated subtitles aligned to the exact video basename (`movie.mp4` -> `movie.zh.srt`) so players like PotPlayer, VLC, IINA, and mpv auto-load them automatically.
