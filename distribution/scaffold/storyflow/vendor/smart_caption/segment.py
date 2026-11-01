"""
segment.py — 中文字幕智能分割（Pass1 标点 + Pass2 递归居中）。

移植自 openclaw_smart_caption/segment_cjk.py 的确定性部分。
Pass3（LLM 语义切割）由 Sub-agent 自己完成，不在此模块中。
Pass4（硬兜底）包含在内，保证输出绝不超标。
"""
from __future__ import annotations

import json
from pathlib import Path


# ── SRT 解析 ──────────────────────────────────────────────

def parse_time(ts: str) -> float:
    if not ts:
        return 0.0
    parts = ts.split(":")
    if len(parts) < 3:
        return 0.0
    h, m = int(parts[0]), int(parts[1])
    s = float(parts[2].replace(",", "."))
    return h * 3600 + m * 60 + s


def format_time(seconds: float) -> str:
    # Round once at millisecond precision, then derive every field from that
    # integer.  Rounding the fractional part independently can produce an
    # invalid `...,1000` field without carrying into the next second.
    total_ms = int(round(seconds * 1000))
    h, remainder = divmod(total_ms, 3_600_000)
    m, remainder = divmod(remainder, 60_000)
    s, ms = divmod(remainder, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def parse_srt(file_path: str) -> list[dict]:
    """解析 SRT → [{text, start_str, end_str}, ...]"""
    content = Path(file_path).read_text(encoding="utf-8")
    blocks = content.strip().split("\n\n")
    items = []
    for block in blocks:
        lines = block.strip().split("\n")
        if len(lines) < 3:
            continue
        times = lines[1].split(" --> ")
        if len(times) != 2:
            continue
        text = " ".join(lines[2:]).replace("\n", " ").replace("\r", "")
        items.append({"text": text, "start_str": times[0].strip(), "end_str": times[1].strip()})
    return items


# ── 工具函数 ──────────────────────────────────────────────

def is_latin(ch: str) -> bool:
    return bool(ch) and ord(ch) < 256


def smart_join(items: list[dict]) -> str:
    if not items:
        return ""
    parts = [items[0]["text"]]
    prev = items[0]["text"]
    for it in items[1:]:
        cur = it["text"]
        if (prev and cur and is_latin(prev[-1]) and is_latin(cur[0])
                and not prev[-1].isspace() and not cur[0].isspace()):
            parts.append(" ")
        parts.append(cur)
        prev = cur
    return "".join(parts)


def clean_final_text(text: str) -> str:
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    # 书名号保持全角：ASCII 化成 <> 会绕过 convert._PUNCT_PATTERN 的剥离，
    # 让尖括号泄漏进成片字幕（idea_1785337209_2ad511ed / EP19 实锤）。
    # 同理双破折号直接转空格——旧的 " - " 会把半角连字符留在成片字幕里。
    text = text.replace("\u2014\u2014", " ")
    start_strip = "，。！？：；、,.!?:; "
    return text.lstrip(start_strip)


def _is_split_punct(text: str) -> bool:
    return any(c in text for c in "，。！？；")


def _strip_trailing_punct(text: str) -> str:
    quote = ""
    if text.endswith('"'):
        quote, text = '"', text[:-1]
    elif text.endswith("'"):
        quote, text = "'", text[:-1]
    text = text.rstrip("，。！？：；,.!?:;—— -")
    return text + quote


# ── Pass2 递归居中分割 ─────────────────────────────────────

def _find_best_split(items: list[dict]):
    n = len(items)
    mid = n // 2
    for offset in range(mid + 1):
        indices = []
        if offset == 0:
            indices.append(mid)
        else:
            if mid - offset >= 0:
                indices.append(mid - offset)
            if mid + offset < n:
                indices.append(mid + offset)
        for idx in indices:
            if idx >= len(items):
                continue
            text = items[idx]["text"]
            # 1. 双破折号（内部）→ 删除并分割
            if "\u2014\u2014" in text:
                parts = text.split("\u2014\u2014", 1)
                left = items[:idx] + ([{**items[idx], "text": parts[0]}] if parts[0] else [])
                right = ([{**items[idx], "text": parts[1]}] if parts[1] else []) + items[idx + 1:]
                return left, right
            # 2. 单破折号对
            if text == "\u2014":
                if idx + 1 < n and items[idx + 1]["text"] == "\u2014":
                    return items[:idx], items[idx + 2:]
                if idx > 0 and items[idx - 1]["text"] == "\u2014":
                    return items[:idx - 1], items[idx + 1:]
            # 3. 顿号→删除
            if "、" in text:
                parts = text.split("、", 1)
                left = items[:idx] + ([{**items[idx], "text": parts[0]}] if parts[0] else [])
                right = ([{**items[idx], "text": parts[1]}] if parts[1] else []) + items[idx + 1:]
                return left, right
            # 4. 左引号类→之前分割
            for ch in ["《", "\u201c", "："]:
                if ch in text:
                    si = text.find(ch)
                    if si == 0:
                        return items[:idx], items[idx:]
                    left = items[:idx] + [{**items[idx], "text": text[:si]}]
                    right = [{**items[idx], "text": text[si:]}] + items[idx + 1:]
                    return left, right
            # 5. 右引号类→之后分割
            for ch in ["》", "\u201d"]:
                if ch in text:
                    si = text.find(ch) + 1
                    if si == len(text):
                        return items[:idx + 1], items[idx + 1:]
                    left = items[:idx] + [{**items[idx], "text": text[:si]}]
                    right = [{**items[idx], "text": text[si:]}] + items[idx + 1:]
                    return left, right
    return None


def _recursive_split(items: list[dict], max_chars: int) -> list[list[dict]]:
    text = clean_final_text(smart_join(items))
    if len(text) <= max_chars:
        return [items]
    result = _find_best_split(items)
    if not result:
        return [items]
    left, right = result
    if not left or not right:
        orig = "".join(x["text"] for x in items)
        new = "".join(x["text"] for x in left + right)
        if orig == new:
            return [items]
    return _recursive_split(left, max_chars) + _recursive_split(right, max_chars)


# ── 时间戳重叠修复 ────────────────────────────────────────

def fix_overlaps(segments: list[dict]) -> list[dict]:
    for seg in segments:
        seg["start_f"] = parse_time(seg["start"])
        seg["end_f"] = parse_time(seg["end"])
    for i in range(1, len(segments)):
        if segments[i - 1]["end_f"] > segments[i]["start_f"]:
            new_end = max(segments[i - 1]["start_f"] + 0.01, segments[i]["start_f"] - 0.01)
            segments[i - 1]["end_f"] = new_end
            segments[i - 1]["end"] = format_time(new_end)
    return segments


# ── Pass4 硬兜底分割 ──────────────────────────────────────

def _hard_split(segments: list[dict], max_chars: int) -> list[dict]:
    """递归硬分割：保证每个段落都不超过 hard_limit。"""
    hard_limit = max_chars + 2
    result = []
    queue = list(segments)
    while queue:
        seg = queue.pop(0)
        text = seg["text"]
        if len(text) <= hard_limit:
            result.append(seg)
            continue
        items = seg.get("items", [])
        if len(items) >= 2:
            target = len(text) // 2
            best_k, min_diff = 1, 999
            for k in range(1, len(items)):
                diff = abs(len(smart_join(items[:k])) - target)
                if diff < min_diff:
                    min_diff, best_k = diff, k
                else:
                    break
            c1, c2 = items[:best_k], items[best_k:]
            # 递归：切完的两半放回队列头部继续检查
            queue.insert(0, {
                "text": clean_final_text(smart_join(c2)), "start": c2[0]["start_str"],
                "end": c2[-1]["end_str"], "items": c2,
            })
            queue.insert(0, {
                "text": clean_final_text(smart_join(c1)), "start": c1[0]["start_str"],
                "end": c1[-1]["end_str"], "items": c1,
            })
        else:
            s_val = parse_time(seg["start"])
            e_val = parse_time(seg["end"])
            mid_t = format_time(s_val + (e_val - s_val) / 2)
            mid_idx = len(text) // 2
            queue.insert(0, {"text": clean_final_text(text[mid_idx:]), "start": mid_t, "end": seg["end"],
                             "items": [{"text": text[mid_idx:], "start_str": mid_t, "end_str": seg["end"]}]})
            queue.insert(0, {"text": clean_final_text(text[:mid_idx]), "start": seg["start"], "end": mid_t,
                             "items": [{"text": text[:mid_idx], "start_str": seg["start"], "end_str": mid_t}]})
    return result


# ── 主入口 ────────────────────────────────────────────────

def segment(input_srt: str, output_srt: str, report_path: str | None = None,
            target_chars: int = 16, max_chars: int = 24) -> dict:
    """逐字 SRT → 智能分割 SRT + oversized 报告。

    只跑 Pass1（标点）+ Pass2（递归居中）+ Pass4（硬兜底）。
    Pass3（LLM 语义切割）由 Sub-agent 外部完成后调 apply_splits()。

    Returns:
        {"output": str, "report": str, "total_segments": int, "oversized": int}
    """
    items = parse_srt(input_srt)
    print(f"加载 {len(items)} 个词条")

    # ── Pass1: 标点分割 ──
    segments = []
    current: list[dict] = []

    for item in items:
        text = item["text"]
        # 混合标点+左引号（如 "，""）→ 引号前分割
        if len(text) >= 2 and text[-1] in '"\u201c《' and _is_split_punct(text[:-1]):
            sub1 = {**item, "text": text[:-1]}
            sub2 = {**item, "text": text[-1]}
            current.append(sub1)
            segments.append({
                "text": clean_final_text(smart_join(current)),
                "start": current[0]["start_str"], "end": current[-1]["end_str"],
                "items": list(current),
            })
            current = [sub2]
            continue

        current.append(item)
        if _is_split_punct(text):
            segments.append({
                "text": clean_final_text(smart_join(current)),
                "start": current[0]["start_str"], "end": current[-1]["end_str"],
                "items": list(current),
            })
            current = []

    if current:
        segments.append({
            "text": clean_final_text(smart_join(current)),
            "start": current[0]["start_str"], "end": current[-1]["end_str"],
            "items": list(current),
        })
    print(f"Pass1 标点分割: {len(segments)} 段")

    # ── Pass2: 递归居中分割 ──
    step2 = []
    for seg in segments:
        for chunk in _recursive_split(seg["items"], max_chars):
            if not chunk:
                continue
            step2.append({
                "text": clean_final_text(smart_join(chunk)),
                "start": chunk[0]["start_str"], "end": chunk[-1]["end_str"],
                "items": chunk,
            })
    segments = step2
    print(f"Pass2 递归分割: {len(segments)} 段")

    # ── 标记 oversized + orig_index（供 Pass3 LLM 消费）──
    oversized_for_llm = []
    for i, seg in enumerate(segments):
        seg["orig_index"] = i
        if len(seg["text"]) > max_chars:
            oversized_for_llm.append({
                "index": i,
                "text": seg["text"],
                "chars": len(seg["text"]),
            })

    # ── Pass4: 硬兜底 ──
    segments = _hard_split(segments, max_chars)
    print(f"Pass4 硬兜底: {len(segments)} 段")

    # ── 修复重叠 ──
    segments = fix_overlaps(segments)

    # ── 写 SRT ──
    out = Path(output_srt)
    out.parent.mkdir(parents=True, exist_ok=True)
    oversized_final = []
    with open(out, "w", encoding="utf-8") as f:
        for i, seg in enumerate(segments):
            text = _strip_trailing_punct(seg["text"])
            seg["text"] = text
            f.write(f"{i + 1}\n{seg['start']} --> {seg['end']}\n{text}\n\n")
            if len(text) > max_chars + 2:
                oversized_final.append({"index": i + 1, "text": text, "length": len(text)})

    # ── 写 oversized 报告（两份：给 LLM 的 + 最终的）──
    rpt = Path(report_path) if report_path else out.with_suffix(".oversized.json")
    report_data = {
        "target_chars": target_chars,
        "max_chars": max_chars,
        "total_segments": len(segments),
        "oversized_for_llm": oversized_for_llm,
        "oversized_final": oversized_final,
    }
    rpt.write_text(json.dumps(report_data, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"✅ 分割完成: {len(segments)} 段, {len(oversized_for_llm)} 段待 LLM 切割, {len(oversized_final)} 段最终超标")
    print(f"   SRT → {out}")
    print(f"   报告 → {rpt}")
    return {
        "output": str(out), "report": str(rpt),
        "total_segments": len(segments), "oversized": len(oversized_for_llm),
    }
