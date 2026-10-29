---
name: academic-paper-translator
description: >-
  Translate academic research papers (PDF, LaTeX, Markdown) into publication-standard Chinese
  or sentence-by-sentence bilingual (English + Chinese) editions. Extracts and embeds original
  high-resolution figures, formats academic booktabs tables (preserving quantitative results,
  translating qualitative ones), renders vector mathematical formulas, integrates with Zotero
  storage directories, and compiles publication-grade PDFs via Typst.
---

# Academic Paper Translator & Publication-Grade Typesetting (V2 Verified Engine)

Translate academic papers into publication-standard Chinese or sentence-by-sentence bilingual editions with 100% sentence-level auditability, original figure embedding, academic three-line tables (booktabs), and native vector equations.

---

## Core Capabilities & Deliverables

1. **Publication-Grade Academic Chinese**: Rigorous domain terminology, formal academic tone, complete preservation of in-text citations `(Author, Year)` and `[1]`.
2. **Four Presentation Modes**:
   * **Mode A (Recommended)**: Sentence-by-sentence interlinear bilingual (逐句交替精读版：英文原句 + 学术深蓝中文译句交替呈现).
   * **Mode B**: Full Chinese publication PDF & Markdown (纯中文出版级全译版).
   * **Mode C**: Paragraph-level bilingual (段落级交替对照).
   * **Mode D**: Dual-column parallel layout (左右双栏平行对照).
3. **Strict ID-Manifest Protocol & Verification**: Every extracted sentence, equation, table cell, and caption receives a deterministic unique ID (e.g. `sec01-p002-b005-s001`). Translation occurs strictly against this manifest to ensure 100% item mapping and bijection, preventing manifest-level omissions and tracking translation completeness through two-tier QA validation.
4. **Geometry-Aware Figure Extraction**: True graphic bounding box computation via PyMuPDF vector drawing paths and bitmap objects, preventing internal axis/legend text from truncating figures.
5. **Two-Tier Quality Assurance Engine**:
   * *Tier 1*: Source text layer coverage verification.
   * *Tier 2*: Strict ID bijection, zero empty translations, numerical/unit preservation, and formula/citation invariance check.
