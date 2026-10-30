"""Behavioral regression tests for provenance, optional parsers and source projects."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pymupdf

from scripts.extract_document import DocumentExtractor, extract_document
from scripts.extract_figures import extract_figures
from scripts.document_ir import load_job, file_hash
from scripts.backends.latex_backend import extract_project, export_project, restore_translation, compile_project
from scripts.backends.mineru_backend import extract_mineru
from scripts.backends.pdf_structure import refine_table, merge_hanging_references, merge_page_continuations, normalize_line_wraps, repeated_running_matter
from scripts.backends.pdf_order import order_pdf_units


class ExtractionV3(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def pdf(self, name='paper.pdf', pages=1):
        path = self.root / name
        with pymupdf.open() as doc:
            for page_no in range(pages):
                page = doc.new_page()
                page.insert_text((50, 60), 'The estimator works only after calibration. It preserves 100000 samples.', fontsize=10)
                page.insert_text((50, 90), 'Repeat this condition.', fontsize=10)
                page.insert_text((50, 120), 'Repeat this condition.', fontsize=10)
            doc.save(path)
        return path

    def test_pdf_occurrence_offsets_and_frozen_job(self):
        path = self.pdf()
        extractor = DocumentExtractor()
        items, _ = extractor.extract(path)
        repeat = [i for i in items if i['source'] == 'Repeat this condition.']
        self.assertEqual(len(repeat), 2)
        self.assertNotEqual(repeat[0]['locators'][0]['region_id'], repeat[1]['locators'][0]['region_id'])
        regions = {r['region_id']: r for r in extractor.regions}
        for item in items:
            loc = item['locators'][0]
            self.assertEqual(item['raw_source'], regions[loc['region_id']]['raw_text'][loc['start']:loc['end']])
        extract_document(path, job_dir=self.root / 'job')
        job = load_job(self.root / 'job')
        self.assertEqual(job['document']['doc_id'], file_hash(path))
        self.assertTrue(all(i['id'].startswith(file_hash(path)[:16]) for i in job['source']))

    def test_native_header_does_not_hide_scan(self):
        path = self.root / 'scan.pdf'
        with pymupdf.open() as scan, pymupdf.open() as text:
            p = text.new_page()
            p.insert_text((50, 70), 'Critical calibration requirements.', fontsize=15)
            page = scan.new_page()
            page.insert_text((50, 30), 'Appendix: Calibration requirements', fontsize=10)
            page.insert_image(pymupdf.Rect(20, 80, 580, 780), stream=p.get_pixmap().tobytes('png'))
            scan.save(path)
        extractor = DocumentExtractor()
        _, stats = extractor.extract(path)
        self.assertEqual(stats['extraction_status'], 'NEEDS_REVIEW')
        self.assertTrue(any(r['role'] == 'image' and r['disposition'] == 'needs_review' for r in extractor.regions))

    def test_explicit_figure_manifest_no_directory_discovery(self):
        path = self.root / 'figures.pdf'
        with pymupdf.open() as doc:
            page = doc.new_page()
            page.draw_rect((60, 100, 500, 300), fill=(.1, .2, .8))
            page.insert_text((60, 330), 'Fig. 1. Calibration.', fontsize=10)
            doc.save(path)
        assets = extract_figures(path, self.root / 'figures')
        plain, _ = DocumentExtractor().extract(path)
        self.assertNotIn('asset_id', next(i for i in plain if i['kind'] == 'figure'))
        explicit, _ = DocumentExtractor(assets_manifest=self.root / 'figures' / 'figures_manifest.json').extract(path)
        figure = next(i for i in explicit if i['kind'] == 'figure')
        self.assertEqual(figure['asset_id'], assets[0]['asset_id'])
        self.assertEqual(assets[0]['doc_id'], file_hash(path))
        self.assertEqual(assets[0]['sha256'], file_hash(assets[0]['path']))

    def test_cross_document_assets_rejected(self):
        path = self.pdf()
        manifest = self.root / 'assets.json'
        manifest.write_text(json.dumps([{'asset_id': 'x', 'doc_id': 'wrong', 'path': str(path), 'sha256': file_hash(path)}]))
        with self.assertRaisesRegex(ValueError, 'different'):
            DocumentExtractor(assets_manifest=manifest).extract(path)

    def test_markdown_table_empty_cell_and_escaped_pipe(self):
        path = self.root / 'paper.md'
        path.write_text('# Results\n\n| Method | Note |\n|---|---|\n| GNSS | x \\| y |\n| INS |  |\n\n```python\nprint(1)\nprint(2)\n```\n', encoding='utf-8')
        extractor = DocumentExtractor()
        items, _ = extractor.extract(path)
        cells = [i for i in items if i['kind'] == 'table_cell']
        self.assertEqual(len(cells), 6)
        escaped = next(i for i in cells if i['source'] == 'x | y')
        self.assertEqual(escaped['raw_source'], r'x \| y')
        self.assertTrue(any(i['action'] == 'preserve' and i.get('empty_cell') for i in cells))
        self.assertEqual(next(i['source'] for i in items if i['kind'] == 'code'), 'print(1)\nprint(2)\n')
        raw = path.read_text(encoding='utf-8')
        covered = set()
        for region in extractor.regions:
            loc = region['locator']
            self.assertEqual(region['raw_text'], raw[loc['start']:loc['end']])
            covered.update(range(loc['start'], loc['end']))
        self.assertEqual(covered, set(range(len(raw))))

    def test_markdown_heading_levels_survive_extraction(self):
        path = self.root / 'levels.md'
        path.write_text('# Main\n\n## Method\n\n### Sensor model\n\nBody text.\n', encoding='utf-8')
        items, _ = DocumentExtractor().extract(path)
        self.assertEqual([(i['source'], i['heading_level']) for i in items if i['kind'] == 'heading'],
                         [('Main', 1), ('Method', 2), ('Sensor model', 3)])

    def test_latex_section_following_body_and_nested_format(self):
        path = self.root / 'main.tex'
        path.write_text(r'\documentclass{article}\begin{document}\section{Method}The \textbf{fully \emph{calibrated}} estimator works. It preserves $x_1$ \cite{ref1}.\begin{equation}\frac{a}{b}=c\end{equation}\end{document}', encoding='utf-8')
        parsed = extract_project(path)
        sentences = [i for i in parsed['items'] if i['kind'] == 'sentence']
        self.assertEqual(len(sentences), 2)
        self.assertIn('calibrated', sentences[0]['source'])
        for sentence in sentences:
            self.assertEqual(restore_translation(sentence, sentence['source']), sentence['raw_source'])
        equation = next(i for i in parsed['items'] if i['kind'] == 'equation')
        self.assertEqual(equation['source'], r'\begin{equation}\frac{a}{b}=c\end{equation}')
        self.assertEqual(equation['math_format'], 'latex')

    def test_latex_project_includes_and_export(self):
        (self.root / 'part.tex').write_text('The sensors are calibrated.', encoding='utf-8')
        main = self.root / 'main.tex'
        main.write_text(r'\documentclass{article}\begin{document}\section{Introduction}\input{part}\end{document}', encoding='utf-8')
        extract_document(main, job_dir=self.root / 'job')
        job = load_job(self.root / 'job')
        self.assertEqual(len(job['document']['input_files']), 2)
        merged = []
        for item in job['source']:
            merged.append({**item, 'translation': '引言' if item['kind'] == 'heading' else '传感器均已完成标定。'})
        output = export_project({**job, 'merged': merged}, self.root / 'translated')
        self.assertIn('传感器均已完成标定', (output / 'part.tex').read_text(encoding='utf-8'))
        self.assertIn(r'\section{引言}', (output / 'main.tex').read_text(encoding='utf-8'))
        self.assertIn('The sensors', (self.root / 'part.tex').read_text())

    def test_latex_unknown_macro_and_cyclic_include_do_not_pass(self):
        path = self.root / 'unknown.tex'
        path.write_text(r'\section{Method}\mystery{untranslated constraints}', encoding='utf-8')
        self.assertTrue(any(w['code'] == 'unsupported_macro' for w in extract_project(path)['warnings']))
        path.write_text(r'\input{unknown}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Cyclic'):
            extract_project(path)

    def test_latex_ambiguous_main_and_missing_engine(self):
        for name in ('one.tex', 'two.tex'):
            (self.root / name).write_text(r'\documentclass{article}\begin{document}Text.\end{document}')
        with self.assertRaisesRegex(ValueError, 'Ambiguous'):
            extract_project(self.root)
        with patch('scripts.backends.latex_backend.shutil.which', return_value=None):
            result = compile_project(self.root, 'one.tex')
        self.assertEqual(result['exit_code'], 4)
        self.assertEqual(result['status'], 'NOT_RUN')

    def mineru_envelope(self, path, pages=2):
        return {'source_sha256': file_hash(path), 'coordinate_system': 'unrotated_top_left_pt',
                'result': {'schema': 'docvortex.middle', 'schema_version': '2.0', 'is_full_document': True,
                           'metadata': {'producer': {'name': 'mineru', 'version': '4.0.0'}},
                           'pages': [{'page_idx': i, 'blocks': [{'type': 'text', 'index': 0, 'bbox': [10, 20, 500, 60], 'confidence': .99,
                                                              'content': [{'type': 'text', 'content': f'Page {i + 1} requires calibration.'}]}]} for i in range(pages)]}}

    def test_mineru_saved_result_contract(self):
        path = self.pdf(pages=2)
        result = self.root / 'mineru.json'
        result.write_text(json.dumps(self.mineru_envelope(path)), encoding='utf-8')
        parsed = extract_mineru(path, result)
        self.assertEqual(parsed['pages'], 2)
        self.assertEqual([i['page'] for i in parsed['items']], [1, 2])
        self.assertTrue(all(i['provenance'] == 'ocr' for i in parsed['items']))

    def test_mineru_rejects_partial_duplicate_and_wrong_source(self):
        path = self.pdf(pages=2)
        result = self.root / 'mineru.json'
        for mutate in (lambda v: v['result']['pages'].pop(),
                       lambda v: v['result']['pages'][1].update(page_idx=0),
                       lambda v: v.update(source_sha256='other'),
                       lambda v: v['result'].update(is_full_document=False)):
            value = self.mineru_envelope(path)
            mutate(value)
            result.write_text(json.dumps(value), encoding='utf-8')
            with self.assertRaises(ValueError):
                extract_mineru(path, result)

    def test_body_scope_exclusions_are_recorded(self):
        path = self.root / 'scope.md'
        path.write_text('# Method\nBody content.\n\n# Appendix\nA necessary detail.\n\n# References\n[1] Author, 2020.\n', encoding='utf-8')
        extractor = DocumentExtractor(scope='body')
        items, stats = extractor.extract(path)
        self.assertTrue(all('Appendix' not in i['section'] for i in items))
        self.assertGreater(stats['excluded_items'], 0)
        self.assertTrue(any(r['disposition'] == 'excluded' for r in extractor.regions))

    def test_native_table_recovers_identifier_rows_and_exact_spelling(self):
        lines = [
            ('acc_bias', [50, 112, 135, 125], 122), ('Accelerometer bias offset', [200, 112, 425, 125], 122),
            ('ang_i_to_b', [50, 132, 135, 145], 142), ('IMU orientation', [200, 132, 425, 145], 142),
        ]
        page = SimpleNamespace(get_text=lambda mode: {'blocks': [{'type': 0, 'lines': [
            {'bbox': box, 'spans': [{'text': text, 'origin': [box[0], baseline]}]} for text, box, baseline in lines]}]})
        table = SimpleNamespace(bbox=[40, 100, 450, 155], rows=[SimpleNamespace(cells=[[40, 100, 180, 155], [180, 100, 450, 155]])],
                                extract=lambda: [['acc bias ang i to b _ _ _', 'Accelerometer bias ofset IMU orientation f']])
        cells, _, warnings = refine_table(table, page, 'p1-tbl1')
        self.assertEqual(warnings, [])
        self.assertEqual([cell['source'] for cell in cells], ['acc_bias', 'Accelerometer bias offset', 'ang_i_to_b', 'IMU orientation'])
        self.assertEqual([(cell['row'], cell['col']) for cell in cells], [(0, 0), (0, 1), (1, 0), (1, 1)])
        self.assertTrue(all(cell['table_rows'] == 2 and cell['table_cols'] == 2 for cell in cells))

    def test_native_table_does_not_guess_ambiguous_multiline_rows(self):
        lines = [('A long label', [50, 112, 135, 125], 122), ('First description', [200, 112, 425, 125], 122),
                 ('continued label', [50, 132, 135, 145], 142), ('continued description', [200, 132, 425, 145], 142)]
        page = SimpleNamespace(get_text=lambda mode: {'blocks': [{'type': 0, 'lines': [
            {'bbox': box, 'spans': [{'text': text, 'origin': [box[0], baseline]}]} for text, box, baseline in lines]}]})
        table = SimpleNamespace(bbox=[40, 100, 450, 155], rows=[SimpleNamespace(cells=[[40, 100, 180, 155], [180, 100, 450, 155]])],
                                extract=lambda: [['A long label continued label', 'First description continued description']])
        cells, _, warnings = refine_table(table, page, 'p1-tbl1')
        self.assertEqual(len(cells), 2)
        self.assertEqual(warnings[0]['code'], 'ambiguous_multiline_table_row')

    def test_pdf_table_order_follows_introducing_prose(self):
        def unit(key, kind, box, **extra):
            return {'id': key, 'kind': kind, 'page': 1, 'source': key,
                    'locators': [{'bbox': box, 'width': 600, 'page': 1}], **extra}
        items = [unit('c10', 'table_cell', [50, 230, 200, 250], table_id='t', row=1, col=0),
                 unit('c00', 'table_cell', [50, 210, 200, 230], table_id='t', row=0, col=0),
                 unit('heading', 'heading', [50, 90, 500, 110]),
                 unit('prose', 'sentence', [50, 150, 500, 190])]
        self.assertEqual([i['id'] for i in order_pdf_units(items, [])], ['heading', 'prose', 'c00', 'c10'])

    def test_running_footer_identified_by_geometry_after_images(self):
        path = self.root / 'footers.pdf'
        with pymupdf.open() as doc, pymupdf.open() as logo:
            logo_page = logo.new_page(width=20, height=20)
            logo_page.draw_rect([0, 0, 20, 20], fill=(.1, .2, .3))
            image = logo_page.get_pixmap().tobytes('png')
            for number in (1, 2):
                page = doc.new_page()
                page.insert_image([20, 20, 40, 40], stream=image)
                page.insert_text((60, 90), 'References', fontsize=14)
                page.insert_text((60, 120), f'Author {number}. Distinct reference title.', fontsize=10)
                page.insert_text((60, 805), 'Publisher footer\n' + str(number), fontsize=8)
            doc.save(path)
        extractor = DocumentExtractor()
        items, stats = extractor.extract(path)
        refs = [i['source'] for i in items if i['kind'] == 'reference']
        self.assertEqual(refs, ['Author 1. Distinct reference title.', 'Author 2. Distinct reference title.'])
        self.assertEqual(stats['running_matter_regions'], 2)
        self.assertEqual(stats['excluded_regions'], 2)

    def test_merged_reference_and_page_continuation_retain_all_parts(self):
        def unit(key, source, page, box, kind='reference'):
            return {'id': key, 'kind': kind, 'section': 'Method', 'page': page, 'source': source, 'raw_source': source,
                    'locators': [{'page': page, 'bbox': box, 'region_id': key}], 'spans': []}
        first = unit('r1', 'Author. A title for vision-', 1, [50, 100, 500, 115])
        continuation = unit('r2', 'aided navigation.', 1, [70, 120, 500, 135])
        regions = [{'region_id': 'r1', 'unit_ids': ['r1']}, {'region_id': 'r2', 'unit_ids': ['r2']}]
        merged = merge_hanging_references([first, continuation], regions)
        self.assertEqual(len(merged), 1)
        self.assertEqual(len(merged[0]['source_parts']), 2)
        self.assertEqual(regions[1]['unit_ids'], ['r1'])
        previous = unit('s1', 'The main limitation is', 1, [50, 700, 500, 720], kind='sentence')
        following = unit('s2', 'the sensor count.', 2, [50, 100, 500, 120], kind='sentence')
        joined = merge_page_continuations([previous, following], [])
        self.assertEqual(joined[0]['source'], 'The main limitation is the sensor count.')
        self.assertEqual([part['locators'][0]['page'] for part in joined[0]['source_parts']], [1, 2])

    def test_single_page_footer_number_does_not_become_a_reference(self):
        path = self.root / 'single-page.pdf'
        with pymupdf.open() as doc:
            page = doc.new_page(width=600, height=800)
            page.insert_text((60, 100), 'References', fontsize=14)
            page.insert_text((60, 140), '[1] Author. A source title. 2026.', fontsize=10)
            page.insert_text((297, 760), '1', fontsize=10)
            doc.save(path)
        extractor = DocumentExtractor()
        with patch.object(pymupdf.Page, 'find_tables', return_value=SimpleNamespace(tables=[])):
            items, stats = extractor.extract(path)
        self.assertEqual([i['source'] for i in items if i['kind'] == 'reference'],
                         ['[1] Author. A source title. 2026.'])
        self.assertEqual(stats['excluded_regions'], 1)
        footer = next(r for r in extractor.regions if r['raw_text'] == '1')
        self.assertEqual(footer['evidence']['role'], 'page_number')
        self.assertEqual(footer['disposition'], 'excluded')

    def test_single_numeric_body_or_edge_content_is_not_a_page_number(self):
        with pymupdf.open() as doc:
            page = doc.new_page(width=600, height=800)
            page.insert_text((297, 400), '1', fontsize=10)
            page.insert_text((60, 750), '1', fontsize=10)
            page.insert_text((297, 780), '42', fontsize=10)
            self.assertEqual(repeated_running_matter(doc), {})

    def test_native_wrap_normalization_preserves_evidence_and_lexical_hyphens(self):
        raw = 'Sensor Data Fusion and Multisensor Fu-\nsion. vision-\naided. https://doi.org/10.1109/\nICRA.2016.7487628'
        from scripts.backends.common import normalize_with_map
        source, _ = normalize_with_map(raw)
        item = {'kind': 'reference', 'source': source, 'raw_source': raw}
        normalize_line_wraps([item], [{'raw_text': raw}])
        self.assertEqual(item['source'], 'Sensor Data Fusion and Multisensor Fusion. vision-aided. https://doi.org/10.1109/ICRA.2016.7487628')
        self.assertEqual(item['raw_source'], raw)
        self.assertEqual(len(item['normalizations']), 3)
        for edit in item['normalizations']:
            self.assertEqual(raw[edit['start']:edit['end']], edit['original'])

    def test_manual_overlay_requires_real_bound_units_and_completeness(self):
        path = self.root / 'labels.pdf'
        with pymupdf.open() as doc, pymupdf.open() as label:
            lp = label.new_page(width=200, height=80)
            lp.insert_text((10, 40), 'Position error [m]', fontsize=12)
            page = doc.new_page()
            page.insert_text((30, 30), 'Calibration results', fontsize=10)
            page.insert_image([50, 100, 450, 260], stream=lp.get_pixmap().tobytes('png'))
            doc.save(path)
        initial = DocumentExtractor()
        initial.extract(path)
        region = next(r for r in initial.regions if r['role'] == 'image')
        overlay = {'source_sha256': file_hash(path), 'reviewer': {'type': 'human', 'id': 'test-reviewer'},
                   'regions': [{'region_id': region['region_id'], 'complete': True, 'evidence': 'Read the complete image text',
                                'units': [{'source': 'Position error [m]', 'bbox': [70, 160, 300, 190]}]}]}
        overlay_path = self.root / 'overlay.json'
        overlay_path.write_text(json.dumps(overlay), encoding='utf-8')
        complete = DocumentExtractor(source_overlay=overlay_path)
        items, stats = complete.extract(path)
        self.assertEqual(stats['warnings'], [])
        self.assertTrue(any(i.get('provenance') == 'manual' and i['source'] == 'Position error [m]' for i in items))
        overlay['regions'][0]['complete'] = False
        overlay_path.write_text(json.dumps(overlay), encoding='utf-8')
        _, partial_stats = DocumentExtractor(source_overlay=overlay_path).extract(path)
        self.assertEqual(partial_stats['extraction_status'], 'NEEDS_REVIEW')
        overlay['regions'][0]['units'][0]['bbox'] = [0, 0, 20, 20]
        overlay_path.write_text(json.dumps(overlay), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'outside'):
            DocumentExtractor(source_overlay=overlay_path).extract(path)
        overlay['source_sha256'] = 'wrong-source'
        overlay_path.write_text(json.dumps(overlay), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'input PDF hash'):
            DocumentExtractor(source_overlay=overlay_path).extract(path)

    def test_complete_manual_scan_is_explicitly_transcribed(self):
        from scripts.backends.manual_overlay import apply_overlay
        path = self.root / 'scan-source.pdf'
        path.write_bytes(b'explicit source bytes for adapter contract test')
        region = {'region_id': 'p1:b0', 'role': 'image', 'disposition': 'needs_review', 'unit_ids': [],
                  'locator': {'page': 1, 'bbox': [0, 0, 500, 700], 'region_id': 'p1:b0'}}
        overlay = {'source_sha256': file_hash(path), 'reviewer': {'type': 'human', 'id': 'full-scan-review'},
                   'regions': [{'region_id': 'p1:b0', 'complete': True, 'evidence': 'Complete visible text transcribed',
                                'units': [{'kind': 'sentence', 'source': 'Calibrate all sensors.', 'bbox': [10, 10, 300, 30]}]}]}
        overlay_path = self.root / 'scan-overlay.json'
        overlay_path.write_text(json.dumps(overlay), encoding='utf-8')
        items, regions = [], [region]
        warnings, _ = apply_overlay(path, overlay_path, items, regions, [],
                                    [{'code': 'image_text_unverified', 'page': 1, 'region_id': 'p1:b0'},
                                     {'code': 'page_has_no_native_text', 'page': 1}])
        self.assertEqual(warnings, [])
        self.assertEqual(items[0]['provenance'], 'manual')
        self.assertEqual(region['evidence']['method'], 'manual_transcription')
        self.assertTrue(region['evidence']['complete'])


if __name__ == '__main__':
    unittest.main()
