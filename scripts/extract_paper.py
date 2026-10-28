# -*- coding: utf-8 -*-
"""
Backwards-compatible wrapper delegating to geometry-aware extract_figures.py.
"""

import sys
import argparse
from extract_figures import extract_figures

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extract figures from PDF (Geometry-aware)")
    parser.add_argument("pdf", help="Path to PDF paper")
    parser.add_argument("--out", default="paper_figures", help="Output directory")
    parser.add_argument("--dpi", type=int, default=250, help="Rendering DPI")
    args = parser.parse_args()
    extract_figures(args.pdf, args.out, args.dpi)
