# -*- coding: utf-8 -*-
"""
Two-tier validation engine for academic paper translation workflows.

Tier 1: Source Extraction Coverage
  - Verifies that all document pages and readable text layers are accounted for in source.jsonl.
  - Detects skipped pages, scanned PDFs with 0 text layer, dropped text blocks, or corrupted PDFs.

Tier 2: Translation Completeness & Factual Integrity
  - Verifies 100% ID bijection between source.jsonl and translated items.
  - Flags missing IDs, duplicate source IDs, duplicate translation IDs, extra orphan IDs,
    and empty translations as FATAL.
  - Numerical & Unit Token Preservation: extracts exact number tokens, signs, scientific notation,
    and physical units (e.g. 0.25 m vs 10.25 m, -0.25 vs 0.25, 1.5e-3 vs 1.5e3, m vs mm).
  - Citation Preservation: verifies [1], [1, 2], (Author, 2024).
  - Formula Preservation: verifies equations like $x+y=z$ and equation labels like (1) or Eq. (1).
  - Semantic Polarity Check: detects polarity flips (e.g. 'not converge' -> '收敛').

Outputs structured coverage_report.json and returns exit code 0 on PASS, 1 on FAIL.
"""

import os
import re
import sys
import math
import json
import argparse
from datetime import datetime, timezone
import pymupdf


# Canonical unit definitions: (canonical_id, [list of alias patterns])
# Longer / compound forms are placed first to guarantee longest-match tokenization.
UNIT_PATTERNS = [
    ("km/h", [r'km/h', r'公里/小时', r'千米/小时']),
    ("m/s", [r'm/s', r'米/秒']),
    ("kHz", [r'kHz', r'千赫兹?', r'千赫']),
    ("MHz", [r'MHz', r'兆赫兹?', r'兆赫']),
    ("GHz", [r'GHz', r'吉赫兹?', r'吉赫']),
    ("dBm", [r'dBm']),
    ("deg", [r'deg', r'度']),
    ("rad", [r'rad', r'弧度']),
    ("min", [r'min', r'分钟']),
    ("mm", [r'mm', r'毫米']),
    ("cm", [r'cm', r'厘米']),
    ("km", [r'km', r'千米', r'公里']),
    ("ms", [r'ms', r'毫秒']),
    ("ns", [r'ns', r'纳秒']),
    ("mV", [r'mV', r'毫伏']),
    ("mA", [r'mA', r'毫安']),
    ("kW", [r'kW', r'千瓦']),
    ("mW", [r'mW', r'毫瓦']),
    ("dB", [r'dB', r'分贝']),
    ("mg", [r'mg', r'毫克']),
    ("kg", [r'kg', r'千克', r'公斤']),
    ("Hz", [r'Hz', r'赫兹?', r'赫']),
    ("m", [r'm(?![a-zA-Z])', r'米(?![a-zA-Z秒])']),
    ("s", [r's(?![a-zA-Z])', r'秒(?![a-zA-Z])']),
    ("h", [r'h(?![a-zA-Z])', r'hr(?![a-zA-Z])', r'小时']),
    ("V", [r'V(?![a-zA-Z])', r'伏特?', r'伏']),
    ("A", [r'A(?![a-zA-Z])', r'安培?', r'安']),
    ("W", [r'W(?![a-zA-Z])', r'瓦特?', r'瓦']),
    ("g", [r'g(?![a-zA-Z])', r'克']),
    ("°", [r'°', r'度']),
    ("%", [r'%', r'百分之', r'个百分点']),
]

# All English unit symbols sorted by length descending so that prefixes never shadow longer units
ALL_UNITS_SORTED = [
    "km/h", "m/s", "kHz", "MHz", "GHz", "dBm", "deg", "rad", "min",
    "mm", "cm", "km", "ms", "ns", "mV", "mA", "kW", "mW", "dB", "mg", "kg", "Hz",
    "m", "s", "h", "V", "A", "W", "g", "°", "%"
]
UNITS_EN_PATTERN = "|".join(re.escape(u) for u in ALL_UNITS_SORTED)

