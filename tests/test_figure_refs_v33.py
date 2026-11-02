"""Native figure and bibliography boundaries; no OCR/table model is used here."""
from pathlib import Path
import copy
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pymupdf

from scripts.backends.pdf_order import order_pdf_units
from scripts.backends.pdf_references import reconcile_numbered_references
from scripts.document_ir import create_job, load_job
from scripts.extract_document import DocumentExtractor
from scripts.extract_figures import extract_figures
from scripts.source_coverage import validate_source_coverage


class FigureReferencesV33Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def extract(self, pdf):
        extractor = DocumentExtractor()
        with patch.object(pymupdf.Page, 'find_tables', return_value=SimpleNamespace(tables=[])):
            items = extractor._extract_pdf(pdf)
        return order_pdf_units(items, extractor.assets), extractor

    def bibliography(self, name='references.pdf', numbers=(1, 2, 3), continuation=False):
        pdf = self.root / name
        with pymupdf.open() as document:
            page = document.new_page()
            page.insert_text((50, 60), 'Body stays outside the bibliography.', fontsize=11)
            page.insert_text((50, 120), 'REFERENCES', fontsize=9)
            page.insert_text((50, 150), f'[{numbers[0]}] Alpha. First title. 2001.\n[{numbers[1]}] Beta. A title beginning', fontsize=8)
            page.insert_text((50, 190), f'with its continuation. 2002.\n[{numbers[2]}] Gamma. Third title.', fontsize=8)
            if continuation:
                page = document.new_page()
                page.insert_text((50, 60), 'The final publication details. 2003.\n[4] Delta. Fourth title. 2004.', fontsize=8)
            else:
                page.insert_text((50, 235), 'Appendix A', fontsize=10)
                page.insert_text((50, 260), 'Appendix prose remains a separate source.', fontsize=10)
            document.save(pdf)
        return pdf

    def test_prose_cross_reference_does_not_create_duplicate_figure(self):
        pdf = self.root / 'two-panels.pdf'
        with pymupdf.open() as document:
            page = document.new_page(width=600, height=800)
            page.draw_rect([50, 90, 140, 170], fill=(.8, .2, .2))
            page.draw_rect([170, 90, 260, 170], fill=(.2, .2, .8))
            page.insert_text((50, 190), 'Figure 8. Two panels with one caption.', fontsize=9)
            page.insert_text((310, 240), '(see Fig. 8 again, and compare the two panels)', fontsize=10)
            page.insert_text((50, 290), 'As Fig. 8 shows, both panels are relevant.', fontsize=10)
            document.save(pdf)
        assets = extract_figures(pdf, self.root / 'assets', dpi=100)
        self.assertEqual(len(assets), 1)
        asset = assets[0]
        self.assertEqual(asset['figure_id'], '8')
        crop = pymupdf.Rect(asset['bbox'])
        self.assertTrue(crop.contains(pymupdf.Rect(50, 90, 260, 170)))
        self.assertLess(crop.y1, 190)
        extractor = DocumentExtractor(assets_manifest=self.root / 'assets/figures_manifest.json')
        with patch.object(pymupdf.Page, 'find_tables', return_value=SimpleNamespace(tables=[])):
            items, _ = extractor.extract(pdf)
        figure = next(item for item in items if item['kind'] == 'figure')
        self.assertEqual(extractor.assets[0]['owner_ids'], [figure['id']])
        self.assertTrue(any(item['source'].startswith('(see Fig. 8') for item in items))

    def test_small_cap_heading_splits_and_joins_exact_reference_fragments(self):
        pdf = self.bibliography()
        items, extractor = self.extract(pdf)
        before_body = next(item['source'] for item in items if item['source'].startswith('Body stays'))
        result, info = reconcile_numbered_references(items, extractor.regions, extractor.warnings)
        refs = [item for item in result if item['kind'] == 'reference']
        self.assertEqual(info['entries'], 3)
        self.assertEqual([item['source'] for item in refs], [
            '[1] Alpha. First title. 2001.',
            '[2] Beta. A title beginning with its continuation. 2002.',
            '[3] Gamma. Third title.'])
        self.assertEqual(next(item['kind'] for item in result if item['source'] == 'REFERENCES'), 'heading')
        self.assertTrue(any(item['source'] == before_body for item in result))
        self.assertTrue(any(item['source'].startswith('Appendix prose') and item['kind'] != 'reference' for item in result))
        self.assertGreater(len(refs[1]['source_parts']), 1)
        raw_regions = {region['region_id']: region['raw_text'] for region in extractor.regions}
        for ref in refs:
            for part in ref['source_parts']:
                loc = part['locators'][0]
                self.assertEqual(part['raw_source'], raw_regions[loc['region_id']][loc['start']:loc['end']])
        coverage = validate_source_coverage(result, pdf, regions=extractor.regions)
        self.assertEqual(coverage['status'], 'PASS', coverage)

    def test_adjacent_column_figures_keep_panels_and_do_not_cross_the_gutter(self):
        pdf = self.root / 'adjacent-columns.pdf'
        with pymupdf.open() as document:
            page = document.new_page(width=612, height=792)
            # The right figure has a component close enough to the gutter to
            # trigger the former caption-margin overlap heuristic.
            page.draw_rect([50, 80, 160, 170], fill=(.8, .2, .2))
            page.draw_rect([180, 80, 295, 170], fill=(.2, .2, .8))
            page.draw_rect([314, 85, 346, 165], fill=(.2, .8, .2))
            page.draw_rect([360, 80, 560, 170], fill=(.8, .8, .2))
            page.insert_textbox([49, 190, 300, 230], 'Figure 8. The left figure has two complete panels.', fontsize=9)
            page.insert_textbox([312, 190, 563, 230], 'Figure 9. The right figure is separate.', fontsize=9)
            document.save(pdf)
        assets = extract_figures(pdf, self.root / 'adjacent-assets', dpi=100)
        left = next(a for a in assets if a['figure_id'] == '8')
        right = next(a for a in assets if a['figure_id'] == '9')
        self.assertTrue(pymupdf.Rect(left['bbox']).contains(pymupdf.Rect(50, 80, 295, 170)))
        self.assertTrue(pymupdf.Rect(right['bbox']).contains(pymupdf.Rect(314, 80, 560, 170)))
        self.assertLess(left['bbox'][2], right['bbox'][0])
        self.assertLess(left['bbox'][2], 312)
        self.assertGreater(right['bbox'][0], 300)
        result, extractor = self.extract(pdf)
        create_job(pdf, self.root / 'job', result, regions=extractor.regions)
        job = load_job(self.root / 'job')
        coverage = validate_source_coverage(job['source'], pdf, regions=job['regions'])
        self.assertEqual(coverage['status'], 'PASS', coverage)

    def test_reference_can_continue_on_the_next_page(self):
        pdf = self.bibliography(continuation=True)
        items, extractor = self.extract(pdf)
        result, info = reconcile_numbered_references(items, extractor.regions, extractor.warnings)
        refs = [item for item in result if item['kind'] == 'reference']
        self.assertEqual(info['entries'], 4)
        third = next(ref for ref in refs if ref.get('reference_no') == 3)
        self.assertEqual({loc['page'] for loc in third['locators']}, {1, 2})
        self.assertIn('final publication details. 2003.', third['source'])
        self.assertEqual(validate_source_coverage(result, pdf, regions=extractor.regions)['status'], 'PASS')

    def test_numbering_ambiguity_retains_original_units_and_warns(self):
        for numbers in ((1, 3, 4), (1, 1, 2), (0, 1, 2)):
            pdf = self.bibliography(name=f'ambiguous-{numbers}.pdf', numbers=numbers)
            items, extractor = self.extract(pdf)
            before_items, before_regions = copy.deepcopy(items), copy.deepcopy(extractor.regions)
            result, info = reconcile_numbered_references(items, extractor.regions, extractor.warnings)
            self.assertEqual(info['entries'], 0)
            self.assertEqual(result, before_items)
            self.assertEqual(extractor.regions, before_regions)
            self.assertIn('ambiguous_reference_numbering', [warning['code'] for warning in extractor.warnings])

    def test_no_heading_means_no_bibliography_rewrite(self):
        pdf = self.root / 'body-citations.pdf'
        with pymupdf.open() as document:
            page = document.new_page()
            page.insert_text((50, 80), '[1] Discusses this boundary.\n[2] Supports another claim.', fontsize=9)
            document.save(pdf)
        items, extractor = self.extract(pdf)
        original = copy.deepcopy(items)
        result, info = reconcile_numbered_references(items, extractor.regions, extractor.warnings)
        self.assertEqual(info['entries'], 0)
        self.assertEqual(result, original)

    def test_cross_region_unit_cannot_be_silently_swallowed(self):
        pdf = self.bibliography()
        items, extractor = self.extract(pdf)
        body = next(item for item in items if item['source'].startswith('Body stays'))
        reference = next(item for item in items if item['source'].startswith('[1]'))
        reference['locators'].append(copy.deepcopy(body['locators'][0]))
        original = copy.deepcopy(items)
        result, info = reconcile_numbered_references(items, extractor.regions, extractor.warnings)
        self.assertEqual(info['entries'], 0)
        self.assertEqual(result, original)
        self.assertIn('ambiguous_reference_ownership', [warning['code'] for warning in extractor.warnings])


if __name__ == '__main__':
    unittest.main()
