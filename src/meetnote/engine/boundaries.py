"""只在原文中定位边界，不用模型改写后的文本替换原文。"""

from meetnote.domain.results import BoundaryDecision


def _unique(text: str, fragment: str) -> int | None:
    index = text.find(fragment)
    if index < 0 or text.find(fragment, index + 1) >= 0:
        return None
    return index


def consumed_prefix(text: str, decision: BoundaryDecision, force: bool) -> int | None:
    if not decision.should_emit or not decision.summary_atoms:
        return None
    if force and not decision.remaining_text.strip():
        return len(text)
    remaining = decision.remaining_text.strip()
    if remaining:
        index = _unique(text, remaining)
        if index is not None and index > 0:
            return index
        # 容忍空白差异，但仍要求唯一匹配，并精确映射回原文位置。
        offsets = [i for i, char in enumerate(text) if not char.isspace()]
        normalized = "".join(text[i] for i in offsets)
        candidate = "".join(remaining.split())
        index = _unique(normalized, candidate)
        if index is not None and index > 0:
            return offsets[index]
    anchor = decision.summary_end_anchor.strip()
    if anchor:
        index = _unique(text, anchor)
        if index is not None:
            return index + len(anchor)
    return None
