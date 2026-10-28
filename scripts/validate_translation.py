# -*- coding: utf-8 -*-
"""
Two-tier validation engine for academic paper translation workflows.

Tier 1: Source Extraction Coverage
  - Verifies that all document pages and readable text layers are accounted for in source.jsonl.
  - Detects skipped pages, dropped text layers, or unextracted blocks.

Tier 2: Translation Completeness & Factual Integrity
  - Verifies 100% ID coverage between source.jsonl and translated items.
  - Flags missing IDs, duplicate IDs, and empty translations as FATAL.
  - Numerical & Unit Preservation: extracts numbers, percentages, scientific notation,
    and physical units (e.g. 0.25 m, 95%, 1.5 \\times 10^{-3}) and ensures they are preserved.
  - Citation Preservation: verifies [1], [1, 2], (Author, 2024) are preserved.
  - Formula / Equation Preservation: verifies equation labels like (1) or Eq. (1) and math symbols.

Outputs structured coverage_report.json and returns exit code 0 on PASS, 1 on FAIL.
"""

import os
import re
import sys
import json
import argparse
from datetime import datetime, timezone
import pymupdf


# Regex for numerical expressions: scientific notation, percentages, numbers with units, decimals, ints
NUMERICAL_REGEX = re.compile(
    r'(?:'
    r'\b\d+(?:\.\d+)?\s*(?:\\times|x|\*|×)\s*10\^?\{?[+-]?\d+\}?|'   # scientific notation: 1.5 \times 10^{-3}
    r'\b\d+(?:\.\d+)?[eE][+-]?\d+\b|'                                # exponential: 1.5e-3
    r'\b\d+(?:\.\d+)?\s*\%|'                                         # percentages: 95%
    r'\b\d+(?:\.\d+)?\s*(?:km/h|m/s|m|cm|mm|km|s|ms|ns|Hz|kHz|MHz|GHz|dB|dBm|kg|g|mg|V|mV|A|mA|W|mW|kW|rad|deg|°)\b|' # with physical units
    r'\b\d+\.\d+\b|'                                                 # floating point
    r'\b\d+\b'                                                       # integers
    r')'
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

# Common unit translations (English -> Chinese or standard variants)
UNIT_ALIASES = {
    "m": ["m", "米"],
    "cm": ["cm", "厘米"],
    "mm": ["mm", "毫米"],
    "km": ["km", "千米", "公里"],
    "s": ["s", "秒"],
    "ms": ["ms", "毫秒"],
    "ns": ["ns", "纳秒"],
    "h": ["h", "hr", "小时"],
    "min": ["min", "分钟"],
    "hz": ["hz", "赫兹"],
    "khz": ["khz", "千赫"],
    "mhz": ["mhz", "兆赫"],
    "ghz": ["ghz", "吉赫"],
    "db": ["db", "分贝"],
    "kg": ["kg", "千克", "公斤"],
    "g": ["g", "克"],
    "deg": ["deg", "度", "°"],
    "°": ["°", "度", "deg"],
    "%": ["%", "个百分点", "百分之"],
    "km/h": ["km/h", "公里/小时", "千米/小时"],
    "m/s": ["m/s", "米/秒"]
}


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

    # Check if whole file is valid single JSON (list, dict-map, or single dict-record)
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
            # Check if this single dict is an item record
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

    # JSONL line-by-line parsing
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
        Checks page accounting and document character layer coverage.
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

        if doc_path and os.path.isfile(doc_path):
            ext = os.path.splitext(doc_path)[1].lower()
            if ext == ".pdf":
                try:
                    doc = pymupdf.open(doc_path)
                    total_pdf_pages = len(doc)
                    tier1_report["doc_pages_count"] = total_pdf_pages

                    # Check for missing pages
                    all_doc_pages = set(range(1, total_pdf_pages + 1))
                    missing_pages = sorted(list(all_doc_pages - set(source_pages)))
                    
                    # Verify if missing pages have text
                    actual_missing = []
                    for p in missing_pages:
                        page = doc[p - 1]
                        txt = page.get_text().strip()
                        if len(txt) > 50:  # has readable text content
                            actual_missing.append(p)

                    if actual_missing:
                        tier1_report["missing_pages"] = actual_missing
                        tier1_report["status"] = "FAIL"
                        tier1_report["details"].append(f"Pages with readable text not extracted: {actual_missing}")

                    # Calculate character coverage ratio
                    raw_text_len = sum(len(doc[p].get_text().strip()) for p in range(total_pdf_pages))
                    source_text_len = sum(len(item.get("source", "").strip()) for item in source_items)

                    if raw_text_len > 0:
                        ratio = round(source_text_len / raw_text_len, 4)
                        tier1_report["char_coverage_ratio"] = ratio
                        # If extraction captures less than 75% of characters from readable PDF, fail Tier 1
                        if ratio < 0.75:
                            tier1_report["status"] = "FAIL"
                            tier1_report["details"].append(f"Low character coverage ratio: {ratio * 100:.1f}%")
                except Exception as e:
                    tier1_report["details"].append(f"PDF coverage check error: {str(e)}")

        elif not source_items:
            tier1_report["status"] = "FAIL"
            tier1_report["details"].append("Source manifest contains 0 items.")

        return tier1_report

    def validate_tier2_integrity(self, source_items: list[dict],
                                  translation_map: dict[str, str],
                                  duplicate_ids: list[str]) -> dict:
        """
        Tier 2 Validation: Translation Completeness & Factual Integrity.
        Checks 100% ID bijection, empty translations, numerical & unit preservation,
        citation preservation, and equation/formula preservation.
        """
        source_id_set = set(item["id"] for item in source_items)
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

            # 1. Numerical & Unit Preservation Check
            num_matches = NUMERICAL_REGEX.findall(src_text)
            for raw_num in num_matches:
                # Extract base number from string
                base_num_match = re.search(r'\d+(?:\.\d+)?', raw_num)
                if not base_num_match:
                    continue
                base_num = base_num_match.group(0)

                # Check if base numeric string exists in translation
                if base_num not in tgt_text:
                    numerical_issues.append({
                        "id": item_id,
                        "expected_entity": raw_num,
                        "numeric_base": base_num,
                        "source": src_text,
                        "translation": tgt_text,
                        "issue": f"Number '{base_num}' from '{raw_num}' not found in translation"
                    })
                    continue

                # Check unit preservation if unit is attached
                unit_match = re.search(r'([A-Za-z/°%]+)$', raw_num.strip())
                if unit_match:
                    unit = unit_match.group(1).lower()
                    aliases = UNIT_ALIASES.get(unit, [unit])
                    # Check if any alias of the unit or the base unit is present
                    has_unit = any(a in tgt_text.lower() for a in aliases)
                    if not has_unit:
                        numerical_issues.append({
                            "id": item_id,
                            "expected_entity": raw_num,
                            "unit": unit,
                            "source": src_text,
                            "translation": tgt_text,
                            "issue": f"Unit '{unit}' from '{raw_num}' not found or translated"
                        })

            # 2. Citation Preservation Check
            bracket_cits = BRACKET_CITATION_REGEX.findall(src_text)
            for cit in bracket_cits:
                # Compact spaces in citation: '[ 1 , 2 ]' -> '[1, 2]'
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
            eq_refs = EQUATION_REF_REGEX.findall(src_text)
            for eq_ref in eq_refs:
                num_m = re.search(r'\d+', eq_ref)
                if num_m:
                    eq_n = num_m.group(0)
                    # Translation should contain (n) or Eq. (n) or 式 (n) or similar
                    has_eq_marker = f"({eq_n})" in tgt_text or f"（{eq_n}）" in tgt_text or f"Eq. ({eq_n})" in tgt_text or f"式({eq_n})" in tgt_text
                    if not has_eq_marker:
                        formula_issues.append({
                            "id": item_id,
                            "expected_equation": eq_ref,
                            "equation_number": eq_n,
                            "source": src_text,
                            "translation": tgt_text,
                            "issue": f"Equation reference '({eq_n})' missing from translation"
                        })

        # Determine Tier 2 Status
        tier2_status = "PASS"
        if missing_ids or duplicate_ids or empty_translations:
            tier2_status = "FAIL"
        elif self.strict and (numerical_issues or citation_issues or formula_issues):
            tier2_status = "FAIL"

        return {
            "status": tier2_status,
            "missing_ids": missing_ids,
            "duplicate_ids": duplicate_ids,
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
    parser.add_argument("--doc", default=None, help="Optional path to original document for Tier 1 coverage check")
    parser.add_argument("--report", default="coverage_report.json", help="Path to save coverage_report.json")
    parser.add_argument("--no-strict", dest="strict", action="store_false",
                        help="Allow non-fatal numerical/citation warnings without failing overall status")
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
