# -*- coding: utf-8 -*-
"""
Post-acceptance Typst typesetting compiler for academic papers.
Renders validated manifest items (with stable IDs) into publication-grade Typst source (.typ)
and compiles them to high-resolution PDFs and page previews.

Supports:
- Mode A: Sentence-by-sentence bilingual interlinear (#bip)
- Mode B: Full academic Chinese publication edition (#zhp)
- Mode C: Paragraph-level bilingual edition
- Mode D: Dual-column parallel layout
"""

import os
import re
import sys
import json
import argparse
import subprocess
from pathlib import Path


TYPST_HEADER_TEMPLATE = """#set page(
  paper: "a4",
  margin: (top: 2.2cm, bottom: 2.2cm, left: 2.0cm, right: 2.0cm),
  header: context {
    if counter(page).get().first() > 1 [
      #text(size: 8pt, fill: luma(100))[
        #grid(
          columns: (1fr, 1fr),
          align(left)[_%(journal)s_],
          align(right)[%(running_title)s]
        )
        #v(-4pt)
        #line(length: 100%, stroke: 0.5pt + luma(180))
      ]
    ]
  },
  footer: context [
    #text(size: 8.5pt, fill: luma(120))[
      #align(center)[- #counter(page).display() -]
    ]
  ]
)

#set text(font: ("Times New Roman", "Microsoft YaHei", "SimSun"), size: 9.5pt, lang: "zh")
#set par(justify: true, leading: 0.78em)
#set math.equation(numbering: "(1)")

// Bilingual interlinear macro
#let bip(en, zh) = [
  #block(
    width: 100%,
    inset: (left: 0pt, right: 0pt, top: 1.5pt, bottom: 1.5pt),
    [
      #text(fill: luma(20), size: 9.5pt)[#en] \\
      #v(1.5pt)
      #text(fill: rgb("#1a4b77"), size: 9.2pt, font: ("Microsoft YaHei", "SimSun"))[#zh]
    ]
  )
  #v(2.5pt)
]

// Pure Chinese paragraph macro
#let zhp(zh) = [
  #block(
    width: 100%,
    inset: (left: 0pt, right: 0pt, top: 1.5pt, bottom: 1.5pt),
    [
      #text(fill: luma(15), size: 9.5pt, font: ("Microsoft YaHei", "SimSun"))[#zh]
    ]
  )
  #v(2.5pt)
]

// Level 1 Section Heading
#let h1(en, zh) = [
  #v(16pt, weak: true)
  #if en != "" [
    #text(size: 13pt, weight: "bold")[#en] \\
    #v(1.5pt)
    #text(size: 11.5pt, weight: "bold", fill: rgb("#1a4b77"))[#zh]
  ] else [
    #text(size: 13pt, weight: "bold", fill: rgb("#1a4b77"))[#zh]
  ]
  #v(8pt, weak: true)
]

// Level 2 Section Heading
#let h2(en, zh) = [
  #v(12pt, weak: true)
  #if en != "" [
    #text(size: 11pt, weight: "bold")[#en] \\
    #v(1.0pt)
    #text(size: 10pt, weight: "bold", fill: rgb("#1a4b77"))[#zh]
  ] else [
    #text(size: 11pt, weight: "bold", fill: rgb("#1a4b77"))[#zh]
  ]
  #v(6pt, weak: true)
]

// Title and Metadata macro
#let paper_title(en, zh, authors, affiliations) = [
  #align(center)[
    #if en != "" [
      #text(size: 16pt, weight: "bold")[#en] \\
      #v(3pt)
      #text(size: 14pt, weight: "bold", fill: rgb("#1a4b77"))[#zh] \\
    ] else [
      #text(size: 16pt, weight: "bold", fill: rgb("#1a4b77"))[#zh] \\
    ]
    #if authors != "" [
      #v(8pt)
      #text(size: 10pt)[#authors] \\
    ]
    #if affiliations != "" [
      #v(2pt)
      #text(size: 8.5pt, fill: luma(80))[#emph(affiliations)]
    ]
    #v(14pt)
  ]
]
"""


