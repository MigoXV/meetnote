"""迁移自原型的纯质量规则，补充对事实变化的保护。"""

import re

from meetnote.domain.results import SummaryAtom


def filter_atoms_against_carry(
    atoms: list[SummaryAtom],
    summarized_text: str,
    carry_text: str,
) -> tuple[list[SummaryAtom], int]:
    if not carry_text.strip():
        return atoms, 0
    accepted: list[SummaryAtom] = []
    deferred_count = 0
    for atom in atoms:
        carry_score = _atom_text_support_score(atom, carry_text)
        summarized_score = _atom_text_support_score(atom, summarized_text)
        if carry_score >= 0.55 and carry_score > summarized_score + 0.15:
            deferred_count += 1
            continue
        accepted.append(atom)
    return accepted, deferred_count


def filter_duplicate_atoms(
    new_atoms: list[SummaryAtom], existing_atoms: list[SummaryAtom]
) -> tuple[list[SummaryAtom], int]:
    accepted: list[SummaryAtom] = []
    duplicate_count = 0
    baseline = list(existing_atoms)
    for atom in new_atoms:
        if any(_is_duplicate_atom(atom, old) for old in baseline):
            duplicate_count += 1
            continue
        accepted.append(atom)
        baseline.append(atom)
    return accepted, duplicate_count


def _is_duplicate_atom(left: SummaryAtom, right: SummaryAtom) -> bool:
    # 相同关键词不代表相同事实，改口、数字变化与决定变化必须保留。
    if left.decisions != right.decisions or left.actions != right.actions:
        return False
    if left.key_points != right.key_points:
        return False
    if re.findall(r"[0-9]+|不|未|取消|暂定", left.summary) != re.findall(
        r"[0-9]+|不|未|取消|暂定", right.summary
    ):
        return False
    if _summary_similarity(left.summary, right.summary) >= 0.72:
        return True
    if _term_overlap(_atom_keywords(left), _atom_keywords(right)) >= 0.70:
        return True
    if _term_overlap(_atom_key_points(left), _atom_key_points(right)) >= 0.70:
        return True
    return False


def _summary_similarity(left: str, right: str) -> float:
    left_tokens = _char_grams(left)
    right_tokens = _char_grams(right)
    if not left_tokens or not right_tokens:
        return 0.0
    overlap = len(left_tokens & right_tokens)
    return overlap / max(1, min(len(left_tokens), len(right_tokens)))


def _char_grams(text: str, size: int = 3) -> set[str]:
    compact = re.sub(r"\s+", "", text)
    if len(compact) <= size:
        return {compact} if compact else set()
    return {compact[index : index + size] for index in range(len(compact) - size + 1)}


def _term_overlap(left: list[str], right: list[str]) -> float:
    left_terms = set(_normalize_terms(left))
    right_terms = set(_normalize_terms(right))
    if not left_terms or not right_terms:
        return 0.0
    common = left_terms & right_terms
    if len(common) < 2:
        return 0.0
    return len(common) / max(1, min(len(left_terms), len(right_terms)))


