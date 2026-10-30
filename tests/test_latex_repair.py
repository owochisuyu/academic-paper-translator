"""Real BibTeX lexical edge cases must not be read as TeX comments."""
from pathlib import Path
import tempfile
import unittest

from scripts.backends.latex_backend import (
    LatexParser, bibtex_group_end, bibtex_quoted_end, extract_project, group_end,
)


class TestBibliographyLexing(unittest.TestCase):
    def parse(self, text):
        with tempfile.TemporaryDirectory(prefix='bib tex 中文 ') as temporary:
            root = Path(temporary)
            path = root / 'references.bib'
            path.write_text(text, encoding='utf-8')
            parser = LatexParser(root)
            parser.parse_bibliography(path)
            return parser.items, parser.warnings

    def test_percent_encoded_url_does_not_swallow_same_line_closing_braces(self):
        text = r'''@inproceedings{first,
          Note = {Available from \url{https://example.org/Joan%20Sola/paper.pdf}},
          Title = {Delayed versus undelayed {SLAM}}, Year = {2005}}
        @article{second, Url = "https://example.org/a%2Fb?rate=10%25",
          Title = "A second study", Year = {2006}}
        '''
        items, warnings = self.parse(text)
        self.assertEqual([item['bibliography_key'] for item in items], ['first', 'second'])
        self.assertEqual([item['raw_source'] for item in items],
                         ['Delayed versus undelayed {SLAM}', 'A second study'])
        self.assertFalse(warnings)

    def test_quotes_within_braced_values_and_nested_braces_remain_data(self):
        text = r'''@article{k,
          title = {The "quoted" {{nested}} case},
          note = "A {protected "inner quote"} field at 20%", year={2024}}
        @article{next, title={Following entry}, year={2025}}'''
        items, _ = self.parse(text)
        self.assertEqual([item['bibliography_key'] for item in items], ['k', 'next'])
        self.assertEqual(items[0]['raw_source'], 'The "quoted" {{nested}} case')

    def test_escaped_quotes_and_percent_data_preserve_field_boundaries(self):
        text = r'''@article{k, note="An escaped \"quote\" and 30% value",
          title="Visible {case} title", year={2024}}'''
        items, _ = self.parse(text)
        self.assertEqual(items[0]['raw_source'], 'Visible {case} title')
        quoted = r'"a {"nested"} \"quote\" and 20%" trailing'
        end = bibtex_quoted_end(quoted, 0, len(quoted))
        self.assertEqual(quoted[end:], ' trailing')

    def test_real_unbalanced_entry_is_still_rejected(self):
        cases = [
            '@article{k, note={https://example.org/a%20b}, title={Missing outer brace}',
            '@article{k, title={Missing inner brace, year={2024}}',
            '@article{k, title="Unclosed quoted title, year={2024}}',
        ]
        for text in cases:
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, 'Unbalanced|Unclosed'):
                self.parse(text)

    def test_field_scanner_cannot_cross_its_entry_limit(self):
        text = '{unclosed field} next entry'
        with self.assertRaisesRegex(ValueError, 'Unbalanced bibliography braces'):
            bibtex_group_end(text, 0, limit=text.index('}'))

    def test_actual_tex_comments_still_hide_braces_to_end_of_line(self):
        text = '{kept % a } brace in a comment\nrest} trailing'
        self.assertEqual(text[group_end(text, 0):], ' trailing')
        with self.assertRaisesRegex(ValueError, 'Unbalanced'):
            group_end('{kept % only closing brace is commented }', 0)