6. **Academic Three-Line Tables (Booktabs)**: Follows [table_guidelines.md](./references/table_guidelines.md) for full translation of descriptive text while preserving exact numerical matrices.
7. **Vector Math Equations**: LaTeX/Typst vector notation with unified numbering.
8. **Zotero Storage Auto-Sync**: Automatically detects paper storage directory in Zotero (e.g. `E:\Document\Zotero\storage\<KEY>\`) and saves deliverables directly into that folder.

---

## 4-Stage Verified Translation Pipeline

```
  [Source PDF / TeX / MD]
             │
             ▼
  ┌────────────────────────────────────────┐
  │ Stage 1: Document Extraction & Manifest │  --> scripts/extract_document.py
  │          (Generates source.jsonl)       │  --> scripts/extract_figures.py
  └────────────────────────────────────────┘
             │
             ▼
  ┌────────────────────────────────────────┐
  │ Stage 2: ID-Bound Translation Dispatch │  --> Subagents translate by ID sets
  │          (Zero-condensation mapping)   │  --> Returns {id: "translation"}
  └────────────────────────────────────────┘
             │
             ▼
  ┌────────────────────────────────────────┐
  │ Stage 3: Two-Tier Audit & Verification  │  --> scripts/validate_translation.py
  │          (100% ID coverage + invariants)│  --> Produces coverage_report.json
  └────────────────────────────────────────┘
             │ (Passes Audit)
             ▼
  ┌────────────────────────────────────────┐
  │ Stage 4: Unified Post-Acceptance Typst  │  --> scripts/compile_typst.py
  │          (Compiles publication PDF)    │  --> Mode A / B / C / D
  └────────────────────────────────────────┘
```

---

### Stage 1: Extraction & Manifest Generation

1. **Extract Structured Manifest**:
   ```bash
   python scripts/extract_document.py "<path_to_paper.pdf>" --out "source.jsonl" --scope full
   ```
   * `--scope full` (Default): Extracts title, abstract, sections, paragraphs, figure captions, tables, footnotes, and references.
   * `--scope body`: Extracts body text only (omitting back-matter references).

2. **Extract Figures with Graphic Geometry**:
   ```bash
   python scripts/extract_figures.py "<path_to_paper.pdf>" --out "paper_figures" --dpi 250
   ```
   * Computes graphic bounding box using vector path/image unions.
   * Supports both standard (`Fig. 1`) and supplementary (`Fig. S1`) figures.

---

### Stage 2: ID-Bound Translation Dispatching

Every translation unit is tied to its canonical `id`. The model or subagent must output a JSON dictionary or append to `translated.jsonl`:

```json
{
  "id": "sec02-p003-b007-s002",
  "kind": "sentence",
  "section": "2.1 Problem Formulation",
  "page": 3,
  "source": "INS drifts over time.",
  "translation": "惯性导航系统（INS）的定位误差会随时间累积并产生漂移。",
  "status": "translated"
}
```

#### Guidelines for Academic Chinese Translation:
* **Accuracy over Form**: Retain all technical nuances, conditions, causes, and consequences.
* **Citation Markers**: Preserve numerical citations `[1]`, `[1-3]` and author citations `(Teunissen, 1995)` verbatim.
* **Math Formulas**: Keep symbols ($R$, $\sigma$, $\lambda$) in native math format; do not translate math variables into Chinese characters.
* **Numbers & Units**: Never round, truncate, or hallucinate numbers (e.g. `0.25 m`, `95%`, `3.14 dB` must remain exact).

---

### Stage 3: Two-Tier Quality Assurance & Verification

Before compiling any deliverable, execute the two-tier verification script:

```bash
python scripts/validate_translation.py --source "source.jsonl" --translated "translated.jsonl" --doc "<path_to_paper.pdf>" --report "coverage_report.json"
# Or using documented aliases:
# python scripts/validate_translation.py --source "source.jsonl" --translated "translated.jsonl" --pdf "<path_to_paper.pdf>" --out "coverage_report.json"
```

The validator checks:
1. **Tier 1 Source Coverage**: Page accounting, non-empty text layer check (detecting scanned PDFs requiring OCR), and block coverage >= 90%.
2. **100% ID Bijection**: Missing IDs, duplicate source IDs, duplicate translation IDs, or extra unmapped IDs cause an immediate audit failure (`FAIL`).
3. **Zero Empty Translations**: No empty or whitespace-only translation strings.
4. **Number & Unit Invariance**: Extracts all exact numbers, signs, scientific notation, and physical units from source and asserts exact token presence in the translation (preventing substring false passes like 0.25 m matching 10.25 m or mm).
5. **Citation Invariance**: Asserts all bracketed `[1]` and author-year citations are preserved.
6. **Formula Invariance**: Asserts equations like `$x+y=z$` and equation references (e.g. `(1)`, `Eq. (2)`) are preserved.
7. **Semantic Polarity Check**: Flags negation discrepancies when source contains negative assertions ('not/cannot') but translation is purely positive.

If any check fails (status `FAIL`), fix the offending item before proceeding to typesetting.

---

### Stage 4: Unified Post-Acceptance Typesetting (Typst)

Once `coverage_report.json` reports `PASS`, compile the final document:

```bash
# Mode A: Sentence-by-sentence Bilingual Interlinear (Recommended)
python scripts/compile_typst.py "translated.jsonl" --mode A --out-pdf "paper_bilingual.pdf" --previews

# Mode B: Full Academic Chinese Publication
python scripts/compile_typst.py "translated.jsonl" --mode B --out-pdf "paper_chinese.pdf" --previews

# Mode C: Paragraph-level Bilingual
python scripts/compile_typst.py "translated.jsonl" --mode C --out-pdf "paper_par_bilingual.pdf"

# Mode D: Dual-column Parallel
python scripts/compile_typst.py "translated.jsonl" --mode D --out-pdf "paper_dual_column.pdf"
```

Deploy the generated `.pdf`, `.typ`, and `paper_figures/` directly to target destinations (e.g. Zotero storage).

---

## Multi-Agent Collaborative Architecture for Long Papers

For long papers (>10 pages / >8,000 words), use a generic, dynamic subagent partitioning protocol:

1. **Section-Based Partitioning**:
   The Master agent inspects `source.jsonl` and divides the item IDs into balanced partitions:
   - `subagent_1`: Section 1 (Title, Abstract, Introduction) -> `ids=[sec01-...]`
   - `subagent_2`: Section 2 (Theoretical Foundations & Mathematical Derivations) -> `ids=[sec02-...]`
   - `subagent_3`: Section 3 (Methodology & Architecture) -> `ids=[sec03-...]`
   - `subagent_4`: Section 4 (Experimental Results & Benchmark Evaluations) -> `ids=[sec04-...]`
   - `subagent_5`: Section 5 (Discussion, Conclusion & Appendices) -> `ids=[sec05-...]`

2. **Isolated Execution & Exact Return**:
   Each subagent receives its assigned list of items and returns an exact ID-to-translation map.

3. **Reassembly & Set-Theoretic Completeness**:
   The Master agent verifies:
   $$\bigcup_{k} \text{IDs}_k = \text{Manifest IDs}, \quad \text{IDs}_i \cap \text{IDs}_j = \emptyset \; (\forall i \neq j)$$
   Once reassembled, the master agent runs `validate_translation.py` and compiles the final deliverables.
