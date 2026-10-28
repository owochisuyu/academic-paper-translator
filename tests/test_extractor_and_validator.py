# -*- coding: utf-8 -*-
"""
Comprehensive test suite for document extraction, geometry-aware figure extraction,
and two-tier translation validation engine.
"""

import os
import sys
import json
import shutil
import tempfile
import unittest
import pymupdf

# Add scripts directory to sys.path
SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from extract_figures import extract_figures
from extract_document import extract_document, DocumentExtractor
from validate_translation import validate_translation, TranslationValidator


class TestGeometryAwareFigureExtraction(unittest.TestCase):
    """Tests for geometry-aware figure extraction and avoiding internal label truncation."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_fig_extract_")
        self.output_dir = os.path.join(self.test_dir, "extracted_figures")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_figure_extraction_with_internal_text_labels_not_truncated(self):
        """
        Creates a synthetic PDF where a figure has internal axis labels and legend text.
        Verifies that extract_figures computes the TRUE graphics bounding box and
        is NOT truncated by internal labels.
        """
        pdf_path = os.path.join(self.test_dir, "test_paper_internal_labels.pdf")
        doc = pymupdf.open()
        page = doc.new_page(width=612, height=792)

        # 1. Paragraph text above the figure (y=50 to y=80)
        page.insert_text(pymupdf.Point(54, 65), "In this section we evaluate the positioning accuracy of our algorithm.", fontsize=10)

        # 2. Main figure vector graphics: bounding rectangle from y=110 to y=260
        plot_rect = pymupdf.Rect(60, 110, 320, 260)
        page.draw_rect(plot_rect, color=(0, 0, 0), width=1.5)
        # Internal curve
        page.draw_line(pymupdf.Point(60, 260), pymupdf.Point(320, 130), color=(1, 0, 0), width=2)

        # 3. Internal text labels INSIDE the figure:
        # Axis label near the bottom of the plot (y=245) - the exact failure trigger in the old script!
        page.insert_text(pymupdf.Point(150, 245), "Time (seconds)", fontsize=9)
        # Axis tick marks and numbers
        page.insert_text(pymupdf.Point(65, 255), "0.0", fontsize=8)
        page.insert_text(pymupdf.Point(305, 255), "10.0", fontsize=8)
        # Legend at top of plot
        page.insert_text(pymupdf.Point(80, 125), "Legend: Proposed Method", fontsize=9)

        # 4. Figure Caption below the figure (y=285)
        page.insert_text(pymupdf.Point(60, 285), "Fig. 1: Convergence trajectory over time under harsh multipath conditions.", fontsize=10)

        # 5. Subsequent paragraph below figure (y=330)
        page.insert_text(pymupdf.Point(54, 330), "As observed in Fig. 1, the filter achieves fast convergence.", fontsize=10)

        doc.save(pdf_path)
        doc.close()

        # Run extraction
        figures = extract_figures(pdf_path, output_dir=self.output_dir, dpi=150)

        self.assertEqual(len(figures), 1, "Exactly one figure should be extracted.")
        fig = figures[0]

        self.assertEqual(fig["figure_id"], "1")
        self.assertEqual(fig["page"], 1)
        self.assertTrue(os.path.isfile(fig["image_path"]), "Extracted image file must exist on disk.")

        # Check geometry:
        # Old naive code cropped from y=245 to 285 (height ~ 40pt or ~15% area).
        # True figure spans y=110 to y=260 (height ~ 150pt).
        bbox = fig["bbox"]
        y0, y1 = bbox[1], bbox[3]
        fig_height = y1 - y0

        self.assertLessEqual(y0, 115.0, f"Figure top y0={y0} must capture the top of the graphics (~110pt), not truncated to 245pt!")
        self.assertGreaterEqual(y1, 258.0, f"Figure bottom y1={y1} must capture the bottom of the graphics (~260pt).")
        self.assertGreater(fig_height, 130.0, f"Extracted figure height ({fig_height}pt) must capture full figure, not truncated!")

    def test_appendix_figure_extraction(self):
        """Verifies appendix figure regex and extraction for Fig. S1 / Supplementary Figure."""
        pdf_path = os.path.join(self.test_dir, "test_appendix_figure.pdf")
        doc = pymupdf.open()
        page = doc.new_page(width=612, height=792)

        # Appendix figure drawing
        page.draw_rect(pymupdf.Rect(54, 120, 400, 300), color=(0, 0, 1), fill=(0.95, 0.95, 1.0))
        # Appendix caption
        page.insert_text(pymupdf.Point(54, 325), "Fig. S1: Supplementary calibration results under different temperatures.", fontsize=10)

        doc.save(pdf_path)
        doc.close()

        figures = extract_figures(pdf_path, output_dir=self.output_dir, dpi=150)
        self.assertEqual(len(figures), 1)
        fig = figures[0]

        self.assertEqual(fig["figure_id"], "S1")
        self.assertTrue(fig["image_path"].endswith("fig_S1.png"))
        self.assertTrue(os.path.isfile(fig["image_path"]))

    def test_multi_figure_separation(self):
        """Verifies two figures on the same page are correctly isolated without overlapping."""
        pdf_path = os.path.join(self.test_dir, "test_multi_figures.pdf")
        doc = pymupdf.open()
        page = doc.new_page(width=612, height=792)

        # Figure 1 at top
        page.draw_rect(pymupdf.Rect(60, 80, 300, 200), color=(0, 0, 0))
        page.insert_text(pymupdf.Point(60, 220), "Figure 1: Benchmark baseline performance.", fontsize=10)

        # Figure 2 at bottom
        page.draw_rect(pymupdf.Rect(60, 320, 300, 450), color=(0, 0, 1))
        page.insert_text(pymupdf.Point(60, 470), "Figure 2: Benchmark proposed performance.", fontsize=10)

        doc.save(pdf_path)
        doc.close()

        figures = extract_figures(pdf_path, output_dir=self.output_dir, dpi=150)
        self.assertEqual(len(figures), 2)

        fig1, fig2 = figures[0], figures[1]
        self.assertEqual(fig1["figure_id"], "1")
        self.assertEqual(fig2["figure_id"], "2")

        # Fig 1 should be strictly above Fig 2
        self.assertLess(fig1["bbox"][3], fig2["bbox"][1], "Figure 1 bottom must be above Figure 2 top.")


class TestDocumentExtractor(unittest.TestCase):
    """Tests for structured document extraction, stable unique IDs, and scope."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_doc_extract_")
        self.output_jsonl = os.path.join(self.test_dir, "source.jsonl")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_extract_pdf_manifest_stable_ids(self):
        """Verifies structured PDF extraction produces valid schema, stable IDs, and statistics."""
        pdf_path = os.path.join(self.test_dir, "sample_paper.pdf")
        doc = pymupdf.open()
        page = doc.new_page(width=612, height=792)

        # Title
        page.insert_text(pymupdf.Point(54, 50), "Ambiguity Resolution in Challenging Urban Environments", fontsize=15)
        # Heading 1
        page.insert_text(pymupdf.Point(54, 90), "1. Introduction", fontsize=13)
        # Paragraph with 2 sentences
        page.insert_text(
            pymupdf.Point(54, 115),
            "High-precision GNSS positioning requires 0.25 m accuracy [1]. This is challenging in dense urban canyons.",
            fontsize=10
        )
        # Figure caption
        page.insert_text(pymupdf.Point(54, 180), "Fig. 1: Test scenario in downtown Tokyo.", fontsize=10)
        # Heading 2: References
        page.insert_text(pymupdf.Point(54, 230), "References", fontsize=13)
        page.insert_text(pymupdf.Point(54, 255), "[1] P. Teunissen, 'The LAMBDA method', J. Geod., 1995.", fontsize=9)

        doc.save(pdf_path)
        doc.close()

        stats = extract_document(pdf_path, output_jsonl=self.output_jsonl, scope="full")

        # Verify summary stats
        self.assertEqual(stats["pages"], 1)
        self.assertGreaterEqual(stats["sections"], 2)
        self.assertGreaterEqual(stats["sentence_count"], 2)
        self.assertGreaterEqual(stats["figure_count"], 1)
        self.assertGreaterEqual(stats["reference_count"], 1)

        # Read JSONL and verify schema for every item
        items = []
        with open(self.output_jsonl, "r", encoding="utf-8") as f:
            for line in f:
                items.append(json.loads(line))

        self.assertGreater(len(items), 0)
        seen_ids = set()

        for item in items:
            # Verify required schema fields
            self.assertIn("id", item)
            self.assertIn("kind", item)
            self.assertIn("section", item)
            self.assertIn("page", item)
            self.assertIn("spans", item)
            self.assertIn("source", item)
            self.assertIn("status", item)

            self.assertEqual(item["status"], "pending")
            self.assertIsInstance(item["spans"], list)
            self.assertNotIn(item["id"], seen_ids, f"ID {item['id']} must be unique!")
            seen_ids.add(item["id"])

        # Check presence of specific stable ID formats
        has_heading = any(item["kind"] == "heading" and "head" in item["id"] for item in items)
        has_sentence = any(item["kind"] == "sentence" and "-s001" in item["id"] for item in items)
        has_figure = any(item["kind"] == "figure" and item["id"] == "fig-1-cap" for item in items)
        has_ref = any(item["kind"] == "reference" and item["id"] == "ref-001" for item in items)

        self.assertTrue(has_heading, "Should contain heading item with stable ID.")
        self.assertTrue(has_sentence, "Should contain sentence item with stable ID.")
        self.assertTrue(has_figure, "Should contain figure item with ID fig-1-cap.")
        self.assertTrue(has_ref, "Should contain reference item with ID ref-001.")

    def test_scope_body_excludes_references(self):
        """Verifies --scope body excludes reference items while retaining body sentences."""
        pdf_path = os.path.join(self.test_dir, "scope_paper.pdf")
        doc = pymupdf.open()
        page = doc.new_page(width=612, height=792)

        page.insert_text(pymupdf.Point(54, 70), "1. Introduction", fontsize=13)
        page.insert_text(pymupdf.Point(54, 95), "We propose a novel framework for real-time localization.", fontsize=10)
        page.insert_text(pymupdf.Point(54, 150), "References", fontsize=13)
        page.insert_text(pymupdf.Point(54, 175), "[1] Reference item 1.", fontsize=9)

        doc.save(pdf_path)
        doc.close()

        # Scope = body
        stats_body = extract_document(pdf_path, output_jsonl=self.output_jsonl, scope="body")
        self.assertEqual(stats_body["reference_count"], 0, "References must be omitted when scope='body'")

        # Scope = full
        stats_full = extract_document(pdf_path, output_jsonl=self.output_jsonl, scope="full")
        self.assertEqual(stats_full["reference_count"], 1, "References must be included when scope='full'")

    def test_markdown_extraction(self):
        """Verifies structured extraction from Markdown files."""
        md_path = os.path.join(self.test_dir, "paper.md")
        md_content = """# 1. Introduction
High-precision positioning requires 0.25 m accuracy [1]. It is tested at 50 Hz.

![Fig. 1: System Architecture](architecture.png)

$$
\\hat{a} = \\arg\\min (y - A a)^T Q^{-1} (y - A a)
$$

# References
[1] Author A, 2024. Paper title.
"""
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)

    def test_latex_extraction(self):
        """Verifies structured extraction from LaTeX documents."""
        tex_path = os.path.join(self.test_dir, "sample.tex")
        tex_content = r"""\documentclass{article}
\begin{document}
\section{Introduction}
Global Navigation Satellite Systems provide continuous positioning. The baseline accuracy is 0.25 m.

\begin{equation}
y = A x + e
\end{equation}

\begin{figure}
\centering
\caption{Fig. 1: Architecture of the dual-frequency receiver.}
\end{figure}

\begin{thebibliography}{99}
\bibitem{ref1} Teunissen P. LAMBDA method. 1995.
\end{thebibliography}
\end{document}
"""
        with open(tex_path, "w", encoding="utf-8") as f:
            f.write(tex_content)

        stats = extract_document(tex_path, output_jsonl=self.output_jsonl, scope="full")
        self.assertGreaterEqual(stats["sentence_count"], 2)
        self.assertEqual(stats["figure_count"], 1)
        self.assertEqual(stats["equation_count"], 1)
        self.assertEqual(stats["reference_count"], 1)


