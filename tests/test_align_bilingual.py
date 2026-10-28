# -*- coding: utf-8 -*-
"""
Exhaustive Unit Tests for Bilingual Sentence Alignment and Manifest Matching.

Tests:
1. Codex 3-vs-2 omission case: Omission detected and flagged, not hidden.
2. English abbreviations protection:
   - 'Dr. Smith proposed the method. It converges.' -> exact 2 sentences.
   - Prof., et al., i.e., e.g., Fig., Table., Eq., Ref., Sec., pp., p., etc.
3. Lowercase math variables and formulas:
   - 'The estimate is unbiased. x is Gaussian.' -> exact 2 sentences.
   - LaTeX command starts ('\alpha denotes...')
4. Citation markers:
   - 'The estimator converges. [12] proves consistency.' -> exact 2 sentences.
5. Empty inputs and ZeroDivisionError prevention:
   - Empty strings, whitespace, empty lists.
6. Chinese sentence splitting:
   - Fullwidth punctuation (。！？), paired quotes, protected decimals.
7. Manifest-based strict bijection matching:
   - Valid bijection, missing IDs, duplicate IDs, empty translations, unmapped IDs.
8. Heuristic sequence alignment:
   - 1-1, 1-0 omission, 0-1 insertion, 2-1 merge, 1-2 split.
"""

from __future__ import annotations

import os
import sys
import unittest

# Ensure scripts directory is on the import path
SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from align_bilingual import (
    clean_text,
    split_en_sentences,
    split_zh_sentences,
    match_manifest_translations,
    align_sentences_heuristic,
    align_sentences,
)


class TestSplitEnSentences(unittest.TestCase):
    """Test suite for English sentence splitting."""

    def test_dr_smith_abbreviation(self):
        """Test Dr. Smith does not cut into an independent sentence."""
        text = "Dr. Smith proposed the method. It converges."
        res = split_en_sentences(text)
        self.assertEqual(res, ["Dr. Smith proposed the method.", "It converges."])

    def test_lowercase_math_symbol_start(self):
        """Test sentence start with a lowercase math variable 'x'."""
        text = "The estimate is unbiased. x is Gaussian."
        res = split_en_sentences(text)
        self.assertEqual(res, ["The estimate is unbiased.", "x is Gaussian."])

    def test_citation_marker_start(self):
        """Test sentence start with citation marker '[12]'."""
        text = "The estimator converges. [12] proves consistency."
        res = split_en_sentences(text)
        self.assertEqual(res, ["The estimator converges.", "[12] proves consistency."])

    def test_latex_command_start(self):
        """Test sentence start with LaTeX Greek command '\\alpha'."""
        text = "The model converged. \\alpha denotes the learning rate."
        res = split_en_sentences(text)
        self.assertEqual(res, ["The model converged.", "\\alpha denotes the learning rate."])

    def test_digit_start(self):
        """Test sentence start with a numeric digit."""
        text = "First experiment finished. 10 samples were retained."
        res = split_en_sentences(text)
        self.assertEqual(res, ["First experiment finished.", "10 samples were retained."])

    def test_academic_and_paper_abbreviations(self):
        """Test paper elements, Latin abbreviations, and units."""
        text = (
            "Refer to Fig. 1 and Table. 2 for approx. min. sec. values "
            "(cf. Ref. [1], p. 5, pp. 10-12, Vol. 3, no. 4, ed. 2, eds. Smith et al.). "
            "Next sentence begins."
        )
        res = split_en_sentences(text)
        self.assertEqual(len(res), 2)
        self.assertTrue(res[0].startswith("Refer to Fig. 1"))
        self.assertEqual(res[1], "Next sentence begins.")

    def test_titles_and_institutions(self):
        """Test titles and organization abbreviations."""
        text = "Prof. Adams and Mr. Brown consulted Dept. of CS at Stanford Univ. It was fruitful."
        res = split_en_sentences(text)
        self.assertEqual(len(res), 2)
        self.assertEqual(
            res[0],
            "Prof. Adams and Mr. Brown consulted Dept. of CS at Stanford Univ."
        )
        self.assertEqual(res[1], "It was fruitful.")

    def test_decimals_and_version_strings(self):
        """Test decimal numbers and software version strings."""
        text = "The p-value was 0.05 on v1.2.3. The loss dropped by .01."
        res = split_en_sentences(text)
        self.assertEqual(len(res), 2)
        self.assertEqual(res[0], "The p-value was 0.05 on v1.2.3.")
        self.assertEqual(res[1], "The loss dropped by .01.")

    def test_ellipses_handling(self):
        """Test ellipses both inside a sentence and across sentences."""
        text_inline = "The values x_1, ..., x_n are strictly positive."
        res_inline = split_en_sentences(text_inline)
        self.assertEqual(res_inline, ["The values x_1, ..., x_n are strictly positive."])

        text_split = "The system paused... Then it resumed."
        res_split = split_en_sentences(text_split)
        self.assertEqual(res_split, ["The system paused...", "Then it resumed."])

    def test_quotes_around_sentences(self):
        """Test quotes enclosing sentences."""
        text = '"The method works well." claimed Dr. Jones. "It converges quickly."'
        res = split_en_sentences(text)
        self.assertEqual(len(res), 2)

    def test_empty_and_whitespace_inputs(self):
        """Test empty and whitespace-only inputs."""
        self.assertEqual(split_en_sentences(""), [])
        self.assertEqual(split_en_sentences("   \n\t  "), [])
        self.assertEqual(split_en_sentences(None), [])  # type: ignore[arg-type]


