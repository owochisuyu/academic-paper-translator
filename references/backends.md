# Optional extraction and source backends

## Native PDF and source overlays

Native extraction uses PyMuPDF text/geometry and table evidence. Geometry ordering,
raw glyph text and region accounting preserve occurrence and location. Parser
uncertainty still needs source inspection; a successful extraction is not a proof
that every visible word was recognized.

The tested PyMuPDF 2.0 installation includes local layout models and imports numeric
runtime dependencies. Its `Page.find_tables()` uses `use_layout=True` by default;
therefore the native backend can run local ONNX layout inference. This is document
analysis, not translation. See the [PyMuPDF changelog](https://pymupdf.io/changelog).
New job metadata records the installed version, table API and observed layout
default. Do not assume the native path has negligible memory cost.

The real five-page EKF_CAL test demonstrated why `use_layout=False` is not a safe
automatic fallback: it returned zero tables on every page, although the source has
four tables. Combining it with horizontal/vertical text strategies instead produced
false page-sized grids (including 61×9, 62×7 and 49×7). The default layout path,
followed by native glyph and row refinement, recovered all four reviewed tables
(7×2, 12×2, 9×2 and 7×2 including headers) with all 70 cells matching an independent
visual transcription.

One local rerun failed ONNX allocation on pages 4–5 while other work was active.
Those failures remained `table_detection_failed` / `NEEDS_REVIEW` in its frozen
job. A serial retry succeeded without warnings; the failed evidence was retained.
Run resource-intensive extraction and render jobs serially on constrained hosts.
For the tested Windows/Python setup the retry used `OPENBLAS_NUM_THREADS=1`; setting
`OMP_NUM_THREADS=1` also bounds OpenMP pools. Neither setting guarantees enough
memory. A failed detector must be retried or independently reviewed, never silently
replaced with the incomplete line-only result.

Native extraction retains exact source regions and records normalization edits.
Cross-page continuations and hanging reference fragments retain separate raw parts
and locators. Line-break hyphens are removed only when the joined spelling is
witnessed elsewhere in the same document; other printed hyphens remain. DOI
continuations are joined only at an incomplete DOI suffix separator. Repeated
running matter is explicitly listed in the exclusion ledger; summary fields
`excluded_regions` and `running_matter_regions` count it separately from omitted
translation-unit IDs.

Figures are discovered from the specified input, written with document/asset hashes,
and explicitly connected. Existing figure files can be supplied using
`--assets-manifest`; the program never scans another document's directories.

For readable text inside an image, the agent can transcribe a source supplement
before freezing a NEW extraction:

```text
python scripts/extract_document.py paper.pdf --job-dir runs/paper-v2 --source-overlay labels.json
```

Overlay shape (actual values required):

```json
{
  "source_sha256": "actual-original-PDF-hash",
  "reviewer": {"type": "agent", "id": "actual source reviewer"},
  "regions": [{
    "region_id": "p4:b1",
    "complete": true,
    "evidence": "Actually inspected the entire region; semantic labels transcribed, numeric ticks retained in the image.",
    "units": [{"source": "Position Error [mm]", "kind": "figure_label", "bbox": [170, 350, 190, 430]}]
  }]
}
```

Use the current unresolved region's ID and original unrotated PDF-point coordinates.
The supplement contains source transcription, not a Chinese replacement for unread
English. Every bbox must lie in the original region; an empty waiver is rejected.
Partial transcription uses complete:false and keeps the warning. Preserve numeric
scales and original images; translate semantic label units through the normal model
workflow. Do not guess unreadable words or mark a region complete after reading
only one label. The overlay and reviewer provenance become part of the new snapshot.

## MinerU saved-result import

MinerU is an optional parser/OCR producer, not a translation engine in this skill.
The importer targets the documented MinerU 4 `docvortex.middle` version 2.0 saved
result contract. It is not the CLI preview/continuation response. See the
[official output contract](https://opendatalab.github.io/MinerU/reference/output_files/).

Supply a local package envelope with:

- source_sha256: exact original PDF digest;
- result: the actual saved MiddleJson object, including its schema/version,
  producer metadata, all pages and complete-document indicator;
- coordinate_system: unrotated_top_left_pt or normalized_1000 when known;
- producer_run: actual runtime/version/configuration evidence as available.

Keep image assets next to the saved result as required by its relative paths.
Unknown fields/types needed for content are not silently discarded. Incomplete page
sets, duplicate pages, source mismatch, failed/partial jobs and unsupported schemas
are rejected. Missing confidence/coordinate certainty is reported for review.

```text
python scripts/extract_document.py paper.pdf --job-dir runs/paper-ocr --backend mineru --mineru-result mineru-package.json
```

This implementation does not install or launch MinerU, download its models, or
silently upload a file. Live OCR quality must be validated separately for the actual
producer version. Contract fixtures verify the importer, not OCR accuracy. Native
`--backend auto` currently chooses native extraction; it never turns partial native
output into a successful remote parse.

## Markdown

Markdown-it parses headings, paragraphs, lists, fenced code, links and pipe tables.
Code and URLs are preserved; text is translated. Empty cells have structural records.
HTML, unsupported inline assets and uncertain constructs remain explicit review
items. The Typst edition is a reflow view, not a promise to preserve arbitrary HTML
or exact original Markdown typography.

## LaTeX source projects

```text
python scripts/extract_document.py source-project --main-file main.tex --job-dir runs/tex-paper
python -m scripts.backends.latex_backend --job-dir runs/tex-paper --out translated-project
python -m scripts.backends.latex_backend --job-dir runs/tex-paper --out translated-project-compiled --engine xelatex
```

The final two commands require current merged data and preflight. The first exports
sources only; the second also requests an installed compiler. Work on a copy, retain
include/input files, pictures, bibliography and class/style dependencies. Multiple
main files need an explicit --main-file.

The bounded parser protects math, references/citations and formatting tokens; unknown
macros or source intervals remain review items. It does not claim full TeX language
interpretation. The project must support Chinese text/fonts; choosing an engine is
not equivalent to making every journal template CJK-compatible. Compilation uses
bounded runs with shell escape disabled and records undefined references. Missing
engines are NOT_RUN, never a successful PDF result.

Typst supports only the explicitly implemented math subset. It refuses unsupported
commands instead of passing raw LaTeX through. Original PDF formulas may use a bound
graphic fallback; label this as preserved graphics, not native vector mathematics.

## BabelDOC / PDFMathTranslate-next

The model used by this skill stays in charge of translation. The
[next whole-PDF API](https://github.com/PDFMathTranslate/PDFMathTranslate-next/blob/main/docs/en/advanced/API/python.md)
invokes its configured translation pipeline; it is not an adapter for this job's
translations.jsonl. A future renderer-only adapter requires a pinned compatible
version, supplied-text injection, a no-translation-call test, and source-ID/output
mapping evidence.

A source/runtime feasibility probe was performed during development. No BabelDOC
render adapter is enabled here. The existing `skip_translation` option by itself
does not inject our translated text. Local lack of dependencies/assets is not proof
that such an adapter is impossible. Keep any external experiment separate from the
accepted native pipeline and retain this skill's completeness checks.
