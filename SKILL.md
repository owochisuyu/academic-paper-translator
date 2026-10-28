---
name: academic-paper-translator
description: >-
  Translate academic research papers (PDF, LaTeX, Markdown) into publication-standard Chinese
  or sentence-by-sentence bilingual (English + Chinese) editions. Extracts and embeds original
  high-resolution figures, formats academic booktabs tables (preserving quantitative results,
  translating qualitative ones), renders vector mathematical formulas, integrates with Zotero
  storage directories, and compiles publication-grade PDFs via Typst.
---

# Academic Paper Translator & Publication-Grade Typesetting

Translate academic papers into publication-standard Chinese or sentence-by-sentence bilingual editions with high-fidelity formatting, original figure embedding, academic three-line tables (booktabs), and native vector equations.

---

## Core Capabilities & Deliverables

1. **Publication-Grade Academic Chinese**: Rigorous domain terminology, formal academic tone, complete preservation of in-text citations `(Author, Year)` and `[1]`.
2. **Four Presentation Modes**:
   * **Mode A (Recommended)**: Sentence-by-sentence interlinear bilingual (逐句交替精读版：英文原句 + 学术深蓝中文译句交替呈现).
   * **Mode B**: Full Chinese publication PDF & Markdown (纯中文出版级全译版).
   * **Mode C**: Paragraph-level bilingual (段落级交替对照).
   * **Mode D**: Dual-column parallel layout (左右双栏平行对照).
3. **High-Resolution Figure Extraction & Embedding**: Automated cropping/extraction of all figures (diagrams, heatmaps, boxplots, trajectories, CDF curves) from the original PDF at 250+ DPI using PyMuPDF.
4. **Three-Tier Academic Table Strategy**:
   * *Quantitative Tables*: Kept in original English with full numerical accuracy and booktabs rules.
   * *Qualitative Tables*: Translated into fluent academic Chinese.
   * *Mixed Tables*: Variable names/acronyms preserved; explanatory text translated.
5. **Vector Math Equations**: All formulas from Eq. (1) to end restored in LaTeX/Typst vector notation with unified numbering.
6. **Zotero Storage Auto-Sync**: Automatically detects paper storage directory in Zotero (e.g. `E:\Document\Zotero\storage\<KEY>\`) and saves the resulting `.pdf` and `.md` deliverables directly into that folder.

---

## Step-by-Step Translation Workflow

### Phase 1: Input Analysis & Environment Setup
1. **Locate Input Paper**:
   * If user mentions a paper title or Zotero, check `E:\Document\Zotero\storage\` for matching item directory.
   * Identify the source PDF or Markdown file.
2. **Verify Tool Availability**:
   * Ensure `typst` and `pymupdf` (fitz) are installed in Python (`pip install typst pymupdf`).
3. **Extract Figures**:
   * Run figure extraction script:
     ```bash
     python C:/Users/28048/.gemini/config/skills/academic-paper-translator/scripts/extract_paper.py "<path_to_pdf>" --out "paper_figures" --dpi 250
     ```

### Phase 2: Translation & Academic Guidelines
1. **Table Handling Rules**:
   * Refer to [table_guidelines.md](./references/table_guidelines.md) for strict classification.
   * Never round, truncate, or hallucinate numbers, standard errors, or p-values.
2. **Citations & Bibliography**:
   * Preserve all in-text citations like `(Teunissen, 1995)` and numerical markers like `[12]`.
   * Omit the final `References` list from translated outputs unless explicitly requested.
3. **Mathematical Formulas**:
   * Preserve every symbol, Greek letter, sub/superscript, and matrix notation.
   * In Typst, use `hat(a)` for $\hat{a}$, `caron(a)` for $\check{a}$, `mat(...)` for matrices, and avoid raw LaTeX backslashes inside `$ ... $`.
4. **Sentence Alignment (for Bilingual Mode)**:
   * Use [align_bilingual.py](./scripts/align_bilingual.py) to split English into sentences and pair each with its corresponding Chinese translation.

### Phase 3: Typst Typesetting & PDF Compilation
1. **Apply Template**:
   * Refer to [layout_templates.md](./references/layout_templates.md) for page margins, running headers, bilingual block macros (`bip`), headings (`h1`, `h2`), and table styling.
2. **Windows Safe UTF-8 Encoding**:
   * Always write files explicitly with `encoding='utf-8'` in Python to prevent Windows CP936/GBK code page corruption.
3. **Compile to PDF**:
   ```python
   import typst
   typst.compile("paper.typ", output="paper.pdf")
   # Optional: generate page previews
   typst.compile("paper.typ", output="preview_{p}.png", format="png", ppi=150)
   ```

### Phase 4: Verification & Deployment
1. **Quality Check**:
   * Verify PDF total pages with `pypdf`.
   * Verify font embedding and visual layout of page 1 and sample figure pages.
2. **Deploy to Target Destination**:
   * Copy the output `.pdf`, `.md`, and `paper_figures/` directly to the target destination (e.g., Zotero storage folder).
3. **Clean Up Intermediate Artifacts**:
   * Delete temporary scripts, text dumps, and intermediate PNG previews in the workspace.


## Multi-Agent Collaborative Translation for Long Papers (长篇论文多Subagent并行协同翻译架构)

When handling comprehensive long papers (>10 pages / >8,000 words), relying on a single context window can risk unintended summarization or paragraph condensation. This skill incorporates a **Modular Multi-Subagent Pipeline**:

1. **Document Decomposition**:
   - Master agent segments the raw PDF text into logical academic sections:
     - sec1: Title, Abstract, Keywords, Section 1 Introduction
     - sec2: Section 2 Mathematical Foundations & Principle
     - sec3: Section 3 Methodology & Feature Engineering (Tables 1-2, Figs 1-7)
     - sec4_1: Section 4.1 Benchmark Evaluation & Feature Importance (Tables 3-6, Figs 8-9)
     - sec4_2: Section 4.2 RTK Positioning Performance (UGV, CAR, SmartPNT-POS, Tables 7-12, Figs 10-20)
     - sec5: Section 5 Conclusions & Back Matter
2. **Subagent Specialization**:
   - Spawns parallel paper-translator subagents with role-specific system prompts.
   - Each subagent extracts 100% verbatim sentences directly from PyMuPDF block streams, ensuring **zero condensation, zero omission, and zero paraphrasing**.
3. **Cross-Agent Validation & Reassembly**:
   - Master agent audits sentence counts and equation labels against original PDF block offsets.
   - Reassembles bilingual (.typ, .md) and pure Chinese (.typ, .md) deliverables and compiles them via Typst.
