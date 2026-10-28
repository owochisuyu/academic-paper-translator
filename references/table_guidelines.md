# Academic Table Handling Guidelines in Translation

When translating academic literature, tables must be treated strictly based on their content classification to guarantee data reproducibility and academic integrity:

## 1. Quantitative Results Tables (定量结果类表格)
* **Definition**: Tables presenting statistical tests, regression analysis, model accuracy/precision/F1, ablation metrics, timing latency, positioning errors, or hyperparameter sweeps.
* **Strategy**: **Keep in Original English (保持英文原文)**.
* **Reasoning**: Contains exact variable symbols ($R$, $\gamma$, $P_f$), standard statistical headers, significance asterisks ($*$, $**$, $***$), and numerical figures. Any translation risks altering mathematical meanings or misrepresenting empirical data.
* **Format**: Render using academic three-line table (Booktabs) rules:
  - `\toprule` (or `table.hline(stroke: 1.2pt)`) at the top
  - `\midrule` (or `table.hline(stroke: 0.6pt)`) under column headers
  - `\bottomrule` (or `table.hline(stroke: 1.2pt)`) at the bottom
  - Table captions can provide bilingual subtitles (e.g., `Table 4 ... | 表 4 ...`).

## 2. Qualitative Descriptive Tables (定性描述类表格)
* **Definition**: Tables explaining concept definitions, literature comparison matrices, theoretical mechanism summaries, or qualitative property mappings.
* **Strategy**: **Translate Text into Academic Chinese (翻译为规范学术中文)**.
* **Reasoning**: Primarily textual communication where Chinese translation aids comprehension without numerical corruption.

## 3. Mixed Tables (混合类表格)
* **Definition**: Tables where specific variable names/mathematical acronyms (e.g., `NUM`, `PDOP`, `ADOP`, `RT`, `PRR`, `PCR`) are followed by descriptive definitions or computational explanations.
* **Strategy**: **Preserve Variable Acronyms + Translate Explanations (保留变量名与公式缩写，翻译解释性文本)**.
