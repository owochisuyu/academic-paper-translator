# -*- coding: utf-8 -*-
"""
Robust Sentence-Level and Manifest-Based Bilingual Alignment Module
for Academic Paper Translation Pipelines.

Provides:
- split_en_sentences: Academic-grade English sentence boundary detector
  protecting scientific abbreviations, decimals, version numbers, citations,
  and lowercase math variable sentence starts.
- split_zh_sentences: Robust Chinese sentence boundary detector handling fullwidth
  punctuation, quotes, and embedded numeric formulas.
- match_manifest_translations: Strict bijection alignment mechanism for ID-indexed
  manifest chunks with full integrity and coverage auditing.
- align_sentences_heuristic: Sequence alignment via dynamic programming incorporating
  character-length ratios and anchor tokens (numbers, equations, citations).
- align_sentences: Backwards-compatible tuple-based interface preventing ZeroDivisionError.
"""

from __future__ import annotations

import re
from typing import Any


def clean_text(text: str | None) -> str:
    """Normalize whitespace and strip leading/trailing spaces safely."""
    if text is None:
        return ""
    return re.sub(r'\s+', ' ', str(text)).strip()


# ==============================================================================
# English Sentence Splitting
# ==============================================================================

# Common sentence starters following an abbreviation or sentence boundary
_SENTENCE_STARTERS = (
    r'(?:It|This|These|That|Those|There|Here|We|They|He|She|You|'
    r'However|Therefore|Moreover|Furthermore|Additionally|In|On|At|For|'
    r'Since|As|Although|While|Thus|Hence|Consequently|Specifically|Finally|'
    r'Meanwhile|Nevertheless|Then|Now)'
)

# Prefix abbreviations that NEVER terminate a sentence (always attach to next word/label)
_PREFIX_ABBREVIATIONS = [
    # Titles
    r'\b(?:Dr|Prof|Mr|Mrs|Ms|Sr|Jr)\.',
    # Paper elements
    r'\b(?:Figs?|Figures?)\.',
    r'\b(?:Tables?)\.',
    r'\b(?:Eqs?|Equations?)\.',
    r'\b(?:Refs?)\.',
    r'\b(?:Secs?|Sections?)\.',
    r'\bCh\.',
    r'\bApp\.',
    r'\b(?:no|No)\.',
    r'\bVol\.',
    r'\bpp\.',
    r'\bp\.\s*(?=[0-9ivxlcdmIVXLCDM]+)',
    r'\b(?:eds?)\.',
    # Latin prefixes
    r'\bi\.e\.',
    r'\be\.g\.',
    r'\bcf\.',
    r'\bca\.',
    r'\bviz\.',
    r'\bapprox\.',
]

# Contextual abbreviations that might terminate a sentence if followed by a sentence starter
_CONTEXTUAL_ABBREVIATIONS = [
    r'\bet\s+al\.',
    r'\betc\.',
    r'\bvs\.',
    r'\bal\.',
    r'\bUniv\.',
    r'\bDept\.',
    r'\bLab\.',
    r'\bInc\.',
    r'\bCorp\.',
    r'\bLtd\.',
    r'\bmin\.',
    r'\bsec\.',
    r'\bhrs?\.',
]

_PREFIX_REGEX = re.compile(
    '|'.join(f'(?:{p})' for p in _PREFIX_ABBREVIATIONS),
    re.IGNORECASE
)

_CONTEXTUAL_REGEX = re.compile(
    r'(' + '|'.join(_CONTEXTUAL_ABBREVIATIONS) + r')(?!\s+' + _SENTENCE_STARTERS + r'\b)',
    re.IGNORECASE
)

_EN_SPLIT_PATTERN = re.compile(
    r'((?:[.?!]+|__ELLIP_\d+__)[\'"”’\)\]\}]*(?=\s+(?:[A-Z0-9\[\("“\'‘\$\\]|[a-z]\b|[α-ωΑ-Ω])|$))'
)


