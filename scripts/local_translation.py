#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Translate SRT cues with a local Ollama model, preserving their timing."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import hashlib
import html
import json
from pathlib import Path
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request


PROMPT_VERSION = "hy-mt2-subtitles-v1"
SOURCE_LANGUAGE_CHOICES = ("ja", "en", "ko", "fr", "de", "es", "ru", "pt", "auto")
TARGET_LANGUAGE_CHOICES = ("zh", "zh-Hant", "en", "ja", "ko", "fr", "de", "es", "ru", "pt")
LANGUAGES = {
    "ja": "日语",
    "en": "英语",
    "ko": "韩语",
    "fr": "法语",
    "de": "德语",
    "es": "西班牙语",
    "ru": "俄语",
    "pt": "葡萄牙语",
    "zh": "简体中文",
    "zh-Hant": "繁体中文",
    "auto": "原语言",
}
BILINGUAL_ORDERS = ("zh-first", "source-first")
DEFAULT_GLOSSARY_NAMES = (
    ".local-sub-glossary.json",
    ".local-sub-glossary.txt",
    "glossary.json",
    "glossary.txt",
)
KANA_RE = re.compile(r"[\u3041-\u3096\u30a1-\u30fa]")
HAN_RE = re.compile(r"[\u3400-\u9fff]")
INTERJECTION_STRIP_RE = re.compile(r"[\s、。，．!?！？…〜・「」『』（）()]")
INTERJECTION_CHAR_RE = re.compile(r"[\u3041-\u3096\u30a1-\u30fa\u30fc]")
TIMING_RE = re.compile(r"^\d{2,}:\d{2}:\d{2}[,.]\d{3}\s+-->\s+\d{2,}:\d{2}:\d{2}[,.]\d{3}(?:\s+.*)?$")
ASS_TIMING_CAPTURE_RE = re.compile(
    r"^(?P<sh>\d{2,}):(?P<sm>\d{2}):(?P<ss>\d{2})[,.](?P<sms>\d{3})\s+-->\s+(?P<eh>\d{2,}):(?P<em_>\d{2}):(?P<es>\d{2})[,.](?P<ems>\d{3})(?:\s+.*)?$"
)
VTT_TIMING_RE = re.compile(
    r"^(?P<start>(?:\d{2,}:)?\d{2}:\d{2}[.,]\d{3})\s+-->\s+(?P<end>(?:\d{2,}:)?\d{2}:\d{2}[.,]\d{3})(?:\s+.*)?$"
)
VTT_TAG_RE = re.compile(r"<[^>]+>")
ASS_TIME_RE = re.compile(r"^(\d+):(\d{2}):(\d{2})[.:](\d{2,3})$")
ASS_TAG_RE = re.compile(r"\{[^}]*\}")
ASS_DRAWING_RE = re.compile(r"\{[^}]*\\p[1-9]\b[^}]*\}")
SRT_MS_RE = re.compile(r"^(\d{2,}):(\d{2}):(\d{2})[,.](\d{3})$")
NUMBER_RE = re.compile(r"^\[(\d+)\]\s*(.*)$")


@dataclass
class Cue:
    index: str
    timing: str
    text: str


def parse_srt(path: Path | str) -> list[Cue]:
    text = Path(path).read_text(encoding="utf-8-sig").strip()
    cues = []
    for number, block in enumerate(re.split(r"\r?\n[ \t]*\r?\n", text), 1):
        lines = block.splitlines()
        if len(lines) < 3 or not lines[0].strip().isdigit() or not TIMING_RE.fullmatch(lines[1].strip()):
            raise ValueError(f"Malformed SRT block {number} in {path}")
        cue = Cue(lines[0].strip(), lines[1].strip(), "\n".join(lines[2:]).strip())
        if not cue.text:
            raise ValueError(f"Empty SRT text at block {number} in {path}")
        cues.append(cue)
    return cues


def _normalize_vtt_timestamp(timestamp: str) -> str:
    clean = timestamp.strip().replace(".", ",")
    if clean.count(":") == 1:
        clean = "00:" + clean
    return clean


def strip_vtt_tags(text: str) -> str:
    """Strip inline WebVTT tags (<c>, timestamps, <v>) and collapse rolling duplicates."""
    lines: list[str] = []
    for raw_line in text.splitlines():
        cleaned = html.unescape(VTT_TAG_RE.sub("", raw_line)).replace("\u00a0", " ").strip()
        if not cleaned:
            continue
        if not lines or lines[-1] != cleaned:
            lines.append(cleaned)
    return "\n".join(lines).strip()


