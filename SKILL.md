---
name: academic-paper-translator
description: >-
  Translate complete academic papers into publication-grade Side-by-Side Bilingual
  (_bilingual.pdf) and Chinese Monograph (_zh.pdf) editions using Typst, with
  automatic local Zotero lookup and attachment mounting. Preserves 100% verbatim
  English figure captions, inline math flow, full affiliation addresses, and
  three-line booktabs tables.
---

# Academic Paper Translator (Zotero-Integrated Publication Edition)

Translate complete academic papers into faithful, publication-grade **Side-by-Side Bilingual (`_bilingual.pdf`)** and **Chinese Monograph (`_zh.pdf`)** editions, and automatically mount them under the corresponding local Zotero item.

## Core Quality & Typesetting Rules

1. **Zero Omission & Quantitative Fidelity**:
   - Translate every section, paragraph, equation description, table, caption, and backmatter statement (Data Availability, Acknowledgements, Funding, Author Information, Ethics) sentence by sentence without summarizing or skipping.
   - Preserve all numbers, tolerances (`±`), percentages, citations, and 100% of the reference list.
2. **Mathematical Formulas (Inline vs. Display)**:
   - **Inline math** MUST remain strictly inline inside paragraphs (`$expr$` with NO spaces inside the dollar signs and NO outer `{...}` braces around the expression, as `{...}` in Typst renders literal visible curly braces).
   - Define multi-letter upright/italic math identifiers in the Typst preamble (e.g., `#let sys = math.upright("sys")`, `#let IF = math.upright("IF")`, `#let fix = math.upright("fix")`, `#let float = math.upright("float")`, `#let odot = symbol("⊙")`) so Typst never fails on multi-letter identifiers.
   - **Display equations** use numbered block math (`#set math.equation(numbering: "(1)")`) matching the original paper's exact equation numbering.
3. **Figures & Captions**:
   - Extract high-resolution figures (supporting both standard above-caption and Springer side-caption layouts) via `scripts/extract_figures.py` or native PDF image xrefs.
   - **100% Verbatim English Captions**: Never rewrite or paraphrase the English figure/table captions in the bilingual edition. Always pair the exact original English caption with its faithful Chinese translation.
4. **Full Author & Affiliation Metadata**:
   - Preserve complete author names, superscript affiliation indices (`#super[1,2]`), corresponding author asterisks (`#super[1\*]`, escaped as `\*`), email addresses (`\@` escaped), and full street addresses/postcodes.
   - Use `#super[1]` instead of `1. ` at line starts inside `#align(center)` blocks to avoid triggering Typst's ordered list (`enum`) indentation.
5. **Typst Escape Rules**:
   - Always escape literal asterisks `\*` in author superscripts/footnotes, literal `\@` in emails, and never prefix table header text with `#` inside `[*{header}*]`.

## Standard Fast Workflow

### Step 1: Locate Paper in Zotero & Extract Assets
Search the local Zotero database (`E:\Document\Zotero\zotero.sqlite`) by title or DOI:
```bash
python "C:\Users\28048\.gemini\config\skills\academic-paper-translator\scripts\zotero_mount.py" --find "<Paper Title or DOI>"
```
Extract high-resolution figures (including Springer side-caption figures):
```bash
python "C:\Users\28048\.gemini\config\skills\academic-paper-translator\scripts\extract_figures.py" "<path-to-pdf>" --out "paper_figures"
```
If the paper has a DOI from Springer Nature / open HTML source, fetch the official HTML or parse the PDF to obtain 100% exact LaTeX equations, tables, and reference lists.

### Step 2: Build & Compile Side-by-Side Bilingual and Chinese Monograph PDFs
Generate the Typst source files (`_bilingual.typ` and `_zh.typ`) and Markdown exports (`_bilingual.md` and `_zh.md`), then compile via `typst.compile()`:
- `<Title>_bilingual.pdf`: A4, `#let bipara(en, zh) = grid(columns: (1fr, 1fr), column-gutter: 18pt, ...)` with full-width centered display equations, three-line booktabs tables, and high-res figures.
- `<Title>_zh.pdf`: Chinese academic monograph edition with `Source Han Serif SC` typography, `first-line-indent: 2em`, and Chinese captions/headers.

### Step 3: Visual Verification
Render key pages (Page 1 title/abstract, equation pages, table/figure pages, backmatter) to PNG using `pymupdf` (`fitz`) and inspect them with `read_file` to verify:
- No stray `{...}` braces in inline math;
- Proper centering of affiliation lines;
- Complete figures and three-line tables without clipping.

### Step 4: Automatic Zotero Local Attachment Mounting
When Zotero is closed, automatically mount both compiled PDFs under the parent item in `zotero.sqlite`:
```bash
python "C:\Users\28048\.gemini\config\skills\academic-paper-translator\scripts\zotero_mount.py" --parent-id <itemID> --bilingual "<Title>_bilingual.pdf" --zh "<Title>_zh.pdf"
```
Also archive the `.typ` sources, `.md` files, and extracted figures folder into the original PDF's Zotero storage directory.
