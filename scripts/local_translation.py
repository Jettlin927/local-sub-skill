#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Translate SRT cues with a local Ollama model, preserving their timing."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request


PROMPT_VERSION = "hy-mt2-subtitles-v1"
LANGUAGES = {"ja": "日语", "en": "英语", "zh": "简体中文", "zh-Hant": "繁体中文", "auto": "原语言"}
KANA_RE = re.compile(r"[\u3041-\u3096\u30a1-\u30fa]")
HAN_RE = re.compile(r"[\u3400-\u9fff]")
INTERJECTION_STRIP_RE = re.compile(r"[\s、。，．!?！？…〜・「」『』（）()]")
INTERJECTION_CHAR_RE = re.compile(r"[\u3041-\u3096\u30a1-\u30fa\u30fc]")
TIMING_RE = re.compile(r"^\d{2,}:\d{2}:\d{2}[,.]\d{3}\s+-->\s+\d{2,}:\d{2}:\d{2}[,.]\d{3}(?:\s+.*)?$")
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


def validate_pair(source_path: Path | str, target_path: Path | str, max_kana_ratio: float = 0.03) -> list[str]:
    for label, path in (("source", source_path), ("target", target_path)):
        if not Path(path).is_file():
            return [f"missing {label} subtitle: {path}"]
    try:
        source, target = parse_srt(source_path), parse_srt(target_path)
    except ValueError as error:
        return [str(error)]
    return _validate_cues(source, target, max_kana_ratio)


def _validate_cues(source: list[Cue], target: list[Cue], max_kana_ratio: float) -> list[str]:
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


def _prompt(texts: list[str], source: str, target: str, numbered: bool) -> str:
    texts = [re.sub(r"(?:〜\s*){2,}", "〜", text) for text in texts]
    texts = [re.sub(r"(?:ぇ\s*){8,}", "ぇ", text) for text in texts]
    prefix = f"将以下{LANGUAGES.get(source, source)}电影对白翻译为{LANGUAGES.get(target, target)}。只输出译文，不要解释或添加内容。翻译要自然、简洁，保持原意。\n"
    if not numbered:
        return prefix + "\n" + texts[0]
    return prefix + "保留每条开头的编号 [1]、[2] 等，不要合并、遗漏或增加条目。每条译文另起一行。\n\n" + "\n".join(f"[{number}] {text.replace(chr(10), ' ')}" for number, text in enumerate(texts, 1))


def translate_srt(source_path: Path | str, target_path: Path | str, *, model: str = "hy-mt2-local", source: str = "ja", target: str = "zh", batch_size: int = 8, host: str = "http://localhost:11434", cache_path: Path | str | None = None, checkpoint_path: Path | str | None = None, retries: int = 2, num_ctx: int = 4096, keep_alive: str = "10m") -> dict:
    started = time.monotonic()
    source_path, target_path = Path(source_path), Path(target_path)
    if source_path.resolve() == target_path.resolve():
        raise ValueError("source and target paths must differ")
    if urllib.parse.urlparse(host).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("translation host must be local")
    if batch_size < 1 or retries < 0 or num_ctx < 1024:
        raise ValueError("batch_size must be positive, retries nonnegative and num_ctx >= 1024")
    cues = parse_srt(source_path)
    cache_path = Path(cache_path) if cache_path else source_path.parent / ".local_translation_cache.json"
    checkpoint_path = Path(checkpoint_path) if checkpoint_path else target_path.with_name(target_path.name + ".checkpoint.json")
    names = {model, model if ":" in model else model + ":latest"}
    entry = next((item for item in _request(host, "/api/tags").get("models", []) if item.get("name") in names or item.get("model") in names), None)
    if not entry or not entry.get("digest"):
        raise RuntimeError(f"local model is not installed or has no digest: {model}")
    identity = {"model": model, "digest": entry["digest"], "source": source, "target": target, "version": PROMPT_VERSION, "num_ctx": num_ctx, "batch_size": batch_size}
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
        payload = {"model": model, "prompt": _prompt(texts, source, target, numbered), "stream": False, "think": False, "keep_alive": keep_alive, "options": {"temperature": 0, "num_ctx": num_ctx, "num_predict": min(num_ctx // 2, max(256, 128 + sum(map(len, texts)) * 2))}}
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
    problems = _validate_cues(cues, translated, 0.03) if target.startswith("zh") else []
    if problems:
        raise RuntimeError("translation validation failed: " + "; ".join(problems))
    write_srt(target_path, translated)
    stats["elapsed"] = time.monotonic() - started
    stats["cache_path"], stats["checkpoint_path"] = str(cache_path), str(checkpoint_path)
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_srt", type=Path)
    parser.add_argument("target_srt", type=Path)
    parser.add_argument("--model", default="hy-mt2-local")
    parser.add_argument("--source", default="ja")
    parser.add_argument("--target", default="zh")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--host", default="http://localhost:11434")
    parser.add_argument("--cache-path", type=Path)
    parser.add_argument("--checkpoint-path", type=Path)
    parser.add_argument("--num-ctx", type=int, default=4096)
    parser.add_argument("--keep-alive", default="10m")
    parser.add_argument("--retries", type=int, default=2)
    args = parser.parse_args()
    options = vars(args).copy()
    source_path, target_path = options.pop("source_srt"), options.pop("target_srt")
    print(json.dumps(translate_srt(source_path, target_path, **options), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
