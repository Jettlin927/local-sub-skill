# local-sub-skill

**100% Local Video → Chinese Subtitle (`.zh.srt`) Generator & AI Agent Skill**
Powered by **Faster-Whisper (`large-v3-turbo`)** + **Ollama (`Tencent Hy-MT2-1.8B Q4_K_M`)**

> Zero cloud API fees · Zero privacy uploads · Runs on consumer GPUs (even 6–8 GB VRAM) · Works as both a standalone Python CLI and a ready-to-install Skill for **Claude Code, OpenAI Codex, Antigravity, Cursor & OpenClaw**.

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

# Translate an existing SRT file offline
npx local-sub-skill translate lecture.en.srt lecture.zh.srt --source en --target zh

# End-to-end local video -> Chinese .zh.srt
npx local-sub-skill run /path/to/video.mp4 --language ja
```

### Option B: Direct Python Usage

### 1. Prerequisites
- Python 3.10+
- [Ollama](https://ollama.com/) running locally (`http://localhost:11434`)
- *(Optional, for video-to-SRT)* `faster-whisper-xxl` executable on `PATH` (or pass `--fw-exe`)

### 2. One-Time Model Setup
```bash
python scripts/setup_local_models.py --asr-model-dir ./models/faster-whisper-large-v3-turbo
```
*(If you only want to translate existing `.srt` files, pass `--skip-whisper`.)*

### 3. Translate Any `.srt` Subtitle File (Pure Offline)
```bash
# English -> Simplified Chinese
python scripts/local_translation.py lecture.en.srt lecture.zh.srt --source en --target zh

# Japanese -> Simplified Chinese
python scripts/local_translation.py film.ja.srt film.zh.srt --source ja --target zh
```

### 4. End-to-End Video → `.zh.srt`
```bash
python scripts/run_single_video.py /path/to/video.mp4 --language ja
```
Outputs `video.ja.srt` and `video.zh.srt` right next to `video.mp4` so **PotPlayer / VLC / mpv / IINA** auto-load the Chinese subtitle immediately.

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

