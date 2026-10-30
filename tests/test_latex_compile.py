"""Compile reports require fresh output and preserve actual source bytes."""
from pathlib import Path
from types import SimpleNamespace
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import pymupdf

from scripts.compile_latex_source import compile_project, classify_log, audit_project_dependencies
from scripts.document_ir import canonical_hash, file_hash


class TestLatexCompile(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        (self.project / 'main.tex').write_text(r'\documentclass{article}\begin{document}Source.\end{document}')
        self.engine = self.root / 'tectonic.exe'
        self.engine.write_bytes(b'test engine identity')

    def runner(self, *, code=0, produce_pdf=True, tex_log='Compilation complete.', timeout=False, mutate_run=False):
        def run(command, **kwargs):
            if command[-1] == '--version':
                return SimpleNamespace(returncode=0, stdout=b'Tectonic 0.17.0\n', stderr=b'')
            self.assertIn('--untrusted', command)
            self.assertEqual(kwargs['env']['TECTONIC_UNTRUSTED_MODE'], '1')
            if timeout:
                raise subprocess.TimeoutExpired(command, kwargs['timeout'], output=b'Partial output')
            build = Path(command[command.index('--outdir') + 1])
            (build / 'main.log').write_text(tex_log)
            (build / 'dependencies.mk').write_text('main.pdf : main.tex\n')
            if produce_pdf:
                with pymupdf.open() as document:
                    page = document.new_page()
                    page.insert_text((30, 30), 'Source.')
                    document.save(build / 'main.pdf')
            if mutate_run:
                (Path(kwargs['cwd']) / 'main.tex').write_text('Changed by compiler')
            return SimpleNamespace(returncode=code, stdout=b'Done', stderr=b'')
        return run

    def compile(self, **kwargs):
        return compile_project(self.project, 'main.tex', self.root / 'output', self.engine, **kwargs)

    def test_success_is_bound_to_immutable_snapshot_and_fresh_pdf(self):
        original = file_hash(self.project / 'main.tex')
        with patch('scripts.compile_latex_source.subprocess.run', side_effect=self.runner()) as process:
            report = self.compile()
        self.assertEqual(report['status'], 'PASS')
        self.assertEqual(report['exit_code'], 0)
        self.assertTrue(report['compile_complete'])
        self.assertEqual(report['pdf']['pages'], 1)
        self.assertEqual(report['pdf']['sha256'], file_hash(self.root / 'output' / report['pdf']['path']))
        self.assertEqual(report['input_set_hash'], canonical_hash(report['source_snapshot']['files']))
        self.assertEqual(original, file_hash(self.project / 'main.tex'))
        self.assertIn('--only-cached', report['engine']['command'])
        self.assertNotEqual(Path(process.call_args.kwargs['cwd']), self.project)

    def test_exit_zero_without_pdf_is_not_complete(self):
        with patch('scripts.compile_latex_source.subprocess.run', side_effect=self.runner(produce_pdf=False)):
            report = self.compile()
        self.assertEqual(report['status'], 'FAIL')
        self.assertFalse(report['compile_complete'])
        self.assertIsNone(report['pdf'])

    def test_nonzero_with_partial_pdf_remains_failure(self):
        with patch('scripts.compile_latex_source.subprocess.run', side_effect=self.runner(code=1)):
            report = self.compile()
        self.assertEqual(report['status'], 'FAIL')
        self.assertFalse(report['compile_complete'])

    def test_timeout_records_failure_and_partial_chatter(self):
        with patch('scripts.compile_latex_source.subprocess.run', side_effect=self.runner(timeout=True)):
            report = self.compile(timeout=1)
        self.assertEqual(report['status'], 'FAIL')
        self.assertFalse(report['compile_complete'])
        self.assertIn('compile_timeout', [w['code'] for w in report['warnings']])
        self.assertEqual((self.root / 'output/stdout.log').read_bytes(), b'Partial output')

    def test_unresolved_references_and_missing_glyphs_require_review(self):
        with patch('scripts.compile_latex_source.subprocess.run', side_effect=self.runner(
                tex_log="LaTeX Warning: Reference `missing' undefined on input line 5.\nMissing character: There is no test glyph!")):
            report = self.compile()
        self.assertEqual(report['status'], 'NEEDS_REVIEW')
        self.assertTrue(report['compile_complete'])
        self.assertEqual({w['code'] for w in report['warnings']}, {'undefined_references', 'missing_character'})

    def test_changed_runtime_input_cannot_pass(self):
        with patch('scripts.compile_latex_source.subprocess.run', side_effect=self.runner(mutate_run=True)):
            report = self.compile()
        self.assertEqual(report['status'], 'NEEDS_REVIEW')
        self.assertIn('compiled_source_changed', [w['code'] for w in report['warnings']])
        self.assertEqual((self.root / 'output/source/main.tex').read_bytes(), (self.project / 'main.tex').read_bytes())

    def test_missing_engine_is_not_run(self):
        report = compile_project(self.project, 'main.tex', self.root / 'output', self.root / 'absent.exe')
        self.assertEqual(report['status'], 'NOT_RUN')
        self.assertFalse(report['compile_complete'])

    def test_existing_output_and_outside_main_are_rejected(self):
        for main, output in [('main.tex', self.project / 'output'), ('../outside.tex', self.root / 'output')]:
            with self.subTest(main=main), self.assertRaises(ValueError):
                compile_project(self.project, main, output, self.engine)
        (self.root / 'output').mkdir()
        with self.assertRaises(ValueError):
            self.compile()

    def test_adapted_source_keeps_original_manifest_and_diff(self):
        original = self.root / 'original'
        original.mkdir()
        (original / 'main.tex').write_text('Old driver selection.')
        with patch('scripts.compile_latex_source.subprocess.run', side_effect=self.runner()):
            report = self.compile(original_project=original, adaptation_reason='Explicit compatibility adjustment for tested engine.')
        change = report['adaptations'][0]
        self.assertTrue(change['reason'])
        self.assertNotEqual(change['before_sha256'], change['after_sha256'])
        self.assertTrue((self.root / 'output' / change['patch_path']).is_file())

    def test_layout_warning_does_not_claim_translation_validation(self):
        warnings = classify_log(r'Overfull \hbox (2pt too wide) in paragraph at lines 2--5')
        self.assertEqual(warnings[0]['code'], 'overfull_box')
        self.assertFalse(warnings[0]['blocking'])

    def test_missing_chapter_is_blocking_despite_successful_engine_exit(self):
        with patch('scripts.compile_latex_source.subprocess.run', side_effect=self.runner(
                tex_log='No file missing-chapter.tex.\nNo file main.aux.')):
            report = self.compile()
        self.assertEqual(report['status'], 'NEEDS_REVIEW')
        self.assertIn('missing_source_file', [w['code'] for w in report['warnings']])

    def test_external_dependency_cannot_be_attested_by_project_hashes(self):
        with patch('scripts.compile_latex_source.subprocess.run', side_effect=self.runner()):
            report = self.compile()
        output = self.root / 'output'
        external = self.root / 'outside.tex'
        external.write_text('Content not frozen in selected source project.')
        (output / 'build/dependencies.mk').write_text(f'main.pdf : main.tex \\\n  {external}\n')
        audited = audit_project_dependencies(output, report['source_snapshot'])
        self.assertEqual(audited['status'], 'NEEDS_REVIEW')
        self.assertEqual(audited['external_or_unbound'], [str(external)])

    def test_font_substitution_requires_review(self):
        warnings = classify_log("LaTeX Font Warning: Font shape `TU/ptm/m/n' undefined\n")
        self.assertEqual(warnings[0]['code'], 'font_substitution')
        self.assertTrue(warnings[0]['blocking'])


if __name__ == '__main__':
    unittest.main()