class TestBibliographySelection(unittest.TestCase):
    def project(self, body, bibliography, bbl=None, extra_files=None):
        temporary = tempfile.TemporaryDirectory(prefix='selected bibliography 中文 ')
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        main = root / 'main.tex'
        main.write_text('\\documentclass{article}\n\\begin{document}\n' + body +
                        '\n\\bibliography{references}\n\\end{document}', encoding='utf-8')
        (root / 'references.bib').write_text(bibliography, encoding='utf-8')
        if bbl is not None:
            (root / 'main.bbl').write_text(bbl, encoding='utf-8')
        for name, content in (extra_files or {}).items():
            (root / name).write_text(content, encoding='utf-8')
        return extract_project(main)

    def test_compiled_bibliography_supplies_visible_entries_not_database_titles(self):
        result = self.project(r'The method works \cite{used}.',
            '@article{used, title={Database version}}\n@article{unused, title={Not in the paper}}',
            r'\begin{thebibliography}{1}\bibitem{used}A. Author, \emph{Printed title}, 2024.\end{thebibliography}')
        references = [i for i in result['items'] if i['kind'] == 'reference']
        self.assertEqual([i['bibliography_key'] for i in references], ['used'])
        self.assertIn('Printed title', references[0]['raw_source'])
        self.assertFalse(any('Not in the paper' in i['raw_source'] for i in result['items']))
        self.assertEqual(result['document']['bibliography_selection']['method'], 'compiled_bbl')
        self.assertTrue(any(Path(p).name == 'references.bib' for p in result['document']['input_files']))
        self.assertTrue(any(r['role'] == 'project_dependency' and r['locator']['file'] == 'references.bib'
                            for r in result['regions']))

    def test_literal_citation_selects_only_used_title_after_hyphenated_metadata(self):
        result = self.project(r'The method works \cite{used}.',
            '@article{used, Date-Added={2020-01-01}, title={Selected title}, year={2024}}\n'
            '@article{unused, title={Not selected}, year={2025}}')
        references = [i for i in result['items'] if i['kind'] == 'reference']
        self.assertEqual([i['raw_source'] for i in references], ['Selected title'])
        self.assertEqual(result['document']['bibliography_selection']['selected_keys'], ['used'])
        self.assertEqual([w['code'] for w in result['warnings']], ['compiled_bibliography_unavailable'])

    def test_title_only_fallback_cannot_pass_printed_bibliography_validation(self):
        from scripts.document_ir import atomic_write_jsonl, create_job
        from scripts.validate_translation import validate_job

        result = self.project(r'Claim \cite{used}.',
            '@article{used,author={A. Author},title={Selected title},journal={Research Journal},year={2024}}')
        selection = result['document']['bibliography_selection']
        self.assertEqual(selection['selected_fields'], ['title'])
        self.assertFalse(selection['rendering_verified'])
        root = Path(result['document']['source_root'])
        job = create_job(root / 'main.tex', root / 'job', result['items'], regions=result['regions'],
                         summary={'warnings': result['warnings']}, extra_document=result['document'])
        atomic_write_jsonl(job['root'] / 'translations.jsonl', [])
        extraction = validate_job(job['root'])['checks']['extraction']
        self.assertEqual(extraction['char_coverage_ratio'], 1.0)
        self.assertEqual(extraction['status'], 'NEEDS_REVIEW')
        self.assertIn('compiled_bibliography_unavailable',
                      [warning['code'] for warning in extraction['parser_warnings']])

    def test_missing_citation_and_stale_compiled_bibliography_are_reported(self):
        for bbl in (None, r'\begin{thebibliography}{1}\bibitem{other}Unrelated entry.\end{thebibliography}'):
            with self.subTest(bbl=bbl):
                result = self.project(r'Claim \cite{missing}.', '@article{other,title={Another title}}', bbl)
                self.assertTrue(any(w['code'] == 'missing_cited_bibliography_entries' for w in result['warnings']))

    def test_existing_compiled_bibliography_is_read_even_when_database_is_missing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'main.tex').write_text(r'\documentclass{article}\begin{document}'
                r'Claim \cite{used}.\bibliography{missing}\end{document}', encoding='utf-8')
            (root / 'main.bbl').write_text(r'\begin{thebibliography}{1}'
                r'\bibitem{used}Printed entry.\end{thebibliography}', encoding='utf-8')
            result = extract_project(root / 'main.tex')
        self.assertEqual(result['document']['bibliography_selection']['selected_keys'], ['used'])
        self.assertTrue(any(w['code'] == 'missing_bibliography' for w in result['warnings']))

    def test_group_following_citation_does_not_replace_its_keys(self):
        result = self.project(r'Claim \cite{used}{following text}.',
            '@article{used,title={Selected}}\n@article{unused,title={Not selected}}')
        self.assertEqual(result['document']['bibliography_selection']['citation_keys'], ['used'])

    def test_dynamic_or_unknown_citation_does_not_select_whole_database(self):
        for citation in (r'\cite{\selectedKey}', r'\customcite{used}'):
            with self.subTest(citation=citation):
                result = self.project('Claim ' + citation + '.', '@article{used,title={Possible title}}')
                self.assertFalse(any(i['kind'] == 'reference' for i in result['items']))
                self.assertTrue(result['document']['bibliography_selection']['uncertain'])
                self.assertTrue(any(w['code'] == 'citation_scope_requires_review' for w in result['warnings']))

    def test_nocite_all_is_an_explicit_full_database_request(self):
        result = self.project(r'\nocite{*}', '@article{a,title={First}}\n@article{b,title={Second}}')
        self.assertEqual([i['bibliography_key'] for i in result['items'] if i['kind'] == 'reference'], ['a', 'b'])
        self.assertTrue(result['document']['bibliography_selection']['nocite_all'])

    def test_comments_do_not_create_citation_or_dependency_scope(self):
        result = self.project('% \\cite{unused}\n% \\bibliography{missing}\nClaim \\cite{used}.',
            '@article{used,title={Selected}}\n@article{unused,title={Not selected}}')
        self.assertEqual(result['document']['bibliography_selection']['citation_keys'], ['used'])
        self.assertEqual([w['code'] for w in result['warnings']], ['compiled_bibliography_unavailable'])

    def test_citations_in_included_files_are_collected_before_database_selection(self):
        result = self.project(r'\input{part}', '@article{used,title={Included citation}}',
                              extra_files={'part.tex': r'Claim \cite{used}.'})
        self.assertEqual([i['bibliography_key'] for i in result['items'] if i['kind'] == 'reference'], ['used'])

    def test_literal_code_does_not_add_database_entries(self):
        result = self.project(r'\begin{verbatim}\cite{unused}\end{verbatim}' +
                              r'Claim \cite{used}.',
                              '@article{used,title={Selected}}\n@article{unused,title={Not selected}}')
        self.assertEqual(result['document']['bibliography_selection']['citation_keys'], ['used'])
        self.assertEqual([i['bibliography_key'] for i in result['items'] if i['kind'] == 'reference'], ['used'])

    def test_unknown_bbl_layout_remains_a_review_item(self):
        result = self.project(r'Claim \cite{used}.', '@article{used,title={Original}}',
            r'\begin{thebibliography}{1}\bibitem{used}\mystery{Printed title}.\end{thebibliography}')
        self.assertTrue(any(w['code'] == 'unsupported_macro' and w.get('command') == 'mystery'
                            for w in result['warnings']))

    def test_macro_title_and_concatenation_remain_unresolved(self):
        for value in ('customTitle', '"Partial " # "remainder"'):
            with self.subTest(value=value):
                result = self.project(r'Claim \cite{used}.', '@article{used,title=' + value + '}')
                self.assertFalse(any(i['kind'] == 'reference' for i in result['items']))
                self.assertTrue(any(w['code'] == 'bibliography_expression_requires_review' for w in result['warnings']))


if __name__ == '__main__':
    unittest.main()