# Exact number entity token regex: preserves bound units and prevents prefix truncation (e.g. 10 ms not 10 m)
NUM_ENTITY_REGEX = re.compile(
    r'(?:'
    r'([+-]?\d+(?:\.\d+)?)\s*(?:\\times|x|\*|×)\s*10\^?\{?([+-]?\d+)\}?|'  # scientific: 1.5 \times 10^{-3}
    r'([+-]?\d+(?:\.\d+)?)[eE]([+-]?\d+)|'                                  # exponential: 1.5e-3
    rf'([+-]?\d+(?:\.\d+)?)\s*({UNITS_EN_PATTERN})(?![a-zA-Z])|'          # with physical unit
    r'([+-]?\d+\.\d+)|'                                                      # float
    r'([+-]?\d+)'                                                            # int
    r')'
)

# Number token extractor for target text
TARGET_NUM_REGEX = re.compile(r'(?<![a-zA-Z0-9_.])([+-]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)(?![a-zA-Z0-9_]|\.\d)')
TARGET_SCI_REGEX = re.compile(r'([+-]?\d+(?:\.\d+)?)\s*(?:\\times|x|\*|×)\s*10\^?\{?([+-]?\d+)\}?')

# Flattened list of (alias_regex, canonical_name) sorted by pattern length descending
ALIAS_TO_CANONICAL = []
for _canon, _aliases in UNIT_PATTERNS:
    for _al in _aliases:
        ALIAS_TO_CANONICAL.append((_al, _canon))
ALIAS_TO_CANONICAL.sort(key=lambda x: len(x[0]), reverse=True)

_unit_rx_parts = [f'(?P<u_{i}>{pat})' for i, (pat, _) in enumerate(ALIAS_TO_CANONICAL)]
_combined_unit_rx = '|'.join(_unit_rx_parts)

NUM_RX_COMBINED = re.compile(
    r'(?<![a-zA-Z0-9_.])'
    r'('
    r'([+-]?\d+(?:\.\d+)?)\s*(?:\\times|x|\*|×)\s*10\^?\{?([+-]?\d+)\}?|'
    r'([+-]?\d+(?:\.\d+)?)[eE]([+-]?\d+)|'
    r'([+-]?\d+\.\d+)|'
    r'([+-]?\d+)'
    r')'
    rf'(?:\s*(?:{_combined_unit_rx}))?'
)

# Regex for bracket citations: [1], [1, 2], [1-3], [4]--[7]
BRACKET_CITATION_REGEX = re.compile(
    r'\[\s*\d+(?:\s*(?:,\s*|[-–—])\s*\d+)*\s*\]'
)

# Regex for author-year citations: (Smith, 2020), (Author et al., 2024), (Wang & Li, 2021)
AUTHOR_YEAR_CITATION_REGEX = re.compile(
    r'\([A-Z][A-Za-z\s&]+(?:et\s+al\.?)?,\s*(?:19|20)\d{2}[a-z]?(?:;[^\)]+)?\)'
)

# Regex for equation references: Eq. (1), Equation (2), Formula (3), (1)
EQUATION_REF_REGEX = re.compile(
    r'(?:(?:Eq\.|Equation|Formula|Eqs\.)\s*\(?\d+[a-z]?\)?|\(\d+[a-z]?\))'
)

# Regex for math formulas: $ ... $ or \( ... \)
MATH_FORMULA_REGEX = re.compile(r'\$([^\$]+)\$|\\\((.*?)\\\)')

# Polarity words
NEGATION_WORDS_EN = re.compile(r'\b(not|never|cannot|can\'t|neither|nor|fails|failed|does\s+not|do\s+not|did\s+not|without)\b', re.IGNORECASE)
NEGATION_WORDS_ZH = ["不", "未", "无法", "不能", "没有", "无", "非", "免", "否"]

