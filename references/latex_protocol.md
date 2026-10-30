# Translating papers supplied as LaTeX projects

Use the printed paper as the source of truth for full-paper content. A TeX project
can generate text, references, counters, tables and entire sections through macros
and conditions. A bounded static parser cannot decide every such expansion. The
compiled-PDF route delegates these semantics to a real TeX engine, then uses the
same source checks, model translation and independent output verification as PDFs.

## Compile a separate source snapshot

`compile_latex_source.py` supports an installed or portable Tectonic executable.
It does not install an engine or silently download packages. Select the main file
explicitly, even if an archive contains duplicate root projects.

```text
python scripts/compile_latex_source.py --project source-project --main-file main.tex --out-dir builds/paper --engine-path /path/to/tectonic --cache-dir engine-cache --timeout 180
```

The default uses cached resources only. Add `--allow-network` when downloading TeX
packages is authorized. First-time format/package downloads may need a longer
bounded timeout. Preserve failed attempts; use a new output directory for a retry
and reuse the cache. A package download timeout is different from a source error.

Inputs are frozen under `source/`; the engine runs on a separate `run/` copy, with
shell escape disabled and untrusted mode enabled. `compile_result.json` records
the source input set, engine version/hash/command, exit state, logs, generated files
and PDF hash/page count. A zero exit code without a fresh PDF is not success.
Missing characters, unresolved references and incomplete reruns remain blocking.
Missing included chapters also block acceptance even if TeX returns zero. The
engine's dependency record must bind every consumed local input to the frozen
project or this run's generated files; external or unknown inputs require review.
Untrusted mode disables dangerous TeX facilities but is not an operating-system
filesystem sandbox. The dependency audit rejects such output after compilation;
run unfamiliar projects in an isolated OS environment when read isolation is needed.
Overfull-box notices are retained for visual inspection instead of being treated
as missing content without examining the actual output.

Some templates explicitly select a pdfTeX driver while Tectonic uses XeTeX. Other
templates require their own engine/packages. Diagnose the actual compiler error.
If a compatibility edit is needed, make a separate adapted project and record it:

```text
python scripts/compile_latex_source.py --project adapted-project --original-project source-project --adaptation-reason "Actual reason for these template compatibility changes" --main-file main.tex --out-dir builds/paper-adapted --engine-path /path/to/tectonic --cache-dir engine-cache
```

The original and adapted source snapshots plus exact diffs remain available. Do not
remove a theorem, equation, example or appendix to make the project compile. A
font/driver change still needs a check for missing glyphs and changed pagination.

## Import the compiled paper

```text
python scripts/import_latex_pdf.py --compilation builds/paper/compile_result.json --job-dir runs/paper --scope full
```

Import requires a completed PASS compilation, current PDF/source/log hashes and
page count. All changed inputs must have explicit, matching adaptation records.
The new job freezes compilation evidence and source snapshots with its PDF, so
changing a macro or compiler output invalidates the job's input provenance.
Unknown blocking warnings cannot be hidden behind a manually changed status.
Saved report data never instructs this importer to execute a command.

Inspect the PDF, source units and source coverage. A compiler PASS does not prove
that a PDF parser read every word, table, formula label or figure. Resolve actual
image/graphic gaps with the [image-reading workflow](ocr_protocol.md), or import a
complete source-bound parser result. Both `--source-overlay` and the optional
`--backend mineru --mineru-result ...` are supported by `import_latex_pdf.py`.
Use a new job when adding an overlay; preserve its original source job and evidence.

### Preserve a fragmented native equation

A PDF parser can split one matrix or aligned equation into several text blocks.
Character coverage alone will not detect that its rows and columns were lost.
Inspect the actual source page and the native region ledger. For an equation with
no translatable words, the reviewing agent can record a complete crop and all of
its native text regions in a source-bound manifest:

```json
{
  "source_sha256": "actual SHA-256 of the compiled source PDF",
  "reviewer": {"type": "agent", "id": "actual reviewing agent"},
  "equations": [{
    "id": "equation-reviewed-001",
    "page": 1,
    "bbox": [100, 200, 500, 260],
    "region_ids": ["p1:b8", "p1:b9"],
    "complete": true,
    "no_translatable_text": true,
    "evidence": "Actual inspection findings, including matrix rows, brackets and equation number"
  }]
}
```

The coordinates above are illustrative. Use the real unrotated PDF point geometry
and unprefixed region IDs, never copy the sample coordinates into a real job.
Apply it during a new extraction:

```text
python scripts/import_latex_pdf.py --compilation builds/paper/compile_result.json --job-dir runs/paper-equations --equation-overlay reviewed-equations.json
```

`extract_document.py` accepts the same option for ordinary native PDFs. Every
selected region and owned text fragment must match the source PDF exactly; partial
groups, duplicate claims, and crops intersecting unrelated text are rejected. The
job freezes the exact manifest JSON as an input, embeds its records, reviewer and hash, keeps the original character
intervals and freezes the equation crop as an asset. It neither waives existing
warnings nor supplies a final visual PASS. Inspect the preserved matrix again in
the translated output. This first version rejects rotated pages and nonempty
`labels`; equations with English labels still need a complete text-aware source
or OCR workflow. Do not declare those words mathematical symbols to bypass review.

Then follow SKILL.md: prepare model translation packets, translate, independently
assess them, compile a candidate and inspect the actual output. The result of this
route is a Chinese or bilingual PDF reflow. It does not automatically rewrite the
original journal's LaTeX template into a Chinese-ready source project.

## Direct source translation/export

```text
python scripts/extract_document.py source-project --main-file main.tex --job-dir runs/source
```

The source parser keeps exact file intervals and protected TeX syntax. Supported
features include nested mathematical structures in table cells, explicit column
spans, appendix/subequations containers, footnote text, and visible text inside
supported math text commands. Mathematical symbols and operators remain protected;
English words in `intertext` or text labels must not be hidden as mathematical code.
Unknown macros, custom environments and bibliography setup retain review findings.

Use the compiled-PDF route if these findings depend on expansion or conditional
execution. Merely adding a macro name to a preservation list does not show that its
visible words were translated. A source export must keep protected syntax and
nonoverlapping intervals, including comment-ending newlines and nonbreaking spaces.
Compiling a translated source project additionally requires compatible Chinese
fonts/packages; successful compilation of the English original does not prove that.
