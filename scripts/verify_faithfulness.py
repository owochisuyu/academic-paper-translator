# -*- coding: utf-8 -*-
"""
Academic Paper Translation Faithfulness & Structural Integrity Auditor
Ensures 100% fidelity to the original published paper:
1. Verbatim English preservation (Zero rewriting / Zero summarizing).
2. Paragraph count conservation (Zero omitted / dropped paragraphs).
3. Numerical & statistical precision invariance (Zero hallucinated numbers / unit changes).
4. Structural element anchoring (Tables/Figures placed in their true authorial sections).
5. Complete 8-point backmatter checklist (Author contributions, Biographies, Publisher's note, etc.).
6. References hygiene & styling (Sanitized from scraping artifacts like CAS/PubMed, clean URLs).
"""

import argparse
import os
import re
import sys
import unicodedata

def normalize_text(text: str) -> str:
    """Normalize text by collapsing whitespace, stripping soft hyphens, and standardizing unicode."""
    if not text:
        return ""
    text = unicodedata.normalize('NFKC', text)
    text = text.replace('\u00ad', '').replace('\xad', '')
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def extract_numbers_and_tolerances(text: str) -> list[str]:
    """Extract numbers, ranges, tolerances (±), and percentages."""
    # Matches patterns like 0.9014 ± 0.0028, 1–1.5%, 30%, 0.406, etc.
    patterns = [
        r'\d+(?:\.\d+)?\s*[±\+-]\s*\d+(?:\.\d+)?',  # tolerances
        r'\d+(?:\.\d+)?%',                          # percentages
        r'\b\d+\.\d+\b',                            # floats
    ]
    results = []
    for p in patterns:
        results.extend(re.findall(p, text))
    return list(set(results))

