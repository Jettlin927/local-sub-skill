#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Environment health check for local-sub-skill across Windows, macOS, and Linux."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import urllib.error
import urllib.request

from run_single_video import (
    DEFAULT_ASR_MODEL,
    has_python_faster_whisper,
    resolve_default_fw_exe,
    resolve_ffmpeg_bin,
    resolve_yt_dlp_bin,
)


def _probe_ollama(host: str, model: str, timeout: float = 2.0) -> dict:
    url = host.rstrip("/") + "/api/tags"
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        return {"running": False, "model_ready": False, "models": [], "error": str(error)}

    models = [item.get("name", "") for item in payload.get("models", []) if isinstance(item, dict)]
    normalized = {name.split(":")[0] for name in models}
    target_base = model.split(":")[0]
    return {
        "running": True,
        "model_ready": model in models or target_base in normalized,
        "models": models,
        "error": None,
    }


def collect_diagnostics(
    host: str = "http://localhost:11434",
    model: str = "hy-mt2-local",
    asr_model_dir: Path | None = None,
    fw_exe: Path | None = None,
    ollama_probe=None,
) -> dict:
    """Collect cross-platform readiness checks for translation and video ASR."""
    resolved_model_dir = Path(asr_model_dir or DEFAULT_ASR_MODEL)
    resolved_fw_exe = Path(fw_exe) if fw_exe is not None else resolve_default_fw_exe()
    ollama_cli = shutil.which("ollama") is not None
    ollama = (ollama_probe or _probe_ollama)(host, model)
    fw_exe_found = resolved_fw_exe.exists() or shutil.which(str(resolved_fw_exe)) is not None
    py_fw = has_python_faster_whisper()
    asr_model_ready = (resolved_model_dir / "model.bin").is_file()
    ffmpeg_found = resolve_ffmpeg_bin("ffmpeg") is not None
    ffprobe_found = resolve_ffmpeg_bin("ffprobe") is not None
    yt_dlp_found = resolve_yt_dlp_bin() is not None

    next_actions: list[str] = []
    if not ollama_cli:
        next_actions.append("Install Ollama from https://ollama.com/")
    elif not ollama["running"]:
        next_actions.append("Start Ollama (`ollama serve` or launch the Ollama app)")
    elif not ollama["model_ready"]:
        next_actions.append("Run `npx local-sub-skill setup --skip-whisper` to create `hy-mt2-local`")

    if not fw_exe_found and not py_fw:
        if platform.system() == "Windows":
            next_actions.append("For video-to-SRT: install `faster-whisper-xxl` on PATH or `pip install faster-whisper`")
        else:
            next_actions.append("For video-to-SRT on macOS/Linux: run `pip install faster-whisper`")
    if not asr_model_ready and fw_exe_found:
        next_actions.append("Run `npx local-sub-skill setup` to download faster-whisper-large-v3-turbo")

    srt_ready = bool(ollama["running"] and ollama["model_ready"])
    asr_ready = bool((fw_exe_found and asr_model_ready) or py_fw)
    return {
        "ok": srt_ready,
        "srt_ready": srt_ready,
        "asr_ready": asr_ready,
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "checks": {
            "ollama_cli": ollama_cli,
            "ollama_running": ollama["running"],
            "ollama_host": host,
            "translation_model": model,
            "translation_model_ready": ollama["model_ready"],
            "ollama_models": ollama.get("models", []),
            "ollama_error": ollama.get("error"),
            "fw_exe": str(resolved_fw_exe),
            "fw_exe_found": fw_exe_found,
            "python_faster_whisper": py_fw,
            "asr_model_dir": str(resolved_model_dir),
            "asr_model_ready": asr_model_ready,
            "ffmpeg_found": ffmpeg_found,
            "ffprobe_found": ffprobe_found,
            "yt_dlp_found": yt_dlp_found,
        },
        "next_actions": next_actions,
    }


def format_report(report: dict) -> str:
    p = report["platform"]
    c = report["checks"]
    mark = lambda flag: "OK" if flag else "MISSING"
    lines = [
        f"local-sub-skill doctor ({p['system']} {p['machine']} | Python {p['python']})",
        f"  [Ollama CLI]             {mark(c['ollama_cli'])}",
        f"  [Ollama Server]          {mark(c['ollama_running'])} ({c['ollama_host']})",
        f"  [Model {c['translation_model']}]    {mark(c['translation_model_ready'])}",
        f"  [faster-whisper-xxl]     {mark(c['fw_exe_found'])} ({c['fw_exe']})",
        f"  [Python faster-whisper]  {mark(c['python_faster_whisper'])}",
        f"  [ASR model.bin]          {mark(c['asr_model_ready'])} ({c['asr_model_dir']})",
        f"  [ffmpeg / ffprobe]       {mark(c.get('ffmpeg_found') and c.get('ffprobe_found'))} (optional: --extract-subs / --mux / --burn)",
        f"  [yt-dlp]                 {mark(c.get('yt_dlp_found'))} (optional: YouTube / Bilibili / URL input)",
        f"  Ready for .srt -> .zh.srt: {'YES' if report['srt_ready'] else 'NO'}",
        f"  Ready for video -> .srt:   {'YES' if report['asr_ready'] else 'NO'}",
    ]
    if report["next_actions"]:
        lines.append("Next actions:")
        lines.extend(f"  - {action}" for action in report["next_actions"])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check local-sub-skill environment readiness.")
    parser.add_argument("--host", default=os.environ.get("OLLAMA_HOST", "http://localhost:11434"))
    parser.add_argument("--model", default="hy-mt2-local")
    parser.add_argument("--asr-model-dir", type=Path, default=DEFAULT_ASR_MODEL)
    parser.add_argument("--fw-exe", type=Path)
    parser.add_argument("--json", action="store_true", help="Emit JSON report.")
    parser.add_argument("--strict", action="store_true", help="Exit 1 if SRT translation prerequisites are missing.")
    args = parser.parse_args(argv)

    report = collect_diagnostics(
        host=args.host,
        model=args.model,
        asr_model_dir=args.asr_model_dir,
        fw_exe=args.fw_exe,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_report(report))
    if args.strict and not report["ok"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