def split_en_sentences(text: str) -> list[str]:
    """
    Split English text into sentences with comprehensive scientific protection.

    Guarantees:
    - Protects titles (Dr., Prof., Mr., etc.)
    - Protects Latin / academic abbreviations (et al., i.e., e.g., cf., vs., etc.)
    - Protects paper elements (Fig., Table., Eq., Ref., Sec., pp., p. 12, etc.)
    - Protects units and organizations (approx., min., sec., Univ., Dept., Inc., etc.)
    - Protects decimals (0.25, .05) and version strings (v1.2.3, 3.10.4)
    - Protects ellipses (... and …)
    - Correctly detects sentence starts with lowercase math symbols (e.g. 'x is Gaussian',
      '\alpha denotes...'), citation markers ('[12] proves...'), digits, and quotes.
    """
    text = clean_text(text)
    if not text:
        return []

    placeholders: dict[str, str] = {}
    counter = 0

    def replace_match(match: re.Match) -> str:
        nonlocal counter
        ph = f"__PH_{counter}__"
        counter += 1
        placeholders[ph] = match.group(0)
        return ph

    def replace_ellipsis(match: re.Match) -> str:
        nonlocal counter
        ph = f"__ELLIP_{counter}__"
        counter += 1
        placeholders[ph] = match.group(0)
        return ph

    # 1. Protect URLs and Emails
    text = re.sub(r'https?://[^\s]+|www\.[^\s]+', replace_match, text)
    text = re.sub(r'[\w\.-]+@[\w\.-]+\.\w+', replace_match, text)

    # 2. Protect ellipses: '...' or unicode '…'
    text = re.sub(r'\.\.\.+|…', replace_ellipsis, text)

    # 3. Protect decimals and version strings: e.g. 0.25, 3.14159, v1.2.3, 3.10.4, .05
    text = re.sub(r'\b[vV]?\d+(?:\.\d+)+(?:-[a-zA-Z0-9]+)?\b', replace_match, text)
    text = re.sub(r'(?<=[\s\(\[])\.\d+\b|^(\.\d+\b)', replace_match, text)

    # 4. Protect abbreviations
    text = _PREFIX_REGEX.sub(replace_match, text)
    text = _CONTEXTUAL_REGEX.sub(replace_match, text)

    # 5. Split by sentence terminators
    parts = _EN_SPLIT_PATTERN.split(text)
    sentences: list[str] = []
    for i in range(0, len(parts) - 1, 2):
        s = (parts[i] + parts[i + 1]).strip()
        if s:
            sentences.append(s)
    if len(parts) % 2 == 1 and parts[-1].strip():
        sentences.append(parts[-1].strip())

    # 6. Restore placeholders in reverse order (to avoid nested collisions)
    restored: list[str] = []
    ph_keys = sorted(
        placeholders.keys(),
        key=lambda x: int(re.search(r'\d+', x).group(0)),  # type: ignore[union-attr]
        reverse=True
    )
    for s in sentences:
        for ph in ph_keys:
            s = s.replace(ph, placeholders[ph])
        s = clean_text(s)
        if s:
            restored.append(s)

    return restored


# ==============================================================================
# Chinese Sentence Splitting
# ==============================================================================

_ZH_SPLIT_PATTERN = re.compile(
    r'([。！？]+[”’」』\"\'\)]*|[.?!]+[”’」』\"\'\)]*(?=\s*[\u4e00-\u9fa5A-Za-z0-9]|$))'
)