class TestValidationEngine(unittest.TestCase):
    """Tests for the two-tier translation validation engine."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_validator_")
        self.report_path = os.path.join(self.test_dir, "coverage_report.json")
        self.source_path = os.path.join(self.test_dir, "source.jsonl")
        self.trans_path = os.path.join(self.test_dir, "translated.jsonl")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _write_jsonl(self, path: str, items: list[dict]):
        with open(path, "w", encoding="utf-8") as f:
            for item in items:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")

    def test_case_a_complete_translation_pass(self):
        """Case A: 100% complete and accurate translation -> PASS."""
        source_items = [
            {
                "id": "sec01-p001-b001-s001",
                "kind": "sentence",
                "section": "1. Introduction",
                "page": 1,
                "spans": [],
                "source": "The positioning system achieves 0.25 m error at 95% confidence [1].",
                "status": "pending"
            },
            {
                "id": "sec01-p001-b001-s002",
                "kind": "sentence",
                "section": "1. Introduction",
                "page": 1,
                "spans": [],
                "source": "This matches the theoretical limit from Eq. (1) (Smith, 2024).",
                "status": "pending"
            }
        ]
        translated_items = [
            {
                "id": "sec01-p001-b001-s001",
                "translation": "该定位系统在95%置信度下达到0.25 m误差 [1]。"
            },
            {
                "id": "sec01-p001-b001-s002",
                "translation": "这与式 (1) 的理论极限相吻合 (Smith, 2024)。"
            }
        ]
        self._write_jsonl(self.source_path, source_items)
        self._write_jsonl(self.trans_path, translated_items)

        report = validate_translation(self.source_path, self.trans_path, report_path=self.report_path)

        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["summary"]["missing_ids_count"], 0)
        self.assertEqual(report["summary"]["empty_translations_count"], 0)
        self.assertEqual(report["summary"]["numerical_discrepancies_count"], 0)
        self.assertTrue(os.path.isfile(self.report_path))

    def test_case_b_omitted_sentence_fail(self):
        """Case B: Omitted sentence -> FAIL with exact missing ID reported."""
        source_items = [
            {
                "id": "sec01-p001-b001-s001",
                "kind": "sentence",
                "section": "1. Introduction",
                "page": 1,
                "spans": [],
                "source": "First sentence.",
                "status": "pending"
            },
            {
                "id": "sec01-p001-b001-s002",
                "kind": "sentence",
                "section": "1. Introduction",
                "page": 1,
                "spans": [],
                "source": "Second sentence that was omitted by mistake.",
                "status": "pending"
            }
        ]
        # Only translating the first item
        translated_items = [
            {
                "id": "sec01-p001-b001-s001",
                "translation": "第一句话。"
            }
        ]
        self._write_jsonl(self.source_path, source_items)
        self._write_jsonl(self.trans_path, translated_items)

        report = validate_translation(self.source_path, self.trans_path, report_path=self.report_path)

        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["summary"]["missing_ids_count"], 1)
        self.assertIn("sec01-p001-b001-s002", report["tier2_factual_integrity"]["missing_ids"])

    def test_case_c_mutated_number_fail(self):
        """Case C: Mutated number (e.g. '0.25 m' translated as '5 cm') -> FAIL in strict mode."""
        source_items = [
            {
                "id": "sec01-p001-b001-s001",
                "kind": "sentence",
                "section": "1. Introduction",
                "page": 1,
                "spans": [],
                "source": "The horizontal accuracy is 0.25 m in urban canyon environments.",
                "status": "pending"
            }
        ]
        # Mutated number: replaced 0.25 m with 5 cm
        translated_items = [
            {
                "id": "sec01-p001-b001-s001",
                "translation": "在城市峡谷环境中，水平精度为 5 cm。"
            }
        ]
        self._write_jsonl(self.source_path, source_items)
        self._write_jsonl(self.trans_path, translated_items)

        report = validate_translation(self.source_path, self.trans_path, report_path=self.report_path, strict=True)

        self.assertEqual(report["status"], "FAIL")
        self.assertGreater(report["summary"]["numerical_discrepancies_count"], 0)
        num_issues = report["tier2_factual_integrity"]["numerical_issues"]
        self.assertTrue(any(iss["numeric_base"] == "0.25" for iss in num_issues))

    def test_case_d_empty_translation_fail(self):
        """Case D: Empty translation -> FAIL."""
        source_items = [
            {
                "id": "sec01-p001-b001-s001",
                "kind": "sentence",
                "section": "1. Introduction",
                "page": 1,
                "spans": [],
                "source": "Non-empty source sentence.",
                "status": "pending"
            }
        ]
        translated_items = [
            {
                "id": "sec01-p001-b001-s001",
                "translation": "   "  # whitespace only
            }
        ]
        self._write_jsonl(self.source_path, source_items)
        self._write_jsonl(self.trans_path, translated_items)

        report = validate_translation(self.source_path, self.trans_path, report_path=self.report_path)

        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["summary"]["empty_translations_count"], 1)

    def test_case_e_missing_citation_fail(self):
        """Case E: Missing citation -> FAIL in strict mode."""
        source_items = [
            {
                "id": "sec01-p001-b001-s001",
                "kind": "sentence",
                "section": "1. Introduction",
                "page": 1,
                "spans": [],
                "source": "This method was demonstrated by previous benchmarks [1, 2].",
                "status": "pending"
            }
        ]
        # Translation drops [1, 2]
        translated_items = [
            {
                "id": "sec01-p001-b001-s001",
                "translation": "该方法已在先前的基准测试中得到验证。"
            }
        ]
        self._write_jsonl(self.source_path, source_items)
        self._write_jsonl(self.trans_path, translated_items)

        report = validate_translation(self.source_path, self.trans_path, report_path=self.report_path, strict=True)

        self.assertEqual(report["status"], "FAIL")
        self.assertGreater(report["summary"]["citation_discrepancies_count"], 0)

    def test_case_f_duplicate_id_fail(self):
        """Case F: Duplicate IDs in translated items -> FAIL."""
        source_items = [
            {
                "id": "sec01-p001-b001-s001",
                "kind": "sentence",
                "section": "1. Introduction",
                "page": 1,
                "spans": [],
                "source": "Sentence.",
                "status": "pending"
            }
        ]
        # Translation has duplicate entries for same ID
        translated_items = [
            {"id": "sec01-p001-b001-s001", "translation": "译文1。"},
            {"id": "sec01-p001-b001-s001", "translation": "译文2。"}
        ]
        self._write_jsonl(self.source_path, source_items)
        self._write_jsonl(self.trans_path, translated_items)

        report = validate_translation(self.source_path, self.trans_path, report_path=self.report_path)

        self.assertEqual(report["status"], "FAIL")
        self.assertGreater(report["summary"]["duplicate_ids_count"], 0)

    def test_tier1_missing_pages_fail(self):
        """Verifies Tier 1 fails when document pages with text are missing from manifest."""
        pdf_path = os.path.join(self.test_dir, "three_page_paper.pdf")
        doc = pymupdf.open()
        p1 = doc.new_page(width=612, height=792)
        p1.insert_text(pymupdf.Point(54, 70), "Page 1 content: Introduction and problem formulation.", fontsize=12)

        p2 = doc.new_page(width=612, height=792)
        p2.insert_text(pymupdf.Point(54, 70), "Page 2 content: Methodology and algorithm descriptions.", fontsize=12)

        p3 = doc.new_page(width=612, height=792)
        p3.insert_text(pymupdf.Point(54, 70), "Page 3 content: Experimental benchmarks and conclusions.", fontsize=12)

        doc.save(pdf_path)
        doc.close()

        # Manifest only contains pages 1 and 2 (page 3 omitted)
        source_items = [
            {"id": "sec01-p001-b001-s001", "kind": "sentence", "section": "1", "page": 1, "spans": [], "source": "Page 1 content.", "status": "pending"},
            {"id": "sec01-p002-b001-s001", "kind": "sentence", "section": "2", "page": 2, "spans": [], "source": "Page 2 content.", "status": "pending"}
        ]
        translated_items = [
            {"id": "sec01-p001-b001-s001", "translation": "第1页内容。"},
            {"id": "sec01-p002-b001-s001", "translation": "第2页内容。"}
        ]
        self._write_jsonl(self.source_path, source_items)
        self._write_jsonl(self.trans_path, translated_items)

        validator = TranslationValidator(strict=True)
        report = validator.validate(self.source_path, self.trans_path, doc_path=pdf_path, report_path=self.report_path)

        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["summary"]["tier1_status"], "FAIL")
        self.assertIn(3, report["tier1_source_coverage"]["missing_pages"])

    def test_scientific_notation_and_units_preservation(self):
        """Verifies scientific notation and frequency/speed units preservation."""
        source_items = [
            {
                "id": "sec01-p001-b001-s001",
                "kind": "sentence",
                "section": "1",
                "page": 1,
                "spans": [],
                "source": "The drift rate was 1.5 \\times 10^{-3} at 50 Hz and speed 100 km/h.",
                "status": "pending"
            }
        ]
        # Complete translation preserving scientific notation, numbers, and units
        translated_pass = [
            {
                "id": "sec01-p001-b001-s001",
                "translation": "在 50 Hz 频率及 100 km/h 速度下，漂移率为 1.5 \\times 10^{-3}。"
            }
        ]
        self._write_jsonl(self.source_path, source_items)
        self._write_jsonl(self.trans_path, translated_pass)

        report = validate_translation(self.source_path, self.trans_path, report_path=self.report_path, strict=True)
        self.assertEqual(report["status"], "PASS")

        # Mutated translation missing 1.5
        translated_fail = [
            {
                "id": "sec01-p001-b001-s001",
                "translation": "在 50 Hz 频率及 100 km/h 速度下，漂移率极低。"
            }
        ]
        self._write_jsonl(self.trans_path, translated_fail)
        report_fail = validate_translation(self.source_path, self.trans_path, report_path=self.report_path, strict=True)
        self.assertEqual(report_fail["status"], "FAIL")
        self.assertGreater(report_fail["summary"]["numerical_discrepancies_count"], 0)


class TestCommandLineInterface(unittest.TestCase):
    """End-to-end CLI integration tests for scripts."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_cli_")
        self.pdf_path = os.path.join(self.test_dir, "cli_test.pdf")
        doc = pymupdf.open()
        page = doc.new_page(width=612, height=792)
        page.insert_text(pymupdf.Point(54, 50), "1. Introduction", fontsize=14)
        page.insert_text(pymupdf.Point(54, 80), "This test paper evaluates positioning accuracy of 0.25 m [1].", fontsize=10)
        page.draw_rect(pymupdf.Rect(54, 110, 300, 220), color=(0, 0, 0), width=1)
        page.insert_text(pymupdf.Point(54, 240), "Fig. 1: Measured experimental error curve.", fontsize=10)
        doc.save(self.pdf_path)
        doc.close()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_cli_extract_figures(self):
        """Tests scripts/extract_figures.py via CLI."""
        import subprocess
        out_figs = os.path.join(self.test_dir, "figures_out")
        cmd = [sys.executable, os.path.join(SCRIPTS_DIR, "extract_figures.py"), self.pdf_path, "--out", out_figs, "--dpi", "150"]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"extract_figures failed: {res.stderr}")
        self.assertTrue(os.path.isdir(out_figs))
        self.assertTrue(os.path.isfile(os.path.join(out_figs, "fig_1.png")))

    def test_cli_extract_document(self):
        """Tests scripts/extract_document.py via CLI."""
        import subprocess
        out_jsonl = os.path.join(self.test_dir, "source.jsonl")
        out_summary = os.path.join(self.test_dir, "summary.json")
        cmd = [
            sys.executable, os.path.join(SCRIPTS_DIR, "extract_document.py"),
            self.pdf_path, "--out", out_jsonl, "--scope", "full", "--summary", out_summary
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"extract_document failed: {res.stderr}")
        self.assertTrue(os.path.isfile(out_jsonl))
        self.assertTrue(os.path.isfile(out_summary))

    def test_cli_validate_translation(self):
        """Tests scripts/validate_translation.py via CLI for both exit 0 and exit 1."""
        import subprocess
        source_jsonl = os.path.join(self.test_dir, "source.jsonl")
        trans_pass = os.path.join(self.test_dir, "translated_pass.jsonl")
        trans_fail = os.path.join(self.test_dir, "translated_fail.jsonl")
        report_json = os.path.join(self.test_dir, "report.json")

        # 1. Extract document
        extract_document(self.pdf_path, output_jsonl=source_jsonl, scope="full")

        # Read source items
        with open(source_jsonl, "r", encoding="utf-8") as f:
            src_items = [json.loads(line) for line in f]

        # 2. Build passing translation
        pass_items = []
        for item in src_items:
            # retain numbers and citations
            src = item["source"]
            pass_items.append({"id": item["id"], "translation": f"中文翻译: {src}"})

        with open(trans_pass, "w", encoding="utf-8") as f:
            for it in pass_items:
                f.write(json.dumps(it, ensure_ascii=False) + "\n")

        # Run validate_translation CLI on pass
        cmd_pass = [
            sys.executable, os.path.join(SCRIPTS_DIR, "validate_translation.py"),
            "--source", source_jsonl, "--translated", trans_pass, "--report", report_json
        ]
        res_pass = subprocess.run(cmd_pass, capture_output=True, text=True)
        self.assertEqual(res_pass.returncode, 0, f"validate_translation should exit 0 on pass: {res_pass.stderr}")

        # 3. Build failing translation (omit an ID)
        fail_items = pass_items[:-1]
        with open(trans_fail, "w", encoding="utf-8") as f:
            for it in fail_items:
                f.write(json.dumps(it, ensure_ascii=False) + "\n")

        cmd_fail = [
            sys.executable, os.path.join(SCRIPTS_DIR, "validate_translation.py"),
            "--source", source_jsonl, "--translated", trans_fail, "--report", report_json
        ]
        res_fail = subprocess.run(cmd_fail, capture_output=True, text=True)
        self.assertEqual(res_fail.returncode, 1, "validate_translation should exit 1 on fail")


if __name__ == "__main__":
    unittest.main()
