"""Behavioral contracts for provenance, agent packets and review state."""
from pathlib import Path
import copy
import json
import tempfile
import unittest
from unittest import mock

from scripts.document_ir import (ContractError, SEMANTIC_CHECKS, atomic_write_json,
    atomic_write_jsonl, canonical_hash, create_job, file_hash, load_job, load_merged_job,
    load_translation_records, read_json, read_jsonl, reviewed_regions, translation_hash,
    validate_semantic_reviews, job_bindings, assert_report_current, translation_bindings)
from scripts.prepare_translation import prepare_job
from scripts.merge_translation import merge_job


class TestAgentWorkflow(unittest.TestCase):
    def test_units_do_not_consume_following_sentence_as_honorific(self):
        from scripts.align_bilingual import split_en_sentences
        self.assertEqual(split_en_sentences('The update interval is 10 ms. The offset is 45°.'),
                         ['The update interval is 10 ms.', 'The offset is 45°.'])
        self.assertEqual(split_en_sentences('Ms. Jones waited 0.5 min. The filter converged.'),
                         ['Ms. Jones waited 0.5 min.', 'The filter converged.'])

    def test_url_sentence_punctuation_is_not_part_of_protected_url(self):
        from scripts.align_bilingual import split_en_sentences
        self.assertEqual(split_en_sentences('See https://example.org/paper. The code is open.'),
                         ['See https://example.org/paper.', 'The code is open.'])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='paper v3 中文 ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.input = self.root / 'paper.md'
        text = 'The estimator reduces error only when all sensors are calibrated.\n\nThe sample count is 100000.\n'
        self.input.write_text(text, encoding='utf-8')
        first, second = text.strip().split('\n\n')
        items = [{'id': 'p1-s1', 'kind': 'sentence', 'source': first, 'parent_id': 'p1', 'page': 1,
                  'locators': [{'file': 'paper.md', 'start': 0, 'end': len(first)}]},
                 {'id': 'p2-s1', 'kind': 'sentence', 'source': second, 'parent_id': 'p2', 'page': 1,
                  'locators': [{'file': 'paper.md', 'start': len(first) + 2, 'end': len(text) - 1}]}]
        regions = [{'region_id': 'r1', 'locator': items[0]['locators'][0], 'raw_text': first,
                    'role': 'text', 'disposition': 'translate', 'unit_ids': ['p1-s1']},
                   {'region_id': 'r2', 'locator': items[1]['locators'][0], 'raw_text': second,
                    'role': 'text', 'disposition': 'translate', 'unit_ids': ['p2-s1']}]
        self.job = create_job(self.input, self.root / 'job', items, regions=regions)
        self.targets = ['仅在所有传感器均完成标定时，该估计器才会降低误差。', '样本数为 100000。']

    def records(self):
        return [{'id': item['id'], 'doc_id': self.job['document']['doc_id'],
                 'source_hash': item['source_hash'], 'source_manifest_hash': self.job['document']['source_manifest_hash'],
                 **translation_bindings(self.job),
                 'translation': target, 'status': 'translated', 'producer': {'type': 'fixture', 'model': 'fixed-response'}}
                for item, target in zip(self.job['source'], self.targets)]

    def merge(self):
        path = self.root / 'agent.jsonl'
        atomic_write_jsonl(path, self.records())
        return merge_job(self.job['root'], path)

    def reviews(self):
        return [{'id': row['id'], 'source_hash': row['source_hash'], 'translation_hash': translation_hash(row),
                 'doc_id': self.job['document']['doc_id'],
                 'source_manifest_hash': self.job['document']['source_manifest_hash'],
                 **translation_bindings(self.job),
                 'independent': True, 'reviewer': {'name': 'fixture reviewer', 'method': 'agent'},
                 'checks': {key: 'PASS' for key in SEMANTIC_CHECKS}, 'status': 'PASS', 'findings': []}
                for row in self.records()]

    def test_semantic_review_requires_document_and_full_source_bindings(self):
        records, reviews = self.records(), self.reviews()
        self.assertEqual(validate_semantic_reviews(self.job, records, reviews)['status'], 'PASS')
        for key in ('doc_id', 'source_manifest_hash'):
            with self.subTest(missing=key):
                incomplete = copy.deepcopy(reviews)
                incomplete[0].pop(key)
                report = validate_semantic_reviews(self.job, records, incomplete)
                self.assertEqual(report['status'], 'NEEDS_REVIEW')
                self.assertIn(f'stale/missing {key}', report['findings'][0]['issues'])

    def test_reextracted_neighbor_invalidates_unchanged_unit_review(self):
        # Source review can precede discovery of a missing clause. Re-extraction
        # corrects the neighbour while leaving the second unit and target intact.
        old_items = copy.deepcopy(self.job['source'])
        old_items[0]['source'] = 'The estimator reduces error.'
        old = create_job(self.input, self.root / 'old-extraction', old_items,
                         regions=self.job['regions'])
        self.assertEqual(old['source'][1]['source_hash'], self.job['source'][1]['source_hash'])
        self.assertNotEqual(old['document']['source_manifest_hash'],
                            self.job['document']['source_manifest_hash'])
        reviews = self.reviews()
        reviews[1]['source_manifest_hash'] = old['document']['source_manifest_hash']
        self.merge()
        review_file = self.root / 'mixed-reviews.jsonl'
        atomic_write_jsonl(review_file, reviews)
        merged = merge_job(self.job['root'], reviews_path=review_file)
        self.assertEqual(merged['semantic_status'], 'NEEDS_REVIEW')
        from scripts.validate_translation import validate_job
        checked = validate_job(self.job['root'])
        self.assertEqual(checked['status'], 'NEEDS_REVIEW')
        self.assertEqual(checked['checks']['semantic']['reviewed'], 1)

    def test_duplicate_keys_and_bad_json_lines_are_not_silently_discarded(self):
        path = self.root / 'invalid.jsonl'
        path.write_text('{"id":"one","id":"two"}\n', encoding='utf-8')
        with self.assertRaisesRegex(ContractError, 'Duplicate JSON key'):
            read_jsonl(path)
        path.write_text('{"id":"one"}\nnot-json\n', encoding='utf-8')
        with self.assertRaisesRegex(ContractError, ':2:'):
            read_jsonl(path)

    def test_canonical_source_metadata_is_not_replaced_by_model(self):
        rows = self.records()
        rows[0]['source'] = 'Only this should remain.'
        path = self.root / 'bad.jsonl'
        atomic_write_jsonl(path, rows)
        with self.assertRaisesRegex(ContractError, 'conflicts with canonical'):
            merge_job(self.job['root'], path)
        self.assertFalse((self.job['root'] / 'translations.jsonl').exists())

    def test_missing_and_duplicate_translations_fail(self):
        path = self.root / 'bad.jsonl'
        atomic_write_jsonl(path, self.records()[:1])
        with self.assertRaisesRegex(ContractError, 'Missing translation'):
            merge_job(self.job['root'], path)
        rows = self.records()
        atomic_write_jsonl(path, rows + rows[:1])
        with self.assertRaisesRegex(ContractError, 'duplicate'):
            merge_job(self.job['root'], path)

    def test_old_source_hash_rejected(self):
        rows = self.records()
        rows[0]['source_hash'] = '0' * 64
        path = self.root / 'bad.jsonl'
        atomic_write_jsonl(path, rows)
        with self.assertRaisesRegex(ContractError, 'stale/missing source_hash'):
            merge_job(self.job['root'], path)

    def test_tampered_frozen_source_and_input_detected(self):
        path = self.job['root'] / 'source.jsonl'
        original = path.read_bytes()
        path.write_bytes(original + b'\n')
        with self.assertRaisesRegex(ContractError, 'Frozen extraction hash mismatch'):
            load_job(self.job['root'])
        path.write_bytes(original)
        (self.job['root'] / self.job['document']['primary_input']).write_text('changed', encoding='utf-8')
        with self.assertRaisesRegex(ContractError, 'Input hash mismatch'):
            load_job(self.job['root'])

    def test_merge_is_source_owned_and_not_semantic_pass(self):
        report = self.merge()
        self.assertEqual(report['semantic_status'], 'NOT_RUN')
        job = load_merged_job(self.job['root'], require_preflight=False)
        self.assertEqual([x['source'] for x in job['merged']], [x['source'] for x in job['source']])
        with self.assertRaisesRegex(ContractError, 'Preflight is not PASS'):
            load_merged_job(self.job['root'])

    def test_modified_merged_metadata_rejected_even_without_preflight(self):
        self.merge()
        path = self.job['root'] / 'merged.jsonl'
        merged = read_jsonl(path)
        merged[0]['page'] = 80
        atomic_write_jsonl(path, merged)
        with self.assertRaisesRegex(ContractError, 'canonical source/translation join'):
            load_merged_job(self.job['root'], require_preflight=False)

    def test_review_is_bound_to_source_and_target(self):
        self.merge()
        reviews = self.reviews()
        atomic_write_jsonl(self.job['root'] / 'reviews.jsonl', reviews)
        self.assertEqual(validate_semantic_reviews(self.job, self.records())['status'], 'PASS')
        targets = self.records()
        targets[0]['translation'] = '该估计器降低误差。'
        result = validate_semantic_reviews(self.job, targets)
        self.assertEqual(result['status'], 'NEEDS_REVIEW')
        self.assertIn('stale/missing translation_hash', result['findings'][0]['issues'])

    def test_partial_or_unresolved_review_never_passes(self):
        reviews = self.reviews()[:1]
        result = validate_semantic_reviews(self.job, self.records(), records=reviews)
        self.assertEqual(result['reviewed'], 1)
        self.assertEqual(result['status'], 'NEEDS_REVIEW')
        reviews = self.reviews()
        reviews[0]['findings'] = [{'source_quote': 'only when all sensors are calibrated',
                                   'issue': 'Condition omitted', 'resolved': False}]
        self.assertEqual(validate_semantic_reviews(self.job, self.records(), records=reviews)['status'], 'NEEDS_REVIEW')

    def test_fabricated_review_quote_is_detected(self):
        reviews = self.reviews()
        reviews[0]['findings'] = [{'source_quote': 'words not in original', 'resolved': True}]
        result = validate_semantic_reviews(self.job, self.records(), records=reviews)
        self.assertEqual(result['status'], 'NEEDS_REVIEW')
        self.assertIn('finding source_quote is absent', ' '.join(result['findings'][0]['issues']))

    def test_no_implicit_source_overwrite(self):
        with self.assertRaisesRegex(ContractError, 'already exists'):
            create_job(self.input, self.job['root'], [])

    def test_packet_partition_context_and_resume(self):
        manifest = prepare_job(self.job['root'], max_items=1)
        self.assertEqual(manifest['total_batches'], 2)
        records = {x['id']: x for x in self.records()}
        for batch in manifest['batches']:
            request = read_json(self.job['root'] / batch['request'])
            self.assertTrue(request['context_only'])
            self.assertEqual(set(request['assigned_ids']) & {x['id'] for x in request['context_only']}, set())
            atomic_write_jsonl(self.job['root'] / batch['response'], [records[x] for x in batch['assigned_ids']])
        resumed = prepare_job(self.job['root'], max_items=1)
        self.assertEqual(resumed['returned_batches'], 2)
        self.assertEqual(merge_job(self.job['root'])['translated_items'], 2)

    def test_glossary_change_rejects_stale_preparation(self):
        manifest = prepare_job(self.job['root'])
        batch = manifest['batches'][0]
        atomic_write_jsonl(self.job['root'] / batch['response'], self.records())
        atomic_write_json(self.job['root'] / 'glossary.json', {'version': 2, 'entries': [
            {'source': 'estimator', 'preferred_translation': '估计器', 'status': 'approved'}]})
        with self.assertRaisesRegex(ContractError, 'Glossary changed'):
            merge_job(self.job['root'])
        updated = prepare_job(self.job['root'])
        self.assertNotEqual(updated['batches'][0]['cache_key'], batch['cache_key'])
        self.assertEqual(updated['returned_batches'], 0)

    def test_prompt_config_change_invalidates_packet_cache(self):
        first = prepare_job(self.job['root'], model='model-one')
        second = prepare_job(self.job['root'], model='model-two')
        self.assertNotEqual(first['batches'][0]['cache_key'], second['batches'][0]['cache_key'])

    def test_review_packets_contain_translation_digests_and_required_checks(self):
        self.merge()
        manifest = prepare_job(self.job['root'], review=True)
        packet = read_json(self.job['root'] / manifest['batches'][0]['request'])
        self.assertEqual(packet['task'], 'review')
        self.assertEqual(set(packet['required_checks']), set(SEMANTIC_CHECKS))
        self.assertEqual(packet['items'][0]['translation_hash'], translation_hash(self.records()[0]))

    def test_source_review_does_not_modify_frozen_ledger(self):
        region = self.job['regions'][0]
        digest = file_hash(self.job['root'] / 'regions.jsonl')
        region['role'] = 'image'
        row = {'region_id': region['region_id'], 'doc_id': self.job['document']['doc_id'],
               'region_hash': canonical_hash(region), 'decision': 'no_translatable_text',
               'unit_ids': [], 'status': 'PASS', 'reason': 'Fixture verification',
               'reviewer': {'name': 'fixture', 'method': 'agent'}}
        atomic_write_jsonl(self.job['root'] / 'source_reviews.jsonl', [row])
        result = reviewed_regions(self.job)
        self.assertTrue(result[0]['evidence']['reviewed'])
        self.assertEqual(digest, file_hash(self.job['root'] / 'regions.jsonl'))
        row['region_hash'] = '0' * 64
        atomic_write_jsonl(self.job['root'] / 'source_reviews.jsonl', [row])
        with self.assertRaisesRegex(ContractError, 'Stale source region'):
            reviewed_regions(self.job)

    def test_current_report_has_all_required_stages(self):
        self.merge()
        report = {'status': 'PASS', 'bindings': job_bindings(self.job), 'checks': {'structural': {'status': 'PASS'}}}
        with self.assertRaisesRegex(ContractError, 'completed required check'):
            assert_report_current(self.job, report)

    def test_changed_checker_or_dependency_invalidates_saved_approval(self):
        self.merge()
        report = {'status': 'PASS', 'bindings': job_bindings(self.job),
                  'checks': {name: {'status': 'PASS'} for name in
                             ('extraction', 'structural', 'factual', 'assets', 'semantic')}}
        assert_report_current(self.job, report)
        changed = copy.deepcopy(report['bindings']['implementation'])
        changed['packages']['typst'] = 'different-runtime'
        with mock.patch('scripts.document_ir.implementation_bindings', return_value=changed):
            with self.assertRaisesRegex(ContractError, 'implementation changes'):
                assert_report_current(self.job, report)

    def test_explicit_legacy_import_still_requires_semantic_review(self):
        path = self.root / 'legacy.json'
        atomic_write_json(path, {'p1-s1': self.targets[0], 'p2-s1': self.targets[1]})
        report = merge_job(self.job['root'], path, legacy=True)
        self.assertEqual(report['semantic_status'], 'NOT_RUN')
        self.assertEqual(load_translation_records(self.job)[0]['producer']['type'], 'legacy-import')


if __name__ == '__main__':
    unittest.main()
