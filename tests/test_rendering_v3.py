"""Behavioral rendering checks; fixed source/target records are test fixtures, not LLM evaluations."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import unicodedata

import pymupdf
from PIL import Image, ImageDraw

from scripts.compile_typst import (RenderError, compile_typst, render_typst_document,
    latex_to_typst, expected_content, compile_job, export_markdown)
from scripts.verify_output import inspect_pdf, validate_visual_review, verify_job
from scripts import document_ir as ir


class RenderingV3Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def render(self, items, mode='A', metadata=None, name='paper'):
        typ = self.root / (name + '.typ')
        pdf = self.root / (name + '.pdf')
        typ.write_text(render_typst_document(items, mode, metadata, str(self.root)), encoding='utf-8')
        self.assertTrue(compile_typst(typ, pdf))
        return pdf

    def text(self, pdf):
        with pymupdf.open(pdf) as doc:
            return '\n'.join(page.get_text() for page in doc)

    def test_missing_image_is_only_allowed_in_explicit_draft(self):
        items = [{'id': 'f1', 'kind': 'figure', 'source': 'Fig. 1. Results.', 'translation': '图 1. 结果。'}]
        with self.assertRaisesRegex(RenderError, 'asset binding'):
            render_typst_document(items, metadata={'output_stage': 'candidate'}, typ_dir=str(self.root))
        self.assertIn('missing graphic', self.text(self.render(items, metadata={'output_stage': 'draft'})))

    def test_exact_asset_identity_is_independent_of_manifest_order(self):
        assets = []
        for ident, color in [('fig10', 'orange'), ('fig1', 'blue')]:
            path = self.root / (ident + '.png')
            Image.new('RGB', (80, 50), color).save(path)
            assets.append({'asset_id': ident, 'doc_id': 'doc', 'path': path.name,
                           'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
        items = [{'id': 'f1', 'kind': 'figure', 'source': 'Fig. 1. Results.', 'translation': '图 1. 结果。',
                  'asset_id': 'fig1', 'doc_id': 'doc'}]
        pdf = self.render(items, metadata={'output_stage': 'candidate', 'assets': assets, 'asset_root': str(self.root)})
        segments, graphics, _ = expected_content(items, 'A')
        checked = inspect_pdf(pdf, segments, graphics, assets, self.root)
        self.assertEqual(checked['status'], 'PASS', checked)
        wrong = [{'id': 'f1', 'asset_id': 'fig10'}]
        self.assertEqual(inspect_pdf(pdf, segments, wrong, assets, self.root)['status'], 'FAIL')

    def test_transparent_image_is_checked_with_its_pdf_soft_mask(self):
        path = self.root / 'transparent.png'
        graphic = Image.new('RGBA', (80, 50), (0, 0, 0, 0))
        ImageDraw.Draw(graphic).rectangle((20, 10, 60, 40), fill=(255, 0, 0, 255))
        graphic.save(path)
        assets = [{'asset_id': 'alpha', 'doc_id': 'doc', 'path': path.name, 'sha256': ir.file_hash(path)}]
        items = [{'id': 'f', 'kind': 'figure', 'source': 'Transparent plot.', 'translation': '透明图。',
                  'asset_id': 'alpha', 'doc_id': 'doc'}]
        pdf = self.render(items, metadata={'output_stage': 'candidate', 'assets': assets, 'asset_root': str(self.root)})
        segments, graphics, _ = expected_content(items, 'A')
        self.assertEqual(inspect_pdf(pdf, segments, graphics, assets, self.root)['status'], 'PASS')

    def test_literals_cannot_execute_typst(self):
        value = '#read("private.txt") @missing [1] $x_1$ ** 25%'
        items = [{'id': 's', 'kind': 'sentence', 'source': value, 'translation': '保留 ' + value}]
        output = self.text(self.render(items))
        self.assertIn('#read("private.txt")', output)
        self.assertIn('[1]', output)
        self.assertIn('$x_1$', output)

    def test_mode_b_translates_heading_caption_and_table(self):
        items = [
            {'id': 'h', 'kind': 'heading', 'source': 'ENGLISH_HEADING', 'translation': '中文标题'},
            {'id': 'c', 'kind': 'table', 'table_id': 't', 'source': 'ENGLISH_CAPTION', 'translation': '中文表题'},
            {'id': 'v', 'kind': 'table_cell', 'table_id': 't', 'row': 0, 'col': 0,
             'source': 'ENGLISH_CELL', 'translation': '中文单元格'},
        ]
        output = self.text(self.render(items, 'B'))
        self.assertNotIn('ENGLISH', output)
        self.assertIn('中文标题', output)
        self.assertIn('中文表题', output)
        self.assertIn('中文单元格', output)

    def test_heading_hierarchy_survives_in_pdf_and_markdown(self):
        items = [{'id': 'h1', 'kind': 'heading', 'source': 'Top heading', 'translation': '一级标题'},
                 {'id': 'h2', 'kind': 'heading', 'source': 'Sub heading', 'translation': '二级标题', 'heading_level': 2}]
        pdf = self.render(items, 'B')
        with pymupdf.open(pdf) as document:
            spans = [span for page in document for block in page.get_text('dict')['blocks']
                     for line in block.get('lines', []) for span in line['spans']]
        sizes = {text: next(span['size'] for span in spans if text in span['text']) for text in ('一级标题', '二级标题')}
        self.assertGreater(sizes['一级标题'], sizes['二级标题'])
        markdown = export_markdown(items, 'B')
        self.assertIn('# 一级标题\n', markdown)
        self.assertIn('## 二级标题\n', markdown)

    def test_mode_c_groups_parent_paragraph_and_mode_d_aligns_columns(self):
        items = [{'id': 'a', 'kind': 'sentence', 'parent_id': 'p', 'source': 'LEFT_SENTINEL one.', 'translation': '右侧标记第一句。'},
                 {'id': 'b', 'kind': 'sentence', 'parent_id': 'p', 'source': 'Second sentence.', 'translation': '第二句。'}]
        pdf_c = self.render(items, 'C', name='modec')
        output = self.text(pdf_c)
        self.assertLess(output.index('Second sentence'), output.index('右侧标记'))
        pdf_d = self.render(items, 'D', name='moded')
        with pymupdf.open(pdf_d) as doc:
            en = doc[0].search_for('LEFT_SENTINEL')[0]
            zh = doc[0].search_for('右侧标记')[0]
            self.assertLess(en.x0, zh.x0)
            self.assertLess(abs(en.y0 - zh.y0), 15)
        segments, _, _ = expected_content(items, 'D')
        self.assertEqual(inspect_pdf(pdf_d, segments)['status'], 'PASS')

    def test_long_parallel_paragraph_retains_both_flows_across_pages(self):
        item = {'id': 'long', 'kind': 'sentence', 'parent_id': 'long-paragraph',
                'source': 'The estimator combines calibrated observations. ' * 110,
                'translation': '该估计器融合已标定的观测数据。' * 110}
        pdf = self.render([item], 'D')
        expected, _, _ = expected_content([item], 'D')
        report = inspect_pdf(pdf, expected, mode='D')
        self.assertGreater(report['page_count'], 1)
        self.assertEqual(report['status'], 'PASS', report['findings'])

    def test_supported_latex_equation_compiles_unknown_macros_fail(self):
        value = r'\begin{equation}\frac{a}{b}=c\end{equation}'
        self.assertEqual(latex_to_typst(value), 'frac(a, b)=c')
        item = {'id': 'eq', 'kind': 'equation', 'source': value, 'action': 'preserve', 'math_format': 'latex'}
        pdf = self.render([item])
        self.assertIn('c', self.text(pdf))
        with self.assertRaises(RenderError):
            latex_to_typst(r'\begin{align}a&=b\end{align}')

    def test_protected_inline_math_and_citation_are_restored(self):
        item = {'id': 'p', 'kind': 'sentence', 'source': 'Use ⟦TEX000001⟧ according to ⟦TEX000002⟧.',
                'translation': '根据 ⟦TEX000002⟧ 使用 ⟦TEX000001⟧。',
                'protected_tokens': {'⟦TEX000001⟧': r'$\frac{a}{b}$', '⟦TEX000002⟧': r'\cite{Smith2020}'}}
        pdf = self.render([item])
        output = self.text(pdf)
        self.assertIn('[Smith2020]', output)
        self.assertNotIn('TEX000001', output)
        expected, _, math_ids = expected_content([item], 'A')
        self.assertEqual(math_ids, ['p'])
        self.assertEqual(inspect_pdf(pdf, expected)['status'], 'PASS')

    def test_markdown_inline_math_renders_and_missing_math_is_rejected(self):
        item = {'id': 'math', 'kind': 'sentence', 'source_format': 'markdown',
                'source': 'The equation is $x_1 + x_2 = y$.', 'translation': '方程为 $x_1 + x_2 = y$。'}
        pdf = self.render([item])
        text = self.text(pdf)
        self.assertNotIn('$', text)
        self.assertNotIn('_', text)
        expected, _, math_ids = expected_content([item], 'A')
        self.assertEqual(math_ids, ['math'])
        self.assertEqual(sum(s.get('verification') == 'math_glyphs' for s in expected), 2)
        self.assertEqual(inspect_pdf(pdf, expected)['status'], 'PASS')
        # Preserve all surrounding prose but remove both equations from a real PDF.
        missing = dict(item, source='The equation is .', translation='方程为 。')
        report = inspect_pdf(self.render([missing], name='missing-math'), expected)
        self.assertEqual(report['status'], 'FAIL')
        self.assertEqual(sum(f['type'] == 'missing_math_glyphs' for f in report['findings']), 2)
        with self.assertRaisesRegex(RenderError, 'Unsupported LaTeX command'):
            render_typst_document([dict(item, source=r'Use $\unknown{x}$ here.')])
        code = dict(item, source='Literal `$x_1$` and a price $5.', translation='Literal `$x_1$` and a price $5.')
        self.assertIn('$x_1$', self.text(self.render([code], name='literal-code')))

    def test_inline_math_and_sentence_punctuation_share_the_prose_baseline(self):
        item = {'id': 'inline', 'kind': 'sentence', 'source_format': 'markdown',
                'source': 'The equation is $x_1 + x_2 = y$ END_MARKER.',
                'translation': '方程为 $x_1 + x_2 = y$ 结束标记。'}
        pdf = self.render([item])
        with pymupdf.open(pdf) as document:
            self.assertEqual(len(document), 1)
            page = document[0]
            spans = [span for block in page.get_text('dict')['blocks']
                     for line in block.get('lines', []) for span in line['spans']]
            characters = [char for block in page.get_text('rawdict')['blocks']
                          for line in block.get('lines', []) for span in line['spans'] for char in span['chars']]
            for prefix, suffix in [('The equation is', 'END_MARKER.'), ('方程为', '结束标记。')]:
                left = next(s for s in spans if prefix in s['text'])
                right = next(s for s in spans if suffix in s['text'])
                self.assertLess(abs(left['origin'][1] - right['origin'][1]), 1)
                self.assertGreater(right['bbox'][0], left['bbox'][2])
                math_chars = [c for c in characters if unicodedata.normalize('NFKC', c['c']) == 'x'
                              and abs(c['origin'][1] - left['origin'][1]) < 1]
                self.assertEqual(len(math_chars), 2)

    def test_short_bilingual_pair_stays_on_same_page(self):
        item = {'id': 'pair', 'kind': 'sentence', 'source': 'ORIGINAL_PAIR_MARKER stays with its translation.',
                'translation': '译文配对标记与原文保持在同一页。'}
        typ = self.root / 'pair.typ'
        pdf = self.root / 'pair.pdf'
        source = render_typst_document([item])
        source = source.replace('#text(fill: red, weight: "bold")[DRAFT — not accepted]', '#v(710pt)')
        typ.write_text(source, encoding='utf-8')
        self.assertTrue(compile_typst(typ, pdf))
        with pymupdf.open(pdf) as document:
            source_pages = [i for i, page in enumerate(document, 1) if page.search_for('ORIGINAL_PAIR_MARKER')]
            target_pages = [i for i, page in enumerate(document, 1) if page.search_for('译文配对标记')]
        self.assertEqual(source_pages, [2])
        self.assertEqual(target_pages, source_pages)

    def test_long_mode_a_pair_can_continue_across_pages(self):
        item = {'id': 'long-a', 'kind': 'paragraph',
                'source': 'The estimator combines calibrated observations. ' * 110,
                'translation': '该估计器融合已标定的观测数据。' * 110}
        pdf = self.render([item], 'A')
        expected, _, _ = expected_content([item], 'A')
        report = inspect_pdf(pdf, expected)
        self.assertGreater(report['page_count'], 1)
        self.assertEqual(report['status'], 'PASS', report['findings'])

    def test_tables_keep_multiple_ids_and_reject_overlapping_cells(self):
        cells = [{'id': str(i), 'kind': 'table_cell', 'table_id': str(i), 'row': 0, 'col': 0,
                  'source': word, 'translation': word} for i, word in enumerate(['FIRSTMODEL', 'SECONDMODEL'])]
        output = self.text(self.render(cells))
        self.assertIn('FIRSTMODEL', output)
        self.assertIn('SECONDMODEL', output)
        with self.assertRaisesRegex(RenderError, 'duplicate'):
            self.render([cells[0], dict(cells[0], id='different')])

    def test_pdf_verification_counts_repeated_occurrences(self):
        item = {'id': 'one', 'kind': 'sentence', 'source': 'Repeat marker.', 'translation': 'Repeat marker.'}
        pdf = self.render([item])
        segments, _, _ = expected_content([item, dict(item, id='two')], 'A')
        report = inspect_pdf(pdf, segments)
        self.assertEqual(report['status'], 'FAIL')
        self.assertEqual(report['segments_found'], 1)

    def test_markdown_table_cells_and_spans_are_structured(self):
        rows = [{'id': 'h1', 'kind': 'table_cell', 'table_id': 't', 'row': 0, 'col': 0,
                 'source': 'Method', 'translation': '方法'},
                {'id': 'h2', 'kind': 'table_cell', 'table_id': 't', 'row': 0, 'col': 1,
                 'source': 'Setting', 'translation': '场景'},
                {'id': 'v1', 'kind': 'table_cell', 'table_id': 't', 'row': 1, 'col': 0,
                 'source': 'Proposed', 'translation': '本文方法'},
                {'id': 'v2', 'kind': 'table_cell', 'table_id': 't', 'row': 1, 'col': 1,
                 'source': 'Open field', 'translation': '开阔区域'}]
        md = export_markdown(rows, 'B')
        self.assertIn('| 方法 | 场景 |', md)
        self.assertIn('| 本文方法 | 开阔区域 |', md)
        rows[2]['colspan'] = 2
        md = export_markdown(rows[:3], 'B')
        self.assertIn('colspan="2"', md)
        self.assertIn('<table>', md)

    def test_job_draft_does_not_require_translations_or_preflight(self):
        original = self.root / 'draft.md'
        original.write_text('An unfinished sentence.', encoding='utf-8')
        job = ir.create_job(original, self.root / 'draft-job', [{'id': 'u', 'kind': 'sentence', 'source': original.read_text(encoding='utf-8')}])
        report = compile_job(job['root'], output_stage='draft')
        self.assertEqual(report['status'], 'draft')
        self.assertIn('UNTRANSLATED', self.text(job['root'] / report['pdf_path']))
        with self.assertRaises((ir.ContractError, OSError)):
            verify_job(job['root'])

    def test_visual_review_must_be_current_and_complete(self):
        review = {'pdf_hash': 'abc', 'page_count': 2, 'reviewed_pages': [1, 2],
                  'reviewer': 'test reviewer', 'checks': ['layout', 'graphics', 'math', 'text_visibility'],
                  'status': 'PASS', 'findings': []}
        self.assertEqual(validate_visual_review(review, 'abc', 2)['status'], 'PASS')
        self.assertEqual(validate_visual_review(review, 'changed', 2)['status'], 'FAIL')
        self.assertEqual(validate_visual_review(dict(review, reviewed_pages=[1]), 'abc', 2)['status'], 'NEEDS_REVIEW')
        self.assertEqual(validate_visual_review(None, 'abc', 2)['status'], 'NOT_RUN')

    def make_job(self):
        original = self.root / 'input.md'
        original.write_text('The method is stable.', encoding='utf-8')
        job = ir.create_job(original, self.root / 'job', [{'id': 's1', 'kind': 'sentence', 'source': 'The method is stable.'}])
        source = job['source'][0]
        records = [{'id': source['id'], 'doc_id': job['document']['doc_id'], 'source_hash': source['source_hash'],
                    'source_manifest_hash': job['document']['source_manifest_hash'], **ir.translation_bindings(job),
                    'translation': '该方法稳定。', 'status': 'translated'}]
        ir.atomic_write_jsonl(job['root'] / 'translations.jsonl', records)
        ir.atomic_write_jsonl(job['root'] / 'merged.jsonl', [{**source, 'translation': records[0]['translation']}])
        # Controlled upstream report fixture, solely for testing the renderer boundary.
        ir.atomic_write_json(job['root'] / 'checks' / 'preflight.json', {'status': 'PASS', 'bindings': ir.job_bindings(job),
            'checks': {name: {'status': 'PASS'} for name in ('extraction', 'structural', 'factual', 'assets', 'semantic')}})
        return job

    def test_job_requires_current_preflight_and_visual_review(self):
        job = self.make_job()
        result = compile_job(job['root'])
        report = verify_job(job['root'])
        self.assertEqual(report['status'], 'NEEDS_REVIEW')
        self.assertFalse((job['root'] / 'deliverables').exists())
        review = {'pdf_hash': result['pdf_hash'], 'page_count': report['automatic']['page_count'],
                  'reviewed_pages': list(range(1, report['automatic']['page_count'] + 1)), 'reviewer': 'fixed unit-test fixture',
                  'checks': ['layout', 'graphics', 'math', 'text_visibility'], 'status': 'PASS', 'findings': []}
        ir.atomic_write_json(job['root'] / 'reviews' / 'visual.json', review)
        self.assertEqual(verify_job(job['root'])['delivery_status'], 'accepted')
        translations = ir.read_jsonl(job['root'] / 'translations.jsonl')
        translations[0]['translation'] = '改动后的译文。'
        ir.atomic_write_jsonl(job['root'] / 'translations.jsonl', translations)
        with self.assertRaises(ir.ContractError):
            compile_job(job['root'])

    def test_render_map_cannot_omit_expected_source_or_targets(self):
        job = self.make_job()
        result = compile_job(job['root'])
        result['segments'] = []
        ir.atomic_write_json(job['root'] / 'checks' / 'render_map.json', result)
        with self.assertRaisesRegex(ir.ContractError, 'canonical'):
            verify_job(job['root'])

    def test_markdown_cannot_be_changed_even_if_render_map_hash_is_replaced(self):
        job = self.make_job()
        result = compile_job(job['root'])
        markdown = job['root'] / result['markdown_path']
        markdown.write_text('A missing translation.', encoding='utf-8')
        result['markdown_hash'] = ir.file_hash(markdown)
        ir.atomic_write_json(job['root'] / 'checks' / 'render_map.json', result)
        with self.assertRaisesRegex(ir.ContractError, 'Markdown does not match'):
            verify_job(job['root'])

    def test_portable_copied_assets_are_rechecked_before_acceptance(self):
        original = self.root / 'asset-source.md'
        original.write_text('A figure.', encoding='utf-8')
        source_image = self.root / 'source.png'
        Image.new('RGB', (80, 50), 'blue').save(source_image)
        asset = {'asset_id': 'plot', 'path': str(source_image), 'sha256': ir.file_hash(source_image)}
        job = ir.create_job(original, self.root / 'asset-job',
            [{'id': 'fig', 'kind': 'figure', 'source': 'A figure.', 'asset_id': 'plot', 'action': 'preserve'}],
            assets=[asset])
        ir.atomic_write_jsonl(job['root'] / 'translations.jsonl', [])
        ir.atomic_write_jsonl(job['root'] / 'merged.jsonl', [{**row, 'translation': row['source']} for row in job['source']])
        ir.atomic_write_json(job['root'] / 'checks' / 'preflight.json', {'status': 'PASS', 'bindings': ir.job_bindings(job),
            'checks': {name: {'status': 'PASS'} for name in ('extraction', 'structural', 'factual', 'assets', 'semantic')}})
        result = compile_job(job['root'])
        copied = job['root'] / result['assets'][0]['path']
        Image.new('RGB', (80, 50), 'red').save(copied)
        with self.assertRaisesRegex(ir.ContractError, 'Portable candidate asset'):
            verify_job(job['root'])


if __name__ == '__main__':
    unittest.main()