class TestSplitZhSentences(unittest.TestCase):
    """Test suite for Chinese sentence splitting."""

    def test_standard_chinese_punctuation(self):
        """Test fullwidth punctuation 。！？."""
        text = "收敛速度提高了 12.5%，误差降低至 0.03。下一代算法表现更优！真的有效吗？"
        res = split_zh_sentences(text)
        self.assertEqual(len(res), 3)
        self.assertEqual(res[0], "收敛速度提高了 12.5%，误差降低至 0.03。")
        self.assertEqual(res[1], "下一代算法表现更优！")
        self.assertEqual(res[2], "真的有效吗？")

    def test_paired_quotes_attachment(self):
        """Test closing quotation marks remain with the terminating sentence."""
        text = "李博士提出：“该方法能够有效收敛。”它在多个基准测试中表现良好。"
        res = split_zh_sentences(text)
        self.assertEqual(len(res), 2)
        self.assertEqual(res[0], "李博士提出：“该方法能够有效收敛。”")
        self.assertEqual(res[1], "它在多个基准测试中表现良好。")

    def test_empty_and_whitespace_zh(self):
        """Test empty Chinese inputs."""
        self.assertEqual(split_zh_sentences(""), [])
        self.assertEqual(split_zh_sentences("   "), [])
        self.assertEqual(split_zh_sentences(None), [])  # type: ignore[arg-type]


class TestMatchManifestTranslations(unittest.TestCase):
    """Test suite for strict ID-based manifest translation matching."""

    def test_valid_bijection(self):
        """Test 100% matched 1-to-1 manifest."""
        src = [
            {"id": "sec1_s1", "text": "Sentence 1."},
            {"id": "sec1_s2", "text": "Sentence 2."}
        ]
        trans = {
            "sec1_s1": "句子 1。",
            "sec1_s2": "句子 2。"
        }
        paired, audit = match_manifest_translations(src, trans)
        self.assertTrue(audit["is_valid"])
        self.assertEqual(audit["coverage_ratio"], 1.0)
        self.assertEqual(audit["missing_ids"], [])
        self.assertEqual(audit["duplicate_source_ids"], [])
        self.assertEqual(audit["duplicate_translation_ids"], [])
        self.assertEqual(audit["unmapped_ids"], [])
        self.assertEqual(audit["empty_translations"], [])
        self.assertEqual(len(paired), 2)
        self.assertEqual(paired[0]["zh"], "句子 1。")
        self.assertFalse(paired[0]["anomalous"])

    def test_missing_translation_id(self):
        """Test detecting omitted translation IDs."""
        src = [
            {"id": "s1", "text": "S1"},
            {"id": "s2", "text": "S2"},
            {"id": "s3", "text": "S3"}
        ]
        trans = {"s1": "Z1", "s2": "Z2"}  # s3 is missing
        paired, audit = match_manifest_translations(src, trans)
        self.assertFalse(audit["is_valid"])
        self.assertIn("s3", audit["missing_ids"])
        self.assertAlmostEqual(audit["coverage_ratio"], 2 / 3, places=3)
        self.assertTrue(paired[2]["anomalous"])
        self.assertIn("missing_translation", paired[2]["anomaly_reasons"])

    def test_duplicate_translation_ids(self):
        """Test detecting duplicate IDs in translation list."""
        src = [{"id": "s1", "text": "S1"}, {"id": "s2", "text": "S2"}]
        trans_list = [
            {"id": "s1", "text": "Z1"},
            {"id": "s1", "text": "Z1 dup"},
            {"id": "s2", "text": "Z2"}
        ]
        paired, audit = match_manifest_translations(src, trans_list)
        self.assertFalse(audit["is_valid"])
        self.assertIn("s1", audit["duplicate_translation_ids"])

    def test_duplicate_source_ids(self):
        """Test detecting duplicate IDs in source list."""
        src = [
            {"id": "s1", "text": "S1"},
            {"id": "s1", "text": "S1 dup"},
            {"id": "s2", "text": "S2"}
        ]
        trans = {"s1": "Z1", "s2": "Z2"}
        paired, audit = match_manifest_translations(src, trans)
        self.assertFalse(audit["is_valid"])
        self.assertIn("s1", audit["duplicate_source_ids"])

    def test_empty_translation_detection(self):
        """Test detecting empty or whitespace translations."""
        src = [{"id": "s1", "text": "S1"}, {"id": "s2", "text": "S2"}]
        trans = {"s1": "Z1", "s2": "   "}
        paired, audit = match_manifest_translations(src, trans)
        self.assertFalse(audit["is_valid"])
        self.assertIn("s2", audit["empty_translations"])
        self.assertTrue(paired[1]["anomalous"])
        self.assertIn("empty_translation", paired[1]["anomaly_reasons"])

    def test_unmapped_translation_ids(self):
        """Test detecting orphan translation IDs not present in source."""
        src = [{"id": "s1", "text": "S1"}]
        trans = {"s1": "Z1", "s_orphan": "Orphan text"}
        paired, audit = match_manifest_translations(src, trans)
        self.assertFalse(audit["is_valid"])
        self.assertIn("s_orphan", audit["unmapped_ids"])