def _normalize_terms(values: list[str]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        term = _trim_keyword_piece(str(value or ""))
        term = re.sub(r"\s+", "", term)
        if len(term) >= 2:
            normalized.append(term)
    return normalized


def _atom_keywords(atom: SummaryAtom) -> list[str]:
    keywords = list(getattr(atom, "keywords", []) or [])
    if keywords:
        return keywords
    return _extractkeywords_from_atoms([atom], limit=8)


def _atom_key_points(atom: SummaryAtom) -> list[str]:
    return list(getattr(atom, "key_points", []) or [])


def _atom_text_support_score(atom: SummaryAtom, text: str) -> float:
    text = re.sub(r"\s+", "", text)
    if not text:
        return 0.0
    terms = list(getattr(atom, "keywords", []) or [])
    if not terms:
        terms = _atom_key_points(atom)
    compact_terms = _normalize_terms(terms)
    if not compact_terms:
        compact_terms = keyword_candidates(atom.summary)
    if not compact_terms:
        return 0.0
    supported = 0
    checked = 0
    text_grams = _char_grams(text, size=2)
    for term in compact_terms[:10]:
        checked += 1
        if term in text:
            supported += 1
            continue
        term_grams = _char_grams(term, size=2)
        if term_grams and len(term_grams & text_grams) / max(1, len(term_grams)) >= 0.55:
            supported += 1
    return supported / max(1, checked)


def keywords_from_atoms(atoms: list[SummaryAtom], limit: int = 8) -> tuple[list[str], list[str]]:
    model_keywords: list[str] = []
    for atom in atoms:
        model_keywords.extend(getattr(atom, "keywords", []) or [])
    accepted, rejected = _quality_gate_keywords(model_keywords, atoms, limit=limit)
    if accepted:
        return accepted, rejected

    fallback_keywords = _extractkeywords_from_atoms(atoms, limit=limit * 2)
    fallback_accepted, fallback_rejected = _quality_gate_keywords(
        fallback_keywords, atoms, limit=limit
    )
    return fallback_accepted, rejected + fallback_rejected


def _quality_gate_keywords(
    values: list[str], atoms: list[SummaryAtom], limit: int = 8
) -> tuple[list[str], list[str]]:
    accepted: list[str] = []
    rejected: list[str] = []
    seen: set[str] = set()
    summary_text = "\n".join(atom.summary for atom in atoms if atom.summary)
    for raw_value in values:
        value = _trim_keyword_piece(str(raw_value or ""))
        if not value:
            continue
        reason = _keyword_reject_reason(value, summary_text)
        if reason:
            rejected.append(f"{value}({reason})")
            continue
        if value in seen:
            continue
        seen.add(value)
        accepted.append(value)
        if len(accepted) >= limit:
            break
    return accepted, rejected


def _keyword_reject_reason(value: str, summary_text: str) -> str:
    if len(value) < 2:
        return "too_short"
    if len(value) > 16 and not _looks_like_code_or_model(value):
        return "too_long"
    generic_words = {
        "相关问题",
        "具体情况",
        "后续优化",
        "后续处理",
        "进一步确认",
        "重点内容",
        "关键内容",
        "主要内容",
        "问题处理",
        "工作安排",
        "当前进展",
        "阶段性说明",
        "本段内容",
    }
    if value in generic_words:
        return "generic"
    if value.startswith(("这是", "这个", "一种", "一个", "相关", "具体", "后续", "主要")):
        return "generic_prefix"
    if _looks_like_fact_phrase(value):
        return "fact_phrase"
    if any(
        marker in value
        for marker in ("，", "。", "；", "但是", "因为", "所以", "需要进一步", "已经")
    ):
        return "sentence_like"
    if len(value) >= 11 and value in re.sub(r"\s+", "", summary_text):
        return "summary_fragment"
    return ""


def _looks_like_fact_phrase(value: str) -> bool:
    predicate_markers = (
        "增加至",
        "增加到",
        "导致",
        "已经",
        "需要",
        "用于",
        "通过",
        "因为",
        "解决",
        "确认",
        "完成",
        "提升",
        "下降",
        "替换",
        "影响",
        "达到",
        "无法",
        "可以",
        "进行",
        "采用",
        "变成",
    )
    return any(marker in value for marker in predicate_markers)


def _looks_like_code_or_model(value: str) -> bool:
    return bool(re.search(r"[A-Za-z0-9]", value))


def _extractkeywords_from_atoms(atoms: list[SummaryAtom], limit: int = 8) -> list[str]:
    candidates: list[str] = []
    for atom in atoms:
        candidates.extend(keyword_candidates(atom.summary))
        for point in atom.key_points:
            candidates.extend(keyword_candidates(point))
    return merge_unique([], candidates)[:limit]


def keyword_candidates(text: str) -> list[str]:
    text = re.sub(r"\s+", "", str(text or ""))
    if not text:
        return []
    pieces = re.split(r"[，。；、,.!?！？;：:\n]+", text)
    candidates: list[str] = []
    for piece in pieces:
        piece = _trim_keyword_piece(piece)
        if _is_good_keyword(piece):
            candidates.append(piece)
        candidates.extend(_embedded_terms(piece))
    return candidates


def _trim_keyword_piece(piece: str) -> str:
    piece = re.sub(
        r"^(关于|针对|目前|已经|需要|通过|发现|讨论|决定|要求|后续|当前|原先|新的|该|其)", "", piece
    )
    piece = re.sub(
        r"(问题|情况|方面|能力|处理|进行|实现|提升|下降|确认|测试|部署|更新|修复|导致|负责|协商|交付|运行|使用)$",
        "",
        piece,
    )
    return piece.strip(" 。，、；;：:")


def _embedded_terms(piece: str) -> list[str]:
    terms: list[str] = []
    for match in re.findall(r"[A-Za-z][A-Za-z0-9_+-]*(?:\s?[A-Za-z0-9_+-]+)*", piece):
        if 2 <= len(match) <= 24:
            terms.append(match)
    for match in re.findall(
        r"[零一二三四五六七八九十百千万0-9]+(?:小时|倍|B|b|K|k|%|个|次|版)", piece
    ):
        terms.append(match)
    return terms


def _is_good_keyword(value: str) -> bool:
    if not value:
        return False
    if len(value) < 2 or len(value) > 14:
        return False
    weak_words = {
        "本段",
        "阶段性说明",
        "关键点",
        "相关问题",
        "具体原因",
        "进一步",
        "后续",
        "目前",
        "可能",
    }
    if value in weak_words:
        return False
    if value.startswith(("这是", "这个", "一种", "一个")):
        return False
    if any(marker in value for marker in ("，", "。", "；", "但是", "因为", "所以")):
        return False
    return True


def merge_unique(existing: list[str], new_values: list[str]) -> list[str]:
    seen = set()
    merged: list[str] = []
    for value in existing + new_values:
        if not value:
            continue
        key = str(value).strip()
        if key and key not in seen:
            seen.add(key)
            merged.append(key)
    return merged
