# -*- coding: utf-8 -*-
"""
Structured document extraction engine for academic papers (PDF, Markdown, LaTeX).

Extracts structured content into a canonical `source.jsonl` manifest.
Every item is assigned a stable, unique ID and conforms to the schema:
{"id": str, "kind": str, "section": str, "page": int, "spans": list, "source": str, "status": "pending"}

Supports configurable scope:
  --scope full (default): all sections, figure captions, tables, footnotes, references.
  --scope body: excludes references, footnotes, and back matter.
"""

import os
import re
import json
import argparse
import pymupdf


# Regular expressions for structure identification
FIGURE_CAPTION_REGEX = re.compile(
    r'^(?:[\s\n]*)(?:Fig\.|Figure|Supplementary\s+Figure)[\s\u202f]*([A-Za-z0-9_.-]+)',
    re.IGNORECASE
)

TABLE_CAPTION_REGEX = re.compile(
    r'^(?:[\s\n]*)(?:Table|Tab\.)[\s\u202f]*([A-Za-z0-9_.-]+)',
    re.IGNORECASE
)

SECTION_HEADING_REGEX = re.compile(
    r'^(?:'
    r'(?:[0-9]+(?:\.[0-9]+)*\.?|[A-Z]\.?)\s+[A-Z]|'
    r'Abstract|Introduction|Related\s+Work|Methodology|Methods|Background|'
    r'Architecture|Implementation|Experimental\s+Setup|Experiments|Results|'
    r'Discussion|Conclusion|Conclusions|Acknowledge?ments?|References|Bibliography|Appendix'
    r')',
    re.IGNORECASE
)

REFERENCE_LINE_REGEX = re.compile(
    r'^(?:\[\s*\d+\s*\]|\([A-Za-z]+,\s*(?:19|20)\d{2}\))\s+'
)

EQUATION_LINE_REGEX = re.compile(
    r'(?:\(\s*([0-9]+[a-z]?)\s*\)|(?:Eq\.|Equation)\s*\(?([0-9]+[a-z]?)\)?)\s*$'
)

try:
    from align_bilingual import split_en_sentences, split_zh_sentences
except ImportError:
    try:
        from scripts.align_bilingual import split_en_sentences, split_zh_sentences
    except ImportError:
        # Fallback if neither is found
        def split_en_sentences(text: str) -> list[str]:
            return [s.strip() for s in re.split(r'(?<=[.?!])\s+', text) if s.strip()]
        def split_zh_sentences(text: str) -> list[str]:
            return [s.strip() for s in re.split(r'(?<=[。！？])\s*', text) if s.strip()]


def clean_text(text: str) -> str:
    """Collapses whitespace while preserving single spaces."""
    if not text:
        return ""
    return re.sub(r'\s+', ' ', text).strip()


def sanitize_id_part(text: str) -> str:
    """Creates a filesystem and manifest safe identifier string."""
    clean = re.sub(r'[^A-Za-z0-9_.-]', '_', text.strip())
    return clean if clean else "001"


