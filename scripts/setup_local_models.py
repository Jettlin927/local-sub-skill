#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Download local subtitle weights and create the verified HunYuan MT2 Ollama model."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys


DEFAULT_MODEL_DIR = Path.cwd() / "models" / "faster-whisper-large-v3-turbo"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pull Ollama Hy-MT2-1.8B Q4_K_M and Faster-Whisper large-v3-turbo weights."
    )
    parser.add_argument(
        "--asr-model-dir",
        type=Path,
        default=DEFAULT_MODEL_DIR,
        help="Output directory for faster-whisper-large-v3-turbo weights.",
    )
    parser.add_argument(
        "--skip-whisper",
        action="store_true",
        help="Only set up the Ollama Hy-MT2-1.8B translation model.",
    )
    args = parser.parse_args()

    subprocess.run(["ollama", "pull", "hf.co/tencent/Hy-MT2-1.8B-GGUF:Q4_K_M"], check=True)
    subprocess.run(
        ["ollama", "create", "hy-mt2-local", "-f", str(Path(__file__).with_name("hy-mt2-local.Modelfile"))],
        check=True,
    )
    if not args.skip_whisper:
        try:
            from faster_whisper.utils import download_model
        except ImportError as error:
            raise SystemExit(
                "faster-whisper Python package is required to download Whisper weights. "
                "Install it with `pip install faster-whisper` or rerun with `--skip-whisper`."
            ) from error
        args.asr_model_dir.mkdir(parents=True, exist_ok=True)
        download_model("turbo", output_dir=str(args.asr_model_dir))
    print(f"Local subtitle models are ready: hy-mt2-local (Ollama) and {args.asr_model_dir}.")


if __name__ == "__main__":
    main()
