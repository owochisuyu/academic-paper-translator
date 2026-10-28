# Academic Table Handling Guidelines in Translation

To guarantee absolute data reproducibility, academic rigor, and true full translation fidelity, tables must follow clear, deterministic processing rules:

---

## 1. Scope Modes: Full Translation vs Data-Preservation

| Scope Mode | Caption | Headers & Categorical Text | Numbers, Symbols & Asterisks | Footnotes & Notes |
|:---|:---|:---|:---|:---|
| **Full Translation Mode (Default)** | Bilingual (`Table 1 ... \| 表 1 ...`) | **Fully Translated into Academic Chinese** | Strictly preserved in original format | **Fully Translated** |
| **Data-Preservation Mode (Optional)** | Bilingual (`Table 1 ... \| 表 1 ...`) | Preserved in original English | Strictly preserved in original format | Bilingual / Translated |

> [!IMPORTANT]
> Under **Full Translation Mode**, every descriptive header (e.g. `Sampling Frequency`, `Convergence Time`, `Positioning Error`) and category label is translated into standardized Chinese domain terminology. **Never skip or omit table headers or notes.**

---

## 2. Quantitative Results Tables (定量结果类表格)
* **Rules**:
  1. **Numerical Data**: Never round, truncate, interpolate, or modify numerical values, decimal places, standard deviations, or significance asterisks (`*`, `**`, `***`).
  2. **Statistical Symbols**: Keep mathematical variables ($R$, $\gamma$, $P_f$, $\sigma$, $\Delta$) in native LaTeX/Typst math notation.
  3. **Column Headers**: Translate descriptive titles into Chinese, but preserve variable symbols in parentheses (e.g. `水平位置误差 RMS (m)`).
  4. **Booktabs Styling**:
     - `\toprule` (`table.hline(stroke: 1.2pt)`) at the top
     - `\midrule` (`table.hline(stroke: 0.6pt)`) under column headers
     - `\bottomrule` (`table.hline(stroke: 1.2pt)`) at the bottom

```typst
#figure(
  table(
    columns: (2fr, 1.2fr, 1.2fr, 1.2fr),
    stroke: none,
    table.hline(stroke: 1.2pt),
    table.header(
      [*实验场景 (Scenario)*], [*固定率 (%)*], [*RMS 水平误差 (m)*], [*收敛历元数*]
    ),
    table.hline(stroke: 0.6pt),
    [城市开阔区域 (Open Sky)], [99.8], [0.018], [12],
    [高架道路下方 (Viaduct)], [84.5], [0.142], [48],
    [高楼遮挡深峡谷 (Deep Canyon)], [62.3], [0.458], [115],
    table.hline(stroke: 1.2pt),
  ),
  caption: [
    #text(weight: "bold")[Table 3] RTK positioning performance comparison under diverse urban environments. \
    #text(weight: "bold", fill: rgb("#1a4b77"))[表 3] 多样化城市环境下的 RTK 定位性能对比。
  ]
)
```

---

## 3. Qualitative Descriptive Tables (定性描述类表格)
* **Strategy**: Translate 100% of the textual content into academic Chinese.
* **Terminology**: Follow standardized IEEE / ACM / Chinese Academy of Sciences terminology mappings.

---

## 4. Mixed Specification Tables (混合参数类表格)
* **Strategy**: Preserve standardized acronyms (e.g., `NUM`, `PDOP`, `ADOP`, `RT`, `PRR`, `PCR`, `GLONASS`, `BDS`, `Galileo`), and translate all explanatory definitions, units, and notes.
