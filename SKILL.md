---
name: academic-paper-translator
description: >-
  Translate complete academic papers into faithful Chinese or sentence-aligned
  bilingual editions using the current agent. Extract PDF, Markdown, or supported
  LaTeX sources into a traceable manifest, preserve figures and quantitative content,
  review translations independently, and verify the actual PDF before acceptance.
---

# Academic paper translation

Use the current agent/model to translate meaning in context. Preserve every claim,
condition, exception, comparison, number, citation, and formula. Natural Chinese
syntax is welcome; summarization and invented explanations are separate work.
The scripts organize and check the work; they do not perform semantic translation.

## Choose the route

- **PDF**: native text/geometry extraction and Typst reflow. Every image/scan region
  has an explicit disposition; a native header never proves its body was read.
- **Markdown**: structured headings, paragraphs, lists, pipe tables, and code.
- **LaTeX**: use source extraction/export for supported constructs. For custom
  macros, generated text, conditional branches or unsupported environments, compile
  a source copy with a real TeX engine and import its PDF into the normal translation
  workflow. Read [latex_protocol.md](references/latex_protocol.md). Compilation
  resolves what is printed; it does not replace source/visual or translation review.
- **Scans/complex PDFs**: prepare image-reading packets for the current vision agent,
  independently check the transcription, then import a source overlay into a new job.
  See [ocr_protocol.md](references/ocr_protocol.md). A complete, source-bound saved
  MinerU result is another route; live MinerU execution is not bundled.

Read [data_contract.md](references/data_contract.md) for job/record schemas,
[translation_protocol.md](references/translation_protocol.md) for model work,
and [backends.md](references/backends.md) only when using source/OCR backends.
For presentation details use [layout_templates.md](references/layout_templates.md)
and [table_guidelines.md](references/table_guidelines.md).

## Workflow

Run scripts from this skill's directory, or invoke their absolute paths. Core
packages are listed in `requirements.txt`; use a project environment. Runtime
versions used in verification are recorded there, not assumed installed everywhere.

1. **Extract a frozen job.** Choose a new directory for each extraction version.

   ```bash
   python scripts/extract_document.py paper.pdf --job-dir runs/paper --scope full
   ```

   Inspect `document.json`, `scope_manifest.json`, `regions.jsonl`, and the original
   pages. Full scope includes body, notes, appendices, references, captions, table
   text, and semantic figure labels. Names, identifiers, equations, code and numeric
   ticks may be preserved explicitly. Repeated headers/footers may be excluded with
   provenance; body exclusions require a partial scope, not a reviewer waiver.

   If extraction has gaps, fix or supplement it and create a new job. For scanned
   regions run `prepare_ocr.py --job-dir runs/paper` and follow the OCR protocol.
   A vision-capable agent reads the actual high-resolution images; another reviewer
   compares its transcription with those images before `--source-overlay` import.
   Saving an image does not translate its labels. Do not edit `source.jsonl` to erase
   a warning.

   For complex LaTeX, switch to the compiled-PDF route when static extraction cannot
   establish the printed content. Retain source snapshots, compiler logs, the PDF
   and every explicit compatibility adaptation. Do not translate inactive branches
   as part of the visible paper or waive an unknown macro to make a check pass.

2. **Set terminology and prepare context packets.** Write approved terms into
   `glossary.json` where useful, then prepare work for the current agent.

   ```bash
   python scripts/prepare_translation.py --job-dir runs/paper
   ```

   Read the active manifest in `batches/translation_manifest.json`. Translate each
   assigned ID using its packet's paragraph, neighbouring context, and approved
   terms. Write only its assigned records to its indicated response JSONL. Copy
   supplied hashes exactly. Unassigned context is not another translation task.
   Run packets sequentially or with available, authorized subagents; the workflow
   does not require an external translation API.

3. **Merge and independently review.** A deterministic join is not semantic QA.

   ```bash
   python scripts/merge_translation.py --job-dir runs/paper
   python scripts/prepare_translation.py --job-dir runs/paper --review
   ```

   An independent model invocation or reviewer reads each review packet, compares
   source and target, assesses every required category and writes its response.
   Do not generate PASS records in a loop without actually reviewing the content.
   Record unresolved findings with exact source quotes. Correct affected translations,
   re-merge, and re-review their changed hashes. Allow at most two automatic repair
   rounds before keeping unresolved items for focused review.

   ```bash
   python scripts/merge_translation.py --job-dir runs/paper --review-batches
   python scripts/validate_translation.py --job-dir runs/paper
   ```

   `checks/preflight.json` separates extraction, structure, facts, assets and
   semantics. NOT_RUN/NEEDS_REVIEW is not PASS. Glossary or generation changes
   invalidate the corresponding saved provenance. Follow the actual report; do not
   lower coverage thresholds or replace missing translations with the English source.

4. **Compile a candidate and inspect the actual output.** Mode A is the default.

   ```bash
   python scripts/compile_typst.py --job-dir runs/paper --mode A --output-stage candidate
   python scripts/verify_output.py --job-dir runs/paper --no-promote
   ```

   Inspect all generated previews and the source figure/formula regions. Check
   clipping, overlap, glyphs, table identity, labels and page transitions. Record
   the actual PDF hash, reviewed page list, reviewer and findings in
   `reviews/visual.json`, following the data contract. Then run:

   ```bash
   python scripts/verify_output.py --job-dir runs/paper
   ```

   Only a current, fully checked candidate is copied to `deliverables/`. It includes
   PDF, Markdown, Typst source, assets and acceptance metadata. Describe the scope
   and review methods; neither hashes nor model review prove universal correctness.

## Incomplete work and compatibility

`--output-stage draft` produces an explicitly marked preview and may show missing
translations/assets. It is never an accepted deliverable. Old positional compiler
inputs require explicit draft mode; use a canonical job for formal verification.

To import old translations, first extract the same source into a job, then use
`merge_translation.py --job-dir ... --translated old.jsonl --import-legacy`.
Source metadata conflicts, missing/extra IDs, and malformed records fail. The import
still needs independent review and current preflight.

Keep supported source syntax and unverified features explicit. No hard-coded Zotero
folder, automatic service upload, default replacement of the chosen model, or promise
of publication readiness is part of this skill. Deliver to the requested directory.
