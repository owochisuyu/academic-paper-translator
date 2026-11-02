# -*- coding: utf-8 -*-
"""
Geometry-aware figure extraction for academic papers using PyMuPDF.

Extracts vector graphics, raster images, and internal labels without
truncation by internal text blocks (e.g. axis labels, legends, tick marks).
Dynamically calculates margins and column layouts, and handles both
standard figures and appendix figures (e.g., Fig. S1, Supplementary Figure 1).
"""

import os
import re
import argparse
import json
import hashlib
from pathlib import Path
import pymupdf

# Regex matching standard and appendix figures:
# e.g., 'Fig. 1', 'Figure 2a', 'Fig. S1', 'Supplementary Figure 3', 'Figure S2-A'
FIGURE_CAPTION_REGEX = re.compile(
    r'(?:Fig\.|Figure|Supplementary\s+Figure)[\s\u202f]*([A-Za-z0-9_.-]+)',
    re.IGNORECASE
)


def sanitize_filename(identifier: str) -> str:
    """Sanitizes figure identifier for safe file names."""
    clean = re.sub(r'[^A-Za-z0-9_.-]', '_', identifier.strip())
    return clean if clean else "unknown"


def extract_figures(pdf_path: str, output_dir: str = "paper_figures", dpi: int = 250) -> list[dict]:
    """
    Extracts all figures from PDF into high-resolution PNGs with accurate geometry.
    
    Args:
        pdf_path: Path to the input PDF document.
        output_dir: Directory where extracted images will be saved.
        dpi: Target rendering DPI (default 250).
        
    Returns:
        List of metadata dicts describing each extracted figure.
    """
    output_dir = os.path.abspath(output_dir)
    os.makedirs(output_dir, exist_ok=True)
    doc_id = hashlib.sha256(Path(pdf_path).read_bytes()).hexdigest()
    doc = pymupdf.open(pdf_path)
    extracted_figures = []

    for pno, page in enumerate(doc):
        page_rect = page.rect
        page_w = page_rect.width
        page_h = page_rect.height

        # 1. Gather all vector drawing rects
        drawings = page.get_drawings()
        valid_drawings = []
        for d in drawings:
            r = pymupdf.Rect(d["rect"])
            # Ignore sub-pixel artifacts or full-page bounding boxes/borders
            if r.width <= 1.0 or r.height <= 1.0:
                continue
            if r.width >= page_w * 0.98 and r.height >= page_h * 0.98:
                continue
            valid_drawings.append(r)

        # 2. Gather all raster image bboxes
        valid_images = []
        try:
            img_infos = page.get_image_info(xrefs=True)
            for info in img_infos:
                if "bbox" in info:
                    r = pymupdf.Rect(info["bbox"])
                    if r.width <= 1.0 or r.height <= 1.0:
                        continue
                    if r.width >= page_w * 0.98 and r.height >= page_h * 0.98:
                        continue
                    valid_images.append(r)
        except Exception:
            # Fallback if get_image_info not supported or fails
            pass

        all_graphics = valid_drawings + valid_images

        # 3. Gather text blocks and identify figure captions
        raw_blocks = page.get_text("blocks")
        text_blocks = []
        for b in raw_blocks:
            if len(b) > 6 and b[6] != 0:
                continue
            t_txt = b[4].strip()
            if not t_txt:
                continue
            text_blocks.append((pymupdf.Rect(b[:4]), t_txt))

        captions = []
        page_dict = page.get_text("dict")
        for b in page_dict.get("blocks", []):
            if b.get("type") != 0:
                continue
            current_cap = None
            for line in b.get("lines", []):
                line_text = "".join(s.get("text", "") for s in line.get("spans", [])).strip()
                if not line_text:
                    continue
                line_rect = pymupdf.Rect(line["bbox"])
                m = FIGURE_CAPTION_REGEX.match(line_text)
                if m:
                    if current_cap:
                        captions.append(current_cap)
                    fig_id = m.group(1).rstrip(".:,;-")
                    current_cap = {
                        "fig_id": fig_id,
                        "rect": line_rect,
                        "text": line_text,
                        "raw_block": b
                    }
                elif current_cap:
                    if abs(line_rect.x0 - current_cap["rect"].x0) < 60.0 and line_rect.y0 >= current_cap["rect"].y0 - 2.0:
                        current_cap["rect"] |= line_rect
                        current_cap["text"] += " " + line_text
                    else:
                        captions.append(current_cap)
                        current_cap = None
            if current_cap:
                captions.append(current_cap)

        if not captions:
            continue

        # Sort captions vertically (top to bottom)
        captions.sort(key=lambda c: (c["rect"].y0, c["rect"].x0))

        # 4. For each caption, compute true figure bounding box
        for idx, cap in enumerate(captions):
            fig_id = cap["fig_id"]
            cap_rect = cap["rect"]

            # Determine column bounds and alignment
            is_full_width = (
                cap_rect.width > 0.55 * page_w or
                (cap_rect.x0 < 0.35 * page_w and cap_rect.x1 > 0.65 * page_w)
            )
            is_left_col = not is_full_width and cap_rect.x1 <= 0.55 * page_w
            is_right_col = not is_full_width and cap_rect.x0 >= 0.45 * page_w

            # Compute preceding caption bottom ONLY within the same column or from preceding full-width figures
            same_column_prev = []
            for c in captions:
                if c["rect"].y1 <= cap_rect.y0:
                    c_rect = c["rect"]
                    c_full = (c_rect.width > 0.55 * page_w or (c_rect.x0 < 0.35 * page_w and c_rect.x1 > 0.65 * page_w))
                    c_left = not c_full and c_rect.x1 <= 0.55 * page_w
                    c_right = not c_full and c_rect.x0 >= 0.45 * page_w

                    if is_full_width or c_full:
                        same_column_prev.append(c_rect.y1)
                    elif is_left_col and c_left:
                        same_column_prev.append(c_rect.y1)
                    elif is_right_col and c_right:
                        same_column_prev.append(c_rect.y1)

            prev_cap_bottom = max(same_column_prev, default=0.0)

            # Define candidate column horizontal boundaries
            if is_full_width:
                col_x0 = 0.04 * page_w
                col_x1 = 0.96 * page_w
            elif is_left_col:
                col_x0 = 0.03 * page_w
                col_x1 = 0.54 * page_w
            else:
                col_x0 = 0.46 * page_w
                col_x1 = 0.97 * page_w

            # Opposite-column captions establish a real gutter. Do not clamp col_x1
            # to short text line ends, but strictly bound by the opposite column's x0.
            strict_column = False
            if is_left_col:
                right_edges = [other['rect'].x0 for other in captions
                               if other is not cap and other['rect'].x0 >= .45 * page_w
                               and other['rect'].x0 > cap_rect.x1]
                if right_edges:
                    col_x1 = min(col_x1, min(right_edges) - 2.0)
                    strict_column = True
            elif is_right_col:
                left_edges = [other['rect'].x1 for other in captions
                              if other is not cap and other['rect'].x1 <= .55 * page_w
                              and other['rect'].x1 < cap_rect.x0]
                if left_edges:
                    col_x0 = max(col_x0, max(left_edges) + 2.0)
                    strict_column = True

            # Filter graphics belonging to this figure (located above the caption)
            matched_graphics = []
            for gr in all_graphics:
                # Vertical check: must be above caption top, and below previous caption
                if gr.y1 > cap_rect.y0 + 8.0:
                    continue
                if gr.y0 < prev_cap_bottom:
                    continue
                if strict_column and (gr.x0 < col_x0 - .5 or gr.x1 > col_x1 + .5):
                    continue

                # Horizontal check: must overlap with caption column
                overlap_x = max(0.0, min(col_x1, gr.x1) - max(col_x0, gr.x0))
                cap_overlap = max(0.0, min(cap_rect.x1 + 40, gr.x1) - max(cap_rect.x0 - 40, gr.x0))

                if is_full_width:
                    if overlap_x > 30.0 or cap_overlap > 30.0:
                        matched_graphics.append(gr)
                else:
                    # In single-column, ensure center or substantial area falls inside column
                    gr_center_x = (gr.x0 + gr.x1) / 2.0
                    if col_x0 <= gr_center_x <= col_x1 or cap_overlap > 20.0:
                        matched_graphics.append(gr)

            # Build figure bounding box
            if matched_graphics:
                # Compute union of all graphical elements
                fig_rect = pymupdf.Rect(matched_graphics[0])
                for gr in matched_graphics[1:]:
                    fig_rect.include_rect(gr)

                # Include internal text labels (axes titles, tick numbers, legend text, subfigure labels)
                # These are blocks lying inside or right next to the graphics bbox and above caption
                for tb_rect, tb_text in text_blocks:
                    if tb_rect == cap_rect:
                        continue
                    # Must be above caption and below previous caption
                    if tb_rect.y1 > cap_rect.y0 + 2.0 or tb_rect.y0 < prev_cap_bottom:
                        continue
                    if strict_column and (tb_rect.x0 < col_x0 - .5 or tb_rect.x1 > col_x1 + .5):
                        continue

                    # Check if text block is inside or adjacent to graphics rectangle
                    is_vertically_inside = (tb_rect.y0 >= fig_rect.y0 - 25.0 and tb_rect.y1 <= fig_rect.y1 + 25.0)
                    is_between_plot_and_cap = (tb_rect.y0 >= fig_rect.y1 - 10.0 and tb_rect.y1 <= cap_rect.y0)
                    
                    overlap_w = max(0.0, min(fig_rect.x1 + 35.0, tb_rect.x1) - max(fig_rect.x0 - 35.0, tb_rect.x0))
                    
                    if (is_vertically_inside or is_between_plot_and_cap) and overlap_w > 0.0:
                        # Ensure it's not a full paragraph above the figure
                        is_body_paragraph = len(tb_text) > 120 and tb_rect.y1 < fig_rect.y0
                        if not is_body_paragraph:
                            fig_rect.include_rect(tb_rect)

            else:
                # Fallback when no vector drawings or raster images are present:
                # Locate preceding text block in column to avoid cropping body paragraphs
                top_y = max(prev_cap_bottom, page_h * 0.06)
                for tb_rect, tb_text in text_blocks:
                    if tb_rect == cap_rect:
                        continue
                    if tb_rect.y1 >= cap_rect.y0 - 5.0:
                        continue
                    if strict_column and (tb_rect.x0 < col_x0 - .5 or tb_rect.x1 > col_x1 + .5):
                        continue
                    overlap_x = max(0.0, min(col_x1, tb_rect.x1) - max(col_x0, tb_rect.x0))
                    if overlap_x > 30.0 and tb_rect.y1 > top_y and tb_rect.y1 < cap_rect.y0 - 15.0:
                        # Only body paragraphs with sentence-like text set the top boundary
                        if len(tb_text) > 60 or tb_text.endswith("."):
                            top_y = tb_rect.y1 + 4.0

                fig_rect = pymupdf.Rect(col_x0, top_y, col_x1, cap_rect.y0 - 2.0)

            # Apply safe margin padding (4pt) clamped within page and caption boundaries
            padding = 4.0
            x0 = max(0.0, fig_rect.x0 - padding)
            y0 = max(0.0, max(prev_cap_bottom, fig_rect.y0 - padding))
            x1 = min(page_w, fig_rect.x1 + padding)
            if strict_column:
                x0, x1 = max(x0, col_x0), min(x1, col_x1)
            y1 = min(cap_rect.y0 - 1.5, fig_rect.y1 + padding)

            # Safety check: if invalid or inverted rect, clamp sensibly
            if y1 <= y0 + 10.0:
                y0 = max(0.0, cap_rect.y0 - 150.0)
                y1 = cap_rect.y0 - 2.0
            if x1 <= x0 + 10.0:
                x0 = col_x0
                x1 = col_x1

            final_fig_rect = pymupdf.Rect(x0, y0, x1, y1)

            # Render at specified DPI
            pix = page.get_pixmap(clip=final_fig_rect, dpi=dpi)
            clean_id = sanitize_filename(fig_id)
            occurrence = sum(f["figure_id"] == fig_id and f["page"] == pno + 1 for f in extracted_figures) + 1
            already_used = any(f["figure_id"] == fig_id for f in extracted_figures)
            out_filename = f"p{pno + 1:03d}_fig_{clean_id}_{occurrence}.png" if already_used else f"fig_{clean_id}.png"
            out_path = os.path.join(output_dir, out_filename)
            pix.save(out_path)

            extracted_figures.append({
                "asset_id": f"{doc_id[:16]}:p{pno + 1:03d}:figure:{fig_id}:{occurrence}",
                "doc_id": doc_id,
                "kind": "figure",
                "representation": "raster",
                "sha256": hashlib.sha256(Path(out_path).read_bytes()).hexdigest(),
                "path": out_path,
                "owner_ids": [],
                "figure_id": fig_id,
                "page": pno + 1,
                "caption": cap["text"],
                "bbox": [round(final_fig_rect.x0, 2), round(final_fig_rect.y0, 2),
                         round(final_fig_rect.x1, 2), round(final_fig_rect.y1, 2)],
                "caption_bbox": [round(cap_rect.x0, 2), round(cap_rect.y0, 2),
                                round(cap_rect.x1, 2), round(cap_rect.y1, 2)],
                "image_path": out_path,
                "width": pix.width,
                "height": pix.height,
                "dpi": dpi
            })

    # Save figures manifest JSON for downstream alignment and compilation
    manifest_path = os.path.join(output_dir, "figures_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(extracted_figures, f, indent=2, ensure_ascii=False)
    doc.close()

    print(f"Extracted {len(extracted_figures)} figures to {output_dir}")
    return extracted_figures


def main():
    parser = argparse.ArgumentParser(description="Geometry-aware figure extraction for academic papers")
    parser.add_argument("pdf", help="Path to input PDF document")
    parser.add_argument("--out", default="paper_figures", help="Output directory for figure images")
    parser.add_argument("--dpi", type=int, default=250, help="Rendering resolution (DPI, default 250)")
    args = parser.parse_args()

    results = extract_figures(args.pdf, output_dir=args.out, dpi=args.dpi)
    for fig in results:
        print(f"  - Fig {fig['figure_id']} (Page {fig['page']}): {fig['width']}x{fig['height']} px -> {fig['image_path']}")


if __name__ == "__main__":
    main()