class TestAlignSentencesHeuristic(unittest.TestCase):
    """Test suite for heuristic sequence alignment."""

    def test_codex_3_vs_2_omission_flagged(self):
        """
        Codex Review Crucial Requirement:
        Prove that a 3-vs-2 sentence count mismatch (omission) is DETECTED and FLAGGED,
        and NOT silently squashed into paired chunks without warning.
        """
        en_list = [
            "First, we analyze the algorithm convergence.",
            "Second, the error bound is derived in Eq. (1).",
            "Third, experimental results demonstrate high performance."
        ]
        zh_list = [
            "首先，我们分析了算法的收敛性。",
            "其次，公式 (1) 推导了误差界限。"
        ]

        res = align_sentences_heuristic(en_list, zh_list)

        # Must produce alignment entries for all 3 English sentences
        self.assertEqual(len(res), 3)

        # Sentences 1 and 2 pair successfully
        self.assertEqual(res[0]["en"], en_list[0])
        self.assertEqual(res[0]["zh"], zh_list[0])
        self.assertFalse(res[0]["anomalous"])

        self.assertEqual(res[1]["en"], en_list[1])
        self.assertEqual(res[1]["zh"], zh_list[1])
        self.assertFalse(res[1]["anomalous"])

        # Sentence 3 must be detected as an omission and flagged anomalous
        self.assertEqual(res[2]["en"], en_list[2])
        self.assertEqual(res[2]["zh"], "")
        self.assertTrue(res[2]["anomalous"])
        self.assertIn("omitted_translation", res[2]["anomaly_reasons"])
        self.assertEqual(res[2]["confidence"], 0.0)

    def test_plain_text_omission_without_anchors(self):
        """Test omission detection on text without explicit numerical anchors."""
        en_list = [
            "Sentence one describes the setting.",
            "Sentence two explains the model.",
            "Sentence three presents the benchmark."
        ]
        zh_list = [
            "第一句话描述了背景设定。",
            "第二句话解释了模型结构。"
        ]
        res = align_sentences_heuristic(en_list, zh_list)
        self.assertEqual(len(res), 3)
        self.assertTrue(any(item["anomalous"] for item in res))
        self.assertTrue(res[2]["anomalous"])
        self.assertEqual(res[2]["zh"], "")

    def test_merge_detection(self):
        """Test that when 2 sentences are legitimately merged, it is flagged with anomalous: True."""
        en_list = [
            "Step 1 is initialization.",
            "Step 2 is optimization.",
            "Step 3 is evaluation."
        ]
        zh_list = [
            "步骤 1 是初始化，步骤 2 是优化。",
            "步骤 3 是评估。"
        ]
        res = align_sentences_heuristic(en_list, zh_list)
        self.assertEqual(len(res), 2)
        # Merged item must be flagged with count_mismatch
        self.assertTrue(res[0]["anomalous"])
        self.assertIn("count_mismatch", res[0]["anomaly_reasons"])
        self.assertFalse(res[1]["anomalous"])

    def test_zero_division_prevention(self):
        """Verify that empty inputs never raise ZeroDivisionError."""
        # Both empty
        self.assertEqual(align_sentences_heuristic([], []), [])
        self.assertEqual(align_sentences([], []), [])

        # English empty, Chinese non-empty
        res_zh = align_sentences_heuristic([], ["孤立中文。"])
        self.assertEqual(len(res_zh), 1)
        self.assertTrue(res_zh[0]["anomalous"])
        self.assertEqual(align_sentences([], ["孤立中文。"]), [("", "孤立中文。")])

        # English non-empty, Chinese empty
        res_en = align_sentences_heuristic(["Orphan English."], [])
        self.assertEqual(len(res_en), 1)
        self.assertTrue(res_en[0]["anomalous"])
        self.assertEqual(align_sentences(["Orphan English."], []), [("Orphan English.", "")])

    def test_clean_text_utility(self):
        """Test clean_text robustness."""
        self.assertEqual(clean_text(None), "")
        self.assertEqual(clean_text("   hello   world \n\t "), "hello world")


if __name__ == "__main__":
    unittest.main()