# Unit aliases with exact regex boundaries
UNIT_SPECS = [
    ("km/h", [re.compile(r'\bkm/h\b', re.I), re.compile(r'公里/小时'), re.compile(r'千米/小时')]),
    ("m/s", [re.compile(r'\bm/s\b', re.I), re.compile(r'米/秒')]),
    ("mm", [re.compile(r'\bmm\b', re.I), re.compile(r'(?<![厘分微纳])毫米')]),
    ("cm", [re.compile(r'\bcm\b', re.I), re.compile(r'厘米')]),
    ("km", [re.compile(r'\bkm\b', re.I), re.compile(r'千米'), re.compile(r'公里')]),
    ("m", [re.compile(r'(?<![a-zA-Z])m(?![a-zA-Z])'), re.compile(r'(?<![毫厘分千微纳])米(?![a-zA-Z秒])')]),
    ("ns", [re.compile(r'\bns\b', re.I), re.compile(r'纳秒')]),
    ("ms", [re.compile(r'\bms\b', re.I), re.compile(r'毫秒')]),
    ("s", [re.compile(r'(?<![a-zA-Z])s(?![a-zA-Z])'), re.compile(r'(?<![毫微纳])秒(?![a-zA-Z])')]),
    ("h", [re.compile(r'(?<![a-zA-Z])h(?![a-zA-Z])'), re.compile(r'\bhr\b', re.I), re.compile(r'小时')]),
    ("min", [re.compile(r'\bmin\b', re.I), re.compile(r'分钟')]),
    ("khz", [re.compile(r'\bkHz\b', re.I), re.compile(r'千赫')]),
    ("mhz", [re.compile(r'\bMHz\b', re.I), re.compile(r'兆赫')]),
    ("ghz", [re.compile(r'\bGHz\b', re.I), re.compile(r'吉赫')]),
    ("hz", [re.compile(r'(?<![a-zA-Z])Hz(?![a-zA-Z])', re.I), re.compile(r'(?<![千兆吉])赫')]),
    ("dbm", [re.compile(r'\bdBm\b', re.I)]),
    ("db", [re.compile(r'(?<![a-zA-Z])dB(?![a-zA-Z])', re.I), re.compile(r'分贝')]),
    ("mg", [re.compile(r'\bmg\b', re.I), re.compile(r'毫克')]),
    ("kg", [re.compile(r'\bkg\b', re.I), re.compile(r'千克'), re.compile(r'公斤')]),
    ("g", [re.compile(r'(?<![a-zA-Z])g(?![a-zA-Z])'), re.compile(r'(?<![毫千微纳])克')]),
    ("deg", [re.compile(r'\bdeg\b', re.I), re.compile(r'度'), re.compile(r'°')]),
    ("°", [re.compile(r'°'), re.compile(r'度')]),
    ("%", [re.compile(r'%'), re.compile(r'百分之'), re.compile(r'个百分点')]),
    ("rad", [re.compile(r'\brad\b', re.I), re.compile(r'弧度')]),
    ("v", [re.compile(r'(?<![a-zA-Z])V(?![a-zA-Z])'), re.compile(r'伏(?:特)?')]),
    ("mv", [re.compile(r'\bmV\b', re.I), re.compile(r'毫伏')]),
    ("a", [re.compile(r'(?<![a-zA-Z])A(?![a-zA-Z])'), re.compile(r'安(?:培)?')]),
    ("ma", [re.compile(r'\bmA\b', re.I), re.compile(r'毫安')]),
    ("kw", [re.compile(r'\bkW\b', re.I), re.compile(r'千瓦')]),
    ("mw", [re.compile(r'\bmW\b', re.I), re.compile(r'毫瓦')]),
    ("w", [re.compile(r'(?<![a-zA-Z])W(?![a-zA-Z])'), re.compile(r'瓦(?:特)?')]),
]


def extract_numeric_entities(text: str) -> list[dict]:
    r"""
    Extracts bound numeric entities (value, canonical_unit, raw_token, full_match).
    Masks out math formulas ($...$ and \(...\)) and bracket citations ([1]) first
    to avoid spurious token extraction.
    """
    # 1. Mask math formulas
    text_clean = re.sub(r'\$[^\$]+\$', lambda m: ' ' * len(m.group(0)), text)
    text_clean = re.sub(r'\\\([^\)]+\\\)', lambda m: ' ' * len(m.group(0)), text_clean)
    # 2. Mask bracket citations [1], [1, 2]
    text_clean = re.sub(r'\[\s*\d+(?:\s*(?:,\s*|[-–—])\s*\d+)*\s*\]', lambda m: ' ' * len(m.group(0)), text_clean)

    entities = []
    for m in NUM_RX_COMBINED.finditer(text_clean):
        full_tok = m.group(1).strip()
        sci_base, sci_exp = m.group(3), m.group(4)
        e_base, e_exp = m.group(5), m.group(6)
        flt = m.group(7)
        itg = m.group(8)
        val = None
        if sci_base is not None and sci_exp is not None:
            try:
                val = float(sci_base) * (10 ** int(sci_exp))
            except ValueError:
                pass
        elif e_base is not None and e_exp is not None:
            try:
                val = float(e_base) * (10 ** int(e_exp))
            except ValueError:
                pass
        elif flt is not None:
            try:
                val = float(flt)
            except ValueError:
                pass
        elif itg is not None:
            try:
                val = float(itg)
            except ValueError:
                pass

        matched_unit = None
        for i, (pat, canon) in enumerate(ALIAS_TO_CANONICAL):
            if m.group(f'u_{i}') is not None:
                matched_unit = canon
                break

        entities.append({
            "value": val,
            "unit": matched_unit,
            "token": full_tok,
            "raw": m.group(0).strip()
        })
    return entities