def split_zh_sentences(text: str) -> list[str]:
    """
    Split Chinese text into sentences with protection for embedded formulas and decimals.

    Handles:
    - Fullwidth punctuation (。！？)
    - Paired quotation marks (”’」』) keeping terminal quotes with the preceding sentence
    - Embedded decimals (e.g. 12.5%, 0.03) and version strings
    - Ellipses (……, ...)
    """
    text = clean_text(text)
    if not text:
        return []

    placeholders: dict[str, str] = {}
    counter = 0

    def replace_match(match: re.Match) -> str:
        nonlocal counter
        ph = f"__ZH_PH_{counter}__"
        counter += 1
        placeholders[ph] = match.group(0)
        return ph

    # Protect URLs and emails
    text = re.sub(r'https?://[^\s]+|www\.[^\s]+', replace_match, text)
    text = re.sub(r'[\w\.-]+@[\w\.-]+\.\w+', replace_match, text)

    # Protect decimals and version strings (e.g. 12.5, 0.03, v1.2.3)
    text = re.sub(r'\b[vV]?\d+(?:\.\d+)+(?:-[a-zA-Z0-9]+)?\b', replace_match, text)

    # Protect ellipses
    text = re.sub(r'……|\.\.\.+|…', replace_match, text)

    # Split by Chinese punctuation
    parts = _ZH_SPLIT_PATTERN.split(text)
    sentences: list[str] = []
    for i in range(0, len(parts) - 1, 2):
        s = (parts[i] + parts[i + 1]).strip()
        if s:
            sentences.append(s)
    if len(parts) % 2 == 1 and parts[-1].strip():
        sentences.append(parts[-1].strip())

    restored: list[str] = []
    ph_keys = sorted(
        placeholders.keys(),
        key=lambda x: int(re.search(r'\d+', x).group(0)),  # type: ignore[union-attr]
        reverse=True
    )
    for s in sentences:
        for ph in ph_keys:
            s = s.replace(ph, placeholders[ph])
        s = clean_text(s)
        if s:
            restored.append(s)

    return restored


# ==============================================================================
# Manifest-Based Strict Bijection Alignment
# ==============================================================================

