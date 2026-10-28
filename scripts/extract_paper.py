# -*- coding: utf-8 -*-
"""
Helper script to extract text blocks, structured sections, and high-resolution figures from an academic PDF paper using PyMuPDF.
"""

import os
import re
import argparse
import pymupdf

def extract_figures(pdf_path, output_dir="paper_figures", dpi=250):
    os.makedirs(output_dir, exist_ok=True)
    doc = pymupdf.open(pdf_path)
    extracted = []
    
    for pno, page in enumerate(doc):
        blocks = page.get_text("blocks")
        for b in blocks:
            text = b[4].strip()
            # Look for captions like "Fig. 1", "Figure 1", "Fig. 1 "
            m = re.match(r'(?:Fig\.|Figure)[\s\u202f\u2002]*(\d+)', text)
            if m:
                fig_no = int(m.group(1))
                cap_rect = pymupdf.Rect(b[:4])
                
                # Determine figure width and top boundary
                is_full_width = cap_rect.width > 260 or cap_rect.x0 < 100 and cap_rect.x1 > 400
                x0 = 56.0 if is_full_width or cap_rect.x0 < 200 else 305.0
                x1 = 538.0 if is_full_width or cap_rect.x0 >= 200 else 285.0
                
                # Find preceding text block in the same column
                top_y = 60.0
                for ob in blocks:
                    if ob[1] >= cap_rect.y0 - 5: continue
                    overlap = max(0, min(x1, ob[2]) - max(x0, ob[0]))
                    if overlap > 30 and ob[3] > top_y and ob[3] < cap_rect.y0 - 10:
                        top_y = ob[3]
                
                fig_rect = pymupdf.Rect(x0, top_y, x1, cap_rect.y0 - 2)
                pix = page.get_pixmap(clip=fig_rect, dpi=dpi)
                out_path = os.path.join(output_dir, f"fig_{fig_no}.png")
                pix.save(out_path)
                extracted.append((fig_no, pno + 1, out_path, pix.width, pix.height))
    
    print(f"Extracted {len(extracted)} figures to {output_dir}")
    return extracted

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extract figures from PDF")
    parser.add_argument("pdf", help="Path to PDF paper")
    parser.add_argument("--out", default="paper_figures", help="Output directory")
    parser.add_argument("--dpi", type=int, default=250, help="Rendering DPI")
    args = parser.parse_args()
    extract_figures(args.pdf, args.out, args.dpi)
