"""
caption_units.py — Build SRT segments from caption_units.json + word-level SRT.

The Volcengine ATA step gives word/character timestamps. This module keeps that
timing source, but uses the pre-production caption unit list as the semantic SRT
segmentation contract.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .convert import PUNCT_CHARS, read_srt, write_srt
from .segment import fix_overlaps, parse_time


# 与 strip-punct 层共用同一份标点真相源——两边口径分叉过一次（全角引号只漏在这边），
# 就会让 punctuation_insensitive 兜底对着「剥离层认、匹配层不认」的字符失效。
_PUNCT = set(PUNCT_CHARS)


def _normalize_text(text: str, *, ignore_punct: bool = False) -> str:
    out: list[str] = []
    for ch in str(text):
        if ch.isspace():
            continue
        if ignore_punct and ch in _PUNCT:
            continue
        out.append(ch)
    return "".join(out)


def _load_caption_units(path: str | Path) -> list[str]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"caption_units.json must be a list: {path}")
    units: list[str] = []
    for expected, row in enumerate(data):
        if not isinstance(row, dict):
            raise ValueError(f"caption unit row {expected} must be an object")
        index = int(row.get("index", expected))
        if index != expected:
            raise ValueError(f"caption unit index mismatch at row {expected}: {index}")
        text = str(row.get("text") or "").strip()
        if not text:
            raise ValueError(f"caption unit row {expected} has empty text")
        units.append(text)
    return units


def _match_caption_units(words: list[dict[str, Any]], units: list[str], *, ignore_punct: bool) -> list[dict[str, Any]]:
    pointer = 0
    segments: list[dict[str, Any]] = []
    for unit_index, unit_text in enumerate(units):
        target = _normalize_text(unit_text, ignore_punct=ignore_punct)
        if not target:
            raise ValueError(f"caption unit {unit_index} is empty after normalization")
        start = pointer
        acc = ""
        while pointer < len(words):
            token = _normalize_text(words[pointer]["text"], ignore_punct=ignore_punct)
            candidate = acc + token
            # 只接受 target 的前缀。旧实现还接受 candidate.startswith(target)，
            # 一旦某个 word 跨越 unit 边界就会一路贪婪吞词，把一个局部越界
            # 放大成几百字后才暴露的失配（诊断指向错误位置，且有静默错配风险）。
            if target.startswith(candidate):
                acc = candidate
                pointer += 1
                if acc == target:
                    break
                continue
            break
        if acc != target:
            preview = "".join(str(w.get("text", "")) for w in words[start:min(len(words), start + 8)])
            raise ValueError(
                f"caption unit {unit_index} did not match word timeline "
                f"(target={unit_text!r}, accumulated={acc!r}, next_words={preview!r})"
            )
        segment_words = words[start:pointer]
        if not segment_words:
            raise ValueError(f"caption unit {unit_index} matched no words")
        segments.append({
            "start": segment_words[0]["start"],
            "end": segment_words[-1]["end"],
            "text": unit_text,
            "unit_index": unit_index,
        })
    leftovers = "".join(_normalize_text(w["text"], ignore_punct=ignore_punct) for w in words[pointer:])
    if leftovers:
        raise ValueError(f"word timeline has unmatched trailing text: {leftovers[:80]!r}")
    return segments


def _segments_with_float_times(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for seg in segments:
        out.append({
            **seg,
            "start_f": parse_time(seg["start"]),
            "end_f": parse_time(seg["end"]),
        })
    return out


def caption_units_to_srt(
    word_level_srt: str | Path,
    caption_units_json: str | Path,
    output_srt: str | Path,
    report_path: str | Path | None = None,
    *,
    max_chars: int = 24,
    allow_punctuation_fallback: bool = True,
) -> dict[str, Any]:
    """Aggregate word-level ATA timestamps into caption-unit SRT segments.

    Returns a report dict. Raises ValueError when the caption units cannot be
    matched to the word timeline; callers may then fall back to smart segmentation.
    """
    words = read_srt(str(word_level_srt))
    if not words:
        raise ValueError(f"word-level SRT has no entries: {word_level_srt}")
    units = _load_caption_units(caption_units_json)

    mode = "exact"
    try:
        segments = _match_caption_units(words, units, ignore_punct=False)
    except ValueError as exact_error:
        if not allow_punctuation_fallback:
            raise
        mode = "punctuation_insensitive"
        try:
            segments = _match_caption_units(words, units, ignore_punct=True)
        except ValueError as fallback_error:
            raise ValueError(
                f"caption unit match failed; exact={exact_error}; "
                f"punctuation_insensitive={fallback_error}"
            ) from fallback_error

    segments = fix_overlaps(_segments_with_float_times(segments))
    oversized = [
        {"index": i + 1, "text": seg["text"], "length": len(seg["text"])}
        for i, seg in enumerate(segments)
        if len(seg["text"]) > max_chars + 2
    ]
    entries = [{"start": seg["start"], "end": seg["end"], "text": seg["text"]} for seg in segments]
    write_srt(entries, str(output_srt))

    report = {
        "mode": "caption_units",
        "match_mode": mode,
        "word_level_srt": str(word_level_srt),
        "caption_units_json": str(caption_units_json),
        "output": str(output_srt),
        "total_segments": len(segments),
        "max_chars": max_chars,
        "oversized_final": oversized,
        "oversized": len(oversized),
    }
    rpt = Path(report_path) if report_path else Path(output_srt).with_suffix(".caption_units.json")
    rpt.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    report["report"] = str(rpt)
    print(f"✅ caption_units 聚合: {len(segments)} 段 → {output_srt}")
    print(f"   匹配模式: {mode}")
    print(f"   报告 → {rpt}")
    return report