def load_manifest(source_path: str) -> list[dict]:
    """Loads source manifest from JSONL or JSON file."""
    if not os.path.isfile(source_path):
        raise FileNotFoundError(f"Source file not found: {source_path}")

    with open(source_path, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]

    if not lines:
        return []

    full_content = "\n".join(lines)
    try:
        data = json.loads(full_content)
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass

    items = []
    for line in lines:
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return items


def load_translations(translated_path: str) -> tuple[dict[str, str], list[str]]:
    """
    Loads translations from JSONL or JSON file.
    Returns:
        (translation_map: {id: translation_text}, duplicate_ids: [id, ...])
    """
    if not os.path.isfile(translated_path):
        raise FileNotFoundError(f"Translation file not found: {translated_path}")

    translation_map = {}
    duplicate_ids = []

    with open(translated_path, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]

    if not lines:
        return translation_map, duplicate_ids

    full_content = "\n".join(lines)
    try:
        data = json.loads(full_content)
        if isinstance(data, list):
            for entry in data:
                item_id = entry.get("id")
                t_text = entry.get("translation") or entry.get("target") or entry.get("translated") or ""
                if item_id in translation_map:
                    duplicate_ids.append(item_id)
                translation_map[item_id] = t_text
            return translation_map, duplicate_ids
        elif isinstance(data, dict):
            if "id" in data:
                item_id = data["id"]
                t_text = data.get("translation") or data.get("target") or data.get("translated") or ""
                translation_map[item_id] = t_text
                return translation_map, duplicate_ids
            else:
                for k, v in data.items():
                    if isinstance(v, dict):
                        t_text = v.get("translation") or v.get("target") or v.get("translated") or ""
                    else:
                        t_text = str(v)
                    translation_map[k] = t_text
                return translation_map, duplicate_ids
    except json.JSONDecodeError:
        pass

    for line in lines:
        try:
            entry = json.loads(line)
            item_id = entry.get("id")
            t_text = entry.get("translation") or entry.get("target") or entry.get("translated") or ""
            if item_id in translation_map:
                duplicate_ids.append(item_id)
            translation_map[item_id] = t_text
        except json.JSONDecodeError:
            pass

    return translation_map, duplicate_ids


