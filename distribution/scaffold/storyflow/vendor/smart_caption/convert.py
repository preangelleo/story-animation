"""
convert.py — SRT 后处理工具（标点→空格、简→繁、应用 LLM 切割、翻译写入）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

try:
    import opencc
    _OPENCC_AVAILABLE = True
except ImportError:
    _OPENCC_AVAILABLE = False

from .segment import parse_time, format_time


# ── SRT 读写 ──────────────────────────────────────────────

def read_srt(path: str) -> list[dict]:
    """解析 SRT → [{index, start, end, text}, ...]"""
    content = Path(path).read_text(encoding="utf-8")
    entries = []
    for block in content.strip().split("\n\n"):
        lines = block.strip().split("\n")
        if len(lines) < 3:
            continue
        idx = int(lines[0].strip()) if lines[0].strip().isdigit() else len(entries) + 1
        times = lines[1].split(" --> ")
        if len(times) != 2:
            continue
        text = " ".join(lines[2:]).strip()
        entries.append({"index": idx, "start": times[0].strip(), "end": times[1].strip(), "text": text})
    return entries


def write_srt(entries: list[dict], path: str) -> str:
    """[{start, end, text}, ...] → SRT 文件。"""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        for i, e in enumerate(entries):
            f.write(f"{i + 1}\n{e['start']} --> {e['end']}\n{e['text']}\n\n")
    return str(out)


# ── 标点 → 空格 ──────────────────────────────────────────

# 所有中英文标点（字幕中不需要的）——字幕链的标点真相源，caption_units 复用本常量。
#
# ⚠️ 旧写法是 r'[...""''...\.\?...]'，看着像「全角双引号 + 全角单引号」，实际那对 ''
# 是**字符串边界**：raw string 在第一个 ' 处结束，后半段成了没有 r 前缀的普通字符串
# （于是 \. 报 DeprecationWarning），而全角 “”‘’ 从来就没进过集合。EP19 的引号泄漏
# 与 caption_units 失配都源于这个视觉陷阱。这里改用显式字符集 + re.escape，
# 不再手写方括号里的转义。
PUNCT_CHARS = (
    "，。！？、；：（）【】《》·…—"   # 全角标点
    "“”‘’"                            # 全角引号
    ",.!?;:()[]{}<>"                  # 半角标点
    "\"'"                             # 半角引号
)
_PUNCT_PATTERN = re.compile(f"[{re.escape(PUNCT_CHARS)}]")


def punctuation_to_space(text: str) -> str:
    """将所有标点替换为空格，合并连续空格，去首尾空格。"""
    result = _PUNCT_PATTERN.sub(" ", text)
    return re.sub(r"\s+", " ", result).strip()


def strip_punctuation_srt(input_path: str, output_path: str) -> str:
    """读 SRT → 每条字幕标点替换为空格 → 写新 SRT。"""
    entries = read_srt(input_path)
    for e in entries:
        e["text"] = punctuation_to_space(e["text"])
    write_srt(entries, output_path)
    print(f"✅ 标点→空格: {input_path} → {output_path}")
    return output_path


# ── 简体→繁体（OpenCC）─────────────────────────────────

def cn_to_tw(input_path: str, output_path: str) -> str:
    """简体 SRT → 繁体台湾用语 SRT（OpenCC s2twp）。"""
    if not _OPENCC_AVAILABLE:
        raise ImportError("需安装 opencc-python-reimplemented: pip install opencc-python-reimplemented")
    converter = opencc.OpenCC("s2twp")
    entries = read_srt(input_path)
    for e in entries:
        e["text"] = converter.convert(e["text"])
    write_srt(entries, output_path)
    print(f"✅ 简→繁: {input_path} → {output_path}")
    return output_path


# ── 应用 LLM 切割结果 ────────────────────────────────────

def apply_splits(word_level_srt: str, splits_json: str, output_path: str) -> str:
    """根据 Sub-agent 的切割决定，在逐字 SRT 基础上重建分段 SRT。

    splits_json 格式:
    [
      {"index": <segment_orig_index>, "split_after": "<在哪个词后面切>"},
      ...
    ]

    此函数重新从 word_level SRT 按切割点构建最终段落。
    """
    # 读逐字 SRT
    words = read_srt(word_level_srt)

    # 读切割指令
    splits = json.loads(Path(splits_json).read_text(encoding="utf-8"))

    # 读 segmented SRT 来获取段落边界（段落的 items 信息已在 segment 阶段丢失了）
    # 更简单的方式：直接用 splits_json 里的信息，结合 word_level 重建
    # 但这需要 Sub-agent 提供精确的 word index 范围

    # 简化实现：Sub-agent 直接输出完整的 SRT 文本
    # 如果 splits_json 是完整 SRT entries 格式
    if splits and isinstance(splits[0], dict) and "start" in splits[0] and "end" in splits[0]:
        write_srt(splits, output_path)
        print(f"✅ 应用切割: {len(splits)} 段 → {output_path}")
        return output_path

    print(f"⚠️ splits 格式不匹配，原样输出")
    return output_path


# ── 翻译写入（Sub-agent 产出 → SRT）─────────────────────

def write_translation_srt(cn_srt_path: str, translations: list[dict], output_path: str) -> str:
    """保持 CN SRT 的时间戳，用翻译文本替换，写成新 SRT。

    translations: [{"index": 1, "text": "translated text"}, ...]
    """
    entries = read_srt(cn_srt_path)
    trans_map = {t["index"]: t["text"] for t in translations}
    for e in entries:
        if e["index"] in trans_map:
            e["text"] = trans_map[e["index"]]
    write_srt(entries, output_path)
    print(f"✅ 翻译 SRT: {len(translations)} 条 → {output_path}")
    return output_path