def escape_typst_content(text: str) -> str:
    """
    Escapes special characters in text when embedded inside Typst content blocks [ ... ].
    Inside content blocks:
    - '#' triggers code execution -> escape to '\\#'
    - '@' triggers reference/label -> escape to '\\@'
    - '$' starts math mode -> escape to '\\$' if literal (preserve if intended)
    - Unpaired brackets '[' or ']' are escaped to prevent delimiter mismatch
    - Double quotes '"' and citation references like '[1]' are safe in content blocks
    """
    if not text:
        return ""
    res = str(text)
    # Escape code and label triggers
    res = res.replace("#", "\\#")
    res = res.replace("@", "\\@")
    # Balance brackets: if brackets are unbalanced, escape them
    open_b = res.count("[")
    close_b = res.count("]")
    if open_b != close_b:
        res = res.replace("[", "\\[").replace("]", "\\]")
    return res


def render_typst_document(manifest_items: list[dict], mode: str = "A", metadata: dict = None, typ_dir: str = ".") -> str:
    """
    Renders manifest items into Typst source code.
    mode:
      - 'A': Bilingual interlinear (#bip)
      - 'B': Full Chinese publication (#zhp)
      - 'C': Paragraph-level bilingual
      - 'D': Dual-column bilingual layout
    """
    meta = metadata or {}
    journal = meta.get("journal", "Academic Translation")
    running_title = meta.get("running_title", "Academic Paper Translator")

    # Safe template substitution without format string collision
    header = TYPST_HEADER_TEMPLATE.replace("%(journal)s", journal).replace("%(running_title)s", running_title)
    # Ensure any double %% from format strings or external patches is sanitized back to valid Typst syntax (100%)
    header = re.sub(r'100%+', '100%', header)

    body_lines = [header]

    if mode == "D":
        body_lines.append("#set page(columns: 2)\n")

    title_en = meta.get("title_en", "")
    title_zh = meta.get("title_zh", "")
    authors = meta.get("authors", "")
    affiliations = meta.get("affiliations", "")

    if title_en or title_zh:
        body_lines.append(
            f'#paper_title([{escape_typst_content(title_en)}], [{escape_typst_content(title_zh)}], [{escape_typst_content(authors)}], [{escape_typst_content(affiliations)}])\n'
        )

    current_section = None

    # Group items to handle tables and table cells together
    i = 0
    n = len(manifest_items)
    while i < n:
        item = manifest_items[i]
        kind = item.get("kind", "sentence")
        en = (item.get("source") or "").strip()
        zh = (item.get("translation") or "").strip() or en

        # Section header check
        sec = item.get("section")
        if sec and sec != current_section:
            current_section = sec
            if kind != "heading":
                sec_zh = item.get("section_zh", sec)
                body_lines.append(f'\n#h1([{escape_typst_content(sec)}], [{escape_typst_content(sec_zh)}])\n')

        if kind == "heading":
            level = item.get("level", 1)
            macro = "h1" if level == 1 else "h2"
            if mode == "B":
                body_lines.append(f'\n#{macro}([], [{escape_typst_content(zh)}])\n')
            else:
                body_lines.append(f'\n#{macro}([{escape_typst_content(en)}], [{escape_typst_content(zh)}])\n')
            i += 1

        elif kind == "sentence":
            if mode == "A":
                body_lines.append(f'#bip([{escape_typst_content(en)}], [{escape_typst_content(zh)}])')
            elif mode == "B":
                body_lines.append(f'#zhp([{escape_typst_content(zh)}])')
            elif mode == "C":
                body_lines.append(f'[{escape_typst_content(en)}]\n\n#text(fill: rgb("#1a4b77"))[{escape_typst_content(zh)}]\n')
            elif mode == "D":
                body_lines.append(f'#bip([{escape_typst_content(en)}], [{escape_typst_content(zh)}])')
            i += 1

        elif kind == "equation":
            eq_typ = item.get("typst_math") or item.get("latex") or en
            # Strip outer $ if present
            eq_typ = eq_typ.strip()
            if eq_typ.startswith("$") and eq_typ.endswith("$"):
                eq_typ = eq_typ[1:-1].strip()
            body_lines.append(f'\n$ {eq_typ} $\n')
            i += 1

        elif kind == "figure":
            fig_path = item.get("image_path", "")
            fig_no = item.get("figure_no") or item.get("id", "").replace("fig-", "").replace("-cap", "")
            cap_en = en
            cap_zh = zh

            clean_no = re.sub(r'[^A-Za-z0-9_.-]', '_', str(fig_no))

            # Helper to check if a file path is valid
            def resolve_img_file(path_cand):
                if not path_cand:
                    return None
                # Check directly
                if os.path.isfile(path_cand):
                    return os.path.abspath(path_cand)
                # Check relative to typ_dir
                cand_typ = os.path.join(typ_dir, path_cand)
                if os.path.isfile(cand_typ):
                    return os.path.abspath(cand_typ)
                # Check relative to cwd
                cand_cwd = os.path.abspath(path_cand)
                if os.path.isfile(cand_cwd):
                    return cand_cwd
                return None

            resolved_path = resolve_img_file(fig_path)

            # If not found directly, discover from figures_manifest.json or common paths
            if not resolved_path:
                manifest_search_roots = [typ_dir, "."]
                if "manifest_path" in meta:
                    manifest_search_roots.append(os.path.dirname(os.path.abspath(meta["manifest_path"])))

                # Collect all figures_manifest.json files
                found_manifests = []
                for s_root in manifest_search_roots:
                    if not os.path.isdir(s_root):
                        continue
                    for root, dirs, files in os.walk(s_root):
                        rel_parts = os.path.relpath(root, s_root).split(os.sep)
                        if len(rel_parts) > 3:
                            dirs.clear()
                            continue
                        if "figures_manifest.json" in files:
                            found_manifests.append(os.path.join(root, "figures_manifest.json"))

                for mf_path in found_manifests:
                    mf_dir = os.path.dirname(os.path.abspath(mf_path))
                    try:
                        with open(mf_path, "r", encoding="utf-8") as mf:
                            fig_list = json.load(mf)
                            for f_entry in fig_list:
                                e_id = str(f_entry.get("figure_id", ""))
                                e_path = f_entry.get("image_path", "")
                                if e_id == str(fig_no) or clean_no in e_path:
                                    # Candidate locations for this image
                                    cands = [
                                        e_path,
                                        os.path.join(mf_dir, os.path.basename(e_path)),
                                        os.path.join(mf_dir, f"fig_{clean_no}.png"),
                                        os.path.join(mf_dir, f"fig_{e_id}.png")
                                    ]
                                    for c in cands:
                                        res = resolve_img_file(c)
                                        if res:
                                            resolved_path = res
                                            break
                                if resolved_path:
                                    break
                    except Exception:
                        pass
                    if resolved_path:
                        break

            # Fallback check common naming conventions
            if not resolved_path:
                common_cands = [
                    f"paper_figures/fig_{clean_no}.png",
                    f"figures/fig_{clean_no}.png",
                    f"column-figures/fig_{clean_no}.png",
                    f"fig_{clean_no}.png"
                ]
                for c in common_cands:
                    res = resolve_img_file(c)
                    if res:
                        resolved_path = res
                        break

            # Strip redundant 'Fig. X' prefix from caption text if already in fig_no
            clean_cap_en = re.sub(r'^(?:Fig\.|Figure|Supplementary\s+Figure)\s*[A-Za-z0-9_.-]*[:.]?\s*', '', cap_en, flags=re.I).strip()
            clean_cap_zh = re.sub(r'^(?:图|附图)\s*[A-Za-z0-9_.-]*[:.]?\s*', '', cap_zh).strip()
            if not clean_cap_en:
                clean_cap_en = cap_en
            if not clean_cap_zh:
                clean_cap_zh = cap_zh

            # Typst requires relative paths to the .typ file directory
            if resolved_path and os.path.isfile(resolved_path):
                rel_fig_path = os.path.relpath(resolved_path, typ_dir).replace('\\', '/')
                img_block = f'image("{rel_fig_path}", width: 85%)'
            else:
                img_block = (
                    f'rect(width: 85%, height: 60pt, stroke: 0.5pt + luma(180), fill: luma(250))[\n'
                    f'  #align(center + horizon)[#text(fill: luma(120), size: 8.5pt)[Figure {fig_no}]]\n'
                    f']'
                )

            cap_header_en = f'#text(weight: "bold")[Fig. {fig_no}] ' if fig_no else ""
            cap_header_zh = f'#text(weight: "bold", fill: rgb("#1a4b77"))[图 {fig_no}] ' if fig_no else ""

            body_lines.append(f"""
#figure(
  {img_block},
  caption: [
    {cap_header_en}{escape_typst_content(clean_cap_en)} \\
    {cap_header_zh}{escape_typst_content(clean_cap_zh)}
  ]
)
""")
            i += 1

        elif kind in ("table", "table_cell"):
            # Render a single table grouped strictly by table_id
            tbl_caption_en = ""
            tbl_caption_zh = ""
            tbl_code = None
            tbl_cells = []

            if kind == "table":
                tbl_caption_en = en
                tbl_caption_zh = zh
                tbl_code = item.get("typst_code")
                active_tbl_id = item.get("table_id") or item.get("id", "").replace("-cap", "")
                i += 1
                # Collect following table_cells belonging strictly to THIS table
                while i < n and manifest_items[i].get("kind") == "table_cell":
                    cell_tbl_id = manifest_items[i].get("table_id")
                    if active_tbl_id and cell_tbl_id and cell_tbl_id != active_tbl_id:
                        break
                    tbl_cells.append(manifest_items[i])
                    i += 1
            else:
                # Starting with table_cell, collect cells with the SAME table_id
                active_tbl_id = item.get("table_id")
                while i < n and manifest_items[i].get("kind") == "table_cell":
                    cell_tbl_id = manifest_items[i].get("table_id")
                    if active_tbl_id and cell_tbl_id and cell_tbl_id != active_tbl_id:
                        break
                    tbl_cells.append(manifest_items[i])
                    i += 1
                # Check if immediately followed by matching table caption
                if i < n and manifest_items[i].get("kind") == "table":
                    cell_tbl_id = manifest_items[i].get("table_id")
                    if not active_tbl_id or not cell_tbl_id or cell_tbl_id == active_tbl_id:
                        tbl_caption_en = manifest_items[i].get("source", "").strip()
                        tbl_caption_zh = manifest_items[i].get("translation", "").strip() or tbl_caption_en
                        tbl_code = manifest_items[i].get("typst_code")
                        i += 1

            if tbl_code:
                body_lines.append(f"\n{tbl_code}\n")
            elif tbl_cells:
                # Compute table dimensions
                max_row = max((c.get("row", 0) for c in tbl_cells), default=0)
                max_col = max((c.get("col", 0) for c in tbl_cells), default=0)
                num_cols = max(max_col + 1, 1)

                # Organize into grid
                grid_cells = {}
                for cell in tbl_cells:
                    r = cell.get("row", 0)
                    c = cell.get("col", 0)
                    grid_cells[(r, c)] = cell

                # Generate Typst table
                cell_renders = []
                for r in range(max_row + 1):
                    for c in range(num_cols):
                        c_item = grid_cells.get((r, c))
                        if c_item:
                            c_src = escape_typst_content(c_item.get("source", "").strip())
                            c_tgt = escape_typst_content(c_item.get("translation", "").strip() or c_src)
                            if mode == "A" and c_tgt and c_tgt != c_src:
                                cell_renders.append(f'  [{c_src} \\ #text(fill: rgb("#1a4b77"), size: 8pt)[{c_tgt}]],')
                            elif mode == "B" and c_tgt:
                                cell_renders.append(f'  [{c_tgt}],')
                            else:
                                cell_renders.append(f'  [{c_tgt or c_src}],')
                        else:
                            cell_renders.append('  [],')

                clean_tbl_en = re.sub(r'^(?:Table|Tab\.)\s*[A-Za-z0-9_.-]*[:.]?\s*', '', tbl_caption_en, flags=re.I).strip()
                clean_tbl_zh = re.sub(r'^(?:表|附表)\s*[A-Za-z0-9_.-]*[:.]?\s*', '', tbl_caption_zh).strip()
                if not clean_tbl_en:
                    clean_tbl_en = tbl_caption_en
                if not clean_tbl_zh:
                    clean_tbl_zh = tbl_caption_zh

                cap_str = ""
                if clean_tbl_en or clean_tbl_zh:
                    cap_str = (
                        f'  caption: [\n'
                        f'    #text(weight: "bold")[{escape_typst_content(clean_tbl_en)}] \\\n'
                        f'    #text(weight: "bold", fill: rgb("#1a4b77"))[{escape_typst_content(clean_tbl_zh)}]\n'
                        f'  ],\n'
                    )

                body_lines.append(f"""
#figure(
{cap_str}  table(
    columns: {num_cols},
    stroke: none,
    table.hline(stroke: 1.2pt),
{chr(10).join(cell_renders)}
    table.hline(stroke: 1.2pt),
  )
)
""")
            else:
                # Table caption only, render figure box so caption is preserved
                clean_tbl_en = re.sub(r'^(?:Table|Tab\.)\s*[A-Za-z0-9_.-]*[:.]?\s*', '', tbl_caption_en, flags=re.I).strip()
                clean_tbl_zh = re.sub(r'^(?:表|附表)\s*[A-Za-z0-9_.-]*[:.]?\s*', '', tbl_caption_zh).strip()
                if not clean_tbl_en:
                    clean_tbl_en = tbl_caption_en
                if not clean_tbl_zh:
                    clean_tbl_zh = tbl_caption_zh

                body_lines.append(f"""
#figure(
  caption: [
    #text(weight: "bold")[{escape_typst_content(clean_tbl_en)}] \\
    #text(weight: "bold", fill: rgb("#1a4b77"))[{escape_typst_content(clean_tbl_zh)}]
  ],
  table(
    columns: 1,
    stroke: none,
    table.hline(stroke: 1.2pt),
    [#text(fill: luma(100), size: 8.5pt)[Table content preserved in source]],
    table.hline(stroke: 1.2pt),
  )
)
""")

        elif kind == "reference":
            body_lines.append(f'+ [{escape_typst_content(en)}] \n  #text(fill: rgb("#1a4b77"), size: 8.5pt)[{escape_typst_content(zh)}]\n')
            i += 1

        else:
            # Fallback for generic text
            body_lines.append(f'#bip([{escape_typst_content(en)}], [{escape_typst_content(zh)}])')
            i += 1

    return "\n".join(body_lines)