def parse_vtt_text(text: str, source_name: str = "<vtt>") -> list[Cue]:
    """Parse WebVTT subtitle text into normalized SRT Cue objects."""
    stripped = text.lstrip("\ufeff").strip()
    if not stripped.startswith("WEBVTT"):
        raise ValueError(f"Malformed WebVTT header in {source_name}")
    blocks = re.split(r"\r?\n[ \t]*\r?\n", stripped)
    cues: list[Cue] = []
    for block in blocks[1:]:
        lines = [line.rstrip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        head = lines[0].strip()
        if head.startswith(("NOTE", "STYLE", "REGION")):
            continue
        timing_idx = 0
        match = VTT_TIMING_RE.fullmatch(head)
        if not match and len(lines) >= 2:
            match = VTT_TIMING_RE.fullmatch(lines[1].strip())
            timing_idx = 1
        if not match:
            continue
        body_lines = lines[timing_idx + 1 :]
        if not body_lines:
            continue
        body = strip_vtt_tags("\n".join(body_lines))
        if not body:
            continue
        timing = f"{_normalize_vtt_timestamp(match.group('start'))} --> {_normalize_vtt_timestamp(match.group('end'))}"
        cues.append(Cue(str(len(cues) + 1), timing, body))
    if not cues:
        raise ValueError(f"No usable cues found in WebVTT {source_name}")
    return cues


def _normalize_ass_timestamp(timestamp: str) -> str:
    match = ASS_TIME_RE.fullmatch(timestamp.strip())
    if not match:
        raise ValueError(f"Malformed ASS timestamp: {timestamp}")
    hours, minutes, seconds, frac = match.groups()
    ms = int(frac) * 10 if len(frac) == 2 else int(frac)
    return f"{int(hours):02d}:{int(minutes):02d}:{int(seconds):02d},{ms:03d}"


def strip_ass_tags(text: str) -> str:
    """Strip ASS/SSA override blocks (`{\\...}`) and normalize line breaks."""
    if ASS_DRAWING_RE.search(text):
        return ""
    cleaned = ASS_TAG_RE.sub("", text)
    cleaned = cleaned.replace(r"\N", "\n").replace(r"\n", "\n").replace(r"\h", " ")
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    return "\n".join(lines).strip()


def parse_ass_text(text: str, source_name: str = "<ass>") -> list[Cue]:
    """Parse ASS/SSA subtitle text into normalized SRT Cue objects."""
    stripped = text.lstrip("\ufeff")
    if not stripped.strip():
        raise ValueError(f"No usable cues found in ASS/SSA {source_name}")
    in_events = "[Events]" not in stripped
    start_idx, end_idx, text_idx, num_fields = 1, 2, 9, 10
    cues: list[Cue] = []
    for raw_line in stripped.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            in_events = line.lower() == "[events]"
            continue
        if not in_events:
            continue
        if line.lower().startswith("format:"):
            fields = [part.strip().lower() for part in line.split(":", 1)[1].split(",")]
            if "start" in fields and "end" in fields and "text" in fields:
                start_idx = fields.index("start")
                end_idx = fields.index("end")
                text_idx = fields.index("text")
                num_fields = len(fields)
            continue
        if not line.lower().startswith("dialogue:"):
            continue
        payload = line.split(":", 1)[1].lstrip()
        parts = payload.split(",", num_fields - 1)
        if len(parts) <= max(start_idx, end_idx, text_idx):
            continue
        body = strip_ass_tags(parts[text_idx])
        if not body:
            continue
        timing = f"{_normalize_ass_timestamp(parts[start_idx])} --> {_normalize_ass_timestamp(parts[end_idx])}"
        cues.append(Cue(str(len(cues) + 1), timing, body))
    if not cues:
        raise ValueError(f"No usable cues found in ASS/SSA {source_name}")
    return cues


def _parse_srt_ms(timestamp: str) -> int:
    match = SRT_MS_RE.fullmatch(timestamp.strip())
    if not match:
        raise ValueError(f"Malformed SRT timestamp: {timestamp}")
    h, m, s, ms = (int(group) for group in match.groups())
    return ((h * 60 + m) * 60 + s) * 1000 + ms


def _format_srt_ms(total_ms: int) -> str:
    clamped = max(0, int(total_ms))
    hours, rem = divmod(clamped, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{ms:03d}"


def shift_timing(timing: str, offset_ms: int) -> str:
    """Shift an SRT `start --> end` timing string by offset_ms, clamping at 0ms."""
    parts = [part.strip() for part in timing.split("-->", 1)]
    if len(parts) != 2:
        raise ValueError(f"Malformed SRT timing: {timing}")
    start_ms = _parse_srt_ms(parts[0])
    end_part = parts[1].split(None, 1)[0]
    end_ms = _parse_srt_ms(end_part)
    new_start = max(0, start_ms + offset_ms)
    min_end = new_start + (1 if end_ms > start_ms else 0)
    new_end = max(min_end, end_ms + offset_ms)
    return f"{_format_srt_ms(new_start)} --> {_format_srt_ms(new_end)}"


def shift_cues(cues: list[Cue], offset_seconds: float) -> list[Cue]:
    """Return cues with timestamps shifted by offset_seconds (positive=later, negative=earlier)."""
    offset_ms = round(float(offset_seconds) * 1000)
    if offset_ms == 0:
        return cues
    return [Cue(cue.index, shift_timing(cue.timing, offset_ms), cue.text) for cue in cues]


def parse_subtitles(path: Path | str) -> list[Cue]:
    """Parse an SRT, WebVTT (.vtt), or ASS/SSA (.ass/.ssa) subtitle file into Cue objects."""
    resolved = Path(path)
    suffix = resolved.suffix.lower()
    if suffix == ".vtt":
        return parse_vtt_text(resolved.read_text(encoding="utf-8-sig"), str(resolved))
    if suffix in {".ass", ".ssa"}:
        return parse_ass_text(resolved.read_text(encoding="utf-8-sig"), str(resolved))
    raw = resolved.read_text(encoding="utf-8-sig")
    leading = raw.lstrip()
    if leading.startswith("WEBVTT"):
        return parse_vtt_text(raw, str(resolved))
    if leading.startswith(("[Script Info]", "[Events]")):
        return parse_ass_text(raw, str(resolved))
    return parse_srt(resolved)


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(text)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def write_srt(path: Path | str, cues: list[Cue]) -> None:
    _atomic_text(Path(path), "\n\n".join(f"{cue.index}\n{cue.timing}\n{cue.text}" for cue in cues) + "\n")


def strip_subtitle_lang_suffix(stem: str) -> str:
    lower_stem = stem.lower()
    for suffix in (".zh-hant", ".zh", ".en", ".ja", ".ko", ".fr", ".de", ".es", ".ru", ".pt", ".source"):
        if lower_stem.endswith(suffix):
            return stem[: -len(suffix)]
    return stem


def derive_target_srt_path(source_path: Path | str, target: str = "zh") -> Path:
    """Derive a `.<target>.srt` sibling path from a source `.srt` or `.vtt` file."""
    path = Path(source_path)
    stem = strip_subtitle_lang_suffix(path.stem)
    return path.with_name(f"{stem}.{target}.srt")


def default_bilingual_path(target_path: Path | str) -> Path:
    """Derive a `.bilingual.srt` sibling path from a translated SRT path."""
    path = Path(target_path)
    stem = strip_subtitle_lang_suffix(path.stem)
    return path.with_name(f"{stem}.bilingual.srt")


def default_ass_path(target_path: Path | str) -> Path:
    """Derive a `.bilingual.ass` sibling path from a translated SRT path."""
    path = Path(target_path)
    stem = strip_subtitle_lang_suffix(path.stem)
    return path.with_name(f"{stem}.bilingual.ass")


def default_notes_path(target_path: Path | str) -> Path:
    """Derive a `.bilingual.md` reading-notes sibling path from a translated SRT path."""
    path = Path(target_path)
    stem = strip_subtitle_lang_suffix(path.stem)
    return path.with_name(f"{stem}.bilingual.md")


def parse_glossary_text(text: str) -> dict[str, str]:
    """Parse JSON object or line-based (`src=tgt`, `src=>tgt`, `src\\ttgt`) glossary text."""
    stripped = text.lstrip("\ufeff").strip()
    if not stripped:
        return {}
    if stripped.startswith("{"):
        parsed = json.loads(stripped)
        if not isinstance(parsed, dict):
            raise ValueError("JSON glossary must be an object mapping source term -> target term")
        return {str(k).strip(): str(v).strip() for k, v in parsed.items() if str(k).strip() and str(v).strip()}
    glossary: dict[str, str] = {}
    for line_no, raw in enumerate(stripped.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        for sep in ("=>", "\t", "="):
            if sep in line:
                src, tgt = line.split(sep, 1)
                src, tgt = src.strip(), tgt.strip()
                if not src or not tgt:
                    raise ValueError(f"Empty glossary key/value at line {line_no}")
                glossary[src] = tgt
                break
        else:
            raise ValueError(f"Invalid glossary syntax at line {line_no}: expected `source=target` or tab-separated pair")
    return glossary


def load_glossary(path: Path | str | None) -> dict[str, str]:
    """Load terminology overrides from a JSON or plain-text glossary file."""
    if path is None:
        return {}
    resolved = Path(path)
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    return parse_glossary_text(resolved.read_text(encoding="utf-8-sig"))


def discover_glossary_path(input_path: Path | str) -> Path | None:
    """Auto-discover a glossary file placed beside the input video or subtitle."""
    directory = Path(input_path).resolve().parent
    for name in DEFAULT_GLOSSARY_NAMES:
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


def _srt_timing_to_ass_pair(timing: str) -> tuple[str, str]:
    match = ASS_TIMING_CAPTURE_RE.fullmatch(timing.strip())
    if not match:
        raise ValueError(f"Unsupported SRT timing for ASS export: {timing}")
    sh = int(match.group("sh"))
    eh = int(match.group("eh"))
    scs = int(match.group("sms")) // 10
    ecs = int(match.group("ems")) // 10
    return (
        f"{sh}:{match.group('sm')}:{match.group('ss')}.{scs:02d}",
        f"{eh}:{match.group('em_')}:{match.group('es')}.{ecs:02d}",
    )


def _escape_ass_text(text: str) -> str:
    escaped = text.strip().replace("{", r"\{").replace("}", r"\}")
    return r"\N".join(line.strip() for line in escaped.splitlines() if line.strip())


def _aligned_pairs(source_cues: list[Cue], target_cues: list[Cue]) -> list[tuple[Cue, str, str]]:
    if len(source_cues) != len(target_cues):
        raise ValueError(f"cue count mismatch: source={len(source_cues)}, target={len(target_cues)}")
    pairs: list[tuple[Cue, str, str]] = []
    for number, (source_cue, target_cue) in enumerate(zip(source_cues, target_cues), 1):
        if source_cue.index != target_cue.index:
            raise ValueError(f"index mismatch at cue {number}")
        if source_cue.timing != target_cue.timing:
            raise ValueError(f"timing mismatch at cue {number}")
        pairs.append((target_cue, source_cue.text.strip(), target_cue.text.strip()))
    return pairs


def build_bilingual_cues(source_cues: list[Cue], target_cues: list[Cue], order: str = "zh-first") -> list[Cue]:
    """Pair source and translated cues into a single bilingual cue list."""
    if order not in BILINGUAL_ORDERS:
        raise ValueError(f"Unsupported bilingual order: {order}. Choose one of {BILINGUAL_ORDERS}.")
    bilingual: list[Cue] = []
    for target_cue, src_text, tgt_text in _aligned_pairs(source_cues, target_cues):
        if src_text == tgt_text:
            combined = tgt_text
        elif order == "zh-first":
            combined = f"{tgt_text}\n{src_text}"
        else:
            combined = f"{src_text}\n{tgt_text}"
        bilingual.append(Cue(target_cue.index, target_cue.timing, combined))
    return bilingual


def render_bilingual_ass(
    source_cues: list[Cue],
    target_cues: list[Cue],
    order: str = "zh-first",
    title: str = "local-sub-skill Bilingual Subtitles",
) -> str:
    """Render styled dual-language Advanced SubStation Alpha (.ass) text from aligned cues."""
    if order not in BILINGUAL_ORDERS:
        raise ValueError(f"Unsupported bilingual order: {order}. Choose one of {BILINGUAL_ORDERS}.")
    header = "\n".join(
        [
            "[Script Info]",
            f"Title: {title}",
            "ScriptType: v4.00+",
            "PlayResX: 1920",
            "PlayResY: 1080",
            "WrapStyle: 0",
            "ScaledBorderAndShadow: yes",
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
            "Style: PrimaryZh,Microsoft YaHei,58,&H00FFFFFF,&H000000FF,&H0012141C,&H64000000,-1,0,0,0,100,100,0,0,1,3,1.2,2,40,40,42,1",
            "Style: SecondarySrc,Arial,36,&H00D8E2F0,&H000000FF,&H0012141C,&H64000000,0,0,0,0,100,100,0,0,1,2.2,1.0,2,40,40,42,1",
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ]
    )
    events: list[str] = []
    for target_cue, src_text, tgt_text in _aligned_pairs(source_cues, target_cues):
        start, end = _srt_timing_to_ass_pair(target_cue.timing)
        zh_esc = _escape_ass_text(tgt_text)
        src_esc = _escape_ass_text(src_text)
        if src_text == tgt_text or not src_esc:
            body = zh_esc
        elif order == "zh-first":
            body = f"{zh_esc}\\N{{\\rSecondarySrc}}{src_esc}"
        else:
            body = f"{{\\rSecondarySrc}}{src_esc}\\N{{\\rPrimaryZh}}{zh_esc}"
        events.append(f"Dialogue: 0,{start},{end},PrimaryZh,,0,0,0,,{body}")
    return header + "\n" + "\n".join(events) + "\n"


def write_bilingual_srt(
    source_path: Path | str,
    target_path: Path | str,
    bilingual_path: Path | str | None = None,
    order: str = "zh-first",
) -> Path:
    """Write a bilingual `.bilingual.srt` from aligned source and translated subtitles."""
    resolved_out = Path(bilingual_path) if bilingual_path else default_bilingual_path(target_path)
    cues = build_bilingual_cues(parse_subtitles(source_path), parse_subtitles(target_path), order=order)
    write_srt(resolved_out, cues)
    return resolved_out


def write_bilingual_ass(
    source_path: Path | str,
    target_path: Path | str,
    ass_path: Path | str | None = None,
    order: str = "zh-first",
) -> Path:
    """Write a styled dual-language `.bilingual.ass` from aligned source and translated subtitles."""
    resolved_out = Path(ass_path) if ass_path else default_ass_path(target_path)
    ass_text = render_bilingual_ass(
        parse_subtitles(source_path),
        parse_subtitles(target_path),
        order=order,
        title=resolved_out.stem,
    )
    _atomic_text(resolved_out, ass_text)
    return resolved_out


def render_bilingual_markdown(
    source_cues: list[Cue],
    target_cues: list[Cue],
    order: str = "zh-first",
    title: str = "Bilingual Transcript",
) -> str:
    """Render timestamp-indexed bilingual Markdown reading notes from aligned cues."""
    if order not in BILINGUAL_ORDERS:
        raise ValueError(f"Unsupported bilingual order: {order}. Choose one of {BILINGUAL_ORDERS}.")
    lines = [f"# {title}", ""]
    for target_cue, src_text, tgt_text in _aligned_pairs(source_cues, target_cues):
        start_ts = target_cue.timing.split("-->", 1)[0].strip().split(",", 1)[0]
        src_flat = " ".join(part.strip() for part in src_text.splitlines() if part.strip())
        tgt_flat = " ".join(part.strip() for part in tgt_text.splitlines() if part.strip())
        if src_flat == tgt_flat or not src_flat:
            lines.append(f"- `{start_ts}` **{tgt_flat}**")
        elif order == "zh-first":
            lines.append(f"- `{start_ts}` **{tgt_flat}**  \n  {src_flat}")
        else:
            lines.append(f"- `{start_ts}` {src_flat}  \n  **{tgt_flat}**")
    return "\n".join(lines) + "\n"


def write_bilingual_markdown(
    source_path: Path | str,
    target_path: Path | str,
    notes_path: Path | str | None = None,
    order: str = "zh-first",
) -> Path:
    """Write bilingual `.bilingual.md` reading notes from aligned source and translated subtitles."""
    resolved_out = Path(notes_path) if notes_path else default_notes_path(target_path)
    md_text = render_bilingual_markdown(
        parse_subtitles(source_path),
        parse_subtitles(target_path),
        order=order,
        title=strip_subtitle_lang_suffix(resolved_out.stem),
    )
    _atomic_text(resolved_out, md_text)
    return resolved_out


def _write_bilingual_outputs(
    source_cues: list[Cue],
    target_cues: list[Cue],
    *,
    bilingual_path: Path | None,
    ass_path: Path | None,
    notes_path: Path | None,
    order: str,
) -> dict[str, str]:
    outputs: dict[str, str] = {}
    if bilingual_path is not None:
        write_srt(bilingual_path, build_bilingual_cues(source_cues, target_cues, order=order))
        outputs["bilingual_srt"] = str(bilingual_path)
    if ass_path is not None:
        _atomic_text(
            ass_path,
            render_bilingual_ass(source_cues, target_cues, order=order, title=ass_path.stem),
        )
        outputs["bilingual_ass"] = str(ass_path)
    if notes_path is not None:
        _atomic_text(
            notes_path,
            render_bilingual_markdown(
                source_cues, target_cues, order=order, title=strip_subtitle_lang_suffix(notes_path.stem)
            ),
        )
        outputs["bilingual_md"] = str(notes_path)
    return outputs


def materialize_bilingual_outputs(
    source_path: Path | str,
    target_path: Path | str,
    *,
    bilingual_path: Path | None = None,
    ass_path: Path | None = None,
    notes_path: Path | None = None,
    order: str = "zh-first",
    shift_seconds: float = 0.0,
) -> dict[str, str]:
    """Regenerate requested exports from the current target using aligned source cues."""
    if bilingual_path is None and ass_path is None and notes_path is None:
        return {}
    # Export filenames can be shared across targets, offsets, and line orderings.
    # Rebuild them without rerunning translation; file existence alone proves nothing.
    return _write_bilingual_outputs(
        shift_cues(parse_subtitles(source_path), shift_seconds),
        parse_subtitles(target_path),
        bilingual_path=bilingual_path,
        ass_path=ass_path,
        notes_path=notes_path,
        order=order,
    )


def validate_pair(
    source_path: Path | str,
    target_path: Path | str,
    max_kana_ratio: float = 0.03,
    *,
    check_cjk: bool = True,
    shift_seconds: float = 0.0,
) -> list[str]:
    for label, path in (("source", source_path), ("target", target_path)):
        if not Path(path).is_file():
            return [f"missing {label} subtitle: {path}"]
    try:
        source, target = parse_subtitles(source_path), parse_subtitles(target_path)
    except ValueError as error:
        return [str(error)]
    if shift_seconds:
        source = shift_cues(source, shift_seconds)
    return _validate_cues(source, target, max_kana_ratio, check_cjk=check_cjk)


def _validate_cues(
    source: list[Cue],
    target: list[Cue],
    max_kana_ratio: float,
    *,
    check_cjk: bool = True,
) -> list[str]:
    if len(source) != len(target):
        return [f"cue count mismatch: source={len(source)}, target={len(target)}"]
    problems = []
    for number, (original, translated) in enumerate(zip(source, target), 1):
        if original.index != translated.index:
            problems.append(f"index mismatch at cue {number}")
        if original.timing != translated.timing:
            problems.append(f"timing mismatch at cue {number}")
        if not translated.text.strip():
            problems.append(f"empty text at cue {number}")
    if check_cjk:
        text = "\n".join(cue.text for cue in target)
        han_count = len(HAN_RE.findall(text))
        if not han_count:
            problems.append("no CJK Han text")
        elif len(KANA_RE.findall(text)) / han_count > max_kana_ratio:
            problems.append("high kana residue")
    return problems


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, value: dict) -> None:
    _atomic_text(path, json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")


def _request(host: str, endpoint: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(host.rstrip("/") + endpoint, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=600) as response:
        value = json.load(response)
    if value.get("error"):
        raise RuntimeError(f"Ollama: {value['error']}")
    return value


def _check_translations(originals: list[str], results: list[str], target: str) -> None:
    if len(originals) != len(results):
        raise ValueError("translation count mismatch")
    for original, result in zip(originals, results):
        if not isinstance(result, str) or not result.strip() or "-->" in result:
            raise ValueError("empty or malformed translation")
        if target.startswith("zh"):
            acronym = re.fullmatch(r"[A-Z][A-Z0-9]{1,7}", original.strip())
            linguistic = KANA_RE.search(original) or re.search(r"[a-zA-Z]{2,}", original)
            tiktok = all(text.strip().rstrip("!?.,:;！？。，、：；").casefold() == "tiktok" for text in (original, result))
            if linguistic and not acronym and not tiktok and not HAN_RE.search(result):
                raise ValueError("translation contains no Chinese text")
            if KANA_RE.search(original) and result.strip() == original.strip():
                raise ValueError("untranslated Japanese text")
            kana = len(KANA_RE.findall(result))
            han = len(HAN_RE.findall(result))
            # Short jargon/loanword cues may keep a few kana (e.g. 出るクイ);
            # only a genuinely untranslated line is rejected.
            if kana > 6 or kana / max(1, han) > 0.5:
                raise ValueError("translation contains Japanese kana residue")


def _interjection_translation(text: str) -> str | None:
    """Map a repeated-kana vocalization cue to a short Chinese interjection.

    Small 1.8B models tend to echo repeated-kana cues until num_predict is exhausted,
    which aborts the whole batch. Mapping them deterministically keeps batches stable.
    """
    compact = re.sub(r"\s+", "", text)
    core = INTERJECTION_STRIP_RE.sub("", compact)
    if len(core) < 6 or len(core) > 200:
        return None
    if not all(INTERJECTION_CHAR_RE.fullmatch(ch) for ch in core):
        return None
    if max(Counter(core).values()) < 5:
        return None
    first = core[0]
    if first in "えぇ":
        return None
    if first in "うぅ":
        return "嗯……"
    if first in "おぉ":
        return "哦……"
    return "啊……"


def _laugh_translation(text: str) -> str | None:
    """Map Japanese net-slang laughter (w / wwww) to a Chinese laugh marker."""
    if re.fullmatch(r"[\swWｗＷ]{3,}", text):
        return "哈哈……"
    return None


def _numbered_response(response: str, count: int) -> list[str]:
    response = response.strip()
    if response.startswith("```") and response.endswith("```"):
        response = "\n".join(response.splitlines()[1:-1])
    results, ids = [], []
    for line in response.splitlines():
        if not line.strip():
            continue
        match = NUMBER_RE.fullmatch(line.strip())
        if match:
            ids.append(int(match[1]))
            results.append(match[2].strip())
        elif results:
            results[-1] += "\n" + line.strip()
        else:
            raise ValueError("unexpected text before first translation id")
    if ids != list(range(1, count + 1)):
        raise ValueError(f"translation ids mismatch: expected 1..{count}, got {ids}")
    return results


def _prompt(
    texts: list[str],
    source: str,
    target: str,
    numbered: bool,
    glossary: dict[str, str] | None = None,
) -> str:
    texts = [re.sub(r"(?:〜\s*){2,}", "〜", text) for text in texts]
    texts = [re.sub(r"(?:ぇ\s*){8,}", "ぇ", text) for text in texts]
    prefix = f"将以下{LANGUAGES.get(source, source)}电影对白翻译为{LANGUAGES.get(target, target)}。只输出译文，不要解释或添加内容。翻译要自然、简洁，保持原意。\n"
    if glossary:
        terms = "; ".join(f"{src}->{tgt}" for src, tgt in sorted(glossary.items()))
        prefix += f"术语表（专有名词严格按此译法）：{terms}\n"
    if not numbered:
        return prefix + "\n" + texts[0]
    return prefix + "保留每条开头的编号 [1]、[2] 等，不要合并、遗漏或增加条目。每条译文另起一行。\n\n" + "\n".join(f"[{number}] {text.replace(chr(10), ' ')}" for number, text in enumerate(texts, 1))


def _resolve_glossary(glossary: dict[str, str] | Path | str | None, source_path: Path) -> dict[str, str]:
    if glossary is None:
        discovered = discover_glossary_path(source_path)
        return load_glossary(discovered) if discovered else {}
    if isinstance(glossary, (str, Path)):
        return load_glossary(glossary)
    return {str(k).strip(): str(v).strip() for k, v in glossary.items() if str(k).strip() and str(v).strip()}


def translate_srt(source_path: Path | str, target_path: Path | str, *, model: str = "hy-mt2-local", source: str = "ja", target: str = "zh", batch_size: int = 8, host: str = "http://localhost:11434", cache_path: Path | str | None = None, checkpoint_path: Path | str | None = None, retries: int = 2, num_ctx: int = 4096, keep_alive: str = "10m", bilingual_path: Path | str | None = None, bilingual_order: str = "zh-first", ass_path: Path | str | None = None, notes_path: Path | str | None = None, glossary: dict[str, str] | Path | str | None = None, shift_seconds: float = 0.0) -> dict:
    started = time.monotonic()
    if bilingual_order not in BILINGUAL_ORDERS:
        raise ValueError(f"Unsupported bilingual order: {bilingual_order}. Choose one of {BILINGUAL_ORDERS}.")
    source_path, target_path = Path(source_path), Path(target_path)
    if source_path.resolve() == target_path.resolve():
        raise ValueError("source and target paths must differ")
    if urllib.parse.urlparse(host).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("translation host must be local")
    if batch_size < 1 or retries < 0 or num_ctx < 1024:
        raise ValueError("batch_size must be positive, retries nonnegative and num_ctx >= 1024")
    resolved_glossary = _resolve_glossary(glossary, source_path)
    cues = shift_cues(parse_subtitles(source_path), shift_seconds)
    cache_path = Path(cache_path) if cache_path else source_path.parent / ".local_translation_cache.json"
    checkpoint_path = Path(checkpoint_path) if checkpoint_path else target_path.with_name(target_path.name + ".checkpoint.json")
    names = {model, model if ":" in model else model + ":latest"}
    entry = next((item for item in _request(host, "/api/tags").get("models", []) if item.get("name") in names or item.get("model") in names), None)
    if not entry or not entry.get("digest"):
        raise RuntimeError(f"local model is not installed or has no digest: {model}")
    identity = {"model": model, "digest": entry["digest"], "source": source, "target": target, "version": PROMPT_VERSION, "num_ctx": num_ctx, "batch_size": batch_size}
    if resolved_glossary:
        identity["glossary"] = sorted(resolved_glossary.items())
    if shift_seconds:
        identity["shift_seconds"] = round(float(shift_seconds), 3)
    fingerprint = _hash({"identity": identity, "cues": [vars(cue) for cue in cues]})
    checkpoint = _read_json(checkpoint_path)
    completed = checkpoint.get("completed", {}) if checkpoint.get("fingerprint") == fingerprint else {}
    completed = {str(i): value for i in range(len(cues)) if isinstance(value := completed.get(str(i)), str)}
    for i in list(completed):
        try:
            _check_translations([cues[int(i)].text], [completed[i]], target)
        except ValueError:
            del completed[i]
    cache = _read_json(cache_path)
    entries = cache.get("entries", {}) if cache.get("version") == PROMPT_VERSION else {}
    stats = {"model": model, "model_digest": entry["digest"], "cue_count": len(cues), "request_count": 0, "cache_hits": 0, "resumed_cues": len(completed), "passthrough_cues": 0, "split_batches": 0, "fallback_requests": 0, "prompt_eval_count": 0, "eval_count": 0, "prompt_eval_cached_count": None, "prompt_cache_metrics_supported": False, "ollama_load_duration": 0.0, "ollama_prompt_eval_duration": 0.0, "ollama_eval_duration": 0.0, "ollama_total_duration": 0.0}

    def save_progress() -> None:
        _write_json(checkpoint_path, {"fingerprint": fingerprint, "identity": identity, "completed": completed, "complete": len(completed) == len(cues)})

    for i, cue in enumerate(cues):
        if not any(character.isalnum() and character != "ー" for character in cue.text):
            completed[str(i)] = cue.text
            stats["passthrough_cues"] += 1
            continue
        laugh = _laugh_translation(cue.text)
        if laugh is not None:
            completed[str(i)] = laugh
            stats["laugh_cues"] = stats.get("laugh_cues", 0) + 1
            continue
        interjection = _interjection_translation(cue.text)
        if interjection is not None:
            completed[str(i)] = interjection
            stats["interjection_cues"] = stats.get("interjection_cues", 0) + 1
    if stats["passthrough_cues"] or stats.get("interjection_cues"):
        save_progress()

    def generate(payload: dict) -> dict:
        for attempt in range(retries + 1):
            stats["request_count"] += 1
            try:
                response = _request(host, "/api/generate", payload)
                break
            except (urllib.error.URLError, TimeoutError):
                if attempt == retries:
                    raise
                time.sleep(min(2 ** attempt, 4))
        for key in ("prompt_eval_count", "eval_count"):
            stats[key] += response.get(key, 0)
        if "prompt_eval_cached_count" in response:
            stats["prompt_cache_metrics_supported"] = True
            stats["prompt_eval_cached_count"] = (stats["prompt_eval_cached_count"] or 0) + response["prompt_eval_cached_count"]
        for key in ("load_duration", "prompt_eval_duration", "eval_duration", "total_duration"):
            stats["ollama_" + key] += response.get(key, 0) / 1_000_000_000
        return response

    def translate_indexes(indexes: list[int], numbered: bool = True) -> None:
        texts = [cues[i].text for i in indexes]
        if len(indexes) > 1 and sum(map(len, texts)) > num_ctx // 3:
            split(indexes)
            return
        if sum(map(len, texts)) > num_ctx // 2:
            raise ValueError(f"subtitle cue {cues[indexes[0]].index} is too long for num_ctx={num_ctx}")
        payload = {"model": model, "prompt": _prompt(texts, source, target, numbered, glossary=resolved_glossary), "stream": False, "think": False, "keep_alive": keep_alive, "options": {"temperature": 0, "num_ctx": num_ctx, "num_predict": min(num_ctx // 2, max(256, 128 + sum(map(len, texts)) * 2))}}
        key = _hash({"identity": identity, "payload": payload})
        results = entries.get(key)
        if results is not None:
            try:
                _check_translations(texts, results, target)
            except (ValueError, TypeError):
                results = None
        if results is None:
            response = generate(payload)
            try:
                if response.get("done_reason") == "length" or response.get("done") is not True:
                    raise ValueError("model output was truncated")
                results = _numbered_response(response.get("response", ""), len(indexes)) if numbered else [response.get("response", "").strip()]
                _check_translations(texts, results, target)
            except ValueError as error:
                if len(indexes) > 1:
                    split(indexes)
                    entries[key] = [completed[str(index)] for index in indexes]
                    _write_json(cache_path, {"version": PROMPT_VERSION, "entries": entries})
                    return
                if numbered:
                    stats["fallback_requests"] += 1
                    translate_indexes(indexes, numbered=False)
                    entries[key] = [completed[str(index)] for index in indexes]
                    _write_json(cache_path, {"version": PROMPT_VERSION, "entries": entries})
                    return
                raise RuntimeError(f"translation failed at cue {cues[indexes[0]].index}: {error}") from error
            entries[key] = results
            _write_json(cache_path, {"version": PROMPT_VERSION, "entries": entries})
        else:
            stats["cache_hits"] += len(indexes)
        for index, result in zip(indexes, results):
            completed[str(index)] = result.strip()
        save_progress()

    def split(indexes: list[int]) -> None:
        stats["split_batches"] += 1
        midpoint = len(indexes) // 2
        translate_indexes(indexes[:midpoint])
        translate_indexes(indexes[midpoint:])

    for start in range(0, len(cues), batch_size):
        pending = [i for i in range(start, min(start + batch_size, len(cues))) if str(i) not in completed]
        if pending:
            translate_indexes(pending)
        print(f"translated {len(completed)}/{len(cues)} cues", flush=True)
    translated = [Cue(cue.index, cue.timing, completed[str(i)]) for i, cue in enumerate(cues)]
    problems = _validate_cues(cues, translated, 0.03, check_cjk=target.startswith("zh"))
    if problems:
        raise RuntimeError("translation validation failed: " + "; ".join(problems))
    write_srt(target_path, translated)
    stats.update(
        _write_bilingual_outputs(
            cues,
            translated,
            bilingual_path=Path(bilingual_path) if bilingual_path is not None else None,
            ass_path=Path(ass_path) if ass_path is not None else None,
            notes_path=Path(notes_path) if notes_path is not None else None,
            order=bilingual_order,
        )
    )
    if resolved_glossary:
        stats["glossary_count"] = len(resolved_glossary)
    if shift_seconds:
        stats["shift_seconds"] = round(float(shift_seconds), 3)
    stats["elapsed"] = time.monotonic() - started
    stats["cache_path"], stats["checkpoint_path"] = str(cache_path), str(checkpoint_path)
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_srt", type=Path, help="Input .srt, .vtt, or .ass/.ssa subtitle file.")
    parser.add_argument("target_srt", nargs="?", type=Path, help="Optional output .zh.srt path (auto-derived if omitted).")
    parser.add_argument("--model", default="hy-mt2-local")
    parser.add_argument("--source", choices=SOURCE_LANGUAGE_CHOICES, default="ja")
    parser.add_argument("--target", choices=TARGET_LANGUAGE_CHOICES, default="zh")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--host", default="http://localhost:11434")
    parser.add_argument("--cache-path", type=Path)
    parser.add_argument("--checkpoint-path", type=Path)
    parser.add_argument("--num-ctx", type=int, default=4096)
    parser.add_argument("--keep-alive", default="10m")
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--bilingual", action="store_true", help="Also emit a .bilingual.srt alongside target_srt.")
    parser.add_argument("--bilingual-path", type=Path, help="Explicit output path for bilingual SRT.")
    parser.add_argument("--bilingual-order", choices=BILINGUAL_ORDERS, default="zh-first")
    parser.add_argument("--ass", action="store_true", help="Also emit a styled dual-language .bilingual.ass subtitle.")
    parser.add_argument("--ass-path", type=Path, help="Explicit output path for styled bilingual ASS.")
    parser.add_argument("--notes", action="store_true", help="Also emit bilingual Markdown reading notes (.bilingual.md).")
    parser.add_argument("--notes-path", type=Path, help="Explicit output path for bilingual Markdown notes.")
    parser.add_argument("--glossary", type=Path, help="Optional JSON or `source=target` text glossary file.")
    parser.add_argument("--shift", dest="shift_seconds", type=float, default=0.0, help="Shift all subtitle timestamps by seconds (e.g. 1.5 or -0.8).")
    args = parser.parse_args()
    options = vars(args).copy()
    source_path = options.pop("source_srt")
    target_path = options.pop("target_srt") or derive_target_srt_path(source_path, target=options["target"])
    emit_bilingual = options.pop("bilingual")
    explicit_bilingual = options.pop("bilingual_path")
    if explicit_bilingual is not None:
        options["bilingual_path"] = explicit_bilingual
    elif emit_bilingual:
        options["bilingual_path"] = default_bilingual_path(target_path)
    emit_ass = options.pop("ass")
    explicit_ass = options.pop("ass_path")
    if explicit_ass is not None:
        options["ass_path"] = explicit_ass
    elif emit_ass:
        options["ass_path"] = default_ass_path(target_path)
    emit_notes = options.pop("notes")
    explicit_notes = options.pop("notes_path")
    if explicit_notes is not None:
        options["notes_path"] = explicit_notes
    elif emit_notes:
        options["notes_path"] = default_notes_path(target_path)
    print(json.dumps(translate_srt(source_path, target_path, **options), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
