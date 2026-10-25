"""Deterministic section alignment and review prioritization.

Priority and flags identify textual review tasks, not clinical risk or a medical
recommendation. Inline segments are raw text and MUST be escaped by renderers.
"""

from __future__ import annotations

import re
from collections import Counter
from difflib import SequenceMatcher

from .documents import canonical_title


_IMPORTANT_SECTIONS = {
    "用法用量", "禁忌", "不良反应", "注意事项", "警告", "黑框警告",
    "孕妇及哺乳期妇女用药", "儿童用药", "老年用药", "特殊人群用药",
    "药物相互作用", "药物过量", "适应症", "功能主治",
}
_NUMBER = re.compile(r"(?<![A-Za-z0-9.])[-+]?\d+(?:[.,]\d+)*(?:\s*[%％])?")
_UNIT = re.compile(r"(?<![A-Za-z])(?:mcg|μg|µg|mg|kg|g|mL|ml|L|IU|mmol|mol|μL|µL|℃|°C|毫克|微克|克|毫升|升|单位|片|粒|支|袋|滴)(?![A-Za-z])", re.IGNORECASE)


def _comparison_text(text: str) -> str:
    """Ignore layout whitespace without merging distinct numeric tokens.

    In particular '1 0 mg' never becomes '10 mg'; decimal points, units,
    punctuation and negation remain significant.
    """
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"(?<=[\u3400-\u9fff]) +(?=[\u3400-\u9fff])", "", text)
    return text


def _indexed(sections: list[dict]) -> tuple[dict[str, dict], list[str]]:
    counts: Counter[str] = Counter()
    result: dict[str, dict] = {}
    order: list[str] = []
    # Recompute keys from headings rather than trusting mutable caller keys.
    for section in sections:
        title = canonical_title(section["title"]).replace("#", "＃")
        counts[title] += 1
        key = title if counts[title] == 1 else f"{title}#{counts[title]}"
        result[key] = section
        order.append(key)
    return result, order


def _location(section: dict | None) -> str:
    if section is None:
        return "无对应章节"
    start, end = section.get("start_line"), section.get("end_line")
    if not isinstance(start, int) or not isinstance(end, int):
        return "手工校正章节"
    return f"提取文本第 {start} 行" if start == end else f"提取文本第 {start}–{end} 行"


def _inline_segments(old: str, new: str) -> tuple[list[dict], bool]:
    if not old:
        return ([{"op": "insert", "text": new}] if new else []), False
    if not new:
        return [{"op": "delete", "text": old}], False
    # Character-level matching is useful for Chinese units and negations, but
    # huge or pathological strings need a bounded whole-section fallback.
    if len(old) + len(new) > 30_000:
        return [{"op": "delete", "text": old}, {"op": "insert", "text": new}], True
    matcher = SequenceMatcher(None, old, new, autojunk=True)
    segments: list[dict] = []
    for op, a_start, a_end, b_start, b_end in matcher.get_opcodes():
        if op == "equal":
            segments.append({"op": "equal", "text": old[a_start:a_end]})
        else:
            if op in {"delete", "replace"}:
                segments.append({"op": "delete", "text": old[a_start:a_end]})
            if op in {"insert", "replace"}:
                segments.append({"op": "insert", "text": new[b_start:b_end]})
    return segments, False


def compare_sections(old_sections: list[dict], new_sections: list[dict]) -> dict:
    """Compare each canonical heading, pairing repeated headings by occurrence.

    Reordering alone is reported as a warning, not falsely presented as new
    clinical content. Differences are returned in new-document order, followed
    by removed sections in old-document order.
    """
    old_map, old_order = _indexed(old_sections)
    new_map, new_order = _indexed(new_sections)
    warnings: list[str] = []
    if any("#" in key for key in old_order + new_order):
        warnings.append("存在重复标题，已按同名章节出现顺序配对；请人工核对章节对应关系。")
    if "全文" in old_map or "全文" in new_map:
        warnings.append("至少一个版本未识别出标准章节；可在创建比对前手工整理章节，以提高定位精度。")
    shared_old = [key for key in old_order if key in new_map]
    shared_new = [key for key in new_order if key in old_map]
    if shared_old != shared_new:
        warnings.append("章节顺序发生变化；已按标题对齐，顺序调整本身不计为内容变更。")
    changes: list[dict] = []
    unchanged = 0
    all_keys = new_order + [key for key in old_order if key not in new_map]
    for key in all_keys:
        old, new = old_map.get(key), new_map.get(key)
        old_text = old["text"] if old else ""
        new_text = new["text"] if new else ""
        if old is not None and new is not None and _comparison_text(old_text) == _comparison_text(new_text):
            unchanged += 1
            continue
        kind = "added" if old is None else "removed" if new is None else "modified"
        section = new if new is not None else old
        title = section["title"]
        canonical = canonical_title(title)
        flags: list[str] = []
        if canonical in _IMPORTANT_SECTIONS:
            flags.append("重点章节变更，需人工复核")
        if _NUMBER.findall(old_text) != _NUMBER.findall(new_text):
            flags.append("数字或比例变化")
        if _UNIT.findall(old_text) != _UNIT.findall(new_text):
            flags.append("单位或剂型计量词变化")
        segments, coarse = _inline_segments(old_text, new_text)
        if coarse:
            flags.append("长章节采用整段展示，请对照原文复核")
        changes.append({
            "section_key": key,
            "section_title": title,
            "kind": kind,
            "priority": "high" if canonical in _IMPORTANT_SECTIONS or any("变化" in flag for flag in flags) else "normal",
            "old_text": old_text,
            "new_text": new_text,
            "old_location": _location(old),
            "new_location": _location(new),
            "segments": segments,
            "flags": flags,
        })
    return {"changes": changes, "unchanged_sections": unchanged, "warnings": warnings}