def compile_typst(typst_file: str, output_pdf: str = None, generate_previews: bool = False, ppi: int = 150) -> bool:
    """
    Compiles a .typ file to PDF and optional PNG previews using Python typst package or typst CLI.
    """
    pdf_out = output_pdf or (os.path.splitext(typst_file)[0] + ".pdf")

    try:
        import typst
        print(f"Compiling {typst_file} to {pdf_out} via Python typst...")
        typst.compile(typst_file, output=pdf_out)

        if generate_previews:
            preview_pattern = os.path.splitext(pdf_out)[0] + "_page_{p}.png"
            print(f"Rendering previews to {preview_pattern} at {ppi} PPI...")
            typst.compile(typst_file, output=preview_pattern, format="png", ppi=ppi)

        print("Typst compilation successful!")
        return True
    except ImportError:
        # Fallback to typst CLI
        cmd = ["typst", "compile", typst_file, pdf_out]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0:
            print(f"Typst CLI compiled {pdf_out} successfully.")
            return True
        else:
            print(f"Typst compilation failed: {res.stderr}")
            return False
    except Exception as e:
        print(f"Typst compilation error: {e}")
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Compile paper manifest to Typst PDF")
    parser.add_argument("manifest", help="Path to validated manifest.jsonl")
    parser.add_argument(
        "--mode",
        default="A",
        choices=["A", "B", "C", "D"],
        help="Mode A: Bilingual Interlinear, Mode B: Full Chinese, Mode C: Paragraph Bilingual, Mode D: Dual Column"
    )
    parser.add_argument("--out-typ", default="output.typ", help="Output .typ path")
    parser.add_argument("--out-pdf", default="output.pdf", help="Output .pdf path")
    parser.add_argument("--previews", action="store_true", help="Generate PNG page previews")
    args = parser.parse_args()

    items = []
    with open(args.manifest, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    items.append(json.loads(line))
                except json.JSONDecodeError as err:
                    print(f"Error parsing JSON on line: {err}", file=sys.stderr)
                    sys.exit(1)

    typ_dir = os.path.dirname(os.path.abspath(args.out_typ)) or "."
    content = render_typst_document(items, mode=args.mode, typ_dir=typ_dir, metadata={"manifest_path": args.manifest})
    with open(args.out_typ, "w", encoding="utf-8") as f:
        f.write(content)

    success = compile_typst(args.out_typ, output_pdf=args.out_pdf, generate_previews=args.previews)
    if not success:
        sys.exit(1)
    sys.exit(0)
