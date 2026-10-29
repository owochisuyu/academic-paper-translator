"""Behavioral integrity regressions beyond the original review examples."""
from __future__ import annotations

from decimal import Decimal
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

import pymupdf

from scripts.numeric_entities import extract_numeric_entities, compare_numeric_entities
from scripts.source_coverage import validate_source_coverage
from scripts.validate_translation import TranslationValidator, load_manifest, load_translations, validate_job
from scripts.document_ir import (create_job, atomic_write_jsonl, atomic_write_json,
                                  translation_hash, SEMANTIC_CHECKS, canonical_hash,
                                  load_job, assert_report_current, ContractError, translation_bindings, reviewed_regions)


class TestExactEntities(unittest.TestCase):
    def check(self, source, target):
        return TranslationValidator().validate([{'id': 's1', 'source': source, 'kind': 'sentence', 'page': 1}], {'s1': target})

    def test_counter_magnitude_is_exact(self):
        for original, changed in [('100000', '100001'), ('10000000000000000000000001', '10000000000000000000000002')]:
            with self.subTest(original=original):
                self.assertEqual(self.check(f'The count is {original}.', f'计数为{changed}。')['status'], 'FAIL')

    def test_scientific_decimal_equivalence_not_parse_failure(self):
        for text in ['1.5e-3', '1.5 × 10^{-3}', '0.0015']:
            self.assertEqual(extract_numeric_entities(text)[0]['value'], Decimal('0.0015'))
        result = self.check('The threshold is 1.5e-3.', '阈值为0.0015。')
        self.assertEqual(result['status'], 'PASS')
        self.assertTrue(result['tier2_factual_integrity']['formatting_issues'])

    def test_degree_alias_and_longest_compound_units(self):
        for source, target in [('45°', '45度'), ('10 ms', '10ms'), ('2 m/s²', '2 m/s^2')]:
            self.assertEqual(self.check(source, target)['status'], 'PASS')
        for source, target in [('2 m/s²', '2 m/s'), ('1 mV', '1 MV'), ('10 ms', '10 m')]:
            self.assertEqual(self.check(source, target)['status'], 'FAIL')

    def test_occurrence_and_explicit_variable_binding(self):
        self.assertEqual(self.check('A=1 m, B=2 m.', 'A=2 m，B=1 m。')['status'], 'FAIL')
        self.assertEqual(self.check('The offsets are 5 m and 5 m.', '偏移为5 m。')['status'], 'FAIL')
        self.assertEqual(self.check('The distance is 5 m.', '距离为5 m，再增加5 m。')['status'], 'FAIL')

    def test_citations_and_math_are_counted(self):
        self.assertEqual(self.check('See [1] and [1].', '见[1]。')['status'], 'FAIL')
        self.assertEqual(self.check('Use $x_1$ and $x_1$.', '使用$x_1$。')['status'], 'FAIL')
        self.assertEqual(self.check('Use $x_1$ [1].', '使用$x_1$ [1]。')['status'], 'PASS')

    def test_placeholder_mutation_is_rejected(self):
        self.assertEqual(self.check('Use ⟦TEX0001⟧ and ⟦TEX0002⟧.', '使用⟦TEX0001⟧。')['status'], 'FAIL')
        self.assertFalse(extract_numeric_entities('Use ⟦TEX0001⟧.'))

    def test_unknown_compact_unit_is_visible(self):
        result = self.check('10furlong', '10furlong')
        self.assertTrue(result['tier2_factual_integrity']['unknown_units'])
        self.assertEqual(result['tier2_factual_integrity']['status'], 'NEEDS_REVIEW')

    def test_semantic_scope_is_honest(self):
        result = self.check('The estimator reduces error only when all sensors are calibrated.', '该估计器降低误差。')
        self.assertEqual(result['semantic']['status'], 'NOT_RUN')
        self.assertEqual(result['rendering']['status'], 'NOT_RUN')


