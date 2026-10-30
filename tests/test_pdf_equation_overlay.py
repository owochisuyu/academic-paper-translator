"""Reviewed native-equation import invariants; fixtures do not certify reviews."""
from pathlib import Path
import copy
import json
import tempfile
import unittest

import pymupdf

from scripts.backends.pdf_equation_overlay import apply_equation_overlay, CONTRACT
from scripts.document_ir import create_job, file_hash, load_job
from scripts.extract_document import DocumentExtractor
from scripts.source_coverage import validate_source_coverage


class TestPDFEquationOverlay(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.pdf = self.root / 'native.pdf'
        with pymupdf.open() as doc:
            page = doc.new_page(width=600, height=700)
            for point, text in [((50, 50), 'Introduction'), ((50, 80), 'Body remains visible.'),
                                ((100, 160), 'M ='), ((160, 140), '1 2'), ((160, 180), '0 1'),
                                ((420, 160), '(7)'), ((50, 240), 'After the equation.')]:
                page.insert_text(point, text, fontsize=10)
            page = doc.new_page(width=600, height=700)
            page.insert_text((50, 400), 'Other page text.', fontsize=10)
            doc.save(self.pdf)
        extractor = DocumentExtractor()
        self.items, _ = extractor.extract(self.pdf)
        self.regions, self.assets, self.warnings = extractor.regions, extractor.assets, extractor.warnings
        self.ids = ['p1:b2', 'p1:b3', 'p1:b4', 'p1:b5']
        self.manifest = {'source_sha256': file_hash(self.pdf),
                         'reviewer': {'type': 'agent', 'id': 'synthetic fixture reviewer'},
                         'equations': [{'id': 'equation-reviewed-001', 'page': 1,
                            'bbox': [95, 125, 440, 188], 'region_ids': self.ids,
                            'complete': True, 'no_translatable_text': True,
                            'evidence': 'Synthetic fixture: variable, numbers and equation number only.'}]}
        self.manifest_path = self.root / 'reviewed-equations.json'

    def apply(self):
        self.manifest_path.write_text(json.dumps(self.manifest), encoding='utf-8')
        return apply_equation_overlay(self.pdf, self.manifest_path, self.items,
                                      self.regions, self.assets, self.warnings)

    def group_items(self):
        return [i for i in self.items if any(loc['region_id'] in self.ids for loc in i['locators'])]

    def use_text_fixture(self, text, number):
        self.pdf = self.root / f'label-{number}.pdf'
        with pymupdf.open() as doc:
            page = doc.new_page(width=600, height=700)
            page.insert_text((50, 150), text, fontsize=10)
            doc.save(self.pdf)
        extractor = DocumentExtractor()
        self.items, _ = extractor.extract(self.pdf)
        self.regions, self.assets, self.warnings = extractor.regions, extractor.assets, extractor.warnings
        self.ids = [r['region_id'] for r in self.regions]
        self.manifest['source_sha256'] = file_hash(self.pdf)
        self.manifest['equations'][0].update(region_ids=self.ids, bbox=[45, 130, 550, 160])

    def test_exact_native_fragments_survive_grouping_freezing_and_coverage(self):
        before = copy.deepcopy(self.group_items())
        old_region_text = {r['region_id']: r['raw_text'] for r in self.regions}
        self.warnings.append({'code': 'independent_warning', 'page': 1})
        warnings, info = self.apply()
        self.assertEqual(warnings, self.warnings)
        self.assertEqual(info['path'], str(self.manifest_path.resolve()))
        self.assertEqual(info['sha256'], file_hash(self.manifest_path))
        self.assertEqual(info['contract'], CONTRACT)
        self.assertEqual(info['validation'], 'structure_only')
        equation = next(i for i in self.items if i['kind'] == 'equation')
        self.assertEqual(equation['math_format'], 'graphic')
        self.assertEqual(equation['action'], 'preserve')
        self.assertEqual(equation['provenance'], 'native')
        self.assertEqual(equation['raw_source'], '\n'.join(i['raw_source'] for i in before))
        self.assertEqual(equation['locators'], [loc for i in before for loc in i['locators']])
        self.assertTrue(all(i['id'] not in {r['id'] for r in self.items} for i in before))
        self.assertEqual(old_region_text, {r['region_id']: r['raw_text'] for r in self.regions})
        for region in self.regions:
            if region['region_id'] in self.ids:
                self.assertEqual(region['unit_ids'], [equation['id']])
                self.assertEqual(region['disposition'], 'preserve')
        self.assertEqual(len(self.assets), 1)
        asset = self.assets[0]
        self.assertEqual(asset['sha256'], file_hash(asset['path']))
        self.assertEqual(asset['bbox'], self.manifest['equations'][0]['bbox'])
        with pymupdf.open(self.pdf) as doc:
            actual = pymupdf.Pixmap(asset['path'])
            expected = doc[0].get_pixmap(clip=pymupdf.Rect(asset['bbox']), dpi=300, alpha=False)
            self.assertEqual((actual.width, actual.height, actual.samples),
                             (expected.width, expected.height, expected.samples))
        coverage = validate_source_coverage(self.items, self.pdf, regions=self.regions)
        self.assertEqual(coverage['status'], 'PASS', coverage)
        self.assertEqual(coverage['char_coverage_ratio'], 1.0)
        create_job(self.pdf, self.root / 'new-job', self.items,
                   regions=self.regions, assets=self.assets)
        frozen = load_job(self.root / 'new-job')
        coverage = validate_source_coverage(frozen['source'], self.pdf, regions=frozen['regions'])
        self.assertEqual(coverage['status'], 'PASS', coverage)

    def test_hash_reviewer_and_complete_review_are_required(self):
        original = copy.deepcopy(self.manifest)
        cases = [('source_sha256', 'wrong'), ('reviewer', {'type': 'agent', 'id': ''})]
        for field, value in cases:
            with self.subTest(field=field):
                self.manifest = copy.deepcopy(original)
                self.manifest[field] = value
                with self.assertRaises(ValueError):
                    self.apply()
        for field, value in (('complete', False), ('complete', 1), ('evidence', ' ')):
            with self.subTest(field=field, value=value):
                self.manifest = copy.deepcopy(original)
                self.manifest['equations'][0][field] = value
                with self.assertRaisesRegex(ValueError, 'complete=true'):
                    self.apply()

    def test_text_labels_stay_unresolved_in_v1(self):
        for field, value in (('no_translatable_text', False), ('no_translatable_text', None),
                             ('labels', [{'source': 'Innovation'}])):
            with self.subTest(field=field):
                row = self.manifest['equations'][0]
                original = copy.deepcopy(row)
                row[field] = value
                with self.assertRaisesRegex(ValueError, 'NEEDS_REVIEW'):
                    self.apply()
                self.manifest['equations'][0] = original

    def test_body_prose_cannot_be_waived_as_a_non_translatable_equation(self):
        self.manifest['equations'][0].update(region_ids=['p1:b1'], bbox=[45, 65, 150, 87])
        before = copy.deepcopy((self.items, self.regions, self.assets, self.warnings))
        with self.assertRaisesRegex(ValueError, 'text-aware workflow'):
            self.apply()
        self.assertEqual((self.items, self.regions, self.assets, self.warnings), before)
        self.assertFalse((self.root / 'equation-overlay-assets').exists())

    def test_conditional_words_labels_and_complex_identifiers_require_text_workflow(self):
        for number, text in enumerate(('x = 0 if y > 0', 'x = 1 otherwise',
                                       'K = 1 Innovation', 'x = text-label',
                                       'y = kalmanGain x', 'x = state_update')):
            with self.subTest(text=text):
                self.use_text_fixture(text, number)
                with self.assertRaisesRegex(ValueError, 'NEEDS_REVIEW.*text-aware workflow'):
                    self.apply()

    def test_standard_math_functions_and_single_letter_variables_remain_supported(self):
        for number, text in enumerate(('y = sin x + cos x', 'z = tan x',
                                       'lim x = log y + exp z', 'min x + max y',
                                       'A = 2 x + B_1', 'R = det M', 'ln x')):
            with self.subTest(text=text):
                self.use_text_fixture(text, number)
                self.apply()
                self.assertEqual(len(self.items), 1)
                self.assertEqual(self.items[0]['kind'], 'equation')
                coverage = validate_source_coverage(self.items, self.pdf, regions=self.regions)
                self.assertEqual(coverage['status'], 'PASS', coverage)

    def test_wrong_geometry_page_and_coordinate_system_rejected(self):
        original = copy.deepcopy(self.manifest['equations'][0])
        for field, value in (('bbox', [105, 125, 440, 188]), ('bbox', [-1, 125, 440, 188]),
                             ('bbox', [95, 125, float('nan'), 188]), ('page', True),
                             ('page', 3), ('coordinate_system', 'pixels')):
            with self.subTest(field=field, value=value):
                self.manifest['equations'][0] = {**original, field: value}
                with self.assertRaises(ValueError):
                    self.apply()

    def test_cross_page_selection_rejected(self):
        self.manifest['equations'][0]['region_ids'] = [*self.ids, 'p2:b0']
        with self.assertRaisesRegex(ValueError, 'one page'):
            self.apply()

    def test_region_text_and_geometry_are_checked_against_pdf_bytes(self):
        region = next(r for r in self.regions if r['region_id'] == self.ids[0])
        original = copy.deepcopy(region)
        region['raw_text'] = 'X ='
        with self.assertRaisesRegex(ValueError, 'native source PDF'):
            self.apply()
        region.clear()
        region.update(original)
        region['locator']['bbox'][0] -= 1
        with self.assertRaisesRegex(ValueError, 'native source PDF'):
            self.apply()

    def test_unselected_native_content_inside_crop_rejected_even_if_ledger_omits_it(self):
        self.manifest['equations'][0]['region_ids'] = self.ids[:-1]
        self.regions[:] = [r for r in self.regions if r['region_id'] != self.ids[-1]]
        with self.assertRaisesRegex(ValueError, 'unselected native content'):
            self.apply()

    def test_non_native_and_semantically_distinct_units_cannot_be_reclassified(self):
        target = self.group_items()[0]
        for kind in ('heading', 'title', 'table_cell', 'table', 'figure', 'figure_label',
                     'caption', 'reference', 'footnote', 'code'):
            with self.subTest(kind=kind):
                target['kind'] = kind
                with self.assertRaisesRegex(ValueError, 'no headings'):
                    self.apply()
        target['kind'] = 'sentence'
        target['provenance'] = 'manual'
        with self.assertRaisesRegex(ValueError, 'unfrozen native'):
            self.apply()

    def test_table_role_and_frozen_source_are_not_current_native_regions(self):
        region = next(r for r in self.regions if r['region_id'] == self.ids[0])
        region['role'] = 'table_text'
        with self.assertRaisesRegex(ValueError, 'unfrozen native'):
            self.apply()
        region['role'] = 'text'
        region['doc_id'] = file_hash(self.pdf)
        with self.assertRaisesRegex(ValueError, 'unfrozen native'):
            self.apply()

    def test_partial_native_character_coverage_cannot_be_waived(self):
        target = next(i for i in self.group_items() if i['source'] == 'M =')
        target['source'] = target['raw_source'] = 'M'
        target['locators'][0]['end'] = 1
        with self.assertRaisesRegex(ValueError, 'partial native character coverage'):
            self.apply()

    def test_every_selected_unit_must_be_consumed(self):
        target = self.group_items()[0]
        region = next(r for r in self.regions if r['region_id'] == target['locators'][0]['region_id'])
        region['unit_ids'] = []
        with self.assertRaisesRegex(ValueError, 'all of its real source units'):
            self.apply()

    def test_unit_cannot_cross_or_be_owned_by_an_unselected_region(self):
        target = self.group_items()[0]
        outside = next(r for r in self.regions if r['region_id'] == 'p1:b1')
        target['locators'].append(copy.deepcopy(outside['locator']))
        with self.assertRaisesRegex(ValueError, 'cross unselected'):
            self.apply()
        target['locators'].pop()
        outside['unit_ids'].append(target['id'])
        with self.assertRaisesRegex(ValueError, 'owned by an unselected'):
            self.apply()

    def test_duplicate_group_and_duplicate_character_claims_rejected_atomically(self):
        row = self.manifest['equations'][0]
        self.manifest['equations'].append({**row, 'id': 'equation-reviewed-002'})
        before = copy.deepcopy((self.items, self.regions, self.assets, self.warnings))
        with self.assertRaisesRegex(ValueError, 'overlap'):
            self.apply()
        self.assertEqual((self.items, self.regions, self.assets, self.warnings), before)
        self.assertFalse((self.root / 'equation-overlay-assets').exists())
        self.manifest['equations'].pop()
        duplicate = copy.deepcopy(self.group_items()[0])
        duplicate['id'] += '-duplicate'
        self.items.append(duplicate)
        region = next(r for r in self.regions if r['region_id'] == duplicate['locators'][0]['region_id'])
        region['unit_ids'].append(duplicate['id'])
        with self.assertRaisesRegex(ValueError, 'duplicate native source coverage'):
            self.apply()

    def test_readable_source_cannot_omit_retained_native_text(self):
        self.group_items()[0]['source'] = 'replacement'
        with self.assertRaisesRegex(ValueError, 'omits or changes'):
            self.apply()


if __name__ == '__main__':
    unittest.main()
