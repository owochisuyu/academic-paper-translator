# Agent translation and independent review

The scripts prepare context and validate artifacts. They do not choose or secretly
call a translation service. The active agent can complete all packets sequentially;
parallel agents are optional and must have disjoint assigned ID sets.

## Translation

1. Read the current `batches/translation_manifest.json`, then each request file.
   Do not reuse an old directory glob as the active assignment list.
2. Read the instructions, full assigned items, parent/section context, neighbouring
   context_only records and approved terms. Translate every assigned item, including
   headings, table text, captions, notes, reference titles and figure-label text.
3. Preserve citations, identifiers, names, dates, DOI/URLs, formula placeholders and
   numbers. Translate reference titles without changing bibliographic identity;
   keep the original reference available in the bilingual/source record. If inline
   source syntax or a reference is ambiguous, report it instead of filling guesses.
4. Return only the stated record schema to the packet's response path. Use natural
   Chinese; do not substitute a sentence-level summary or add explanatory claims.
5. Merge active responses. Format failures affect the relevant batch. Missing IDs,
   extra IDs and duplicate IDs cannot be aligned heuristically into success.

Examples of facts that must survive translation: "only when", "unless", "at most",
"at least", "except", comparison baselines, direction of causality, confidence
levels and which sensor or variable a numeric value belongs to. Exact numeric
multisets alone cannot detect all semantic swaps.

Protected LaTeX tokens such as `⟦TEX0001⟧` are copied verbatim in the same order.
They are restored by the proper output backend, never translated into prose.
Unknown macros remain explicit source issues. Do not rewrite raw math syntax as if
LaTeX and Typst were interchangeable.

## Glossary, cache and context changes

Approved glossary entries can be section scoped. A candidate term is not a binding
choice. The source IDs supporting a term help resolve genuine polysemy. Avoid global
text replacement across names, formulas and previously valid phrases.

Requests are content addressed using source/context, matched terminology, prompt
version, model and generation settings. Files remain available after interruption;
`returned` means a response exists with the right ID set, not that QA passed.
Run prepare again to resume and inspect active batches. Changed global glossary or
translation policy invalidates saved provenance conservatively, even if an individual
term did not affect every sentence. Reuse requires a fresh correctly bound assessment,
not editing hash fields on an old response to make it appear current.

The default packet sizing uses a conservative serialized UTF-8 byte bound and
reserves output capacity. It is not a tokenizer benchmark. Configure smaller limits
if the host's actual tokenizer/context accounting requires them. A single overlarge
unit fails preparation rather than being truncated. Model-specific providers/rate
limits are not part of the core implementation.

## Independent semantic assessment

Run prepare with --review after all translations have been merged. Use an independent
invocation or reviewer, recording the actual identity and method. Review original
context, never just the translator's self-evaluation. Same-model independent calls
are allowed and must not be represented as a different model or human review.

For each ID inspect all required categories, including quantities and reference
identity. Findings must quote actual source text. Mark omissions and unsupported
additions even if structure/numbers pass. Copy `doc_id`, `source_manifest_hash`,
`source_hash`, `translation_hash`, `glossary_hash` and `generation_hash` from the
actual review packet. Every review is bound to the current document, full source
manifest, unit, target, glossary and generation policy. Correcting a neighbouring
source unit changes the manifest and invalidates reviews made with earlier context,
even if this unit's own wording is unchanged. A change to the target also invalidates
the old review. If only a subset was reviewed, keep the others NOT_RUN.

A repair cycle is translate affected IDs -> merge -> factual checks -> independent
review. Limit automatic repairs to two rounds and temporary execution retries to
three attempts. Persistent ambiguity stays NEEDS_REVIEW; a retry count never permits
silently dropping a sentence or clearing an unresolved finding.

The final PDF must undergo a separate visual check. A correct JSONL can still be
rendered with a wrong figure, clipped row or missing glyph.