class TranslationValidator:
    """Two-tier validation engine for source extraction and translation integrity."""

    def __init__(self, strict: bool = True):
        self.strict = strict

    def validate_tier1_coverage(self, source_items: list[dict], doc_path: str = None) -> dict:
        """
        Tier 1 Validation: Source Extraction Coverage.
        Checks page accounting, scanned PDF detection, and document character layer coverage.
        """
        source_pages = sorted(list(set(item.get("page", 1) for item in source_items)))
        tier1_report = {
            "status": "PASS",
            "source_items_count": len(source_items),
            "manifest_pages": source_pages,
            "missing_pages": [],
            "char_coverage_ratio": 1.0,
            "details": []
        }

        # Check for empty source items
        if not source_items:
            tier1_report["status"] = "FAIL"
            tier1_report["char_coverage_ratio"] = 0.0
            tier1_report["details"].append("Source manifest contains 0 items.")
            return tier1_report

        if doc_path:
            if not os.path.isfile(doc_path):
                tier1_report["status"] = "FAIL"
                tier1_report["details"].append(f"Document file does not exist: {doc_path}")
                return tier1_report

            ext = os.path.splitext(doc_path)[1].lower()
            if ext == ".pdf":
                try:
                    doc = pymupdf.open(doc_path)
                    total_pdf_pages = len(doc)
                    tier1_report["doc_pages_count"] = total_pdf_pages

                    if total_pdf_pages == 0:
                        tier1_report["status"] = "FAIL"
                        tier1_report["details"].append("PDF document contains 0 pages.")
                        return tier1_report

                    # Check for missing pages
                    all_doc_pages = set(range(1, total_pdf_pages + 1))
                    missing_pages = sorted(list(all_doc_pages - set(source_pages)))

                    actual_missing = []
                    for p in missing_pages:
                        page = doc[p - 1]
                        txt = page.get_text().strip()
                        has_imgs = len(page.get_images()) > 0
                        has_draws = len(page.get_drawings()) > 0
                        if len(txt) > 0:
                            actual_missing.append(p)
                        elif has_imgs or has_draws:
                            actual_missing.append(p)
                            tier1_report["details"].append(
                                f"Page {p} contains image/drawing content with 0 text layer extracted (requires OCR or figure extraction)."
                            )

                    if actual_missing:
                        tier1_report["missing_pages"] = actual_missing
                        tier1_report["status"] = "FAIL"
                        tier1_report["details"].append(f"Pages with unextracted content: {actual_missing}")

                    # Calculate character coverage ratio
                    raw_text_len = sum(len(doc[p].get_text().strip()) for p in range(total_pdf_pages))
                    source_text_len = sum(len(item.get("source", "").strip()) for item in source_items)

                    # Check for scanned PDF (0 raw text characters)
                    if raw_text_len == 0:
                        has_images = any(len(doc[p].get_images()) > 0 or len(doc[p].get_drawings()) > 0 for p in range(total_pdf_pages))
                        tier1_report["status"] = "FAIL"
                        tier1_report["char_coverage_ratio"] = 0.0
                        if has_images:
                            tier1_report["details"].append("Scanned PDF detected with 0 text layer characters. OCR is required.")
                        else:
                            tier1_report["details"].append("PDF contains 0 text characters across all pages.")
                        return tier1_report

                    ratio = round(source_text_len / raw_text_len, 4) if raw_text_len > 0 else 0.0
                    tier1_report["char_coverage_ratio"] = ratio

                    # Strict coverage check: require >= 90% character coverage in strict mode
                    req_ratio = 0.90 if self.strict else 0.75
                    if ratio < req_ratio:
                        tier1_report["status"] = "FAIL"
                        tier1_report["details"].append(
                            f"Low character coverage ratio: {ratio * 100:.1f}% (required >= {req_ratio * 100:.1f}%)"
                        )

                    # Block-level coverage check: ensure every substantial text block is represented
                    unextracted_blocks = []
                    for pno in range(total_pdf_pages):
                        page = doc[pno]
                        b_list = page.get_text("blocks")
                        # Items on this page
                        p_items = [it for it in source_items if it.get("page") == pno + 1]
                        if not p_items:
                            p_items = source_items
                        p_source_concat = " ".join(it.get("source", "") for it in p_items)
                        p_source_norm = re.sub(r'\s+', ' ', p_source_concat).lower()

                        # Detect tables to skip table text blocks
                        table_rects = []
                        try:
                            tabs = page.find_tables()
                            table_rects = [pymupdf.Rect(t.bbox) for t in tabs.tables]
                        except Exception:
                            pass

                        for b in b_list:
                            # Ignore non-text blocks
                            if b[6] != 0:
                                continue
                            b_rect = pymupdf.Rect(b[:4])
                            if any((b_rect & tr).get_area() > 0.5 * b_rect.get_area() for tr in table_rects):
                                continue
                            clean_b = re.sub(r'\s+', ' ', b[4]).strip()
                            if len(clean_b) < 25:
                                continue
                            b_words = [w.lower() for w in re.findall(r'\b\w+\b', clean_b)]
                            if not b_words:
                                continue
                            matches = sum(1 for w in b_words if w in p_source_norm)
                            cov = matches / len(b_words)
                            if cov < 0.40:
                                unextracted_blocks.append({
                                    "page": pno + 1,
                                    "text": clean_b[:80]
                                })

                    if unextracted_blocks:
                        tier1_report["unextracted_blocks"] = unextracted_blocks
                        tier1_report["status"] = "FAIL"
                        tier1_report["details"].append(
                            f"Detected {len(unextracted_blocks)} text block(s) omitted from extraction."
                        )

                except Exception as e:
                    tier1_report["status"] = "FAIL"
                    tier1_report["details"].append(f"PDF coverage check error: {str(e)}")

        return tier1_report

    def validate_tier2_integrity(self, source_items: list[dict],
                                  translation_map: dict[str, str],
                                  duplicate_ids: list[str]) -> dict:
        """
        Tier 2 Validation: Translation Completeness & Factual Integrity.
        Checks 100% ID bijection, empty translations, numerical & unit preservation,
        citation preservation, formula preservation, and semantic polarity.
        """
        # 1. Source ID duplicate check
        seen_src = set()
        duplicate_source_ids = []
        for item in source_items:
            sid = item.get("id")
            if sid in seen_src:
                duplicate_source_ids.append(sid)
            seen_src.add(sid)

        source_id_set = seen_src
        translated_id_set = set(translation_map.keys())

        missing_ids = sorted(list(source_id_set - translated_id_set))
        extra_ids = sorted(list(translated_id_set - source_id_set))

        empty_translations = []
        numerical_issues = []
        citation_issues = []
        formula_issues = []

        for item in source_items:
            item_id = item["id"]
            src_text = item.get("source", "").strip()

            if item_id not in translation_map:
                continue

            tgt_text = translation_map[item_id].strip()

            # Empty translation check
            if src_text and not tgt_text:
                empty_translations.append({
                    "id": item_id,
                    "kind": item.get("kind"),
                    "source": src_text
                })
                continue

            # 1. Numerical & Unit Preservation Check (Bound Entity Multiset Matching)
            src_entities = extract_numeric_entities(src_text)
            tgt_entities = extract_numeric_entities(tgt_text)
            remaining_tgt = list(tgt_entities)

            for s_ent in src_entities:
                s_val = s_ent["value"]
                s_unit = s_ent["unit"]
                s_tok = s_ent["token"]
                s_raw = s_ent["raw"]

                found_idx = None
                for idx, t_ent in enumerate(remaining_tgt):
                    t_val = t_ent["value"]
                    t_unit = t_ent["unit"]
                    t_tok = t_ent["token"]

                    # Check unit match: if source has a unit, target must match canonical unit
                    if s_unit is not None:
                        if t_unit is None or s_unit.lower() != t_unit.lower():
                            continue
                    else:
                        # If source has no physical unit, target cannot bind to an incompatible physical unit
                        if t_unit is not None and t_unit not in ("%", "°", "deg"):
                            continue

                    # Check value match
                    if s_val is not None and t_val is not None:
                        # Sign check: strict
                        if (s_val < 0 and t_val >= 0) or (s_val > 0 and t_val <= 0):
                            continue
                        if s_val == 0.0:
                            if t_val == 0.0:
                                found_idx = idx
                                break
                        else:
                            # Strict relative tolerance, no zero-crossing false accept (abs_tol=0.0)
                            if math.isclose(s_val, t_val, rel_tol=1e-5, abs_tol=0.0):
                                found_idx = idx
                                break
                    elif s_tok == t_tok:
                        found_idx = idx
                        break

                if found_idx is not None:
                    remaining_tgt.pop(found_idx)
                else:
                    numerical_issues.append({
                        "id": item_id,
                        "expected_entity": s_raw,
                        "expected_token": s_tok,
                        "numeric_base": s_tok,
                        "unit": s_unit,
                        "source": src_text,
                        "translation": tgt_text,
                        "issue": f"Numeric entity '{s_raw}' not matched or bound incorrectly in translation"
                    })

            # 2. Citation Preservation Check
            bracket_cits = BRACKET_CITATION_REGEX.findall(src_text)
            for cit in bracket_cits:
                clean_cit = re.sub(r'\s+', '', cit)
                clean_tgt = re.sub(r'\s+', '', tgt_text)
                if clean_cit not in clean_tgt:
                    citation_issues.append({
                        "id": item_id,
                        "expected_citation": cit,
                        "source": src_text,
                        "translation": tgt_text,
                        "issue": f"Bracket citation '{cit}' missing from translation"
                    })

            author_cits = AUTHOR_YEAR_CITATION_REGEX.findall(src_text)
            for cit in author_cits:
                year_m = re.search(r'(19|20)\d{2}', cit)
                author_m = re.search(r'\(([A-Z][A-Za-z]+)', cit)
                if year_m and year_m.group(0) not in tgt_text:
                    citation_issues.append({
                        "id": item_id,
                        "expected_citation": cit,
                        "year": year_m.group(0),
                        "source": src_text,
                        "translation": tgt_text,
                        "issue": f"Citation year '{year_m.group(0)}' missing from translation"
                    })
                elif author_m and author_m.group(1) not in tgt_text:
                    citation_issues.append({
                        "id": item_id,
                        "expected_citation": cit,
                        "author": author_m.group(1),
                        "source": src_text,
                        "translation": tgt_text,
                        "issue": f"Citation author '{author_m.group(1)}' missing from translation"
                    })

            # 3. Formula / Equation Preservation Check
            # Check equation references: Eq. (1)
            eq_refs = EQUATION_REF_REGEX.findall(src_text)
            for eq_ref in eq_refs:
                num_m = re.search(r'\d+', eq_ref)
                if num_m:
                    eq_n = num_m.group(0)
                    has_eq_marker = (
                        f"({eq_n})" in tgt_text or
                        f"（{eq_n}）" in tgt_text or
                        f"Eq. ({eq_n})" in tgt_text or
                        f"式({eq_n})" in tgt_text or
                        f"式 ({eq_n})" in tgt_text
                    )
                    if not has_eq_marker:
                        formula_issues.append({
                            "id": item_id,
                            "expected_equation": eq_ref,
                            "equation_number": eq_n,
                            "source": src_text,
                            "translation": tgt_text,
                            "issue": f"Equation reference '({eq_n})' missing from translation"
                        })

            # Check math formulas: $x+y=z$
            src_maths = [m[0] or m[1] for m in MATH_FORMULA_REGEX.findall(src_text)]
            tgt_maths = [m[0] or m[1] for m in MATH_FORMULA_REGEX.findall(tgt_text)]
            if src_maths:
                clean_src_maths = [re.sub(r'\s+', '', m) for m in src_maths]
                clean_tgt_maths = [re.sub(r'\s+', '', m) for m in tgt_maths]
                for orig_m, c_m in zip(src_maths, clean_src_maths):
                    # Check if exact math formula is present in target math expressions or target text
                    if c_m not in clean_tgt_maths and c_m not in re.sub(r'\s+', '', tgt_text):
                        formula_issues.append({
                            "id": item_id,
                            "expected_formula": f"${orig_m}$",
                            "source": src_text,
                            "translation": tgt_text,
                            "issue": f"Formula '${orig_m}$' altered or missing in translation"
                        })

            # 4. Semantic Polarity Check (Negation preservation)
            has_neg_en = bool(NEGATION_WORDS_EN.search(src_text))
            has_neg_zh = any(w in tgt_text for w in NEGATION_WORDS_ZH)
            if has_neg_en and not has_neg_zh:
                formula_issues.append({
                    "id": item_id,
                    "type": "polarity_discrepancy",
                    "source": src_text,
                    "translation": tgt_text,
                    "issue": "Semantic polarity discrepancy: source contains negation, but translation lacks negative polarity marker"
                })

        # Determine Tier 2 Status
        tier2_status = "PASS"
        all_duplicate_ids = sorted(list(set(duplicate_ids + duplicate_source_ids)))

        # Fatal conditions: missing IDs, duplicate IDs, empty translations, or extra orphan IDs
        if missing_ids or all_duplicate_ids or empty_translations or extra_ids:
            tier2_status = "FAIL"
        elif self.strict and (numerical_issues or citation_issues or formula_issues):
            tier2_status = "FAIL"

        return {
            "status": tier2_status,
            "missing_ids": missing_ids,
            "duplicate_ids": all_duplicate_ids,
            "extra_ids": extra_ids,
            "empty_translations": empty_translations,
            "numerical_issues": numerical_issues,
            "citation_issues": citation_issues,
            "formula_issues": formula_issues
        }

    def validate(self, source_path_or_items, translated_path_or_items,
                 doc_path: str = None, report_path: str = None) -> dict:
        """
        Executes full two-tier validation and generates coverage_report.json.
        """
        if isinstance(source_path_or_items, str):
            source_items = load_manifest(source_path_or_items)
        else:
            source_items = source_path_or_items

        if isinstance(translated_path_or_items, str):
            translation_map, duplicate_ids = load_translations(translated_path_or_items)
        elif isinstance(translated_path_or_items, dict):
            translation_map = translated_path_or_items
            duplicate_ids = []
        elif isinstance(translated_path_or_items, list):
            translation_map = {}
            duplicate_ids = []
            for item in translated_path_or_items:
                i_id = item.get("id")
                if i_id in translation_map:
                    duplicate_ids.append(i_id)
                translation_map[i_id] = item.get("translation") or item.get("target") or ""
        else:
            raise TypeError("Unsupported translated input format")

        tier1_report = self.validate_tier1_coverage(source_items, doc_path=doc_path)
        tier2_report = self.validate_tier2_integrity(source_items, translation_map, duplicate_ids)

        overall_status = "PASS"
        if tier1_report["status"] != "PASS" or tier2_report["status"] != "PASS":
            overall_status = "FAIL"

        report = {
            "status": overall_status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "strict_mode": self.strict,
            "summary": {
                "tier1_status": tier1_report["status"],
                "tier2_status": tier2_report["status"],
                "total_source_items": len(source_items),
                "total_translated_items": len(translation_map),
                "missing_ids_count": len(tier2_report["missing_ids"]),
                "duplicate_ids_count": len(tier2_report["duplicate_ids"]),
                "extra_ids_count": len(tier2_report["extra_ids"]),
                "empty_translations_count": len(tier2_report["empty_translations"]),
                "numerical_discrepancies_count": len(tier2_report["numerical_issues"]),
                "citation_discrepancies_count": len(tier2_report["citation_issues"]),
                "formula_discrepancies_count": len(tier2_report["formula_issues"])
            },
            "tier1_source_coverage": tier1_report,
            "tier2_factual_integrity": tier2_report
        }

        if report_path:
            os.makedirs(os.path.dirname(os.path.abspath(report_path)), exist_ok=True)
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2, ensure_ascii=False)

        return report


