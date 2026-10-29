# -*- coding: utf-8 -*-
"""
Exhaustive regression test suite covering all failure modes and counterexamples
identified in Codex Review V2 (skill-review-v2.md).
"""

import os
import sys
import json
import shutil
import tempfile
import unittest
import subprocess
from pathlib import Path

# Add scripts directory to sys.path
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(TESTS_DIR, ".."))
SCRIPTS_DIR = os.path.join(PROJECT_ROOT, "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import pymupdf
import typst
from align_bilingual import split_en_sentences, split_zh_sentences
from extract_document import DocumentExtractor, extract_document
from extract_figures import extract_figures
from validate_translation import TranslationValidator, validate_translation
from compile_typst import render_typst_document, compile_typst


class TestV2CompilerRegression(unittest.TestCase):
    """Verifies that compile_typst renders all kinds and compiles cleanly."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_typst_reg_")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_plain_sentence_compiles(self):
        items = [{"kind": "sentence", "source": "The method is stable.", "translation": "该方法稳定。"}]
        rendered = render_typst_document(items, mode="A")
        typ_path = os.path.join(self.test_dir, "test_plain.typ")
        pdf_path = os.path.join(self.test_dir, "test_plain.pdf")
        with open(typ_path, "w", encoding="utf-8") as f:
            f.write(rendered)
        success = compile_typst(typ_path, output_pdf=pdf_path)
        self.assertTrue(success)
        self.assertTrue(os.path.isfile(pdf_path))

    def test_quoted_sentence_compiles(self):
        items = [{"kind": "sentence", "source": 'The method is "stable".', "translation": '该方法“稳定”。'}]
        rendered = render_typst_document(items, mode="A")
        typ_path = os.path.join(self.test_dir, "test_quote.typ")
        pdf_path = os.path.join(self.test_dir, "test_quote.pdf")
        with open(typ_path, "w", encoding="utf-8") as f:
            f.write(rendered)
        success = compile_typst(typ_path, output_pdf=pdf_path)
        self.assertTrue(success)

    def test_citation_bracket_compiles_without_slash(self):
        items = [{"kind": "sentence", "source": "The method is stable [1].", "translation": "该方法稳定 [1]。"}]
        rendered = render_typst_document(items, mode="A")
        typ_path = os.path.join(self.test_dir, "test_cit.typ")
        pdf_path = os.path.join(self.test_dir, "test_cit.pdf")
        with open(typ_path, "w", encoding="utf-8") as f:
            f.write(rendered)
        success = compile_typst(typ_path, output_pdf=pdf_path)
        self.assertTrue(success)
        with pymupdf.open(pdf_path) as doc:
            txt = "\n".join(p.get_text() for p in doc)
            self.assertIn("[1]", txt)
            self.assertNotIn("\\[1\\]", txt)

    def test_table_cell_and_caption_present_in_pdf(self):
        items = [
            {"kind": "sentence", "source": "The method is stable.", "translation": "该方法稳定。"},
            {"kind": "table_cell", "source": "TABLECELL_SENTINEL", "translation": "表格单元测试标记", "row": 0, "col": 0},
            {"kind": "table", "source": "Table 1. CAPTION_SENTINEL", "translation": "表 1. 表题测试标记"}
        ]
        rendered = render_typst_document(items, mode="A")
        self.assertIn("TABLECELL_SENTINEL", rendered)
        typ_path = os.path.join(self.test_dir, "test_tbl.typ")
        pdf_path = os.path.join(self.test_dir, "test_tbl.pdf")
        with open(typ_path, "w", encoding="utf-8") as f:
            f.write(rendered)
        success = compile_typst(typ_path, output_pdf=pdf_path)
        self.assertTrue(success)
        with pymupdf.open(pdf_path) as doc:
            txt = "\n".join(p.get_text() for p in doc)
            self.assertIn("TABLECELL_SENTINEL", txt)
            self.assertIn("CAPTION_SENTINEL", txt)

    def test_figure_without_image_path_compiles(self):
        items = [{"kind": "figure", "id": "fig-1", "source": "Fig. 1. Result.", "translation": "图 1. 结果。"}]
        rendered = render_typst_document(items, mode="A")
        typ_path = os.path.join(self.test_dir, "test_fig.typ")
        pdf_path = os.path.join(self.test_dir, "test_fig.pdf")
        with open(typ_path, "w", encoding="utf-8") as f:
            f.write(rendered)
        success = compile_typst(typ_path, output_pdf=pdf_path)
        self.assertTrue(success)


class TestV2ValidatorFalsePassRegression(unittest.TestCase):
    """Verifies that validate_translation rejects all counterexamples from Codex Review V2."""

    def setUp(self):
        self.validator = TranslationValidator(strict=True)

    def _item(self, source, ident="s1", kind="sentence", page=1):
        return {"id": ident, "kind": kind, "section": "", "page": page,
                "spans": [], "source": source, "status": "pending"}

    def test_numeric_discrepancy_0_25_vs_10_25(self):
        report = self.validator.validate([self._item("The error is 0.25 m.")], {"s1": "误差为 10.25 m。"})
        self.assertEqual(report["status"], "FAIL")
        self.assertGreater(report["summary"]["numerical_discrepancies_count"], 0)

    def test_sign_discrepancy_neg_0_25_vs_0_25(self):
        report = self.validator.validate([self._item("The bias is -0.25 m.")], {"s1": "偏差为 0.25 m。"})
        self.assertEqual(report["status"], "FAIL")
        self.assertGreater(report["summary"]["numerical_discrepancies_count"], 0)

    def test_exponent_discrepancy_1_5e_minus_3_vs_1_5e3(self):
        report = self.validator.validate([self._item("The drift is 1.5e-3.")], {"s1": "漂移为 1.5e3。"})
        self.assertEqual(report["status"], "FAIL")
        self.assertGreater(report["summary"]["numerical_discrepancies_count"], 0)

    def test_unit_discrepancy_m_vs_mm(self):
        report = self.validator.validate([self._item("The error is 0.25 m.")], {"s1": "误差为 0.25 mm。"})
        self.assertEqual(report["status"], "FAIL")
        self.assertGreater(report["summary"]["numerical_discrepancies_count"], 0)

    def test_formula_discrepancy_x_plus_y_vs_x_minus_y(self):
        report = self.validator.validate([self._item("The equation is $x+y=z$.")], {"s1": "方程为 $x-y=z$。"})
        self.assertEqual(report["status"], "FAIL")
        self.assertGreater(report["summary"]["formula_discrepancies_count"], 0)

    def test_polarity_discrepancy_does_not_converge(self):
        report = self.validator.validate([self._item("The method does not converge.")], {"s1": "该方法收敛。"})
        self.assertEqual(report["status"], "FAIL")

    def test_id_extra_ghost_translation_fails(self):
        report = self.validator.validate(
            [self._item("The method is stable.")],
            {"s1": "该方法稳定。", "ghost": "新增的未分配译文。"}
        )
        self.assertEqual(report["status"], "FAIL")
        self.assertIn("ghost", report["tier2_factual_integrity"]["extra_ids"])

    def test_duplicate_source_ids_fail(self):
        report = self.validator.validate(
            [self._item("Sentence A.", "s1"), self._item("Sentence B.", "s1")],
            {"s1": "译文。"}
        )
        self.assertEqual(report["status"], "FAIL")
        self.assertIn("s1", report["tier2_factual_integrity"]["duplicate_ids"])

    def test_scanned_pdf_zero_text_fails(self):
        tmp_pdf = tempfile.mktemp(suffix=".pdf")
        doc = pymupdf.open()
        page = doc.new_page()
        page.draw_rect((50, 50, 300, 300), color=(0, 0, 1), fill=(0.9, 0.9, 1))
        doc.save(tmp_pdf)
        doc.close()

        report = self.validator.validate([], {}, doc_path=tmp_pdf)
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["tier1_source_coverage"]["char_coverage_ratio"], 0.0)
        os.remove(tmp_pdf)

    def test_corrupted_pdf_fails(self):
        tmp_broken = tempfile.mktemp(suffix=".pdf")
        Path(tmp_broken).write_bytes(b"Invalid broken PDF binary content")
        report = self.validator.validate([self._item("Valid.")], {"s1": "有效。"}, doc_path=tmp_broken)
        self.assertEqual(report["status"], "FAIL")
        os.remove(tmp_broken)


class TestV2ExtractorRegression(unittest.TestCase):
    """Verifies document extraction improvements."""

    def test_sentence_split_consistency(self):
        texts = [
            ("Dr. Smith proposed the method. It converges.", 2),
            ("The estimate is unbiased. x is Gaussian.", 2),
            ("The estimator converges. [12] proves consistency.", 2),
        ]
        for text, expected_count in texts:
            sentences = split_en_sentences(text)
            self.assertEqual(len(sentences), expected_count, f"Failed on: {text}")

    def test_latex_table_cells_extracted(self):
        tmp_tex = tempfile.mktemp(suffix=".tex")
        Path(tmp_tex).write_text(r"""\begin{table}
\caption{Accuracy results}
\begin{tabular}{lc}
Method & Accuracy \\
GNSS & 0.25 \\
\end{tabular}
\end{table}
""", encoding="utf-8")
        items, _ = DocumentExtractor().extract(tmp_tex)
        sources = [i["source"] for i in items]
        self.assertIn("Accuracy results", sources)
        self.assertIn("Method", sources)
        self.assertIn("Accuracy", sources)
        self.assertIn("GNSS", sources)
        self.assertIn("0.25", sources)
        os.remove(tmp_tex)

    def test_side_by_side_column_figure_extraction(self):
        tmp_pdf = tempfile.mktemp(suffix=".pdf")
        doc = pymupdf.open()
        page = doc.new_page(width=595, height=842)
        # Left column figure & caption
        page.draw_rect((50, 100, 270, 300), color=(0, 0, 1))
        page.draw_line((50, 300), (270, 100), color=(0, 0, 1))
        page.insert_text((50, 330), "Fig. 1. Left result.", fontsize=10)
        # Right column figure & caption
        page.draw_rect((325, 100, 545, 300), color=(1, 0, 0))
        page.draw_line((325, 300), (545, 100), color=(1, 0, 0))
        page.insert_text((325, 345), "Fig. 2. Right result.", fontsize=10)
        doc.save(tmp_pdf)
        doc.close()

        tmp_out = tempfile.mkdtemp(prefix="test_fig_side_")
        figs = extract_figures(tmp_pdf, output_dir=tmp_out, dpi=150)
        self.assertEqual(len(figs), 2)
        truth_boxes = {
            "1": pymupdf.Rect(50, 100, 270, 300),
            "2": pymupdf.Rect(325, 100, 545, 300)
        }
        for fig in figs:
            truth = truth_boxes[fig["figure_id"]]
            fig_rect = pymupdf.Rect(fig["bbox"])
            ratio = round((fig_rect & truth).get_area() / truth.get_area(), 4)
            self.assertEqual(ratio, 1.0, f"Fig {fig['figure_id']} retained area ratio is {ratio}, expected 1.0")

        shutil.rmtree(tmp_out, ignore_errors=True)
        os.remove(tmp_pdf)


if __name__ == "__main__":
    unittest.main()