class TestStrictImport(unittest.TestCase):
    def test_bad_jsonl_and_duplicate_keys_are_not_dropped(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / 'source.jsonl'
            file.write_text('{"id":"s1","source":"A"}\nnot-json\n', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, ':2:'):
                load_manifest(file)
            file.write_text('{"id":"s1","id":"s2","source":"A"}\n', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'Duplicate JSON key'):
                load_manifest(file)

    def test_non_string_and_conflicting_translation_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / 'translated.json'
            for data in [{'s1': 2}, {'s1': {'translation': 'A', 'target': 'B'}}, {'s1': {'id': 's2', 'translation': 'A'}}]:
                file.write_text(json.dumps(data), encoding='utf-8')
                with self.subTest(data=data), self.assertRaises(ValueError):
                    load_translations(file)


class TestSourceCoverage(unittest.TestCase):
    def make_pdf(self, root, lines):
        file = Path(root) / 'source.pdf'
        with pymupdf.open() as doc:
            page = doc.new_page(width=600, height=800)
            for index, line in enumerate(lines):
                page.insert_text((40, 50 + 28 * index), line, fontsize=10)
            doc.save(file)
        with pymupdf.open(file) as doc:
            rows = [{'id': f'u{n}', 'kind': 'sentence', 'page': 1, 'source': b[4].strip(),
                     'spans': [{'bbox': list(b[:4])}]} for n, b in enumerate(doc[0].get_text('blocks')) if b[6] == 0]
        return file, rows

    def test_overlapping_vocabulary_does_not_cover_missing_condition(self):
        with tempfile.TemporaryDirectory() as root:
            file, rows = self.make_pdf(root, ['The estimator combines sensor observations.'] * 10 + ['The estimator cannot combine sensor observations without calibration.'])
            self.assertEqual(validate_source_coverage(rows, file)['status'], 'PASS')
            report = validate_source_coverage(rows[:-1], file)
            self.assertEqual(report['status'], 'FAIL')
            self.assertTrue(report['unextracted_blocks'])
            self.assertGreater(report['char_coverage_ratio'], 0.80)

    def test_short_duplicate_occurrence_is_not_lost(self):
        with tempfile.TemporaryDirectory() as root:
            file, rows = self.make_pdf(root, ['Calibrate.', 'Calibrate.', 'Stop.'])
            self.assertEqual(validate_source_coverage(rows, file)['status'], 'PASS')
            self.assertEqual(validate_source_coverage([rows[0], rows[2]], file)['status'], 'FAIL')

    def test_wrong_position_cannot_borrow_another_occurrence(self):
        with tempfile.TemporaryDirectory() as root:
            file, rows = self.make_pdf(root, ['First value.', 'Second value.'])
            rows[1]['spans'] = rows[0]['spans']
            report = validate_source_coverage(rows, file)
            self.assertEqual(report['status'], 'FAIL')
            self.assertTrue(report['unmatched_source_items'])

    def test_native_header_does_not_cover_scanned_body(self):
        with tempfile.TemporaryDirectory() as root:
            file = Path(root) / 'mixed.pdf'
            with pymupdf.open() as scan:
                page = scan.new_page(width=500, height=650)
                page.insert_text((30, 60), 'Calibration is required before sensor fusion.', fontsize=14)
                data = page.get_pixmap().tobytes('png')
            with pymupdf.open() as doc:
                page = doc.new_page(width=600, height=800)
                page.insert_text((40, 35), 'Appendix: Calibration requirements')
                page.insert_image((40, 70, 540, 720), stream=data)
                doc.save(file)
            rows = [{'id': 'header', 'source': 'Appendix: Calibration requirements', 'page': 1, 'kind': 'heading'}]
            report = validate_source_coverage(rows, file)
            self.assertEqual(report['status'], 'NEEDS_REVIEW')
            self.assertEqual(len(report['scan_candidates']), 1)
            self.assertEqual(report['char_coverage_ratio'], 1.0)

    def test_manual_transcription_requires_same_image_location(self):
        with tempfile.TemporaryDirectory() as root:
            file = Path(root) / 'scan.pdf'
            with pymupdf.open() as scan:
                page = scan.new_page(width=300, height=300)
                page.insert_text((20, 50), 'Calibration required.')
                image = page.get_pixmap().tobytes('png')
            with pymupdf.open() as doc:
                page = doc.new_page(width=500, height=500)
                page.insert_image((50, 100, 350, 400), stream=image)
                doc.save(file)
            item = {'id': 'label', 'kind': 'figure_label', 'page': 1, 'source': 'Calibration required.',
                    'provenance': 'manual', 'locators': [{'page': 1, 'bbox': [60, 120, 300, 170], 'method': 'manual_transcription'}]}
            region = {'region_id': 'image', 'role': 'image', 'locator': {'page': 1, 'bbox': [50, 100, 350, 400]},
                      'disposition': 'preserve', 'unit_ids': ['label'],
                      'evidence': {'method': 'manual_transcription', 'complete': True}}
            result = validate_source_coverage([item], file, regions=[region])
            self.assertEqual(result['status'], 'PASS', result)
            self.assertEqual(result['native_text_check'], 'NOT_APPLICABLE')
            self.assertIsNone(result['char_coverage_ratio'])
            item['locators'][0]['bbox'] = [10, 10, 100, 30]
            self.assertEqual(validate_source_coverage([item], file, regions=[region])['status'], 'NEEDS_REVIEW')

    def test_text_source_ledger_must_cover_file(self):
        with tempfile.TemporaryDirectory() as root:
            file = Path(root) / 'paper.md'
            text = 'First statement.\nOmitted condition.'
            file.write_text(text, encoding='utf-8')
            rows = [{'id': 'u1', 'kind': 'sentence', 'source': 'First statement.'}]
            region = {'region_id': 'r1', 'raw_text': text[:16], 'locator': {'file': file.name, 'start': 0, 'end': 16},
                      'unit_ids': ['u1'], 'disposition': 'translate'}
            report = validate_source_coverage(rows, file, regions=[region])
            self.assertEqual(report['status'], 'FAIL')
            self.assertTrue(report['unextracted_blocks'])

    def test_cross_page_parts_cover_both_pages_without_hiding_omission(self):
        with tempfile.TemporaryDirectory() as root:
            file = Path(root) / 'two-pages.pdf'
            with pymupdf.open() as doc:
                for text in ['The method applies only', 'when all sensors are calibrated.']:
                    page = doc.new_page()
                    page.insert_text((40, 50), text)
                doc.save(file)
            parts = []
            with pymupdf.open(file) as doc:
                for page_index, page in enumerate(doc):
                    block = page.get_text('blocks')[0]
                    raw = block[4].rstrip('\n')
                    parts.append({'source': raw, 'raw_source': raw,
                                  'locators': [{'page': page_index + 1, 'bbox': list(block[:4])}]})
            item = {'id': 'joined', 'kind': 'sentence', 'page': 1, 'source_parts': parts,
                    'source': ' '.join(p['source'] for p in parts),
                    'raw_source': '\n'.join(p['raw_source'] for p in parts),
                    'locators': [loc for p in parts for loc in p['locators']]}
            result = validate_source_coverage([item], file)
            self.assertEqual(result['status'], 'PASS', result)
            self.assertEqual(result['char_coverage_ratio'], 1.0)
            shortened = deepcopy(item)
            shortened['source'] = 'The method applies only'
            self.assertEqual(validate_source_coverage([shortened], file)['status'], 'FAIL')
            wrong_page = deepcopy(item)
            wrong_page['source_parts'][1]['locators'][0]['page'] = 1
            wrong_page['locators'][1]['page'] = 1
            self.assertEqual(validate_source_coverage([wrong_page], file)['status'], 'FAIL')

    def test_pdf_raw_text_cannot_mask_unsupported_source_normalization(self):
        with tempfile.TemporaryDirectory() as root:
            file, rows = self.make_pdf(root, ['Fusion improves the estimate.', 'Fu-', 'sion requires calibration.'])
            parts = [{'source': r['source'], 'raw_source': r['source'],
                      'locators': [{'page': 1, 'bbox': r['spans'][0]['bbox']}]} for r in rows[1:]]
            raw = '\n'.join(p['raw_source'] for p in parts)
            joined = {'id': 'wrapped', 'page': 1, 'kind': 'reference', 'source_parts': parts,
                      'raw_source': raw, 'source': 'Fusion requires calibration.',
                      'locators': [loc for p in parts for loc in p['locators']],
                      'normalizations': [{'start': 2, 'end': 4, 'original': '-\n', 'replacement': '',
                                          'method': 'same_document_spelling', 'joined_spelling': 'Fusion'}]}
            self.assertEqual(validate_source_coverage([rows[0], joined], file)['status'], 'PASS')
            arbitrary = deepcopy(joined)
            arbitrary['normalizations'][0]['method'] = 'remove_unimportant_text'
            self.assertEqual(validate_source_coverage([rows[0], arbitrary], file)['status'], 'FAIL')
            unwitnessed = deepcopy(joined)
            unwitnessed['normalizations'][0]['joined_spelling'] = 'Fusional'
            self.assertEqual(validate_source_coverage([rows[0], unwitnessed], file)['status'], 'FAIL')

    def test_text_source_unit_must_represent_its_claimed_input(self):
        from scripts.backends.markdown_backend import extract_markdown
        with tempfile.TemporaryDirectory() as root:
            file = Path(root) / 'paper.md'
            file.write_text('Only calibrated sensors reduce error.\n', encoding='utf-8')
            extracted = extract_markdown(file)
            rows, regions = extracted['items'], extracted['regions']
            self.assertEqual(validate_source_coverage(rows, file, regions=regions)['status'], 'PASS')
            rows[0]['source'] = 'Sensors reduce error.'
            report = validate_source_coverage(rows, file, regions=regions)
            self.assertEqual(report['status'], 'FAIL', report)

    def test_text_region_cannot_borrow_a_unit_from_elsewhere(self):
        from scripts.backends.markdown_backend import extract_markdown
        with tempfile.TemporaryDirectory() as root:
            file = Path(root) / 'paper.md'
            file.write_text('First statement.\n\nOnly calibrated sensors reduce error.\n', encoding='utf-8')
            extracted = extract_markdown(file)
            rows, regions = extracted['items'], extracted['regions']
            rows = rows[:1]
            for region in regions:
                if region['disposition'] == 'translate':
                    region['unit_ids'] = [rows[0]['id']]
            self.assertEqual(validate_source_coverage(rows, file, regions=regions)['status'], 'FAIL')

    def test_missing_prose_relabelled_syntax_is_not_pass(self):
        from scripts.backends.markdown_backend import extract_markdown
        with tempfile.TemporaryDirectory() as root:
            file = Path(root) / 'paper.md'
            file.write_text('First statement.\n\nOnly calibrated sensors reduce error.\n', encoding='utf-8')
            extracted = extract_markdown(file)
            rows, regions = extracted['items'], extracted['regions']
            rows = rows[:1]
            for region in regions:
                if 'Only calibrated' in region['raw_text']:
                    region.update(role='source_syntax', disposition='preserve', unit_ids=[])
            report = validate_source_coverage(rows, file, regions=regions)
            self.assertEqual(report['status'], 'NEEDS_REVIEW', report)

    def test_full_scope_ledger_cannot_exclude_missing_body(self):
        from scripts.backends.markdown_backend import extract_markdown
        with tempfile.TemporaryDirectory() as root:
            file = Path(root) / 'paper.md'
            file.write_text('First statement.\n\nOnly calibrated sensors reduce error.\n', encoding='utf-8')
            extracted = extract_markdown(file)
            rows, regions = extracted['items'], extracted['regions']
            for region in regions:
                if 'Only calibrated' in region['raw_text']:
                    region.update(disposition='excluded', evidence={'reason': 'Declared unimportant'})
            result = validate_source_coverage(rows[:1], file, regions=regions, scope={'scope': 'full'})
            self.assertEqual(result['status'], 'FAIL', result)
            pdf, units = self.make_pdf(root, ['First statement.', 'Only calibrated sensors reduce error.'])
            region = {'region_id': 'omitted-body', 'page': 1, 'role': 'footer', 'disposition': 'excluded',
                      'raw_text': units[1]['source'], 'spans': units[1]['spans'],
                      'evidence': {'reason': 'Pretend footer'}}
            result = validate_source_coverage(units[:1], pdf, regions=[region], scope={'scope': 'full'})
            self.assertEqual(result['status'], 'FAIL', result)

    def test_markdown_escaped_cells_and_tex_protected_tokens_keep_coverage(self):
        from scripts.backends.markdown_backend import extract_markdown
        from scripts.backends.latex_backend import extract_project
        with tempfile.TemporaryDirectory() as root:
            md = Path(root) / 'paper.md'
            md.write_text('# Study\n\n| Meaning | Value |\n|---|---|\n| A \\| B | 5 |\n', encoding='utf-8')
            extracted = extract_markdown(md)
            result = validate_source_coverage(extracted['items'], md, regions=extracted['regions'])
            self.assertEqual(result['status'], 'PASS', result)
            tex = Path(root) / 'main.tex'
            tex.write_text('\\documentclass{article}\n\\begin{document}\nThe error is $x_1$ only when calibrated.\n\\end{document}\n', encoding='utf-8')
            extracted = extract_project(tex)
            result = validate_source_coverage(extracted['items'], tex, regions=extracted['regions'])
            self.assertEqual(result['status'], 'PASS', result)
            extracted['items'][0]['source'] = extracted['items'][0]['source'].replace('only when calibrated.', '')
            self.assertEqual(validate_source_coverage(extracted['items'], tex, regions=extracted['regions'])['status'], 'FAIL')

    def test_tex_visible_link_label_is_translatable_and_wrappers_are_retained(self):
        from scripts.backends.latex_backend import extract_project
        with tempfile.TemporaryDirectory() as root:
            tex = Path(root) / 'main.tex'
            tex.write_text('\\documentclass{article}\n\\begin{document}\nUse \\href{https://example.org/}{only calibrated sensors}.\n\\end{document}\n', encoding='utf-8')
            extracted = extract_project(tex)
            source = ' '.join(item['source'] for item in extracted['items'])
            self.assertIn('only calibrated sensors', source)
            self.assertNotIn('https://example.org/', source)
            self.assertFalse(extracted['warnings'])
            report = validate_source_coverage(extracted['items'], tex, regions=extracted['regions'])
            self.assertEqual(report['status'], 'PASS', report)
            self.assertTrue(any('https://example.org/' in value for item in extracted['items']
                                for value in item.get('protected_tokens', {}).values()))


class TestCanonicalValidation(unittest.TestCase):
    def build_job(self, root):
        path = Path(root) / 'paper.md'
        text = 'The threshold is 0.0015.'
        path.write_text(text, encoding='utf-8')
        items = [{'id': 's1', 'source': text, 'raw_source': text, 'kind': 'sentence', 'page': 1,
                  'locators': [{'file': path.name, 'start': 0, 'end': len(text)}]}]
        regions = [{'region_id': 'r1', 'raw_text': text, 'locator': {'file': path.name, 'start': 0, 'end': len(text)},
                    'role': 'paragraph', 'disposition': 'translate', 'unit_ids': ['s1']}]
        job = create_job(path, Path(root) / 'job', items, regions=regions)
        item = job['source'][0]
        target = {'id': item['id'], 'doc_id': job['document']['doc_id'], 'source_hash': item['source_hash'],
                  'source_manifest_hash': job['document']['source_manifest_hash'],
                  'translation': '阈值为0.0015。', 'status': 'translated'}
        target.update(translation_bindings(job))
        atomic_write_jsonl(job['root'] / 'translations.jsonl', [target])
        return job, target

    def review(self, job, target):
        atomic_write_jsonl(job['root'] / 'reviews.jsonl', [{'id': target['id'], 'source_hash': target['source_hash'],
            'doc_id': job['document']['doc_id'], 'source_manifest_hash': job['document']['source_manifest_hash'],
            'translation_hash': translation_hash(target), 'reviewer': {'name': 'fixture reviewer', 'method': 'agent'},
            'independent': True, 'checks': {k: 'PASS' for k in SEMANTIC_CHECKS}, 'findings': [], 'status': 'PASS',
            **translation_bindings(job)}])

    def test_semantic_not_run_blocks_candidate(self):
        with tempfile.TemporaryDirectory() as root:
            job, target = self.build_job(root)
            report = validate_job(job['root'])
            self.assertEqual(report['checks']['semantic']['status'], 'NOT_RUN')
            self.assertEqual(report['status'], 'NEEDS_REVIEW')
            self.review(job, target)
            report = validate_job(job['root'])
            self.assertEqual(report['status'], 'PASS', report)
            self.assertEqual(report['rendering']['status'], 'NOT_RUN')

    def test_stale_review_and_stale_pass_are_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            job, target = self.build_job(root)
            self.review(job, target)
            original = validate_job(job['root'])
            self.assertEqual(original['status'], 'PASS', original)
            target['translation'] = '此处阈值为0.0015。'
            atomic_write_jsonl(job['root'] / 'translations.jsonl', [target])
            with self.assertRaises(ContractError):
                assert_report_current(load_job(job['root']), original)
            self.assertEqual(validate_job(job['root'])['status'], 'NEEDS_REVIEW')

    def test_glossary_change_cannot_bless_old_translation_and_review(self):
        from scripts.prepare_translation import prepare_job
        with tempfile.TemporaryDirectory() as root:
            job, target = self.build_job(root)
            prepare_job(job['root'])
            target.update(translation_bindings(job))
            atomic_write_jsonl(job['root'] / 'translations.jsonl', [target])
            self.review(job, target)
            self.assertEqual(validate_job(job['root'])['status'], 'PASS')
            atomic_write_json(job['root'] / 'glossary.json', {'version': 2, 'entries': [
                {'source': 'threshold', 'preferred_translation': '门限', 'status': 'approved'}]})
            result = validate_job(job['root'])
            self.assertEqual(result['status'], 'FAIL', result)
            self.assertIn('glossary_hash', result['checks']['contract']['error'])

    def source_review(self, job, region, decision, unit_ids=None):
        record = {'region_id': region['region_id'], 'doc_id': job['document']['doc_id'],
                  'region_hash': canonical_hash(region), 'decision': decision,
                  'unit_ids': unit_ids or [], 'reviewer': {'name': 'fixture', 'method': 'agent'},
                  'reason': 'Fixture review', 'status': 'PASS'}
        atomic_write_jsonl(job['root'] / 'source_reviews.jsonl', [record])

    def test_full_scope_cannot_waive_body_paragraph(self):
        with tempfile.TemporaryDirectory() as root:
            job, target = self.build_job(root)
            self.source_review(job, job['regions'][0], 'excluded')
            with self.assertRaises(ContractError):
                reviewed_regions(job)
            self.assertEqual(validate_job(job['root'])['status'], 'FAIL')

    def test_visual_review_cannot_map_foreign_header_or_fake_ocr(self):
        with tempfile.TemporaryDirectory() as root:
            file = Path(root) / 'page.pdf'
            with pymupdf.open() as doc:
                page = doc.new_page()
                page.insert_text((40, 40), 'A native header.')
                doc.save(file)
            item = {'id': 'header', 'source': 'A native header.', 'kind': 'heading', 'page': 1,
                    'provenance': 'native', 'locators': [{'page': 1, 'bbox': [40, 25, 180, 45]}]}
            region = {'region_id': 'body-image', 'role': 'image', 'disposition': 'needs_review',
                      'locator': {'page': 1, 'bbox': [40, 100, 500, 700]}, 'unit_ids': []}
            job = create_job(file, Path(root) / 'job', [item], regions=[region])
            self.source_review(job, job['regions'][0], 'labels_covered', [job['source'][0]['id']])
            with self.assertRaises(ContractError):
                reviewed_regions(job)
            # A native text unit cannot become OCR evidence just by changing the review decision.
            job['regions'][0]['locator']['bbox'] = [35, 20, 200, 60]
            self.source_review(job, job['regions'][0], 'ocr_verified', [job['source'][0]['id']])
            with self.assertRaises(ContractError):
                reviewed_regions(job)

    def test_tampered_manifest_rejected_before_checks(self):
        with tempfile.TemporaryDirectory() as root:
            job, target = self.build_job(root)
            atomic_write_jsonl(job['root'] / 'source.jsonl', [])
            self.assertEqual(validate_job(job['root'])['status'], 'FAIL')


if __name__ == '__main__':
    unittest.main()