def validate_translation(source_path: str, translated_path: str,
                         doc_path: str = None, report_path: str = "coverage_report.json",
                         strict: bool = True) -> dict:
    validator = TranslationValidator(strict=strict)
    return validator.validate(source_path, translated_path, doc_path=doc_path, report_path=report_path)


def main():
    parser = argparse.ArgumentParser(description="Two-tier translation validation engine")
    parser.add_argument("--source", required=True, help="Path to canonical source.jsonl manifest")
    parser.add_argument("--translated", required=True, help="Path to translated.jsonl or translations JSON")
    parser.add_argument(
        "--doc", "--pdf", "--source-doc", dest="doc", default=None,
        help="Optional path to original document for Tier 1 coverage check"
    )
    parser.add_argument(
        "--report", "--out", dest="report", default="coverage_report.json",
        help="Path to save coverage_report.json"
    )
    parser.add_argument(
        "--no-strict", dest="strict", action="store_false",
        help="Allow non-fatal numerical/citation warnings without failing overall status"
    )
    parser.set_defaults(strict=True)
    args = parser.parse_args()

    report = validate_translation(
        args.source,
        args.translated,
        doc_path=args.doc,
        report_path=args.report,
        strict=args.strict
    )

    summary = report["summary"]
    print("=" * 65)
    print(f"Translation Validation Report: [{report['status']}]")
    print("=" * 65)
    print(f"  Tier 1 Source Coverage:       {summary['tier1_status']}")
    print(f"  Tier 2 Factual Integrity:     {summary['tier2_status']}")
    print(f"  Source Items:                 {summary['total_source_items']}")
    print(f"  Translated Items:             {summary['total_translated_items']}")
    print(f"  Missing IDs:                  {summary['missing_ids_count']}")
    print(f"  Duplicate IDs:                {summary['duplicate_ids_count']}")
    print(f"  Extra IDs:                    {summary['extra_ids_count']}")
    print(f"  Empty Translations:           {summary['empty_translations_count']}")
    print(f"  Numerical Discrepancies:      {summary['numerical_discrepancies_count']}")
    print(f"  Citation Discrepancies:       {summary['citation_discrepancies_count']}")
    print(f"  Formula Discrepancies:        {summary['formula_discrepancies_count']}")
    print(f"  Report written to:            {args.report}")
    print("=" * 65)

    if report["status"] == "PASS":
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
