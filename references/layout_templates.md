# Typst Academic Paper Layout Templates

## 1. Bilingual Interlinear Template (逐句交替精读模板)

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
          align(right)[Authors | Title (中英双语对照)]
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

// 句对排版宏
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

// 标题宏
#let h1(en, zh) = [
  #v(16pt, weak: true)
  #text(size: 13pt, weight: "bold")[#en] \
  #v(1.5pt)
  #text(size: 11.5pt, weight: "bold", fill: rgb("#1a4b77"))[#zh]
  #v(8pt, weak: true)
]
```

## 2. Windows Safe UTF-8 Writing in Python
```python
# To prevent Windows CP936/GBK code page mangling multibyte UTF-8 characters:
with open("output.typ", "w", encoding="utf-8") as f:
    f.write(typst_string)
```

## 3. Typst Compilation via Python
```python
import typst

# Direct compile to PDF:
typst.compile("paper.typ", output="paper.pdf")

# Direct compile to high-res PNG previews (150-250 DPI):
typst.compile("paper.typ", output="preview_{p}.png", format="png", ppi=150)
```