def match_manifest_translations(
    source_items: list[dict[str, Any]],
    translations: dict[str, Any] | list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Strict ID-based bijection alignment between source items and translated items.

    Audits:
    - 100% coverage
    - 0 duplicate source IDs
    - 0 duplicate translation IDs
    - 0 unmapped IDs (orphans in translations)
    - 0 empty/whitespace translations

    Returns:
        (paired_items, audit_summary)
    """
    paired_items: list[dict[str, Any]] = []

    # 1. Audit source items
    source_id_counts: dict[str, int] = {}
    source_by_id: dict[str, dict[str, Any]] = {}
    duplicate_source_ids: list[str] = []

    for item in source_items:
        sid = str(item.get("id")) if item.get("id") is not None else None
        if sid is None:
            continue
        source_id_counts[sid] = source_id_counts.get(sid, 0) + 1
        if source_id_counts[sid] == 2:
            duplicate_source_ids.append(sid)
        if sid not in source_by_id:
            source_by_id[sid] = item

    # 2. Parse and audit translations
    trans_by_id: dict[str, dict[str, Any]] = {}
    duplicate_translation_ids: list[str] = []

    if isinstance(translations, dict):
        for tid_raw, tval in translations.items():
            tid = str(tid_raw)
            if isinstance(tval, dict):
                text = tval.get("translation") or tval.get("text") or tval.get("zh") or ""
            else:
                text = str(tval) if tval is not None else ""
            trans_by_id[tid] = {
                "id": tid,
                "text": text,
                "raw": tval
            }
    elif isinstance(translations, list):
        trans_id_counts: dict[str, int] = {}
        for t_item in translations:
            tid_raw = t_item.get("id")
            if tid_raw is None:
                continue
            tid = str(tid_raw)
            trans_id_counts[tid] = trans_id_counts.get(tid, 0) + 1
            if trans_id_counts[tid] == 2:
                duplicate_translation_ids.append(tid)

            text = t_item.get("translation") or t_item.get("text") or t_item.get("zh") or ""
            if tid not in trans_by_id:
                trans_by_id[tid] = {
                    "id": tid,
                    "text": str(text) if text is not None else "",
                    "raw": t_item
                }

    # 3. Find missing, empty, and unmapped IDs
    missing_ids: list[str] = []
    empty_translations: list[str] = []
    valid_paired_count = 0

    for item in source_items:
        sid = str(item.get("id")) if item.get("id") is not None else None
        s_text = item.get("text") or item.get("en") or item.get("source") or ""

        pair_record: dict[str, Any] = {
            "id": sid,
            "source": item,
            "en": str(s_text),
            "translation": "",
            "zh": "",
            "anomalous": False,
            "anomaly_reasons": []
        }

        if sid is None or sid not in trans_by_id:
            if sid is not None:
                missing_ids.append(sid)
            pair_record["anomalous"] = True
            pair_record["anomaly_reasons"].append("missing_translation")
        else:
            t_obj = trans_by_id[sid]
            t_text = clean_text(t_obj["text"])
            pair_record["translation"] = t_text
            pair_record["zh"] = t_text
            if not t_text:
                empty_translations.append(sid)
                pair_record["anomalous"] = True
                pair_record["anomaly_reasons"].append("empty_translation")
            else:
                valid_paired_count += 1

        paired_items.append(pair_record)

    unmapped_ids = [tid for tid in trans_by_id if tid not in source_by_id]

    # Attach any orphan translations
    for uid in unmapped_ids:
        t_obj = trans_by_id[uid]
        paired_items.append({
            "id": uid,
            "source": None,
            "en": "",
            "translation": t_obj["text"],
            "zh": t_obj["text"],
            "anomalous": True,
            "anomaly_reasons": ["unmapped_translation"]
        })

    total_source = len(source_items)
    coverage_ratio = (valid_paired_count / total_source) if total_source > 0 else 1.0

    is_valid = (
        len(missing_ids) == 0 and
        len(unmapped_ids) == 0 and
        len(duplicate_source_ids) == 0 and
        len(duplicate_translation_ids) == 0 and
        len(empty_translations) == 0
    )

    audit_summary: dict[str, Any] = {
        "total_source_items": total_source,
        "total_translation_items": len(trans_by_id),
        "valid_paired_count": valid_paired_count,
        "coverage_ratio": coverage_ratio,
        "missing_ids": missing_ids,
        "unmapped_ids": unmapped_ids,
        "duplicate_source_ids": duplicate_source_ids,
        "duplicate_translation_ids": duplicate_translation_ids,
        "empty_translations": empty_translations,
        "is_valid": is_valid
    }

    return paired_items, audit_summary


# ==============================================================================
# Heuristic Sequence Alignment (Character-Ratio & Anchor-Token DP)
# ==============================================================================

def _extract_anchors(text: str) -> set[str]:
    """Extract numbers, citations, formulas, and acronyms for cross-lingual anchoring."""
    anchors = set()
    for m in re.finditer(r'\b\d+(?:\.\d+)?\b', text):
        anchors.add(m.group(0))
    for m in re.finditer(r'\[\d+\]', text):
        anchors.add(m.group(0))
    for m in re.finditer(r'\b[A-Z]{2,}\b', text):
        anchors.add(m.group(0))
    for m in re.finditer(r'\\[a-zA-Z]+', text):
        anchors.add(m.group(0))
    return anchors


def _pair_similarity(en: str, zh: str) -> tuple[float, float]:
    """
    Compute alignment affinity and normalized confidence [0.0, 1.0]
    between an English sentence candidate and a Chinese sentence candidate.
    """
    len_e = len(clean_text(en))
    len_z = len(clean_text(zh))
    if len_e == 0 or len_z == 0:
        return -2.0, 0.0

    r = len_z / max(len_e, 1)
    if 0.3 <= r <= 1.2:
        len_score = 1.0 - min(1.0, abs(r - 0.65))
    elif r < 0.25 or r > 1.8:
        len_score = -1.5
    else:
        len_score = max(-1.0, 0.5 - abs(r - 0.65) * 2.0)

    anchors_e = _extract_anchors(en)
    anchors_z = _extract_anchors(zh)

    if anchors_e and anchors_z:
        intersection = len(anchors_e & anchors_z)
        union = len(anchors_e | anchors_z)
        anchor_score = (intersection / union) * 1.5 - (0.5 if intersection == 0 else 0.0)
    elif anchors_e or anchors_z:
        anchor_score = 0.0
    else:
        anchor_score = 0.2

    score = 0.8 * len_score + 1.0 * anchor_score
    confidence = max(0.0, min(1.0, (score + 1.0) / 2.5))
    return score, confidence


def align_sentences_heuristic(en_list: list[str], zh_list: list[str]) -> list[dict[str, Any]]:
    """
    Heuristic sequence alignment between English and Chinese sentence lists.

    Key principles:
    - Never silently squashes or merges sentences without flagging anomalous: True.
    - Accurately detects omissions (1-0), insertions (0-1), merges (2-1), and splits (1-2).
    - Protects against ZeroDivisionError on empty inputs.
    - Returns structured metadata for every pairing.
    """
    if not en_list and not zh_list:
        return []

    if not zh_list:
        return [
            {
                "en": s,
                "zh": "",
                "en_index": i,
                "zh_index": None,
                "confidence": 0.0,
                "anomalous": True,
                "anomaly_reasons": ["missing_translation"]
            }
            for i, s in enumerate(en_list)
        ]

    if not en_list:
        return [
            {
                "en": "",
                "zh": s,
                "en_index": None,
                "zh_index": j,
                "confidence": 0.0,
                "anomalous": True,
                "anomaly_reasons": ["unmapped_translation"]
            }
            for j, s in enumerate(zh_list)
        ]

    # Equal lengths 1-1 pairing
    if len(en_list) == len(zh_list):
        pairs: list[dict[str, Any]] = []
        for i, (en_s, zh_s) in enumerate(zip(en_list, zh_list)):
            c_en = clean_text(en_s)
            c_zh = clean_text(zh_s)
            anom = False
            reasons: list[str] = []
            if not c_en or not c_zh:
                anom = True
                reasons.append("empty_sentence")
                conf = 0.0
            else:
                _, conf = _pair_similarity(c_en, c_zh)
            pairs.append({
                "en": en_s,
                "zh": zh_s,
                "en_index": i,
                "zh_index": i,
                "confidence": round(conf, 4),
                "anomalous": anom,
                "anomaly_reasons": reasons
            })
        return pairs

    # Different lengths: DP sequence alignment
    n = len(en_list)
    m = len(zh_list)

    OMISSION_PENALTY = 0.5
    MERGE_PENALTY = 0.8

    dp = [[-1e9] * (m + 1) for _ in range(n + 1)]
    backptr: list[list[tuple[str, int, int] | None]] = [[None] * (m + 1) for _ in range(n + 1)]

    dp[0][0] = 0.0
    for i in range(1, n + 1):
        dp[i][0] = dp[i - 1][0] - OMISSION_PENALTY
        backptr[i][0] = ("1-0", i - 1, 0)
    for j in range(1, m + 1):
        dp[0][j] = dp[0][j - 1] - OMISSION_PENALTY
        backptr[0][j] = ("0-1", 0, j - 1)

    for i in range(1, n + 1):
        for j in range(1, m + 1):
            # 1-1 match
            sim_11, _ = _pair_similarity(en_list[i - 1], zh_list[j - 1])
            best_val = dp[i - 1][j - 1] + sim_11
            best_ptr = ("1-1", i - 1, j - 1)

            # 1-0 omission
            cand_10 = dp[i - 1][j] - OMISSION_PENALTY
            if cand_10 > best_val:
                best_val = cand_10
                best_ptr = ("1-0", i - 1, j)

            # 0-1 insertion
            cand_01 = dp[i][j - 1] - OMISSION_PENALTY
            if cand_01 > best_val:
                best_val = cand_01
                best_ptr = ("0-1", i, j - 1)

            # 2-1 merge (two English sentences translated into one Chinese sentence)
            if i >= 2:
                merged_en = en_list[i - 2] + " " + en_list[i - 1]
                sim_21, _ = _pair_similarity(merged_en, zh_list[j - 1])
                cand_21 = dp[i - 2][j - 1] + sim_21 - MERGE_PENALTY
                if cand_21 > best_val:
                    best_val = cand_21
                    best_ptr = ("2-1", i - 2, j - 1)

            # 1-2 split (one English sentence translated into two Chinese sentences)
            if j >= 2:
                merged_zh = zh_list[j - 2] + " " + zh_list[j - 1]
                sim_12, _ = _pair_similarity(en_list[i - 1], merged_zh)
                cand_12 = dp[i - 1][j - 2] + sim_12 - MERGE_PENALTY
                if cand_12 > best_val:
                    best_val = cand_12
                    best_ptr = ("1-2", i - 1, j - 2)

            dp[i][j] = best_val
            backptr[i][j] = best_ptr

    # Backtrack path
    curr_i, curr_j = n, m
    ops: list[tuple[str, int, int, int, int]] = []
    while curr_i > 0 or curr_j > 0:
        step = backptr[curr_i][curr_j]
        if step is None:
            break
        op_type, prev_i, prev_j = step
        ops.append((op_type, curr_i, curr_j, prev_i, prev_j))
        curr_i, curr_j = prev_i, prev_j

    ops.reverse()

    aligned: list[dict[str, Any]] = []
    for op_type, ci, cj, pi, pj in ops:
        if op_type == "1-1":
            en_s = en_list[pi]
            zh_s = zh_list[pj]
            _, conf = _pair_similarity(en_s, zh_s)
            anom = conf < 0.3
            reasons = ["low_confidence"] if anom else []
            aligned.append({
                "en": en_s,
                "zh": zh_s,
                "en_index": pi,
                "zh_index": pj,
                "confidence": round(conf, 4),
                "anomalous": anom,
                "anomaly_reasons": reasons
            })
        elif op_type == "1-0":
            # Omission
            en_s = en_list[pi]
            aligned.append({
                "en": en_s,
                "zh": "",
                "en_index": pi,
                "zh_index": None,
                "confidence": 0.0,
                "anomalous": True,
                "anomaly_reasons": ["omitted_translation"]
            })
        elif op_type == "0-1":
            # Insertion
            zh_s = zh_list[pj]
            aligned.append({
                "en": "",
                "zh": zh_s,
                "en_index": None,
                "zh_index": pj,
                "confidence": 0.0,
                "anomalous": True,
                "anomaly_reasons": ["unmapped_translation"]
            })
        elif op_type == "2-1":
            en_chunk = " ".join(en_list[pi:ci])
            zh_s = zh_list[pj]
            _, conf = _pair_similarity(en_chunk, zh_s)
            aligned.append({
                "en": en_chunk,
                "zh": zh_s,
                "en_index": list(range(pi, ci)),
                "zh_index": pj,
                "confidence": round(conf, 4),
                "anomalous": True,
                "anomaly_reasons": ["count_mismatch", "merged_source_sentences"]
            })
        elif op_type == "1-2":
            en_s = en_list[pi]
            zh_chunk = " ".join(zh_list[pj:cj])
            _, conf = _pair_similarity(en_s, zh_chunk)
            aligned.append({
                "en": en_s,
                "zh": zh_chunk,
                "en_index": pi,
                "zh_index": list(range(pj, cj)),
                "confidence": round(conf, 4),
                "anomalous": True,
                "anomaly_reasons": ["count_mismatch", "split_target_sentences"]
            })

    return aligned


def align_sentences(en_list: list[str], zh_list: list[str]) -> list[tuple[str, str]]:
    """
    Backwards-compatible wrapper returning a list of (en, zh) pairs.
    Never raises ZeroDivisionError on empty inputs.
    """
    aligned = align_sentences_heuristic(en_list, zh_list)
    return [(item["en"], item["zh"]) for item in aligned]
