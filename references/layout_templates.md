# Typst Academic Paper Layout Templates

This reference documents the publication-grade Typst layout templates for compiling academic papers into bilingual interlinear, pure Chinese, or dual-column editions.

---

## 1. Bilingual Interlinear Template (Mode A: 逐句交替精读模板)

```typst
#set page(
  paper: "a4",
  margin: (top: 2.2cm, bottom: 2.2cm, left: 2.0cm, right: 2.0cm),
  header: context {
    if counter(page).get().first() > 1 [
      #text(size: 8pt, fill: luma(100))[
        #grid(
          columns: (1fr, 1fr),
          align(left)[_Journal Name_ (2026)],
          align(right)[Authors | Title (中英双语精读对照)]
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

// 逐句对排版宏 (Interlinear Pair)
#let bip(en, zh) = [
  #block(
    width: 100%,
    inset: (left: 0pt, right: 0pt, top: 1.5pt, bottom: 1.5pt),
    [
      #text(fill: luma(20), size: 9.5pt)[#en] \
      #v(1.5pt)
      #text(fill: rgb("#1a4b77"), size: 9.2pt, font: ("Microsoft YaHei", "SimSun"))[#zh]
    ]
  )
  #v(2.5pt)
]

// 纯中文段落宏 (Mode B)
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

// 一级标题宏
#let h1(en, zh) = [
  #v(16pt, weak: true)
  #text(size: 13pt, weight: "bold")[#en] \
  #v(1.5pt)
  #text(size: 11.5pt, weight: "bold", fill: rgb("#1a4b77"))[#zh]
  #v(8pt, weak: true)
]

// 二级标题宏
#let h2(en, zh) = [
  #v(12pt, weak: true)
  #text(size: 11pt, weight: "bold")[#en] \
  #v(1.0pt)
  #text(size: 10pt, weight: "bold", fill: rgb("#1a4b77"))[#zh]
  #v(6pt, weak: true)
]
```

---

## 2. Dual-Column Parallel Layout Template (Mode D: 左右双栏平行对照)

```typst
#set page(
  paper: "a4",
  flipped: true, // A4 Landscape
  margin: (top: 2.0cm, bottom: 2.0cm, left: 1.8cm, right: 1.8cm),
)

#grid(
  columns: (1fr, 1fr),
  column-gutter: 2.5cm,
  [
    #set text(lang: "en", size: 9pt)
    #align(center)[#text(size: 13pt, weight: "bold")[English Original Text]]
    // English content stream
  ],
  [
    #set text(lang: "zh", size: 9pt)
    #align(center)[#text(size: 13pt, weight: "bold", fill: rgb("#1a4b77"))[学术中文对照全译]]
    // Chinese content stream
  ]
)
```

---

## 3. Typst Compilation Workflow in Python

```python
import typst

# Compile directly to PDF:
typst.compile("paper.typ", output="paper.pdf")

# Generate high-resolution page previews (200 PPI):
typst.compile("paper.typ", output="preview_page_{p}.png", format="png", ppi=200)
```
