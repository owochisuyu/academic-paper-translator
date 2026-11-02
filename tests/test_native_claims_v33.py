"""Native character occurrences retain their identity when mathematical boxes overlap."""
import copy
from pathlib import Path
import tempfile
import unittest

import pymupdf

from scripts.source_coverage import validate_source_coverage


class NativeClaimsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'overlapping.pdf'
        with pymupdf.open() as doc:
            page = doc.new_page()
            page.insert_text((50, 60), 'A1B', fontsize=18)
            page.insert_text((55, 44), '1', fontsize=7)
            doc.save(self.path)
        self.items = []
        with pymupdf.open(self.path) as doc:
            for block in doc[0].get_text('dict')['blocks']:
                raw = '\n'.join(''.join(span['text'] for span in line['spans']) for line in block['lines'])
                self.items.append({'id': 'unit-' + str(block['number']), 'page': 1,
                    'kind': 'sentence', 'source': raw, 'raw_source': raw,
                    'locators': [{'page': 1, 'block_id': block['number'], 'bbox': list(block['bbox']),
                                  'start': 0, 'end': len(raw)}]})
        self.assertEqual({i['source'] for i in self.items}, {'1', 'A1B'})
        self.assertTrue(pymupdf.Rect(self.items[0]['locators'][0]['bbox']).intersects(
            pymupdf.Rect(self.items[1]['locators'][0]['bbox'])))
        self.items.sort(key=lambda i: len(i['source']))

    def test_small_overlapping_glyph_does_not_consume_neighbours_occurrence(self):
        result = validate_source_coverage(self.items, self.path)
        self.assertEqual(result['status'], 'PASS', result)
        self.assertEqual(result['covered_characters'], result['source_characters'])
        self.assertEqual(result['unmatched_source_items'], [])

    def test_false_explicit_interval_cannot_fall_back_to_another_occurrence(self):
        for change in ({'block_id': 99}, {'end': 2}, {'start': 2, 'end': 3}, {'block_id': True}):
            with self.subTest(change=change):
                items = copy.deepcopy(self.items)
                items[0]['locators'][0].update(change)
                result = validate_source_coverage(items, self.path)
                self.assertEqual(result['status'], 'FAIL')
                self.assertTrue(result['unmatched_source_items'])

    def test_duplicate_explicit_claim_cannot_consume_the_same_digit_elsewhere(self):
        duplicate = copy.deepcopy(self.items[0])
        duplicate['id'] = 'duplicate'
        result = validate_source_coverage([self.items[0], duplicate, self.items[1]], self.path)
        self.assertEqual(result['status'], 'FAIL')
        self.assertEqual([i['id'] for i in result['unmatched_source_items']], ['duplicate'])

    def test_geometry_only_legacy_records_remain_supported(self):
        items = copy.deepcopy(self.items)
        # Long block first avoids the very ambiguity that native intervals solve.
        items.sort(key=lambda i: len(i['source']), reverse=True)
        for item in items:
            item['locators'] = [{'page': 1, 'bbox': item['locators'][0]['bbox']}]
        self.assertEqual(validate_source_coverage(items, self.path)['status'], 'PASS')


if __name__ == '__main__':
    unittest.main()
