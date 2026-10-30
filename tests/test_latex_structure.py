"""Source boundaries must follow TeX syntax rather than split visible content."""
from pathlib import Path
import re
import tempfile
import unittest

from scripts.backends.latex_backend import extract_project, restore_translation, export_project, uncomment_tex
from scripts.backends.latex_lex import command_at, group_end, math_span_end, find_command_start
from scripts.extract_document import extract_document
from scripts.document_ir import load_job


class TestLatexStructure(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def parse(self, body, extra=None):
        path = self.root / 'main.tex'
        path.write_text(r'\documentclass{article}\begin{document}' + body + r'\end{document}', encoding='utf-8')
        for name, data in (extra or {}).items():
            (self.root / name).write_text(data, encoding='utf-8')
        result = extract_project(path)
        # Exact raw intervals survive the new lexer, even inside table/math text.
        for item in result['items']:
            if item.get('empty_cell'):
                continue
            locator = item['locators'][0]
            raw = (self.root / locator['file']).read_text(encoding='utf-8')[locator['start']:locator['end']]
            self.assertEqual(item['raw_source'], raw)
            if item.get('action') != 'preserve':
                restored = restore_translation(item, item['source'])
                self.assertEqual(re.sub(r'\s+', '', restored), re.sub(r'\s+', '', raw))
        return result

    def test_command_arity_does_not_consume_the_following_group(self):
        for raw, expected in ((r'\section[Short]{Method}{Body.}', r'\section[Short]{Method}'),
                              (r'\textbf{First}{Second}', r'\textbf{First}'),
                              (r'\cite{key}{Following}', r'\cite{key}'),
                              (r'\centering{Visible text}', r'\centering'),
                              (r'\begin{quote}{Visible text}', r'\begin{quote}')):
            with self.subTest(raw=raw):
                self.assertEqual(raw[:command_at(raw, 0)[1]], expected)
        result = self.parse(r'\section{Method}{Following body text.}')
        self.assertEqual(next(i['source'] for i in result['items'] if i['kind'] == 'heading'), 'Method')
        self.assertTrue(any('Following body text.' in i['source'] for i in result['items']))

    def test_nested_optional_braces_do_not_end_argument_early(self):
        raw = r'[label={x]y},width=5pt]'
        self.assertEqual(group_end(raw, 0, '[', ']'), len(raw))
        raw = '\\section% ignored brace }\n[short]{Full title}{Next body}'
        self.assertEqual(raw[:command_at(raw, 0)[1]], raw[:raw.index('{Next body}')])

    def test_inline_math_commands_cannot_flush_a_paragraph(self):
        for math in (r'$x\label{eq:x}\text{if }y$', r'\(x\label{eq:x}\text{if }y\)'):
            with self.subTest(math=math):
                result = self.parse('The estimate ' + math + ' remains bounded.')
                sentences = [i for i in result['items'] if i['kind'] == 'sentence']
                self.assertEqual(len(sentences), 1)
                self.assertIn('if ', sentences[0]['source'])
                self.assertTrue(any(r'\label{eq:x}' in token for token in sentences[0]['protected_tokens'].values()))
                self.assertFalse(result['warnings'])

    def test_math_closing_delimiters_respect_escapes_and_comments(self):
        for raw in (r'$a\$b$', r'$a\\$', '$x % $ in comment\n+y$',
                    '\\(x % \\) in comment\n+y\\)'):
            with self.subTest(raw=raw):
                self.assertEqual(math_span_end(raw, 0), len(raw))
        with self.assertRaisesRegex(ValueError, 'Unclosed math'):
            math_span_end(r'$x\$', 0)

    def test_false_environment_ends_in_comments_and_literal_code_are_ignored(self):
        result = self.parse('\\begin{quote}\nFirst. % \\end{quote}\n'
                            r'\verb|\end{quote}| Second.\begin{verbatim}\end{quote}\end{verbatim}'
                            r'Third.\end{quote}After.')
        joined = ' '.join(i['source'] for i in result['items'])
        self.assertIn('Second.', joined)
        self.assertIn('Third.', joined)
        self.assertIn('After.', joined)
        self.assertFalse(result['warnings'])

    def test_table_matrix_delimiters_and_commented_rows_are_not_outer_cells(self):
        body = (r'\begin{tabular}{lll}\multicolumn{2}{c}{Visible heading} & Final\\[2pt]' '\n'
                '% hidden & fake & row \\\\\n'
                r'$M=\begin{bsmallmatrix}a&b\\c&d\end{bsmallmatrix}$ & B & C\\' '\n'
                '% another & omitted \\\\\n' r'\bottomrule\end{tabular}')
        result = self.parse(body)
        cells = [i for i in result['items'] if i['kind'] == 'table_cell']
        self.assertEqual(len(cells), 5)
        self.assertEqual({(c['table_rows'], c['table_cols']) for c in cells}, {(2, 3)})
        self.assertEqual([(c['row'], c['col'], c['colspan']) for c in cells],
                         [(0, 0, 2), (0, 2, 1), (1, 0, 1), (1, 1, 1), (1, 2, 1)])
        self.assertIn('Visible heading', cells[0]['source'])
        self.assertFalse(any('hidden' in c['source'] or 'omitted' in c['source'] for c in cells))
        self.assertTrue(any('bsmallmatrix' in token for token in cells[2]['protected_tokens'].values()))
        self.assertFalse(result['warnings'])

    def test_literal_delimiters_in_table_cells_do_not_create_rows(self):
        result = self.parse(r'\begin{tabular}{ll}\verb|x&y\\| & Visible\\\end{tabular}')
        cells = [i for i in result['items'] if i['kind'] == 'table_cell']
        self.assertEqual(len(cells), 2)
        self.assertEqual({(c['table_rows'], c['table_cols']) for c in cells}, {(1, 2)})
        self.assertFalse(result['warnings'])

    def test_inconsistent_or_dynamic_table_spans_remain_review_items(self):
        for body, warning in ((r'\begin{tabular}{ll}A&B\\C\\\end{tabular}', 'inconsistent_table_rows'),
                              (r'\begin{tabular}{ll}\multicolumn{\n}{c}{Heading} & B\\\end{tabular}', 'unsupported_table_span')):
            with self.subTest(body=body):
                result = self.parse(body)
                self.assertIn(warning, [w['code'] for w in result['warnings']])

    def test_subequations_and_appendices_expose_real_text_and_includes(self):
        result = self.parse(r'\begin{subequations}A relation follows.'
                            r'\begin{equation}x=y\end{equation}\end{subequations}'
                            r'\begin{appendices}\input{appendix}\end{appendices}',
                            {'appendix.tex': r'\section{Proof}The omitted appendix is visible.\cite{appkey}'})
        self.assertTrue(any('omitted appendix is visible' in i['source'] for i in result['items']))
        self.assertTrue(any(i['kind'] == 'equation' for i in result['items']))
        self.assertFalse(any(w['code'] == 'unsupported_environment' for w in result['warnings']))

    def test_footnote_text_is_a_translation_unit(self):
        result = self.parse(r'A claim\footnotemark\ remains.\footnotetext{Only under calibration.}')
        self.assertEqual([i['source'] for i in result['items'] if i['kind'] == 'footnote'],
                         ['Only under calibration.'])
        self.assertFalse(result['warnings'])

    def test_bbl_setup_is_not_a_fake_reference_and_glue_is_protected(self):
        bbl = (r'\begin{thebibliography}{1}\providecommand{\newblock}{\relax}'
               r'\csname url@samestyle\endcsname\bibitem{one}'
               r'A. Author, \emph{A title}.\hskip 1em plus 0.5em minus 0.4em\relax Publisher.'
               r'\end{thebibliography}')
        result = self.parse(r'Claim \cite{one}.\bibliography{refs}',
                            {'main.bbl': bbl, 'refs.bib': '@book{one,title={A title}}'})
        references = [i for i in result['items'] if i['kind'] == 'reference']
        self.assertEqual(len(references), 1)
        self.assertEqual(references[0]['bibliography_key'], 'one')
        self.assertNotIn('0.5em', references[0]['source'])
        self.assertTrue(any('0.5em' in s for s in references[0]['protected_tokens'].values()))
        self.assertTrue(any(w['code'] == 'unsupported_bibliography_setup' for w in result['warnings']))

    def test_bibitem_boundary_ignores_comments_escapes_literals_and_definitions(self):
        for prefix in ('% \\bibitem{fake}Commented reference.\n',
                       r'\\bibitem{fake}', r'\verb|\bibitem{fake}|',
                       r'\begin{verbatim}\bibitem{fake}\end{verbatim}',
                       r'\providecommand{\example}{\bibitem{fake}Hidden reference.}'):
            with self.subTest(prefix=prefix):
                raw = prefix + r'\bibitem{real}Actual reference.'
                self.assertEqual(find_command_start(raw, 0, len(raw), 'bibitem'), len(prefix))

    def test_commented_bibitem_is_not_a_reference_and_export_keeps_comment_boundary(self):
        body = ('\\begin{thebibliography}{2}\n'
                '% \\bibitem{preamblefake}Not a printed reference.\n'
                '\\bibitem{real}Actual reference.\n'
                '% \\bibitem{fake}Hidden reference.\nStill actual.\n'
                '\\bibitem{next}Next reference.\n\\end{thebibliography}')
        result = self.parse(body)
        references = [i for i in result['items'] if i['kind'] == 'reference']
        self.assertEqual([i['bibliography_key'] for i in references], ['real', 'next'])
        self.assertIn('Actual reference.', references[0]['source'])
        self.assertIn('Still actual.', references[0]['source'])
        self.assertNotIn('Hidden reference.', references[0]['source'])
        self.assertFalse(result['warnings'])

        main = self.root / 'main.tex'
        original = main.read_text(encoding='utf-8')
        extract_document(main, job_dir=self.root / 'job')
        job = load_job(self.root / 'job')
        replacements = {'Actual reference.': '实际参考文献。', 'Still actual.': '仍属该条。'}
        merged = []
        for item in job['source']:
            translation = item['source']
            for before, after in replacements.items():
                translation = translation.replace(before, after)
            merged.append({**item, 'translation': translation})
        output = export_project({**job, 'merged': merged}, self.root / 'translated')
        translated = (output / 'main.tex').read_text(encoding='utf-8')
        expected = original
        for before, after in replacements.items():
            expected = expected.replace(before, after)
        self.assertIn('% \\bibitem{fake}Hidden reference.\n仍属该条。', translated)
        self.assertEqual(re.sub(r'\s+', '', uncomment_tex(translated)),
                         re.sub(r'\s+', '', uncomment_tex(expected)))

    def test_unknown_commands_and_truncated_math_still_block(self):
        result = self.parse(r'The \unrecognized{condition} applies. A malformed $x remains.')
        self.assertIn('unsupported_macro', [w['code'] for w in result['warnings']])
        self.assertIn('unclosed_inline_math', [w['code'] for w in result['warnings']])

    def test_display_math_exposes_intertext_and_text_labels_without_touching_operators(self):
        result = self.parse(r'\begin{align}x&=y\quad\textrm{Innovation}\\'
                            r'\intertext{while rotation matrices use a single matrix product.}'
                            r'K&=P\quad\text{Kalman gain}\\'
                            r'\shortintertext{State update}'
                            r'z&=\mathrm{SO}(3)\quad\mbox{Closure}\end{align}')
        labels = [i for i in result['items'] if i.get('role') == 'math_text']
        self.assertEqual([i['source'] for i in labels], ['Innovation',
                         'while rotation matrices use a single matrix product.',
                         'Kalman gain', 'State update', 'Closure'])
        equation = next(i for i in result['items'] if i['kind'] == 'equation')
        self.assertEqual(equation['action'], 'preserve')
        self.assertEqual({i['parent_id'] for i in labels}, {equation['id']})
        self.assertFalse(result['warnings'])

    def test_display_math_font_commands_expose_visible_text(self):
        for command in ('textbf', 'textit', 'texttt', 'textsc', 'textup', 'textnormal',
                        'textrm', 'textsf', 'textmd', 'textsl', 'emph', 'underline'):
            with self.subTest(command=command):
                result = self.parse('\\begin{equation}x=\\' + command
                                    + r'{Experiment A}\quad\mathrm{SO}(3)\end{equation}')
                labels = [i for i in result['items'] if i.get('role') == 'math_text']
                self.assertEqual([i['source'] for i in labels], ['Experiment A'])
                self.assertEqual(labels[0]['action'], 'translate')
                self.assertFalse(result['warnings'])
                raw = '\\' + command + '{Experiment A}{Following group}'
                self.assertEqual(raw[:command_at(raw, 0)[1]], '\\' + command + '{Experiment A}')

    def test_nested_font_commands_have_one_translation_edit(self):
        body = r'\begin{equation}x=\textbf{Experiment \textsc{A}}\end{equation}'
        result = self.parse(body)
        labels = [i for i in result['items'] if i.get('role') == 'math_text']
        self.assertEqual(len(labels), 1)
        self.assertIn('Experiment ', labels[0]['source'])
        self.assertIn('A', labels[0]['source'])
        self.assertFalse(result['warnings'])
        main = self.root / 'main.tex'
        original = main.read_text(encoding='utf-8')
        extract_document(main, job_dir=self.root / 'job')
        job = load_job(self.root / 'job')
        merged = [{**item, 'translation': item['source'].replace('Experiment ', '实验 ')}
                  for item in job['source'] if item['action'] == 'translate']
        output = export_project({**job, 'merged': merged}, self.root / 'translated')
        translated = (output / 'main.tex').read_text(encoding='utf-8')
        self.assertEqual(translated, original.replace('Experiment ', '实验 '))

    def test_math_text_export_patches_only_nonoverlapping_text_arguments(self):
        raw = (r'\documentclass{article}\begin{document}'
               r'\begin{align}x&=y\quad\text{Innovation}\\'
               r'\intertext{Apply the correction.}z&=x+1\end{align}'
               r'\end{document}')
        main = self.root / 'main.tex'
        main.write_text(raw, encoding='utf-8')
        extract_document(main, job_dir=self.root / 'job')
        job = load_job(self.root / 'job')
        translations = {'Innovation': '新息', 'Apply the correction.': '应用改正。'}
        merged = [{**item, 'translation': translations.get(item['source'], '')} for item in job['source']]
        output = export_project({**job, 'merged': merged}, self.root / 'translated')
        translated = (output / 'main.tex').read_text(encoding='utf-8')
        self.assertEqual(translated, raw.replace('Innovation', '新息').replace('Apply the correction.', '应用改正。'))

    def test_inline_math_text_preserves_syntax_and_translates_in_its_parent_sentence(self):
        result = self.parse(r'The identity is $x+0=x\quad\textrm{Identity}$ and remains valid.')
        sentences = [i for i in result['items'] if i['kind'] == 'sentence']
        self.assertEqual(len(sentences), 1)
        item = sentences[0]
        self.assertIn('Identity', item['source'])
        translation = item['source'].replace('Identity', '单位元')
        self.assertEqual(restore_translation(item, translation), item['raw_source'].replace('Identity', '单位元'))
        self.assertFalse(result['warnings'])

    def test_nonbreaking_space_is_preserved_as_tex_syntax(self):
        result = self.parse(r'The result is in Fig.~\ref{fig:one}.')
        item = next(i for i in result['items'] if 'result' in i['source'])
        self.assertIn('~', item['protected_tokens'].values())
        self.assertEqual(restore_translation(item, item['source']), item['raw_source'])

    def test_comment_terminator_survives_source_export(self):
        result = self.parse('The estimate % a comment\nremains bounded.')
        item = next(i for i in result['items'] if 'estimate' in i['source'])
        self.assertIn('% a comment\n', item['protected_tokens'].values())
        restored = restore_translation(item, item['source'])
        self.assertEqual(restored, item['raw_source'])
        self.assertIn('\nremains bounded.', restored)

    def test_nested_math_text_labels_have_one_edit_span_and_punctuation_stays_in_math(self):
        result = self.parse(r'\begin{equation}x=\text{Case \textbf{One}}\quad\text{,}\end{equation}')
        labels = [i for i in result['items'] if i.get('role') == 'math_text']
        self.assertEqual(len(labels), 1)
        self.assertIn('Case ', labels[0]['source'])
        self.assertIn('One', labels[0]['source'])
        self.assertFalse(result['warnings'])


if __name__ == '__main__':
    unittest.main()