class DocumentExtractor:
    """Structured academic document extraction engine."""

    def __init__(self, scope: str = "full"):
        self.scope = scope.lower()
        if self.scope not in ("full", "body"):
            raise ValueError(f"Invalid scope '{scope}'. Must be 'full' or 'body'.")

    def extract(self, input_path: str) -> tuple[list[dict], dict]:
        """
        Extracts structured content from PDF, Markdown, or LaTeX.
        Returns (items, summary_stats).
        """
        if not os.path.isfile(input_path):
            raise FileNotFoundError(f"Input file not found: {input_path}")

        ext = os.path.splitext(input_path)[1].lower()
        if ext == ".pdf":
            items = self._extract_pdf(input_path)
            total_pages = self._get_pdf_page_count(input_path)
        elif ext in (".md", ".markdown"):
            items = self._extract_markdown(input_path)
            total_pages = 1
        elif ext in (".tex", ".latex"):
            items = self._extract_latex(input_path)
            total_pages = 1
        else:
            raise ValueError(f"Unsupported file format '{ext}'. Must be PDF (.pdf), Markdown (.md), or LaTeX (.tex).")

        # Apply scope filtering if scope == 'body'
        if self.scope == "body":
            filtered_items = []
            for item in items:
                sec_lower = item["section"].lower()
                kind = item["kind"]
                if kind in ("reference", "footnote"):
                    continue
                if "reference" in sec_lower or "bibliography" in sec_lower or "acknowledgement" in sec_lower:
                    if kind != "heading":
                        continue
                filtered_items.append(item)
            items = filtered_items

        stats = self._compute_summary(items, total_pages)
        return items, stats

    def _get_pdf_page_count(self, pdf_path: str) -> int:
        try:
            doc = pymupdf.open(pdf_path)
            return len(doc)
        except Exception:
            return 1

    def _extract_pdf(self, pdf_path: str) -> list[dict]:
        doc = pymupdf.open(pdf_path)
        items = []

        current_sec_title = "Title & Abstract"
        sec_idx = 0
        ref_counter = 0
        eq_counter = 0
        sections_seen = {current_sec_title}

        for pno, page in enumerate(doc):
            page_num = pno + 1
            # 1. First extract native tables if any
            extracted_tables = []
            try:
                tabs = page.find_tables()
                for t_idx, tab in enumerate(tabs.tables):
                    tbl_id = f"p{page_num}-tbl{t_idx + 1}"
                    table_cells = tab.extract()
                    # Add table cell items
                    for r_idx, row in enumerate(table_cells):
                        for c_idx, cell_text in enumerate(row):
                            val = clean_text(cell_text) if cell_text else ""
                            if val:
                                cell_id = f"{tbl_id}-cell-r{r_idx + 1}c{c_idx + 1}"
                                items.append({
                                    "id": cell_id,
                                    "kind": "table_cell",
                                    "table_id": tbl_id,
                                    "section": current_sec_title,
                                    "page": page_num,
                                    "row": r_idx,
                                    "col": c_idx,
                                    "spans": [{"bbox": list(tab.bbox)}],
                                    "source": val,
                                    "status": "pending"
                                })
                    extracted_tables.append(pymupdf.Rect(tab.bbox))
            except Exception:
                pass

            # 2. Extract text blocks and spans
            text_dict = page.get_text("dict")
            blocks = text_dict.get("blocks", [])

            for b_idx, block in enumerate(blocks):
                # Ignore raster image blocks
                if block.get("type") != 0:
                    continue

                block_rect = pymupdf.Rect(block.get("bbox", [0, 0, 0, 0]))
                lines = block.get("lines", [])
                block_text_parts = []
                spans_meta = []

                for line in lines:
                    line_spans = line.get("spans", [])
                    line_text = ""
                    for sp in line_spans:
                        text = sp.get("text", "")
                        line_text += text
                        spans_meta.append({
                            "bbox": [round(c, 2) for c in sp.get("bbox", [0, 0, 0, 0])],
                            "font": sp.get("font", ""),
                            "size": round(sp.get("size", 10.0), 1),
                            "flags": sp.get("flags", 0)
                        })
                    block_text_parts.append(line_text)

                raw_block_text = " ".join(block_text_parts).strip()
                if not raw_block_text:
                    continue

                # Skip if block substantially overlaps with an already extracted table
                is_inside_table = any(
                    (block_rect & tbl_rect).get_area() > 0.35 * block_rect.get_area() or
                    (block_rect & tbl_rect).get_area() > 0.35 * tbl_rect.get_area()
                    for tbl_rect in extracted_tables
                )
                if is_inside_table:
                    continue

                # Determine if block is a Section Heading
                is_heading = False
                max_font_size = max((s["size"] for s in spans_meta), default=10.0)
                is_bold = any(s["flags"] & 2 != 0 or "bold" in s["font"].lower() for s in spans_meta)

                if (max_font_size >= 11.5 or (is_bold and max_font_size >= 10.0)) and len(raw_block_text) < 120:
                    if SECTION_HEADING_REGEX.search(raw_block_text):
                        is_heading = True

                if is_heading:
                    sec_idx += 1
                    current_sec_title = clean_text(raw_block_text)
                    sections_seen.add(current_sec_title)
                    heading_id = f"sec{sec_idx:02d}-head"
                    items.append({
                        "id": heading_id,
                        "kind": "heading",
                        "section": current_sec_title,
                        "page": page_num,
                        "spans": spans_meta,
                        "source": current_sec_title,
                        "status": "pending"
                    })
                    continue

                # Check for Figure Caption
                fig_match = FIGURE_CAPTION_REGEX.search(raw_block_text)
                if fig_match and (fig_match.start() < 10 or FIGURE_CAPTION_REGEX.match(raw_block_text)):
                    fig_id = sanitize_id_part(fig_match.group(1).rstrip(".:,;-"))
                    items.append({
                        "id": f"fig-{fig_id}-cap",
                        "kind": "figure",
                        "section": current_sec_title,
                        "page": page_num,
                        "spans": spans_meta,
                        "source": clean_text(raw_block_text),
                        "status": "pending"
                    })
                    continue

                # Check for Table Caption
                tbl_match = TABLE_CAPTION_REGEX.search(raw_block_text)
                if tbl_match and (tbl_match.start() < 10 or TABLE_CAPTION_REGEX.match(raw_block_text)):
                    tbl_id = sanitize_id_part(tbl_match.group(1).rstrip(".:,;-"))
                    items.append({
                        "id": f"tbl-{tbl_id}-cap",
                        "kind": "table",
                        "section": current_sec_title,
                        "page": page_num,
                        "spans": spans_meta,
                        "source": clean_text(raw_block_text),
                        "status": "pending"
                    })
                    continue

                # Check for Reference item
                is_ref_sec = "reference" in current_sec_title.lower() or "bibliography" in current_sec_title.lower()
                if is_ref_sec or REFERENCE_LINE_REGEX.match(raw_block_text):
                    ref_counter += 1
                    items.append({
                        "id": f"ref-{ref_counter:03d}",
                        "kind": "reference",
                        "section": current_sec_title,
                        "page": page_num,
                        "spans": spans_meta,
                        "source": clean_text(raw_block_text),
                        "status": "pending"
                    })
                    continue

                # Check for Standalone Display Equation
                eq_match = EQUATION_LINE_REGEX.search(raw_block_text)
                if eq_match and len(raw_block_text) < 150:
                    eq_counter += 1
                    eq_label = eq_match.group(1) or eq_match.group(2) or str(eq_counter)
                    items.append({
                        "id": f"eq-{sanitize_id_part(eq_label)}",
                        "kind": "equation",
                        "section": current_sec_title,
                        "page": page_num,
                        "spans": spans_meta,
                        "source": clean_text(raw_block_text),
                        "status": "pending"
                    })
                    continue

                # Regular Body Sentences
                sentences = split_en_sentences(raw_block_text)
                for s_idx, sent in enumerate(sentences):
                    s_id = f"sec{sec_idx:02d}-p{page_num:03d}-b{b_idx + 1:03d}-s{s_idx + 1:03d}"
                    items.append({
                        "id": s_id,
                        "kind": "sentence",
                        "section": current_sec_title,
                        "page": page_num,
                        "spans": spans_meta,
                        "source": sent,
                        "status": "pending"
                    })

        return items

    def _extract_markdown(self, md_path: str) -> list[dict]:
        with open(md_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        items = []
        current_sec_title = "Title & Abstract"
        sec_idx = 0
        ref_counter = 0
        eq_counter = 0
        b_idx = 0

        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if not line:
                i += 1
                continue

            # Heading
            if line.startswith("#"):
                sec_idx += 1
                heading_text = line.lstrip("#").strip()
                current_sec_title = heading_text
                items.append({
                    "id": f"sec{sec_idx:02d}-head",
                    "kind": "heading",
                    "section": current_sec_title,
                    "page": 1,
                    "spans": [],
                    "source": heading_text,
                    "status": "pending"
                })
                i += 1
                continue

            # Equation block ($$ ... $$)
            if line.startswith("$$"):
                eq_counter += 1
                eq_lines = [line]
                if not line.endswith("$$") or line == "$$":
                    i += 1
                    while i < len(lines) and not lines[i].strip().endswith("$$"):
                        eq_lines.append(lines[i].strip())
                        i += 1
                    if i < len(lines):
                        eq_lines.append(lines[i].strip())
                eq_text = "\n".join(eq_lines)
                items.append({
                    "id": f"eq-{eq_counter:03d}",
                    "kind": "equation",
                    "section": current_sec_title,
                    "page": 1,
                    "spans": [],
                    "source": eq_text,
                    "status": "pending"
                })
                i += 1
                continue

            # Table row or Markdown Table
            if line.startswith("|") and line.endswith("|"):
                tbl_lines = [line]
                i += 1
                while i < len(lines) and lines[i].strip().startswith("|"):
                    tbl_lines.append(lines[i].strip())
                    i += 1
                items.append({
                    "id": f"tbl-{b_idx + 1:03d}",
                    "kind": "table",
                    "section": current_sec_title,
                    "page": 1,
                    "spans": [],
                    "source": "\n".join(tbl_lines),
                    "status": "pending"
                })
                continue

            # Figure caption / image
            fig_match = FIGURE_CAPTION_REGEX.search(line)
            if fig_match or line.startswith("!["):
                fig_id = sanitize_id_part(fig_match.group(1).rstrip(".:,;-")) if fig_match else f"{b_idx + 1:03d}"
                items.append({
                    "id": f"fig-{fig_id}-cap",
                    "kind": "figure",
                    "section": current_sec_title,
                    "page": 1,
                    "spans": [],
                    "source": line,
                    "status": "pending"
                })
                i += 1
                continue

            # Reference entry
            is_ref_sec = "reference" in current_sec_title.lower() or "bibliography" in current_sec_title.lower()
            if is_ref_sec or REFERENCE_LINE_REGEX.match(line):
                ref_counter += 1
                items.append({
                    "id": f"ref-{ref_counter:03d}",
                    "kind": "reference",
                    "section": current_sec_title,
                    "page": 1,
                    "spans": [],
                    "source": line,
                    "status": "pending"
                })
                i += 1
                continue

            # Paragraph block
            b_idx += 1
            para_lines = [line]
            i += 1
            while i < len(lines) and lines[i].strip() and not lines[i].strip().startswith(("#", "$$", "|", "![")):
                para_lines.append(lines[i].strip())
                i += 1
            para_text = " ".join(para_lines)

            sentences = split_en_sentences(para_text)
            for s_idx, sent in enumerate(sentences):
                s_id = f"sec{sec_idx:02d}-p001-b{b_idx:03d}-s{s_idx + 1:03d}"
                items.append({
                    "id": s_id,
                    "kind": "sentence",
                    "section": current_sec_title,
                    "page": 1,
                    "spans": [],
                    "source": sent,
                    "status": "pending"
                })

        return items

    def _extract_latex(self, tex_path: str) -> list[dict]:
        with open(tex_path, "r", encoding="utf-8") as f:
            content = f.read()

        items = []
        current_sec_title = "Title & Abstract"
        sec_idx = 0
        ref_counter = 0
        eq_counter = 0
        b_idx = 0

        # Remove LaTeX comments
        clean_content = re.sub(r'(?<!\\)%.*$', '', content, flags=re.MULTILINE)
        # Ensure section commands are isolated by newlines
        clean_content = re.sub(r'(\\(?:sub)*section\*?\{[^}]+\})', r'\n\n\1\n\n', clean_content)

        # Split into blocks or paragraphs
        raw_paras = clean_content.split("\n\n")

        for p_raw in raw_paras:
            p = p_raw.strip()
            # Remove LaTeX preamble / document boundaries
            p = re.sub(r'\\(?:documentclass|usepackage|begin\{document\}|end\{document\})(?:\[[^\]]*\])?(?:\{[^}]*\})?', '', p).strip()
            if not p:
                continue

            # Section headings
            sec_m = re.search(r'\\(?:sub)*section\*?\{([^}]+)\}', p)
            if sec_m:
                sec_idx += 1
                current_sec_title = sec_m.group(1).strip()
                items.append({
                    "id": f"sec{sec_idx:02d}-head",
                    "kind": "heading",
                    "section": current_sec_title,
                    "page": 1,
                    "spans": [],
                    "source": current_sec_title,
                    "status": "pending"
                })
                continue

            # Equations
            if "\\begin{equation}" in p or "\\begin{align}" in p or "$$" in p:
                eq_counter += 1
                items.append({
                    "id": f"eq-{eq_counter:03d}",
                    "kind": "equation",
                    "section": current_sec_title,
                    "page": 1,
                    "spans": [],
                    "source": p,
                    "status": "pending"
                })
                continue

            # Figures
            if "\\begin{figure}" in p:
                cap_m = re.search(r'\\caption\{([^}]+)\}', p)
                cap_text = cap_m.group(1).strip() if cap_m else p
                fig_m = FIGURE_CAPTION_REGEX.search(cap_text)
                fig_id = sanitize_id_part(fig_m.group(1).rstrip(".:,;-")) if fig_m else f"{b_idx + 1:03d}"
                items.append({
                    "id": f"fig-{fig_id}-cap",
                    "kind": "figure",
                    "section": current_sec_title,
                    "page": 1,
                    "spans": [],
                    "source": cap_text,
                    "status": "pending"
                })
                continue

            # Tables
            if "\\begin{table}" in p or "\\begin{tabular}" in p:
                cap_m = re.search(r'\\caption\{([^}]+)\}', p)
                cap_text = cap_m.group(1).strip() if cap_m else "Table"
                tbl_m = TABLE_CAPTION_REGEX.search(cap_text)
                tbl_id = sanitize_id_part(tbl_m.group(1).rstrip(".:,;-")) if tbl_m else f"{b_idx + 1:03d}"
                items.append({
                    "id": f"tbl-{tbl_id}-cap",
                    "kind": "table",
                    "section": current_sec_title,
                    "page": 1,
                    "spans": [],
                    "source": cap_text,
                    "status": "pending"
                })

                # Extract tabular body rows and cells if present
                tabular_m = re.search(r'\\begin\{tabular\}\{[^}]*\}(.*?)\\end\{tabular\}', p, re.DOTALL)
                if tabular_m:
                    tab_content = tabular_m.group(1).strip()
                    raw_rows = [r.strip() for r in tab_content.split(r'\\') if r.strip()]
                    cell_r = 0
                    for r_str in raw_rows:
                        clean_r = re.sub(r'\\(hline|toprule|midrule|bottomrule)', '', r_str).strip()
                        if not clean_r:
                            continue
                        cells = [c.strip() for c in clean_r.split('&')]
                        for c_idx, cell_text in enumerate(cells):
                            clean_cell = re.sub(r'\\text(?:bf|it|rm|sf)\{([^}]+)\}', r'\1', cell_text).strip()
                            clean_cell = clean_text(clean_cell)
                            if clean_cell:
                                items.append({
                                    "id": f"tbl-{tbl_id}-cell-r{cell_r + 1}c{c_idx + 1}",
                                    "kind": "table_cell",
                                    "table_id": f"tbl-{tbl_id}",
                                    "section": current_sec_title,
                                    "page": 1,
                                    "row": cell_r,
                                    "col": c_idx,
                                    "spans": [],
                                    "source": clean_cell,
                                    "status": "pending"
                                })
                        cell_r += 1
                continue

            # Bibitem
            if "\\bibitem" in p:
                for line in p.split("\n"):
                    line = line.strip()
                    if line.startswith("\\bibitem"):
                        ref_counter += 1
                        items.append({
                            "id": f"ref-{ref_counter:03d}",
                            "kind": "reference",
                            "section": current_sec_title,
                            "page": 1,
                            "spans": [],
                            "source": line,
                            "status": "pending"
                        })
                continue

            # Regular body paragraph
            b_idx += 1
            # Strip LaTeX commands like \textbf{}, \cite{}, etc. for plain sentences
            plain_para = re.sub(r'\\[a-zA-Z]+\*?(?:\[[^\]]*\])?\{([^}]*)\}', r'\1', p)
            sentences = split_en_sentences(plain_para)
            for s_idx, sent in enumerate(sentences):
                s_id = f"sec{sec_idx:02d}-p001-b{b_idx:03d}-s{s_idx + 1:03d}"
                items.append({
                    "id": s_id,
                    "kind": "sentence",
                    "section": current_sec_title,
                    "page": 1,
                    "spans": [],
                    "source": sent,
                    "status": "pending"
                })

        return items

    def _compute_summary(self, items: list[dict], total_pages: int) -> dict:
        sections = set()
        sentence_count = 0
        figure_count = 0
        table_count = 0
        reference_count = 0
        equation_count = 0

        for item in items:
            sections.add(item["section"])
            kind = item["kind"]
            if kind == "sentence":
                sentence_count += 1
            elif kind in ("figure", "figure_caption"):
                figure_count += 1
            elif kind in ("table", "table_caption", "table_cell"):
                table_count += 1
            elif kind == "reference":
                reference_count += 1
            elif kind == "equation":
                equation_count += 1

        return {
            "pages": total_pages,
            "sections": len(sections),
            "sentence_count": sentence_count,
            "figure_count": figure_count,
            "table_count": table_count,
            "reference_count": reference_count,
            "equation_count": equation_count,
            "total_items": len(items)
        }


def extract_document(input_path: str, output_jsonl: str = "source.jsonl",
                     scope: str = "full", summary_path: str = None) -> dict:
    """
    Extracts structured document into a canonical source.jsonl file.
    
    Args:
        input_path: Path to PDF, Markdown, or LaTeX document.
        output_jsonl: Output path for canonical source.jsonl manifest.
        scope: 'full' or 'body'.
        summary_path: Optional path to save summary JSON statistics.
        
    Returns:
        Summary statistics dict.
    """
    extractor = DocumentExtractor(scope=scope)
    items, stats = extractor.extract(input_path)

    os.makedirs(os.path.dirname(os.path.abspath(output_jsonl)), exist_ok=True)
    with open(output_jsonl, "w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    if summary_path:
        os.makedirs(os.path.dirname(os.path.abspath(summary_path)), exist_ok=True)
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(stats, f, indent=2, ensure_ascii=False)

    return stats


def main():
    parser = argparse.ArgumentParser(description="Structured document extraction for academic papers")
    parser.add_argument("input", help="Path to input document (PDF, Markdown, or LaTeX)")
    parser.add_argument("--out", default="source.jsonl", help="Output path for canonical source.jsonl manifest")
    parser.add_argument("--scope", default="full", choices=["full", "body"],
                        help="Extraction scope ('full': all items; 'body': excludes references/footnotes)")
    parser.add_argument("--summary", default=None, help="Optional output path for summary statistics JSON")
    args = parser.parse_args()

    stats = extract_document(args.input, output_jsonl=args.out, scope=args.scope, summary_path=args.summary)
    print("=" * 60)
    print("Document Extraction Complete")
    print("=" * 60)
    print(f"  Scope:           {args.scope}")
    print(f"  Pages:           {stats['pages']}")
    print(f"  Sections:        {stats['sections']}")
    print(f"  Sentences:       {stats['sentence_count']}")
    print(f"  Figures:         {stats['figure_count']}")
    print(f"  Tables:          {stats['table_count']}")
    print(f"  References:      {stats['reference_count']}")
    print(f"  Equations:       {stats['equation_count']}")
    print(f"  Total Items:     {stats['total_items']}")
    print(f"  Manifest Path:   {args.out}")
    print("=" * 60)


if __name__ == "__main__":
    main()
