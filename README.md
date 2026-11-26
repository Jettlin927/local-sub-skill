# local-sub-skill

**100% Local Video → Chinese & Bilingual Subtitle (`.zh.srt` / `.bilingual.srt`) Generator & AI Agent Skill**
Powered by **Faster-Whisper (`large-v3-turbo`)** + **Ollama (`Tencent Hy-MT2-1.8B Q4_K_M`)**

> Zero cloud API fees · Zero privacy uploads · Runs on consumer GPUs (even 6–8 GB VRAM) · Supports **bilingual `.bilingual.srt` output** & **batch folder mode** · Works as both a standalone Python CLI and a ready-to-install Skill for **Claude Code, OpenAI Codex, Antigravity, Cursor & OpenClaw**.

---

## Why This Exists (为什么做这个)

Translating a 2-hour Japanese/English lecture, documentary, or film with cloud LLM APIs is slow, expensive, and sends local media transcripts to third-party servers.

`local-sub-skill` pairs two ultra-efficient local models with production-grade SRT reliability:
- **ASR**: `Faster-Whisper large-v3-turbo` (`int8_float16`, VAD-filtered, batched)
- **Local Translation**: `Tencent Hy-MT2-1.8B` (`Q4_K_M` via Ollama — ~1.1 GB weights, fast & accurate EN/JA → ZH)
- **Zero-Dependency Translation Core**: `scripts/local_translation.py` uses Python's standard library only — no LangChain, no bloated SDKs.
- **Production Guardrails**:
  - Exact SRT cue index & timestamp preservation (`atomic` file writes)
  - Automatic batch-splitting & single-cue fallback if the 1.8B model drops a cue ID
  - Crash-safe checkpoint resume & model-digest-aware translation cache
  - Automated quality validation (empty cue check, timing alignment, Japanese kana-residue ratio)
  - Automatic Ollama VRAM unload before running Whisper ASR so 8 GB GPUs never OOM!

---

## Quick Start (快速开始)

### Option A: One-Line `npx` Install (Recommended / 最推荐)

```bash
# Install the Skill into Claude Code, Codex & Antigravity in one command
npx local-sub-skill install
# (Or directly from GitHub before npm propagation:)
npx github:Jettlin927/local-sub-skill install

# Pull Ollama Hy-MT2-1.8B + Faster-Whisper large-v3-turbo weights
npx local-sub-skill setup

# Check Ollama, model weights & ASR readiness (Windows / macOS / Linux)
npx local-sub-skill doctor

# Launch the zero-dependency local Drag-and-Drop Web Studio (http://127.0.0.1:8765)
npx local-sub-skill gui

# Translate an existing .srt or YouTube WebVTT (.vtt) file offline (auto-derives .zh.srt, .bilingual.srt & styled .bilingual.ass)
npx local-sub-skill translate lecture.en.vtt --source en --target zh --bilingual --ass --glossary glossary.txt

# End-to-end local video -> Chinese .zh.srt + bilingual .bilingual.srt + styled .bilingual.ass
npx local-sub-skill run /path/to/video.mp4 --language ja --bilingual --ass

# Batch-process an entire folder of videos or standalone .srt/.vtt subtitles
npx local-sub-skill batch /path/to/videos --language ja --bilingual --ass --continue-on-error
```

### Option B: Zero-Terminal Windows Drag-and-Drop Bundle & macOS/Linux Notes
- **Windows Drag-and-Drop**: Run `npm run package:bundle` (or `python packaging/package_windows_bundle.py`) to build `dist/local-sub-desktop-win64-v1.4.0.zip` containing `DropVideoOrSrtHere.bat` (drag any `.mp4`/`.mkv`/`.srt`/`.vtt`/folder onto the icon to generate `.zh.srt`, `.bilingual.srt`, and styled `.bilingual.ass`) and `LaunchGUI.bat`.
- **macOS / Linux**: If `faster-whisper-xxl` is not installed, `run` and `batch` automatically fall back to Python `faster-whisper` (`pip install faster-whisper`) and default to CPU `int8` on macOS Apple Silicon / non-CUDA machines.

### Option C: Direct Python Usage

### 1. Prerequisites
- Python 3.10+
- [Ollama](https://ollama.com/) running locally (`http://localhost:11434`)
- *(Optional, for video-to-SRT)* `faster-whisper-xxl` executable on `PATH` (or pass `--fw-exe`)

### 2. One-Time Model Setup
```bash
python scripts/setup_local_models.py --asr-model-dir ./models/faster-whisper-large-v3-turbo
```
*(If you only want to translate existing `.srt` files, pass `--skip-whisper`.)*

### 3. Translate Any `.srt` or `.vtt` Subtitle File (Pure Offline)
```bash
# YouTube WebVTT (.vtt) or SRT -> Simplified Chinese + Bilingual .bilingual.srt
python scripts/local_translation.py lecture.en.vtt --source en --target zh --bilingual

# Japanese -> Simplified Chinese (source text on top, Chinese on bottom)
python scripts/local_translation.py film.ja.srt film.zh.srt --source ja --target zh --bilingual --bilingual-order source-first
```

### 4. End-to-End Video or Batch Folder → `.zh.srt` & `.bilingual.srt`
```bash
python scripts/run_single_video.py /path/to/video.mp4 --language ja --bilingual
python scripts/run_batch.py /path/to/lectures --language en --bilingual --recursive --continue-on-error
```
Outputs `video.ja.srt`, `video.zh.srt`, and optional `video.bilingual.srt` right next to `video.mp4` so **PotPlayer / VLC / mpv / IINA** auto-load the Chinese or bilingual subtitle immediately.

---

## Use as an AI Agent Skill (Claude Code / Codex / OpenClaw)

Drop this folder into your agent's skills directory (e.g., `~/.claude/skills/local-sub-skill` or `~/.codex/skills/local-sub-skill`), then simply ask your agent:
> *"Translate `~/Downloads/talk.mp4` into Chinese subtitles locally."*

---

## Run Unit Tests (No GPU / Model Required)

```bash
PYTHONPATH=scripts python -m unittest discover -s tests -v
```

## License

MIT — built by [@Jettlin927](https://github.com/Jettlin927).

> **Why go local?** Wondering how much running locally actually saves compared to cloud LLM APIs on multi-turn Claude Code / Codex workflows? Try **[AgentCostLab](https://jettlin927.github.io/agentcostlab/)** ([GitHub](https://github.com/Jettlin927/agentcostlab)) to simulate O(N²) agent loop costs & lint your `SKILL.md` / `CLAUDE.md` token overhead.

