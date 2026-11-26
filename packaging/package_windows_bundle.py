#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build the portable Windows Drag-and-Drop Desktop Bundle zip (zero dependencies)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import zipfile


REPO_ROOT = Path(__file__).resolve().parent.parent


def build_bundle(output_dir: Path | None = None) -> Path:
    pkg = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
    version = pkg["version"]
    dest_dir = (output_dir or (REPO_ROOT / "dist")).resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)
    zip_path = dest_dir / f"local-sub-desktop-win64-v{version}.zip"

    entries: list[tuple[Path, str]] = [
        (REPO_ROOT / "packaging" / "windows" / "DropVideoOrSrtHere.bat", "DropVideoOrSrtHere.bat"),
        (REPO_ROOT / "packaging" / "windows" / "LaunchGUI.bat", "LaunchGUI.bat"),
        (REPO_ROOT / "packaging" / "windows" / "BUNDLE_GUIDE_ZH.md", "README_ZH.md"),
        (REPO_ROOT / "LICENSE", "LICENSE"),
        (REPO_ROOT / "SKILL.md", "SKILL.md"),
    ]
    scripts_dir = REPO_ROOT / "scripts"
    for item in sorted(scripts_dir.iterdir()):
        if item.is_file() and item.suffix in {".py", ".Modelfile"}:
            entries.append((item, f"scripts/{item.name}"))

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for src, arcname in entries:
            archive.write(src, arcname=arcname)
    return zip_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Package the Windows drag-and-drop desktop bundle zip.")
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / "dist")
    args = parser.parse_args()
    out = build_bundle(args.output_dir)
    print(f"Built portable bundle: {out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
