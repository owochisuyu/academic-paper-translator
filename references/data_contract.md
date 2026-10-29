# Canonical document jobs (schema 3)

The original files, extraction, source regions and assets are frozen at creation.
Create a new job for changed extraction or manual transcription. Hashes detect
stale/mixed work; they are not cryptographic authentication against someone
rewriting all artifacts deliberately.

## Files and identity

- `document.json`: schema, document ID, primary input, every copied input file and
  hash, extraction configuration, scope and frozen-file hashes.
- `source.jsonl`: source-owned ID, source/source_hash, kind, action, parent,
  order, section, locators and type-specific metadata. Actions are translate or
  preserve. Canonical IDs have a document-hash prefix; paragraph/table/region
  relationships are explicit.
- `regions.jsonl`: raw source regions, locators, raw/normalized text, unit IDs,
  disposition, parser evidence and uncertainty. PDF positions use points and
  unrotated top-left coordinates; text sources use file/character offsets.
- `assets.json`: exact asset_id, document ID, path, SHA-256, kind, source locators
  and owner IDs. Job-relative paths may not escape the job. No filename guessing.
- `scope_manifest.json`: full/body, counts, explicit exclusions and scope policy.
- `glossary.json`: version plus entries with source, preferred_translation,
  status (approved or candidate), optional scope/aliases/evidence_ids. Only approved
  matching entries constrain packets. Conflicting approved entries fail.
- `translation_policy.json`: actual prompt version and generation/context settings.
  Preparing translation packets creates this file; preparing review packets does
  not change it.

The copied primary input is `document.primary_input`, relative to the job root.
For a TeX project every discovered dependency is bound to the project identity.
PDF pages are 1-based; backend page indices are converted at the import boundary.

## Translation and review records

Use the hashes supplied in the active request packet. Minimal translation record:

```json
{
  "id": "document-prefix:source-id",
  "doc_id": "actual-document-hash",
  "source_hash": "actual-source-unit-hash",
  "source_manifest_hash": "actual-source-manifest-hash",
  "glossary_hash": "actual-glossary-hash",
  "generation_hash": "actual-translation-policy-hash",
  "translation": "仅在所有传感器完成标定时，该估计器才会降低误差。",
  "status": "translated",
  "producer": {"type": "agent", "model": "actual model", "run_id": "actual invocation"}
}
```

Hash strings above are illustrative, not valid example artifacts. `needs_review`
may replace `translated` when the source is uncertain; that cannot be accepted
without resolution. Translation metadata must not override source/kind/page/assets.
`merged.jsonl` is generated as an exact canonical source-plus-translation join;
preserve items receive their source representation from the program.

A semantic review has `id`, `doc_id`, `source_manifest_hash`, `source_hash`, `translation_hash`, `glossary_hash`,
`generation_hash`, `independent: true`, `reviewer: {name, method, run_id}`, `status`,
`checks`, and `findings`. Method is agent or human. All these categories must be
assessed with PASS/FAIL/NEEDS_REVIEW: omission, addition, conditions, negation,
causality, comparison, references, quantities, terminology. An unresolved finding
includes an exact source_quote, issue/type/severity/suggestion and resolved:false.
Resolved records must refer to the current target, not an earlier incorrect version.
A partial review is reported with its actual reviewed/required denominator.
Bind reviews to the complete source manifest as well as the individual unit:
re-extracting a corrected neighbouring paragraph invalidates an earlier context
assessment even when the reviewed sentence and its translation have not changed.
An old review without these bindings needs reassessment or an explicit provenance
migration by its actual reviewer from the same previously inspected packet.

For programmatic callers, `document_ir.translation_bindings(job)` returns the
current glossary/generation digests. Adding these to a translation records its
provenance; it does not verify that its wording follows the glossary. That remains
part of the independent terminology assessment.

## Source-region assessments

`prepare_translation.py --job-dir ... --source-review` creates a request template
with NOT_RUN statuses, never approvals. Supply actual assessments in
`source_reviews.jsonl`, bound by region_id, doc_id and
`region_hash = canonical_hash(frozen_region)`.

Each record needs reviewer {name,method}, status, decision and reason. Decisions:

- no_translatable_text: a visual region whose content is legitimately preserved.
- labels_covered: semantic labels map to existing units within that page/bbox.
- ocr_verified: existing OCR/manual-transcription units within that region were
  compared with the source. A native header or unrelated unit cannot clear this.
- excluded: only declared header/footer/page-number roles (plus explicitly excluded
  back matter in body scope). This cannot waive a missing full-scope body paragraph.

The latter two mappings use explicit unit_ids. To add actual missing words, use an
extraction overlay and create a new job; a review record cannot invent source units.
Assessments are applied to a copy of the ledger, preserving frozen files.

## Rendering and visual records

`checks/preflight.json` binds every source/input/asset, translations, reviews,
glossary/policy, source assessments, merged data, script hashes, Python version and
core dependency versions. A changed checker or renderer requires new preflight,
even when the document files are unchanged. All required check categories
must pass (or have a valid not-applicable condition). The compiler repeats the
binding checks. Changing a file after preflight requires a new preflight.

`checks/render_map.json` binds the produced PDF, Typst and Markdown files. The
verifier reconstructs expectations from merged data and reopens the actual PDF.
A manually created map alone is not evidence of rendered content.

After inspecting every actual page, record `reviews/visual.json`:

```json
{
  "pdf_hash": "actual-candidate-sha256",
  "page_count": 2,
  "reviewed_pages": [1, 2],
  "reviewer": {"name": "actual reviewer", "method": "agent"},
  "checks": ["layout", "graphics", "math", "text_visibility"],
  "status": "PASS",
  "findings": []
}
```

Do not fill this from page count alone. Text extraction cannot prove a glyph is
visible beneath an overlay, or that source cropping retained the intended diagram.
If review is incomplete, use NEEDS_REVIEW and list findings. Automatic hard failures
are not waived by a visual PASS. The accepted output records scope and actual
semantic/visual coverage; reruns with changed content invalidate old evidence.

## Status and error behavior

Check statuses are PASS, FAIL, NEEDS_REVIEW, NOT_RUN or NOT_APPLICABLE. Candidate is
not accepted. New job CLIs use exit 2 for invalid data/check failure, 3 for review
needed, 4 for missing backend dependencies, and 5 for execution failures where
supported. Legacy validator keeps exit 1 on failure for compatibility.

Bad nonempty JSONL lines, duplicate keys/IDs, non-string translations, cross-document
records and stale hashes fail with locations. No malformed line is silently skipped.
Partial external parser results never qualify as complete source extraction.