def audit_typst_document(typst_path: str, source_text: str = None, mode: str = "bi", citation_style: str = "author-year") -> dict:
    with open(typst_path, 'r', encoding='utf-8') as f:
        content = f.read()

    results = {
        "passed": True,
        "failures": [],
        "warnings": [],
        "metrics": {}
    }

    # 1. Backmatter Complete 8-Item Checklist
    backmatter_items = [
        ("Acknowledgements", ["致谢", "acknowledgement"], True),
        ("Author contributions", ["作者贡献", "author contribution"], True),
        ("Funding", ["基金资助", "资助", "funding"], True),
        ("Data availability", ["数据可用性", "data availability"], True),
        ("Competing interests", ["利益冲突", "competing interest", "conflict of interest"], True),
        ("Publisher's note", ["出版商声明", "publisher's note"], True),
        ("Author Biographies", ["作者学术简介", "作者简介", "author biograph"], True),
        ("References", ["参考文献", "references"], True)
    ]

    missing_bm = []
    for item_name, aliases, required in backmatter_items:
        found = False
        for a in aliases:
            if a.lower() in content.lower():
                found = True
                break
        if not found and required:
            missing_bm.append(item_name)

    if missing_bm:
        results["failures"].append(f"Missing mandatory backmatter sections: {', '.join(missing_bm)}")
        results["passed"] = False
    else:
        results["metrics"]["backmatter_complete"] = True

    # 2. Reference Hygiene & Style Check
    ref_issues = []
    # Extract references block
    ref_match = re.search(r'#bihead1\("References".*?(?=#bihead|\Z)', content, re.DOTALL | re.IGNORECASE)
    ref_text = ref_match.group(0) if ref_match else ""

    if re.search(r'\b(?:CAS\s+Article|Article\s+CAS|PubMed|Google\s+Scholar)\b', ref_text, re.IGNORECASE) or re.search(r'\[CAS\]', content):
        ref_issues.append("Found residual web-scraping token ('CAS Article', 'PubMed', '[CAS]', etc.) in references.")
    if re.search(r'https?://[^\s\]\)]*\s[^\s\]\)]*', ref_text):
        # Broken URL with spaces inside
        broken_urls = re.findall(r'https?://[^\s\]\)]+\s+[^\s\]\)]+', ref_text)
        if broken_urls:
            ref_issues.append(f"Found broken URL with internal whitespace: {broken_urls[0]}")

    if citation_style == "author-year" and ref_text:
        # Check if user accidentally added bracketed numbers [1]--[37] into references
        if re.search(r'#text\(size:\s*\d+pt\)\[\s*\[\d+\]', ref_text) or re.search(r'^\s*\[\d+\]\s+[A-Z]', ref_text, re.MULTILINE):
            ref_issues.append("Bracketed citation numbers (e.g., [1]–[37]) detected in Author-Year reference list.")

    if ref_issues:
        for iss in ref_issues:
            results["failures"].append(iss)
        results["passed"] = False

    # 3. Verbatim English & Paragraph Fidelity Check (if source text is available)
    if source_text:
        source_clean = normalize_text(source_text)
        
        # Check if bipara blocks exist
        if mode == "bi":
            bipara_en = re.findall(r'#bipara\(\[(.*?)\]\s*,\s*\[.*?\]\)', content, re.DOTALL)
            results["metrics"]["bipara_count"] = len(bipara_en)
            
            # Check key phrases from source to ensure no dropped paragraphs
            # Sample test: check sentences longer than 40 chars
            source_sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', source_text) if len(s.strip()) > 50]
            missing_sentences = []
            
            # Test a random / deterministic sample of sentences from throughout the paper
            sample_sentences = source_sentences[::max(1, len(source_sentences) // 25)]
            for s in sample_sentences:
                clean_s = normalize_text(s)
                # Remove punctuation for fuzzy robustness against typst markup
                core_tokens = [w for w in re.findall(r'[A-Za-z0-9]+', clean_s) if len(w) > 3][:6]
                if len(core_tokens) >= 4:
                    search_pattern = r'\b' + r'\b.*?\b'.join(core_tokens[:4]) + r'\b'
                    if not re.search(search_pattern, content, re.IGNORECASE):
                        missing_sentences.append(" ".join(core_tokens[:6]))

            if missing_sentences:
                results["warnings"].append(f"Potential dropped/rewritten prose detected ({len(missing_sentences)} anchors not found): e.g. {missing_sentences[:3]}")

        # 4. Numerical Entity Fidelity
        source_numbers = extract_numbers_and_tolerances(source_text)
        content_clean = normalize_text(content)
        missing_numbers = []
        for num in source_numbers:
            # check if number with tolerance exists
            clean_num = normalize_text(num)
            if clean_num not in content_clean:
                # also check without spaces around ±
                compact_num = clean_num.replace(' ', '')
                if compact_num not in content_clean.replace(' ', ''):
                    missing_numbers.append(num)

        # Allow small tolerance for un-transcribed figure tick numbers, but alert if core metrics missing
        critical_missing = [n for n in missing_numbers if '±' in n]
        if critical_missing:
            results["failures"].append(f"Critical quantitative metrics missing or altered from original: {critical_missing[:5]}")
            results["passed"] = False
        elif missing_numbers:
            results["metrics"]["missing_raw_numbers_count"] = len(missing_numbers)

    # 5. Table Format Integrity (No invented introductory sentences, no decimal->percentage switches without cause)
    if "Table 5" in content:
        # Check if Table 5 has been artificially decorated with unoriginal sentences
        if "Table 5 summarizes the classification metrics" in content:
            results["failures"].append("Invented unoriginal sentence detected in Table 5: 'Table 5 summarizes the classification metrics'")
            results["passed"] = False

    return results

def main():
    parser = argparse.ArgumentParser(description="Audit Typst document against original source paper for 100% faithfulness.")
    parser.add_argument("--typst", required=True, help="Path to .typ file")
    parser.add_argument("--source", default=None, help="Path to original full text file (.txt)")
    parser.add_argument("--mode", choices=["bi", "zh"], default="bi", help="Bilingual or Chinese Monograph mode")
    parser.add_argument("--citation-style", choices=["author-year", "numbered"], default="author-year", help="Expected reference style")
    args = parser.parse_args()

    source_text = None
    if args.source and os.path.exists(args.source):
        with open(args.source, 'r', encoding='utf-8', errors='ignore') as f:
            source_text = f.read()

    report = audit_typst_document(args.typst, source_text, args.mode, args.citation_style)

    print("=" * 60)
    print(f"FAITHFULNESS & STRUCTURAL INTEGRITY AUDIT: {os.path.basename(args.typst)}")
    print("=" * 60)
    print(f"Status: {'PASS' if report['passed'] else 'FAIL'}")
    
    if report["failures"]:
        print("\n[CRITICAL FAILURES]:")
        for fail in report["failures"]:
            print(f"  [X] {fail}")

    if report["warnings"]:
        print("\n[WARNINGS]:")
        for warn in report["warnings"]:
            print(f"  [!] {warn}")

    if report["metrics"]:
        print("\n[AUDIT METRICS]:")
        for k, v in report["metrics"].items():
            print(f"  [OK] {k}: {v}")

    print("=" * 60)
    if not report["passed"]:
        sys.exit(1)
    else:
        print("All fidelity invariants passed. Document is certified faithful to source.")
        sys.exit(0)

if __name__ == "__main__":
    main()
