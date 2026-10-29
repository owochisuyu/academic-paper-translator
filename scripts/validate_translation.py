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


# Exact number entity token regex
NUM_ENTITY_REGEX = re.compile(
    r'(?:'
    r'([+-]?\d+(?:\.\d+)?)\s*(?:\\times|x|\*|×)\s*10\^?\{?([+-]?\d+)\}?|'  # scientific: 1.5 \times 10^{-3}
    r'([+-]?\d+(?:\.\d+)?)[eE]([+-]?\d+)|'                                  # exponential: 1.5e-3
    r'([+-]?\d+(?:\.\d+)?)\s*(km/h|m/s|mm|cm|km|m|ns|ms|s|kHz|MHz|GHz|Hz|dBm|dB|mg|kg|g|mV|V|mA|A|kW|mW|W|rad|deg|°|%)|' # with unit
    r'([+-]?\d+\.\d+)|'                                                      # float
    r'([+-]?\d+)'                                                            # int
    r')'
)

# Number token extractor for target text
TARGET_NUM_REGEX = re.compile(r'(?<![a-zA-Z0-9_.])([+-]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)(?![a-zA-Z0-9_]|\.\d)')
TARGET_SCI_REGEX = re.compile(r'([+-]?\d+(?:\.\d+)?)\s*(?:\\times|x|\*|×)\s*10\^?\{?([+-]?\d+)\}?')

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
]


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
                        if len(txt) > 30:
                            actual_missing.append(p)

                    if actual_missing:
                        tier1_report["missing_pages"] = actual_missing
                        tier1_report["status"] = "FAIL"
                        tier1_report["details"].append(f"Pages with readable text not extracted: {actual_missing}")

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

                    ratio = round(source_text_len / raw_text_len, 4)
                    tier1_report["char_coverage_ratio"] = ratio

                    # Strict coverage check: require >= 90% character coverage in strict mode
                    req_ratio = 0.90 if self.strict else 0.75
                    if ratio < req_ratio:
                        tier1_report["status"] = "FAIL"
                        tier1_report["details"].append(
                            f"Low character coverage ratio: {ratio * 100:.1f}% (required >= {req_ratio * 100:.1f}%)"
                        )

                    # Block accounting: check if major text blocks were dropped
                    total_major_blocks = 0
                    for p in range(total_pdf_pages):
                        b_list = doc[p].get_text("blocks")
                        major_blocks = [b for b in b_list if b[4].strip() and len(b[4].strip()) > 35]
                        total_major_blocks += len(major_blocks)

                    if total_major_blocks > 0 and len(source_items) < total_major_blocks:
                        tier1_report["status"] = "FAIL"
                        tier1_report["details"].append(
                            f"Extracted items ({len(source_items)}) is less than detected document text blocks ({total_major_blocks}). Possible unextracted paragraphs."
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

            # 1. Numerical & Unit Preservation Check (Exact Token Matching)
            # Find all numbers in target text (both standard and scientific notation)
            tgt_num_tokens = TARGET_NUM_REGEX.findall(tgt_text)
            tgt_num_values = []
            for t_tok in tgt_num_tokens:
                try:
                    tgt_num_values.append((t_tok, float(t_tok)))
                except ValueError:
                    tgt_num_values.append((t_tok, None))

            for m in TARGET_SCI_REGEX.finditer(tgt_text):
                b_str, e_str = m.groups()
                try:
                    t_val = float(b_str) * (10 ** int(e_str))
                    tgt_num_values.append((f"{b_str}e{e_str}", t_val))
                except ValueError:
                    pass

            # Parse numbers and units from source
            for m in NUM_ENTITY_REGEX.finditer(src_text):
                raw_num = m.group(0).strip()
                groups = m.groups()

                expected_val = None
                expected_token = raw_num
                expected_unit = None

                # 1) Scientific notation: base * 10^exp
                if groups[0] is not None and groups[1] is not None:
                    base_str, exp_str = groups[0], groups[1]
                    try:
                        expected_val = float(base_str) * (10 ** int(exp_str))
                        expected_token = f"{base_str}e{exp_str}"
                    except ValueError:
                        pass
                # 2) Exponential notation: base e exp
                elif groups[2] is not None and groups[3] is not None:
                    base_str, exp_str = groups[2], groups[3]
                    try:
                        expected_val = float(base_str) * (10 ** int(exp_str))
                        expected_token = f"{base_str}e{exp_str}"
                    except ValueError:
                        pass
                # 3) With physical unit
                elif groups[4] is not None and groups[5] is not None:
                    base_str, unit_str = groups[4], groups[5]
                    try:
                        expected_val = float(base_str)
                        expected_token = base_str
                    except ValueError:
                        pass
                    expected_unit = unit_str
                # 4) Float or Int
                elif groups[6] is not None:
                    expected_token = groups[6]
                    try:
                        expected_val = float(expected_token)
                    except ValueError:
                        pass
                elif groups[7] is not None:
                    expected_token = groups[7]
                    try:
                        expected_val = float(expected_token)
                    except ValueError:
                        pass

                # Check numerical value existence in target tokens
                matched_num = False
                for t_tok, t_val in tgt_num_values:
                    # Value equality check
                    if expected_val is not None and t_val is not None:
                        # Check sign strictly: negative cannot match positive
                        if (expected_val < 0 and t_val > 0) or (expected_val > 0 and t_val < 0):
                            continue
                        if math.isclose(expected_val, t_val, rel_tol=1e-5, abs_tol=1e-7):
                            matched_num = True
                            break
                    elif expected_token == t_tok:
                        matched_num = True
                        break

                if not matched_num:
                    numerical_issues.append({
                        "id": item_id,
                        "expected_entity": raw_num,
                        "expected_token": expected_token,
                        "numeric_base": expected_token,
                        "source": src_text,
                        "translation": tgt_text,
                        "issue": f"Numeric value '{expected_token}' from '{raw_num}' not found in translation"
                    })
                    continue

                # Check unit preservation if unit is present
                if expected_unit:
                    u_key = expected_unit.lower()
                    unit_matched = False
                    for u_name, regex_list in UNIT_SPECS:
                        if u_name == u_key:
                            for rx in regex_list:
                                if rx.search(tgt_text):
                                    unit_matched = True
                                    break
                            break

                    if not unit_matched:
                        numerical_issues.append({
                            "id": item_id,
                            "expected_entity": raw_num,
                            "unit": expected_unit,
                            "source": src_text,
                            "translation": tgt_text,
                            "issue": f"Unit '{expected_unit}' from '{raw_num}' not found or mistranslated"
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
