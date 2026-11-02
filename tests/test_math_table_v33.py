"""Native table geometry and exact source-fragment regression checks."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

import pymupdf

from scripts.backends.common import normalize_with_map
from scripts.backends.pdf_structure import native_lines, refine_table
from scripts.document_ir import create_job, load_job
from scripts.source_coverage import validate_source_coverage


def fake_page(lines, drawings=None):
    blocks = [{'type': 0, 'number': 7, 'lines': [
        {'bbox': bbox, 'spans': [{'text': text, 'bbox': bbox, 'origin': [bbox[0], baseline], 'font': font}]}
        for text, bbox, baseline, font in lines]}]
    page = SimpleNamespace(get_text=lambda mode: {'blocks': blocks}, number=0, rotation=0,
                           rect=pymupdf.Rect(0, 0, 600, 800))
    if drawings is not None:
        page.get_drawings = lambda: drawings
    return page


def table_fixture(rows, grid):
    boxes = [pymupdf.Rect(box) for row in rows for box in row if box is not None]
    union = pymupdf.Rect(boxes[0])
    for box in boxes[1:]:
        union |= box
    return SimpleNamespace(bbox=list(union), rows=[SimpleNamespace(cells=row) for row in rows],
                           extract=lambda: grid)


class NativeMathTableTests(unittest.TestCase):
    def test_native_line_intervals_retain_empty_lines_and_repeated_words(self):
        page = fake_page([
            ('same', [50, 50, 80, 60], 58, 'Serif'),
            ('', [50, 60, 80, 70], 68, 'Serif'),
            ('same', [50, 70, 80, 80], 78, 'Serif')])
        lines = native_lines(page)
        self.assertEqual([line['locator']['start'] for line in lines], [0, 6])
        raw = 'same\n\nsame'
        for line in lines:
            loc = line['locator']
            self.assertEqual(raw[loc['start']:loc['end']], line['text'])
            self.assertEqual(loc['block_id'], 7)
            self.assertEqual(loc['region_id'], 'p1:b7')

    def test_tall_math_delimiter_is_assigned_by_baseline_not_font_box_center(self):
        page = fake_page([
            ('R', [50, 90, 60, 98], 97, 'Math'),
            ('[', [50, 94, 54, 103], 102, 'CMEX8'),
            ('1 2', [55, 103, 75, 110], 109, 'Math'),
            ('0 1', [55, 112, 75, 119], 118, 'Math'),
            (']', [77, 94, 81, 103], 102, 'CMEX8')])
        table = table_fixture([[[40, 80, 100, 100]], [[40, 100, 100, 120]]], [['R'], ['[1 2;0 1]']])
        cells, _, warnings = refine_table(table, page, 't')
        self.assertEqual([w['code'] for w in warnings], ['ambiguous_multiline_table_row'])
        self.assertEqual(cells[0]['source'], 'R')
        self.assertIn('[', cells[1]['source'])
        self.assertIn(']', cells[1]['source'])
        parts = cells[1]['source_parts']
        self.assertEqual(cells[1]['native_locators'], [loc for part in parts for loc in part['locators']])
        self.assertIs(cells[1]['native_locators'][0], parts[0]['locators'][0])

    def test_contiguous_header_recovers_span_without_an_internal_rule(self):
        drawings = [{'items': [('l', pymupdf.Point(100, 70), pymupdf.Point(100, 100))]}]
        page = fake_page([('Lie group M', [60, 50, 145, 60], 58, 'Serif'),
                          ('circle', [50, 80, 90, 90], 88, 'Serif'),
                          ('S', [115, 80, 125, 90], 88, 'Math')], drawings=drawings)
        table = table_fixture([[[40, 40, 100, 70], [100, 40, 160, 70]],
                               [[40, 70, 100, 100], [100, 70, 160, 100]]],
                              [['Lie group', 'M'], ['circle', 'S']])
        cells, _, warnings = refine_table(table, page, 't')
        self.assertEqual(warnings, [])
        self.assertEqual(len(cells), 3)
        self.assertEqual((cells[0]['row'], cells[0]['col'], cells[0]['colspan']), (0, 0, 2))
        self.assertEqual(cells[0]['table_cols'], 2)
        self.assertEqual(cells[0]['source'], 'Lie group M')

    def test_unruled_table_does_not_infer_a_span_from_adjacent_labels(self):
        page = fake_page([('Left right', [60, 50, 145, 60], 58, 'Serif')], drawings=[])
        table = table_fixture([[[40, 40, 100, 70], [100, 40, 160, 70]]], [['Left', 'right']])
        cells, _, warnings = refine_table(table, page, 't')
        self.assertEqual(len(cells), 2)
        self.assertEqual([cell['colspan'] for cell in cells], [1, 1])
        self.assertTrue(any(w['code'] == 'table_text_crosses_cell_boundary' for w in warnings))

    def test_drawn_separator_blocks_merging_and_false_whole_line_assignment(self):
        drawings = [{'items': [('l', pymupdf.Point(100, 40), pymupdf.Point(100, 70))]}]
        page = fake_page([('Left right', [60, 50, 145, 60], 58, 'Serif')], drawings=drawings)
        table = table_fixture([[[40, 40, 100, 70], [100, 40, 160, 70]]], [['Left', 'right']])
        cells, _, warnings = refine_table(table, page, 't')
        self.assertEqual(len(cells), 2)
        self.assertEqual([cell['colspan'] for cell in cells], [1, 1])
        self.assertEqual([cell['source'] for cell in cells], ['Left', 'right'])
        self.assertTrue(any(w['code'] == 'table_text_crosses_cell_boundary' for w in warnings))
        self.assertTrue(all('source_parts' not in cell for cell in cells))

    def test_line_at_shared_boundary_has_only_one_owner(self):
        page = fake_page([('x', [50, 91, 60, 101], 100, 'Math')])
        table = table_fixture([[[40, 80, 100, 100]], [[40, 100, 100, 120]]], [[''], ['x']])
        cells, _, warnings = refine_table(table, page, 't')
        self.assertEqual(warnings, [])
        self.assertEqual([cell['source'] for cell in cells], ['', 'x'])
        self.assertEqual(sum(len(cell.get('source_parts', [])) for cell in cells), 1)

    def test_reordered_table_lines_keep_exact_native_parts_after_freezing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source.pdf'
            with pymupdf.open() as document:
                page = document.new_page(width=300, height=300)
                # PDF content order deliberately differs from row order.
                page.insert_text((50, 140), 'Bottom value', fontsize=10)
                page.insert_text((50, 100), 'Top value', fontsize=10)
                document.save(source)
            with pymupdf.open(source) as document:
                page = document[0]
                table = table_fixture([[[40, 80, 180, 160]]], [['Top value Bottom value']])
                cells, _, _ = refine_table(table, page, 't')
            self.assertEqual(len(cells), 1)
            cell = cells[0]
            self.assertEqual(cell['source'], 'Top value\nBottom value')
            self.assertEqual([p['locators'][0]['block_id'] for p in cell['source_parts']], [1, 0])
            item = {'id': 'cell', 'kind': 'table_cell', 'table_id': 't', 'row': 0, 'col': 0,
                    'action': 'translate', 'source': normalize_with_map(cell['source'])[0],
                    'raw_source': cell['source'], 'source_parts': cell['source_parts'],
                    'locators': cell['native_locators'], 'spans': cell['native_spans']}
            coverage = validate_source_coverage([item], source, regions=[])
            self.assertEqual(coverage['status'], 'PASS', coverage)
            create_job(source, root / 'job', [item], regions=[])
            job = load_job(root / 'job')
            frozen_item = job['source'][0]
            self.assertEqual(frozen_item['locators'], [loc for part in frozen_item['source_parts'] for loc in part['locators']])
            coverage = validate_source_coverage(job['source'], source, regions=[])
            self.assertEqual(coverage['status'], 'PASS', coverage)


if __name__ == '__main__':
    unittest.main()
