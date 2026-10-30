"""Compilation is provenance for a PDF, never a translation/coverage exemption."""
import copy
import shutil
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import pymupdf

from scripts.document_ir import (ContractError, atomic_write_json, canonical_hash,
                                 file_hash, load_job)
from scripts.import_latex_pdf import import_compilation, validate_compilation


class TestCompiledLatexImport(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source = self.root / 'source'
        source.mkdir()
        (source / 'main.tex').write_text(r'\documentclass{article}\begin{document}Visible branch.\end{document}', encoding='utf-8')
        (source / 'style.sty').write_text(r'\newcommand{\hidden}{Visible branch.}', encoding='utf-8')
        (self.root / 'run.log').write_text('Synthetic compiler fixture: exit 0.', encoding='utf-8')
        pdf = self.root / 'output.pdf'
        with pymupdf.open() as document:
            page = document.new_page()
            page.insert_text((72, 72), 'Visible branch.')
            document.save(pdf)
        files = [{'path': p.name, 'sha256': file_hash(p)} for p in sorted(source.iterdir())]
        (self.root / 'build').mkdir()
        dependency_record = self.root / 'build/dependencies.mk'
        dependency_record.write_text('output.pdf : main.tex \\\n  style.sty\n', encoding='utf-8')
        self.report = {'schema_version': 1, 'type': 'latex_source_compile', 'status': 'PASS',
            'compile_complete': True, 'exit_code': 0, 'warnings': [], 'main_file': 'main.tex',
            'source_snapshot': {'root': 'source', 'files': files, 'input_set_hash': canonical_hash(files)},
            'engine': {'version': 'synthetic-test-fixture', 'command': ['fixture-compiler', 'main.tex']},
            'logs': [{'path': 'run.log', 'sha256': file_hash(self.root / 'run.log')}],
            'pdf': {'path': pdf.name, 'sha256': file_hash(pdf), 'pages': 1},
            'dependency_audit': {'status': 'PASS', 'external_or_unbound': [], 'files': copy.deepcopy(files),
                'record': {'path': 'build/dependencies.mk', 'sha256': file_hash(dependency_record)}}}
        self.path = self.root / 'compile_result.json'
        self.save()

    def save(self):
        atomic_write_json(self.path, self.report)

    def test_import_freezes_all_inputs_and_does_not_accept_translation(self):
        # No actual compiler is claimed by this fixture; import behavior is tested.
        destination = self.root / 'job'
        with patch('pymupdf.Page.find_tables', return_value=type('Tables', (), {'tables': []})()):
            result = import_compilation(self.path, destination)
        self.assertEqual(result['translation_status'], 'NOT_RUN')
        job = load_job(destination)
        self.assertEqual(job['document']['source_format'], 'pdf')
        self.assertEqual(job['document']['origin_format'], 'latex')
        self.assertEqual(len(job['document']['input_files']), 6)
        self.assertTrue(any(u['source'] == 'Visible branch.' for u in job['source']))
        self.assertFalse((destination / 'deliverables/acceptance.json').exists())
        self.assertFalse((destination / 'translations.jsonl').exists())
        (destination / 'input/source/style.sty').write_text('Changed macro.', encoding='utf-8')
        with self.assertRaisesRegex(ContractError, 'Input hash mismatch'):
            load_job(destination)

    def test_failed_incomplete_or_warned_compilation_is_not_importable(self):
        for change in ({'status': 'NEEDS_REVIEW'}, {'status': 'FAIL'}, {'compile_complete': False},
                       {'exit_code': 1}, {'warnings': ['undefined reference']}):
            with self.subTest(change=change):
                original = copy.deepcopy(self.report)
                self.report.update(change)
                self.save()
                with self.assertRaises(ContractError):
                    validate_compilation(self.path)
                self.report = original

    def test_equation_overlay_is_applied_and_original_manifest_is_frozen(self):
        pdf = self.root / 'output.pdf'
        # Synthetic compilation fixture; this tests importing evidence, not a real engine.
        with pymupdf.open() as document:
            page = document.new_page()
            page.insert_text((72, 72), 'M = 1')
            document.save(pdf)
        self.report['pdf']['sha256'] = file_hash(pdf)
        self.save()
        manifest = self.root / 'reviewed-equation.json'
        atomic_write_json(manifest, {'source_sha256': file_hash(pdf),
            'reviewer': {'type': 'agent', 'id': 'synthetic fixture'},
            'equations': [{'id': 'equation-reviewed-001', 'page': 1,
                'bbox': [68, 56, 130, 78], 'region_ids': ['p1:b0'],
                'complete': True, 'no_translatable_text': True,
                'evidence': 'Fixture contains only M, equals sign, and the number one.'}]})
        exact_bytes = manifest.read_bytes()
        destination = self.root / 'equation-job'
        with patch('pymupdf.Page.find_tables', return_value=type('Tables', (), {'tables': []})()):
            result = import_compilation(self.path, destination, equation_overlay=manifest)
        self.assertEqual(result['equation_count'], 1)
        job = load_job(destination)
        frozen = destination / job['document']['equation_overlay']['path']
        self.assertEqual(frozen.read_bytes(), exact_bytes)
        self.assertIn({'path': job['document']['equation_overlay']['path'],
                       'sha256': file_hash(frozen)}, job['document']['input_files'])
        self.assertEqual(job['source'][0]['action'], 'preserve')
        # The outside input is now independent; changing the frozen copy is not allowed.
        manifest.write_text('{}', encoding='utf-8')
        load_job(destination)
        frozen.write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(ContractError, 'Input hash mismatch'):
            load_job(destination)

    def test_pdf_source_and_log_tampering_are_rejected(self):
        for relative in ('output.pdf', 'source/main.tex', 'run.log', 'build/dependencies.mk'):
            with self.subTest(relative=relative):
                path = self.root / relative
                content = path.read_bytes()
                path.write_bytes(content + b' tampered')
                with self.assertRaisesRegex(ContractError, 'hash mismatch'):
                    validate_compilation(self.path)
                path.write_bytes(content)

    def test_nonblocking_layout_notices_are_retained_for_visual_review(self):
        self.report['warnings'] = [{'code': 'overfull_box', 'blocking': False,
                                    'count': 1, 'examples': ['Overfull \\hbox']}]
        self.save()
        _, report, _, _ = validate_compilation(self.path)
        self.assertEqual(report['warnings'], self.report['warnings'])
        self.report['warnings'][0]['code'] = 'missing_character'
        self.save()
        with self.assertRaisesRegex(ContractError, 'warnings'):
            validate_compilation(self.path)

    def test_source_set_main_file_and_page_count_must_match(self):
        for key, value in (('input_set_hash', 'wrong'), ('main_file', 'missing.tex'), ('pages', 2)):
            original = copy.deepcopy(self.report)
            target = self.report['source_snapshot'] if key == 'input_set_hash' else self.report['pdf'] if key == 'pages' else self.report
            target[key] = value
            self.save()
            with self.assertRaises(ContractError):
                validate_compilation(self.path)
            self.report = original

    def test_path_escape_and_duplicate_inputs_are_rejected(self):
        for change in ('escape', 'duplicate'):
            original = copy.deepcopy(self.report)
            rows = self.report['source_snapshot']['files']
            if change == 'escape':
                rows[0]['path'] = '../../outside.tex'
            else:
                rows.append(dict(rows[0]))
            self.report['source_snapshot']['input_set_hash'] = canonical_hash(rows)
            self.save()
            with self.assertRaises(ContractError):
                validate_compilation(self.path)
            self.report = original

    def test_missing_engine_or_logs_cannot_claim_success(self):
        for field in ('engine', 'logs'):
            original = self.report.pop(field)
            self.save()
            with self.assertRaises(ContractError):
                validate_compilation(self.path)
            self.report[field] = original

    def test_compiler_adaptations_are_explicit_and_content_bound(self):
        shutil.copytree(self.root / 'source', self.root / 'original')
        self.report['original_source_snapshot'] = {
            **copy.deepcopy(self.report['source_snapshot']), 'root': 'original'}
        main = self.root / 'source/main.tex'
        before = file_hash(main)
        main.write_text(main.read_text(encoding='utf-8') + '\n% explicit driver adaptation', encoding='utf-8')
        after = file_hash(main)
        rows = self.report['source_snapshot']['files']
        next(row for row in rows if row['path'] == 'main.tex')['sha256'] = after
        next(row for row in self.report['dependency_audit']['files'] if row['path'] == 'main.tex')['sha256'] = after
        self.report['source_snapshot']['input_set_hash'] = canonical_hash(rows)
        self.save()
        with self.assertRaisesRegex(ContractError, 'explicit adaptation'):
            validate_compilation(self.path)
        self.report['adaptations'] = [{'file': 'main.tex', 'before_sha256': before,
            'after_sha256': after, 'reason': 'Synthetic test adaptation; no actual compiler run.'}]
        self.save()
        validate_compilation(self.path)
        self.report['adaptations'][0]['before_sha256'] = 'stale'
        self.save()
        with self.assertRaisesRegex(ContractError, 'source bytes'):
            validate_compilation(self.path)

    def test_changed_or_unhashed_adaptation_diff_is_rejected(self):
        shutil.copytree(self.root / 'source', self.root / 'original')
        self.report['original_source_snapshot'] = {
            **copy.deepcopy(self.report['source_snapshot']), 'root': 'original'}
        main = self.root / 'source/main.tex'
        before = file_hash(main)
        main.write_text(main.read_text(encoding='utf-8') + '\n% adapter change', encoding='utf-8')
        rows = self.report['source_snapshot']['files']
        after = file_hash(main)
        next(row for row in rows if row['path'] == 'main.tex')['sha256'] = after
        next(row for row in self.report['dependency_audit']['files'] if row['path'] == 'main.tex')['sha256'] = after
        self.report['source_snapshot']['input_set_hash'] = canonical_hash(rows)
        patch_path = self.root / 'change.patch'
        patch_path.write_text('+% adapter change\n', encoding='utf-8')
        self.report['adaptations'] = [{'file': 'main.tex', 'before_sha256': before,
            'after_sha256': after, 'reason': 'Synthetic test adaptation.',
            'patch_path': 'change.patch', 'patch_sha256': file_hash(patch_path)}]
        self.save()
        validate_compilation(self.path)
        patch_path.write_text('Unrelated diff.', encoding='utf-8')
        with self.assertRaisesRegex(ContractError, 'patch hash mismatch'):
            validate_compilation(self.path)
        self.report['adaptations'][0].pop('patch_sha256')
        self.save()
        with self.assertRaisesRegex(ContractError, 'SHA256'):
            validate_compilation(self.path)

    def test_external_or_unrecorded_source_dependencies_block_import(self):
        for change in ({'external_or_unbound': ['outside.tex']}, {'status': 'NEEDS_REVIEW'}, {'files': []}):
            original = copy.deepcopy(self.report['dependency_audit'])
            self.report['dependency_audit'].update(change)
            self.save()
            with self.assertRaises(ContractError):
                validate_compilation(self.path)
            self.report['dependency_audit'] = original


if __name__ == '__main__':
    unittest.main()
