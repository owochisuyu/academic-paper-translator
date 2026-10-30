"""Dependency declarations never waive the visible TeX document or raw provenance."""
from pathlib import Path
import tempfile
import unittest

from scripts.source_coverage import validate_source_coverage


class TestProjectDependencies(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.main = self.root / 'paper.tex'
        self.main.write_text('The result is bounded.', encoding='utf-8')
        self.source = [{'id': 's1', 'kind': 'sentence', 'source': 'The result is bounded.',
                        'raw_source': 'The result is bounded.',
                        'locators': [{'file': 'paper.tex', 'start': 0, 'end': 22}]}]
        self.regions = [{'region_id': 'r1', 'role': 'text', 'disposition': 'translate',
                         'unit_ids': ['s1'], 'raw_text': 'The result is bounded.',
                         'locator': {'file': 'paper.tex', 'start': 0, 'end': 22}}]

    def dependency(self, name='refs.bib', role='bibliography_database'):
        text = '@book{unused, title={Unreferenced library item}}' if role == 'bibliography_database' else r'\AtBeginDocument{An injected visible notice.}'
        (self.root / name).write_text(text, encoding='utf-8')
        declaration = {'file': name, 'role': role, 'reason': 'Retained project dependency; visible bibliography comes from the selected source.'}
        self.regions.append({'region_id': 'dep', 'role': 'project_dependency', 'disposition': 'preserve',
                             'unit_ids': [], 'raw_text': text,
                             'locator': {'file': name, 'start': 0, 'end': len(text)},
                             'evidence': {'method': 'project-dependency', 'reason': declaration['reason']}})
        return declaration

    def check(self, declarations):
        return validate_source_coverage(self.source, self.main, self.regions, source_root=self.root,
                                        input_files=[{'path': path.name} for path in self.root.iterdir()],
                                        project_dependencies=declarations)

    def test_unused_bibliography_database_is_retained_without_becoming_prose(self):
        report = self.check([self.dependency()])
        self.assertEqual(report['status'], 'PASS', report)
        self.assertEqual(report['char_coverage_ratio'], 1.0)

    def test_style_content_is_not_certified_by_copying_its_file(self):
        report = self.check([self.dependency('local.sty', 'style')])
        self.assertEqual(report['status'], 'NEEDS_REVIEW', report)
        self.assertEqual(report['unverified_dependency_expansion'], ['local.sty'])

    def test_main_tex_cannot_be_declared_a_dependency(self):
        with self.assertRaisesRegex(ValueError, 'Invalid project dependency'):
            self.check([{'file': 'paper.tex', 'role': 'style', 'reason': 'Hide omitted prose'}])

    def test_dependency_must_be_an_actual_frozen_input(self):
        declaration = self.dependency()
        declaration['file'] = 'missing.bib'
        with self.assertRaisesRegex(ValueError, 'Invalid project dependency'):
            self.check([declaration])

    def test_dependency_region_requires_declaration_and_exact_raw_text(self):
        declaration = self.dependency()
        self.assertEqual(self.check([])['status'], 'FAIL')
        self.regions[-1]['raw_text'] += ' altered'
        self.assertEqual(self.check([declaration])['status'], 'FAIL')

    def test_declared_dependency_does_not_excuse_truncated_visible_source(self):
        declaration = self.dependency()
        self.source[0]['source'] = 'The result.'
        self.assertEqual(self.check([declaration])['status'], 'FAIL')


if __name__ == '__main__':
    unittest.main()
