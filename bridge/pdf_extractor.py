import re
import os
from typing import List, Dict, Any
from pypdf import PdfReader

class PDFExtractor:
    """
    Academic paper PDF structure extractor and chunker.
    Cleans text, detects section headers, filters out references,
    and breaks the document into coherent translation sections with dynamic titles.
    """

    def __init__(self, pdf_path: str):
        if not os.path.exists(pdf_path):
            raise FileNotFoundError(f"PDF file not found: {pdf_path}")
        self.pdf_path = pdf_path
        self.reader = PdfReader(pdf_path)

    def extract_clean_pages(self) -> List[str]:
        """Extract text page by page with academic header/footer cleanup."""
        pages_text = []
        for page_idx, page in enumerate(self.reader.pages):
            text = page.extract_text() or ""
            lines = text.splitlines()
            cleaned_lines = []

            for line in lines:
                stripped = line.strip()
                # Skip isolated page numbers (e.g. "12", "Page 3 of 10")
                if re.match(r'^(?:Page\s+)?\d+(?:\s+of\s+\d+)?$', stripped, re.IGNORECASE):
                    continue
                # Skip common running headers (e.g. Conference headers, arXiv headers)
                if re.match(r'^(?:arXiv:\d+\.\d+|IEEE\s+TRANSACTIONS|ACM\s+Conference|Preprint|Submitted\s+to).*', stripped, re.IGNORECASE):
                    continue
                cleaned_lines.append(line)

            page_content = "\n".join(cleaned_lines)
            # Reconnect broken words (hyphenation at line ends)
            page_content = re.sub(r'(\w+)-\n(\w+)', r'\1\2', page_content)
            pages_text.append(page_content)

        return pages_text

    @staticmethod
    def is_section_heading(line: str) -> tuple[bool, str]:
        """
        Detects whether a given line is an academic section heading.
        Returns (is_heading, normalized_title).
        """
        line = line.strip()
        if not line or len(line) > 75:
            return False, ""

        # Must start with uppercase letter, Roman numeral, or non-zero digit
        if not (line[0].isupper() or (line[0].isdigit() and line[0] != '0')):
            return False, ""

        # End markers: References / Bibliography
        if re.match(r'^(?:(?:\d+\.?\s+|[IVXLCDM]+\.?\s+)?(?:References|Bibliography))\b', line, re.IGNORECASE):
            return True, "References"

        # Check Abstract
        if re.match(r'^Abstract\b[:.]?', line, re.IGNORECASE):
            return True, "Abstract"

        # Check Numbered Headings (e.g., 1 Introduction, 2.1 The Model, III. EXPERIMENTS)
        m_num = re.match(r'^([1-9]\d*(?:\.\d+)*|[IVXLCDM]+)\.?\s+([A-Z][A-Za-z0-9\s\-–—:,]{2,60})$', line)
        if m_num:
            title = m_num.group(0).strip()
            # Must not end in sentence punctuation
            if not title.endswith('.') and not re.search(r'\b(is|are|was|were|can|with|for|that|which)\b\s*$', title, re.IGNORECASE):
                return True, title

        # Check Known Academic Section Keywords
        kw_pattern = (
            r'^(?:(?:(?:[1-9]\d*\.?\d*\.?|[IVXLCDM]+\.?)\s+)?)'
            r'(Introduction|Related\s+Work|Background|System\s+Overview|System\s+Model|'
            r'Methodology|Method|Proposed\s+Method|Proposed\s+Framework|Architecture|Algorithm|'
            r'Implementation\s+Details|Experiments?|Experimental\s+(?:Setup|Results|Evaluation)|'
            r'Evaluation|Results?(?:\s+and\s+Discussion)?|Discussion|Ablation\s+Study|'
            r'Comparative\s+Analysis|Limitations|Conclusions?(?:\s+and\s+Future\s+Work)?|'
            r'Future\s+Work|Appendix(?:\s+[A-Z0-9]+)?)\b'
        )
        m_kw = re.match(kw_pattern, line, re.IGNORECASE)
        if m_kw and len(line.split()) <= 10 and not line.endswith('.'):
            return True, line

        return False, ""

    def extract_sections(self, max_chunk_chars: int = 3500) -> List[Dict[str, Any]]:
        """
        Extract document into logical sections with dynamic, accurate section titles.
        If a section is longer than max_chunk_chars, it is split into sub-chunks with Part designations.
        Automatically stops before 'References' / 'Bibliography'.
        """
        clean_pages = self.extract_clean_pages()
        raw_lines = []
        for page in clean_pages:
            for l in page.splitlines():
                stripped = l.strip()
                if stripped:
                    raw_lines.append(stripped)

        raw_sections = []
        cur_title = "Abstract / Overview"
        cur_lines = []

        for line in raw_lines:
            is_h, h_title = self.is_section_heading(line)
            if is_h:
                if h_title.lower() in ("references", "bibliography"):
                    break
                if cur_lines:
                    text_block = "\n".join(cur_lines).strip()
                    if text_block:
                        raw_sections.append({"title": cur_title, "text": text_block})
                    cur_lines = []
                cur_title = h_title
            else:
                cur_lines.append(line)

        if cur_lines:
            text_block = "\n".join(cur_lines).strip()
            if text_block:
                raw_sections.append({"title": cur_title, "text": text_block})

        # If opening section is just short title/authors (< 300 chars) and next is Abstract, merge them
        if len(raw_sections) >= 2 and raw_sections[0]["title"] == "Abstract / Overview" and len(raw_sections[0]["text"]) < 300 and raw_sections[1]["title"].lower().startswith("abstract"):
            raw_sections[1]["text"] = raw_sections[0]["text"] + "\n\n" + raw_sections[1]["text"]
            raw_sections.pop(0)

        # If no headings matched, fallback to single block chunking
        if not raw_sections:
            full_text = "\n\n".join(clean_pages).strip()
            if full_text:
                raw_sections.append({"title": "正文内容", "text": full_text})

        # Process each section into chunks if needed
        final_sections: List[Dict[str, Any]] = []

        for sec in raw_sections:
            title = sec["title"]
            text = sec["text"]

            if len(text) <= max_chunk_chars:
                final_sections.append({
                    "title": title,
                    "content": text,
                    "section_index": len(final_sections) + 1
                })
            else:
                # Split large section into sub-chunks by paragraphs or lines
                paras = [p.strip() for p in text.split("\n") if p.strip()]
                chunks = []
                current_chunk = []
                current_len = 0

                for para in paras:
                    if current_len + len(para) > max_chunk_chars and current_chunk:
                        chunks.append("\n".join(current_chunk))
                        current_chunk = [para]
                        current_len = len(para)
                    else:
                        current_chunk.append(para)
                        current_len += len(para)

                if current_chunk:
                    chunks.append("\n".join(current_chunk))

                total_sub = len(chunks)
                for part_idx, chunk_text in enumerate(chunks, 1):
                    sub_title = f"{title} (Part {part_idx}/{total_sub})" if total_sub > 1 else title
                    final_sections.append({
                        "title": sub_title,
                        "content": chunk_text,
                        "section_index": len(final_sections) + 1
                    })

        return final_sections
