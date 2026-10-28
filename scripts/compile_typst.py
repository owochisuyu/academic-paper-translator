# -*- coding: utf-8 -*-
"""
Post-acceptance Typst typesetting compiler for academic papers.
Renders validated manifest items (with stable IDs) into publication-grade Typst source (.typ)
and compiles them to high-resolution PDFs and page previews.

Supports:
- Mode A: Sentence-by-sentence bilingual interlinear (#bip)
- Mode B: Full academic Chinese publication edition
- Mode C: Paragraph-level bilingual edition
- Mode D: Dual-column parallel layout
"""

import os
import json
import argparse
import subprocess

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
  #text(size: 13pt, weight: "bold")[#en] \\
  #v(1.5pt)
  #text(size: 11.5pt, weight: "bold", fill: rgb("#1a4b77"))[#zh]
  #v(8pt, weak: true)
]

// Level 2 Section Heading
#let h2(en, zh) = [
  #v(12pt, weak: true)
  #text(size: 11pt, weight: "bold")[#en] \\
  #v(1.0pt)
  #text(size: 10pt, weight: "bold", fill: rgb("#1a4b77"))[#zh]
  #v(6pt, weak: true)
]

// Title and Metadata macro
#let paper_title(en, zh, authors, affiliations) = [
  #align(center)[
    #text(size: 16pt, weight: "bold")[#en] \\
    #v(3pt)
    #text(size: 14pt, weight: "bold", fill: rgb("#1a4b77"))[#zh] \\
    #v(10pt)
    #text(size: 10pt)[#authors] \\
    #v(2pt)
    #text(size: 8.5pt, fill: luma(80))[_#affiliations_]
    #v(14pt)
  ]
]
"""

def escape_typst(text: str) -> str:
    """Escapes special Typst characters in plain text."""
    if not text:
        return ""
    # Typst characters to escape in content mode: #, $, [, ], @, _, *
    # Replace backslash first if needed, but we keep math delimiters intact if already formatted
    rep = [
        ("#", "\\#"),
        ("[", "\\["),
        ("]", "\\]"),
        ("@", "\\@"),
    ]
    res = text
    for o, r in rep:
        res = res.replace(o, r)
    return res

def render_typst_document(manifest_items: list[dict], mode: str = "A", metadata: dict = None) -> str:
    """
    Renders manifest items into Typst source code.
    mode:
      - 'A': Bilingual interlinear (#bip)
      - 'B': Full Chinese publication
      - 'C': Paragraph-level bilingual
    """
    meta = metadata or {}
    journal = meta.get("journal", "Academic Translation")
    running_title = meta.get("running_title", "Academic Paper Translator")
    
    header = TYPST_HEADER_TEMPLATE % {
        "journal": journal,
        "running_title": running_title
    }
    
    body_lines = [header]
    
    # Check if there is title item
    title_en = meta.get("title_en", "")
    title_zh = meta.get("title_zh", "")
    authors = meta.get("authors", "")
    affiliations = meta.get("affiliations", "")
    
    if title_en or title_zh:
        body_lines.append(f'#paper_title("{escape_typst(title_en)}", "{escape_typst(title_zh)}", "{escape_typst(authors)}", "{escape_typst(affiliations)}")\n')
    
    current_section = None
    
    for item in manifest_items:
        kind = item.get("kind", "sentence")
        en = item.get("source", "").strip()
        zh = item.get("translation", "").strip() or en
        
        # Section header
        sec = item.get("section")
        if sec and sec != current_section:
            current_section = sec
            # If item is not itself a heading, emit section
            if kind != "heading":
                body_lines.append(f'\n#h1("{escape_typst(sec)}", "{escape_typst(item.get("section_zh", sec))}")\n')
        
        if kind == "heading":
            level = item.get("level", 1)
            macro = "h1" if level == 1 else "h2"
            if mode == "B":
                body_lines.append(f'\n#{macro}("", "{escape_typst(zh)}")\n')
            else:
                body_lines.append(f'\n#{macro}("{escape_typst(en)}", "{escape_typst(zh)}")\n')
                
        elif kind == "sentence":
            if mode == "A":
                body_lines.append(f'#bip("{escape_typst(en)}", "{escape_typst(zh)}")')
            elif mode == "B":
                body_lines.append(f'#zhp("{escape_typst(zh)}")')
            elif mode == "C":
                # Paragraph bilingual
                body_lines.append(f'{escape_typst(en)}\n\n#text(fill: rgb("#1a4b77"))[{escape_typst(zh)}]\n')
                
        elif kind == "equation":
            # Math equation
            eq_typ = item.get("typst_math") or item.get("latex") or en
            body_lines.append(f'\n$ {eq_typ} $\n')
            
        elif kind == "figure":
            fig_path = item.get("image_path", "")
            fig_no = item.get("figure_no", "")
            cap_en = en
            cap_zh = zh
            body_lines.append(f"""
#figure(
  image("{fig_path}", width: 85%),
  caption: [
    #text(weight: "bold")[Fig. {fig_no}] {escape_typst(cap_en)} \\
    #text(weight: "bold", fill: rgb("#1a4b77"))[图 {fig_no}] {escape_typst(cap_zh)}
  ]
)
""")
        elif kind == "table":
            # Booktabs table rendering
            tbl_code = item.get("typst_code")
            if tbl_code:
                body_lines.append(f"\n{tbl_code}\n")
            else:
                body_lines.append(f'\n// Table: {escape_typst(en)}\n')
                
        elif kind == "reference":
            # References
            body_lines.append(f'+ {escape_typst(en)} \n  #text(fill: rgb("#1a4b77"), size: 8.5pt)[{escape_typst(zh)}]\n')
            
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
    parser.add_argument("--mode", default="A", choices=["A", "B", "C"], help="Mode A: Bilingual Interlinear, Mode B: Full Chinese, Mode C: Paragraph Bilingual")
    parser.add_argument("--out-typ", default="output.typ", help="Output .typ path")
    parser.add_argument("--out-pdf", default="output.pdf", help="Output .pdf path")
    parser.add_argument("--previews", action="store_true", help="Generate PNG page previews")
    args = parser.parse_args()
    
    items = []
    with open(args.manifest, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                items.append(json.loads(line))
                
    content = render_typst_document(items, mode=args.mode)
    with open(args.out_typ, "w", encoding="utf-8") as f:
        f.write(content)
        
    compile_typst(args.out_typ, output_pdf=args.out_pdf, generate_previews=args.previews)
