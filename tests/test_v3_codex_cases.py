# -*- coding: utf-8 -*-
"""
Tests verifying resolution of all 7 issues from Codex Round 3 Review:
1. Multi-table grouping and rendering without table overwrite.
2. Figure manifest discovery and Typst image embedding.
3. Mixed native/scanned PDF page dropping detection.
4. Block-level omission detection (e.g. omitted condition paragraph).
5. Bound numeric entity validation (rejecting 1e-8->0, swapped units, dropped duplicate quantities).
6. Unit prefix longest-match parsing (10 ms not truncated to 10 m).
7. Non-spurious handling of valid physical units (rad, V) and math formula subscripts ($x_1+y_2=z_3$).
"""

import os
import json
import tempfile
import unittest
from pathlib import Path
import pymupdf

from scripts.extract_document import DocumentExtractor
from scripts.extract_figures import extract_figures
from scripts.compile_typst import render_typst_document, compile_typst
from scripts.validate_translation import TranslationValidator, NUM_ENTITY_REGEX


class TestCodexRound3Fixes(unittest.TestCase):
    def setUp(self):
        self.validator = TranslationValidator(strict=True)

    def test_issue1_two_tables_rendered_without_overwrite(self):
        """Issue 1: Multiple tables on the same page must both be rendered and preserved."""
        with tempfile.TemporaryDirectory() as td:
            pdf_path = os.path.join(td, "two_tables.pdf")
            doc = pymupdf.open()
            page = doc.new_page(width=595, height=842)
            body = "The table entries are symbolic model identifiers used in the evaluation."
            page.insert_text((50, 50), body, fontsize=10)
            for top, values in [(100, ("FIRSTMODEL", "OPENFIELD")), (300, ("SECONDMODEL", "DENSECITY"))]:
                for x in [50, 250, 500]:
                    page.draw_line((x, top), (x, top + 80))
                for y in [top, top + 40, top + 80]:
                    page.draw_line((50, y), (500, y))
                for x, y, text in [(60, top + 25, "Method"), (260, top + 25, "Setting"),
                                   (60, top + 65, values[0]), (260, top + 65, values[1])]:
                    page.insert_text((x, y), text, fontsize=10)
            doc.save(pdf_path)
            doc.close()

            rows, _ = DocumentExtractor().extract(pdf_path)
            # Must extract cells from both tables with distinct table_ids
            t1_cells = [r for r in rows if r.get("table_id") == "p1-tbl1"]
            t2_cells = [r for r in rows if r.get("table_id") == "p1-tbl2"]
            self.assertTrue(len(t1_cells) > 0)
            self.assertTrue(len(t2_cells) > 0)

            # Compile to Typst
            typ_path = os.path.join(td, "out.typ")
            pdf_out = os.path.join(td, "out.pdf")
            translated = [{**r, "translation": r["source"], "status": "translated"} for r in rows]
            typ_content = render_typst_document(translated, mode="A", typ_dir=td)
            with open(typ_path, "w", encoding="utf-8") as f:
                f.write(typ_content)

            ok = compile_typst(typ_path, output_pdf=pdf_out)
            self.assertTrue(ok)
            with pymupdf.open(pdf_out) as out_doc:
                out_txt = "\n".join(p.get_text() for p in out_doc)
                self.assertIn("FIRSTMODEL", out_txt)
                self.assertIn("SECONDMODEL", out_txt)

    def test_issue2_figure_manifest_joined_and_image_embedded(self):
        """Issue 2: Extracted figures must be joined and embedded into the final PDF."""
        with tempfile.TemporaryDirectory() as td:
            pdf_path = os.path.join(td, "fig_test.pdf")
            doc = pymupdf.open()
            page = doc.new_page(width=595, height=842)
            page.draw_rect((60, 100, 520, 320), color=(0.1, 0.2, 0.8), fill=(0.9, 0.95, 1))
            page.draw_line((80, 290), (475, 120), color=(0.8, 0, 0), width=3)
            page.draw_circle((290, 200), 45, color=(0, 0.6, 0.2), width=3)
            page.insert_text((60, 350), "Fig. 1. Calibration result.", fontsize=10)
            doc.save(pdf_path)
            doc.close()

            # 1. Extract document
            rows, _ = DocumentExtractor().extract(pdf_path)
            # 2. Extract figures
            fig_dir = os.path.join(td, "figures")
            extract_figures(pdf_path, fig_dir, dpi=150)

            # 3. Compile
            translated = [{**r, "translation": "图 1. 校准结果。", "status": "translated"} for r in rows]
            typ_path = os.path.join(td, "out.typ")
            pdf_out = os.path.join(td, "out.pdf")
            typ_content = render_typst_document(translated, mode="A", typ_dir=td)
            with open(typ_path, "w", encoding="utf-8") as f:
                f.write(typ_content)

            ok = compile_typst(typ_path, output_pdf=pdf_out)
            self.assertTrue(ok)
            with pymupdf.open(pdf_out) as out_doc:
                img_cnt = sum(len(p.get_images()) for p in out_doc)
                self.assertGreaterEqual(img_cnt, 1, "Image must be embedded in final PDF, not a placeholder box")

    def test_issue3_mixed_scanned_page_flagged_as_missing(self):
        """Issue 3: In a mixed PDF, a page with only raster/scanned content must fail coverage check."""
        with tempfile.TemporaryDirectory() as td:
            scan_pdf = os.path.join(td, "scan.pdf")
            scan_doc = pymupdf.open()
            s_page = scan_doc.new_page()
            # Render raster text
            text_doc = pymupdf.open()
            t_page = text_doc.new_page()
            t_page.insert_text((50, 80), "Critical appendix.", fontsize=12)
            pix = t_page.get_pixmap(dpi=150)
            s_page.insert_image(s_page.rect, stream=pix.tobytes("png"))
            scan_doc.save(scan_pdf)
            text_doc.close()
            scan_doc.close()

            # Mixed PDF: Page 1 text, Page 2 scan
            mixed_pdf = os.path.join(td, "mixed.pdf")
            mixed_doc = pymupdf.open()
            m1 = mixed_doc.new_page()
            m1.insert_text((50, 70), "The proposed method combines observations.", fontsize=10)
            with pymupdf.open(scan_pdf) as s_src:
                mixed_doc.insert_pdf(s_src)
            mixed_doc.save(mixed_pdf)
            mixed_doc.close()

            mixed_rows, _ = DocumentExtractor().extract(mixed_pdf)
            # Only page 1 items extracted
            targets = {r["id"]: "所提出的方法融合观测。" for r in mixed_rows}
            report = self.validator.validate(mixed_rows, targets, doc_path=mixed_pdf)
            self.assertEqual(report["status"], "FAIL")
            self.assertIn(2, report["tier1_source_coverage"].get("missing_pages", []))

    def test_issue4_omitted_condition_paragraph_detected(self):
        """Issue 4: Dropping a condition paragraph must fail even if character ratio > 90%."""
        with tempfile.TemporaryDirectory() as td:
            pdf_path = os.path.join(td, "condition.pdf")
            doc = pymupdf.open()
            page = doc.new_page()
            sentence = "The estimator combines sensor observations to reduce random measurement errors."
            body = " ".join([sentence] * 10)
            page.insert_textbox((50, 60, 540, 280), body, fontsize=10)
            condition = "The system cannot operate without calibration."
            page.insert_text((50, 330), condition, fontsize=10)
            doc.save(pdf_path)
            doc.close()

            all_rows, _ = DocumentExtractor().extract(pdf_path)
            partial = [r for r in all_rows if "without calibration" not in r["source"]]
            targets = {r["id"]: "该估计器融合传感器观测以降低随机测量误差。" for r in partial}
            report = self.validator.validate(partial, targets, doc_path=pdf_path)
            self.assertEqual(report["status"], "FAIL")
            tier1 = report["tier1_source_coverage"]
            self.assertTrue(len(tier1.get("unextracted_blocks", [])) > 0)

    def test_issue5_numeric_false_accepts_rejected(self):
        """Issue 5: Zero-tolerance numeric, unit binding, and duplicate count checks."""
        # 1. 1e-8 -> 0 must FAIL
        r1 = self.validator.validate(
            [{"id": "s1", "kind": "sentence", "source": "The drift is 1e-8.", "page": 1}],
            {"s1": "漂移为 0。"}
        )
        self.assertEqual(r1["status"], "FAIL")

        # 2. Swapped values: 1 m, 2 s vs 2 m, 1 s must FAIL
        r2 = self.validator.validate(
            [{"id": "s2", "kind": "sentence", "source": "The distance is 1 m and the delay is 2 s.", "page": 1}],
            {"s2": "距离为 2 m，延迟为 1 s。"}
        )
        self.assertEqual(r2["status"], "FAIL")

        # 3. Dropped duplicate: 5 m and 5 m vs 5 m must FAIL
        r3 = self.validator.validate(
            [{"id": "s3", "kind": "sentence", "source": "The offsets are 5 m and 5 m.", "page": 1}],
            {"s3": "偏移为 5 m。"}
        )
        self.assertEqual(r3["status"], "FAIL")

    def test_issue6_unit_prefix_longest_match(self):
        """Issue 6: 10 ms must not be truncated to 10 m."""
        source = "The delay is 10 ms."
        entities = [m.group(0) for m in NUM_ENTITY_REGEX.finditer(source)]
        self.assertEqual(entities, ["10 ms"])

        # Correct translation with ms must PASS
        r_correct = self.validator.validate(
            [{"id": "s1", "kind": "sentence", "source": source, "page": 1}],
            {"s1": "延迟为 10 ms。"}
        )
        self.assertEqual(r_correct["status"], "PASS")

        # Incorrect translation with m must FAIL
        r_wrong = self.validator.validate(
            [{"id": "s1", "kind": "sentence", "source": source, "page": 1}],
            {"s1": "延迟为 10 m。"}
        )
        self.assertEqual(r_wrong["status"], "FAIL")

    def test_issue7_numeric_false_rejects_pass(self):
        """Issue 7: Valid units (rad, V), space-less units (10ms), and math subscripts ($x_1$) must PASS."""
        test_cases = [
            ("The angle is 1 rad.", "角度为 1 rad。"),
            ("The voltage is 5 V.", "电压为 5 V。"),
            ("The delay is 10 ms.", "延迟为 10ms。"),
            ("The equation is $x_1+y_2=z_3$.", "方程为 $x_1+y_2=z_3$。"),
        ]
        for src, tgt in test_cases:
            rep = self.validator.validate(
                [{"id": "s1", "kind": "sentence", "source": src, "page": 1}],
                {"s1": tgt}
            )
            self.assertEqual(rep["status"], "PASS", f"Failed on valid translation: {src} -> {tgt}: {rep}")


if __name__ == "__main__":
    unittest.main()
