# Layout and output verification

`compile_typst.py --job-dir JOB --mode A --output-stage candidate` consumes a canonical merged job and its current preflight report. The compiler checks input bindings again; legacy JSONL can only be compiled with explicit `--output-stage draft`.

| Mode | Actual presentation |
| --- | --- |
| A | Each source sentence followed by its target sentence. Preserved units appear once. |
| B | Target text for every translatable kind; explicitly preserved source remains. |
| C | Adjacent ordinary text with the same parent_id is grouped as one source/target paragraph. Protected TeX units retain their individual mapping. |
| D | A two-cell table for each corresponding paragraph, with English at left and Chinese at right. Long rows can continue across pages. |

A/C bilingual pairs are measured at their actual column width. Pairs up to 45mm high stay on one page; longer pairs may continue across pages so a large paragraph does not overflow.

The low-level `render_typst_document()` function is a preview API. Its default output is visibly marked DRAFT. It never searches the current directory for assets. A figure needs an exact asset ID or, for legacy drafts only, an explicit image path.

Ordinary strings are emitted using Typst string literals, so source/target text cannot execute Typst code. Unknown kinds and unsupported mathematical syntax are errors. The limited LaTeX converter handles equation, frac, sqrt, arithmetic, selected Greek symbols and operators. It does not support arbitrary macros or align environments. Graphic formulas retain their source image. Protected inline TeX math uses this same bounded converter; cite/ref tokens are shown with their keys in the Typst reading copy. Use the original LaTeX route for resolved bibliography and cross-reference numbering.

Markdown units also restore delimited inline `$...$` and `\(...\)` math through the bounded converter. Backtick code spans and ordinary currency stay literal. Unknown TeX macros and ambiguous/unclosed math require correction. Simple arithmetic and subscript/superscript formulas have a normalized glyph check against the actual PDF; complex formulas still require visual review because PDF extraction cannot prove their arrangement.

The Markdown export uses the same merged records, with table grids, HTML spans where necessary, protected source formulas, and relative image links. Candidate assets are copied beside the output so the PDF, Typst, Markdown and assets can be moved together.

## Actual output checks

Run `python scripts/verify_output.py --job-dir JOB` after compilation. It reopens the PDF, checks normalized text occurrences, required image fingerprints and text outside page boundaries, and renders every page into `checks/previews/PDF_SHA256/`. The report's `preview_dir` points to the current PDF's pages. It also rechecks copied portable assets and compares Markdown with the canonical merged export.

Text extraction alone cannot detect all hidden or overlapping content. Inspect every generated preview and compare source figures/formulas where needed. Record the actual assessment in `reviews/visual.json`:

```json
{
  "pdf_hash": "actual SHA-256 from checks/render_map.json",
  "page_count": 2,
  "reviewed_pages": [1, 2],
  "reviewer": {"name": "actual reviewer", "method": "agent or human"},
  "checks": ["layout", "graphics", "math", "text_visibility"],
  "status": "PASS",
  "findings": []
}
```

Do not fill a review automatically from an exit code. Any unresolved finding or unreviewed page prevents acceptance. Rerun the verifier after review; only a current candidate with successful checks is copied to `deliverables/`. Changed PDF, source, translations, reviews, assets or glossary invalidate the corresponding result.

Pixel fingerprints establish approximate raster-image identity, not source crop correctness. Vector graphics and mathematical presentation remain explicit visual-review items. Reflow changes page counts; matching the original page count is not an acceptance condition.
