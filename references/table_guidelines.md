# Table preservation and translation

Every table has its own table_id. Captions and cells refer to that ID; document order alone must not associate them. Each cell records zero-based row/column, rowspan/colspan, original text, action and source locator. Empty cells are explicit records, so an empty cell can be distinguished from an omitted one.

Translate descriptive headers, categories, table captions and notes. Preserve exact numbers, symbols, uncertainty, significant digits and scientific notation under the selected display policy. Unknown units or ambiguous cells remain review items; do not replace them with inferred values. Bibliographic or mathematical content marked preserve retains the source.

The renderer groups all cells by exact table_id, detects overlapping coordinates and refuses incomplete canonical grids. It emits a breakable Typst table with top/bottom rules; cells contain source and target in bilingual modes, and target text in Mode B. Row and column spans are retained. Header repetition is not inferred from the first row; inspect long tables at page boundaries. Caption-only tables are rejected in candidate mode. An explicit draft can display their incomplete state.

Markdown exports simple grids as pipe tables and spanning cells as HTML tables. Avoid manually rebuilding a different table for Markdown, since that could diverge from the PDF.

Check the actual output, including same-page multiple tables, long cells, spans, numeric precision, captions and table notes. A successful compile does not establish table completeness. Table-cell text is included in post-render occurrence accounting; visual inspection checks alignment, clipping and context.
