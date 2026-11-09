# Academic Paper Translator & Zotero Bridge

> **Dual Architecture**: A production-grade **Antigravity AI Agent Skill** for side-by-side bilingual (`_bilingual.pdf`) and Chinese monograph (`_zh.pdf`) academic paper translations via Typst, coupled with a native **Zotero 7/8+ Extension & Python Bridge** for real-time reading and automatic library mounting.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/Tests-288%20Passed-brightgreen.svg)]()
[![Zotero Compatibility](https://img.shields.io/badge/Zotero-7.0%20%7C%208.0%2B-blue)]()

---

## Architecture Overview

```
academic-paper-translator/
├── SKILL.md                 # Antigravity Agent Skill entry & quality protocol
├── references/              # Protocols: data_contract, latex, ocr, layout, tables
├── scripts/                 # Core Python translation, extraction & rendering engine
│   ├── extract_document.py  # Frozen document IR extraction
│   ├── extract_figures.py   # Springer side-caption aware high-res figure extractor
│   ├── compile_typst.py     # Side-by-side bilingual & Chinese monograph Typst compiler
│   ├── zotero_mount.py      # Local Zotero library lookup & automatic PDF mounter
│   ├── prepare_translation.py
│   ├── merge_translation.py
│   ├── validate_translation.py
│   ├── verify_output.py
│   └── deploy.py            # Zotero XPI build & deployment
├── tests/                   # 288 automated test fixtures & verification suites
├── addon/                   # Zotero 7/8+ native JavaScript extension frontend
├── bridge/                  # Zotero background Python translation bridge (CLI & server)
└── dist/                    # Packaged .xpi extension binary for Zotero
```

---

## 1. As an Antigravity Agent Skill

Invoke directly inside Google Antigravity / Gemini CLI:

```text
/academic-paper-translator 翻译 <论文标题 或 DOI>
```

### Core Capabilities
- **Publication-Grade Dual Typst Layout**: Generates true sentence-aligned side-by-side (`_bilingual.pdf`) and Chinese monograph (`_zh.pdf`) editions.
- **Mathematical Integrity**: Preserves 100% of numbered formulas ($Eq. 1 - 31+$) and cleanly embeds inline math ($r$, $s$, $\text{sys}$, $\text{ADOP}$) without artificial `{...}` braces or linebreaks.
- **High-Resolution Figures**: Automatically extracts embedded vector/raster figures, accurately handling complex layouts including Springer side-captions.
- **100% Verbatim English Captions**: Never rewrites original figure/table captions; pairs verbatim original English with faithful Chinese descriptions.
- **Automatic Zotero Mounting**: Automatically discovers local Zotero library items and mounts translated PDFs as child attachments.

---

## 2. As a Zotero Extension

1. Download or build the extension file from `dist/antigravity-translator@google.com.xpi`.
2. In Zotero: **Tools** -> **Plugins** -> Settings -> **Install Add-on From File...**.
3. Enjoy seamless streaming translation in Zotero's built-in PDF reader with automatic note taking and side-by-side preview.

---

## Development & Testing

Run the full verification test suite:

```bash
python -m unittest discover -s tests -p "test_*.py"
```

Tested with Python 3.10+, PyMuPDF, and Typst.
