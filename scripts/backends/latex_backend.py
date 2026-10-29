"""Bounded LaTeX source parser and source-project export/compile helpers.

Unsupported macros are kept verbatim and reported. This is intentionally not a TeX
interpreter: expansion-dependent text requires review instead of silent omission.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

from .common import digest, normalize_with_map, register_syntax_gaps
try:
    from ..align_bilingual import split_en_sentences
except ImportError:
    from align_bilingual import split_en_sentences

MATH_ENVIRONMENTS = {'equation', 'equation*', 'align', 'align*', 'alignat', 'alignat*', 'gather', 'gather*', 'multline', 'multline*', 'displaymath', 'math'}
TEXT_COMMANDS = {'section', 'subsection', 'subsubsection', 'chapter', 'paragraph', 'title', 'caption', 'footnote'}
INLINE_PROTECTED = {'cite', 'citep', 'citet', 'ref', 'eqref', 'pageref', 'label', 'url', 'includegraphics', 'ensuremath'}
FORMATS = {'textbf', 'textit', 'emph', 'textrm', 'textsf', 'texttt', 'underline'}
LAYOUT = {'centering', 'hline', 'toprule', 'midrule', 'bottomrule', 'small', 'footnotesize', 'noindent', 'maketitle', 'clearpage', 'newpage'}


def group_end(text, start, opener='{', closer='}'):
    if start >= len(text) or text[start] != opener:
        raise ValueError(f'Expected {opener} at offset {start}')
    depth, index = 1, start + 1
    while index < len(text):
        if text[index] == '\\':
            index += 2
            continue
        if text[index] == '%':
            newline = text.find('\n', index)
            index = len(text) if newline < 0 else newline + 1
            continue
        if text[index] == opener:
            depth += 1
        elif text[index] == closer:
            depth -= 1
            if not depth:
                return index + 1
        index += 1
    raise ValueError(f'Unbalanced {opener}{closer} starting at {start}')


def command_at(text, index):
    match = re.match(r'\\([A-Za-z]+\*?|.)', text[index:], re.S)
    if not match:
        return None
    name, cursor = match.group(1), index + len(match.group())
    args = []
    while cursor < len(text):
        scan = cursor
        while scan < len(text) and text[scan].isspace():
            scan += 1
        if scan >= len(text) or text[scan] not in '[{':
            break
        left = text[scan]
        end = group_end(text, scan, left, ']' if left == '[' else '}')
        args.append((left, scan + 1, end - 1))
        cursor = end
    return name, cursor, args


def _required(args):
    return [arg for arg in args if arg[0] == '{']


def _environment_end(text, start, name, body_start):
    token = re.compile(r'\\(begin|end)\s*\{' + re.escape(name) + r'\}')
    depth = 1
    for match in token.finditer(text, body_start):
        if text[max(0, match.start() - 1):match.start()] == '\\':
            continue
        depth += 1 if match.group(1) == 'begin' else -1
        if not depth:
            return match.start(), match.end()
    raise ValueError(f'Unclosed LaTeX environment {name} at {start}')


class LatexParser:
    def __init__(self, root):
        self.root = Path(root)
        self.items, self.regions, self.assets, self.warnings = [], [], [], []
        self.files, self.active = set(), set()
        self.section = 'Title & Abstract'
        self.sequence, self.section_count, self.reference_count, self.table_count, self.figure_count = 0, 0, 0, 0, 0
        self.current_table = None
        self.current_figure = None

    def warn(self, code, path, start, **extra):
        self.warnings.append({'code': code, 'file': Path(path).relative_to(self.root).as_posix(), 'offset': start, **extra})

    def protect(self, raw, path, offset):
        output, positions, protected = [], [], {}
        def plain(value, start, end):
            output.append(value)
            positions.extend([(start + j, start + j + 1) for j in range(len(value))] if len(value) == end - start else [(start, end)] * len(value))
        def placeholder(value, start, end):
            key = f'⟦TEX{len(protected) + 1:06d}⟧'
            protected[key] = value
            output.append(key)
            positions.extend([(start, end)] * len(key))
        index = 0
        while index < len(raw):
            if raw[index] == '%':
                end = raw.find('\n', index)
                end = len(raw) if end < 0 else end
                placeholder(raw[index:end], index, end)
                index = end
            elif raw[index] == '$':
                delim = '$$' if raw.startswith('$$', index) else '$'
                end = raw.find(delim, index + len(delim))
                while end > 0 and raw[end - 1] == '\\':
                    end = raw.find(delim, end + len(delim))
                if end < 0:
                    self.warn('unclosed_inline_math', path, offset + index)
                    end = len(raw)
                else:
                    end += len(delim)
                placeholder(raw[index:end], index, end)
                index = end
            elif raw[index] == '\\':
                cmd = command_at(raw, index)
                if cmd is None:
                    plain(raw[index], index, index + 1)
                    index += 1
                    continue
                name, end, args = cmd
                req = _required(args)
                if (name in FORMATS and req) or (name == 'href' and len(req) == 2):
                    # The visible href label is prose; only its URL and command
                    # wrapper are protected from translation.
                    _, arg_start, arg_end = req[-1] if name == 'href' else req[0]
                    placeholder(raw[index:arg_start], index, arg_start)
                    # Nested known formatting is retained in a protected inner span if needed.
                    inner, inner_positions, inner_tokens = self.protect(raw[arg_start:arg_end], path, offset + arg_start)
                    remap = {}
                    for key, value in inner_tokens.items():
                        fresh = f'⟦TEX{len(protected) + 1:06d}⟧'
                        remap[key] = fresh
                        protected[fresh] = value
                    inner = re.sub(r'⟦TEX\d{6}⟧', lambda m: remap[m.group()], inner)
                    output.append(inner)
                    positions.extend([(a + arg_start, b + arg_start) for a, b in inner_positions])
                    placeholder(raw[arg_end:end], arg_end, end)
                    index = end
                elif name == '(':
                    close = raw.find('\\)', end)
                    if close < 0:
                        self.warn('unclosed_inline_math', path, offset + index)
                        close = len(raw) - 2
                    end = close + 2
                    placeholder(raw[index:end], index, end)
                    index = end
                elif name in INLINE_PROTECTED or name in LAYOUT or not name[0].isalpha():
                    placeholder(raw[index:end], index, end)
                    index = end
                else:
                    placeholder(raw[index:end], index, end)
                    self.warn('unsupported_macro', path, offset + index, command=name)
                    index = end
            elif raw[index] in '{}':
                placeholder(raw[index], index, index + 1)
                index += 1
            else:
                plain(raw[index], index, index + 1)
                index += 1
        return ''.join(output), positions, protected

    def emit(self, path, text, start, end, kind='sentence', action='translate', extra=None, split=True):
        raw = text[start:end]
        if not raw.strip():
            return []
        self.sequence += 1
        filename = path.relative_to(self.root).as_posix()
        region_id = f'tex:{filename}:{start}:{end}'
        parent = f'tex:{filename}:para{len(re.findall(chr(10) + chr(10), text[:start]))}'
        locator = {'file': filename, 'start': start, 'end': end, 'line_start': text.count('\n', 0, start) + 1, 'region_id': region_id}
        protected = {}
        if action == 'translate':
            masked, origin, protected = self.protect(raw, path, start)
        else:
            masked, origin = raw, [(i, i + 1) for i in range(len(raw))]
        if action == 'preserve':
            normalized = masked
            normalized_map = [[i, i + 1] for i in range(len(masked))]
        else:
            normalized, normalized_map = normalize_with_map(masked)
        sentences = split_en_sentences(normalized) if split else [normalized]
        output, cursor = [], 0
        for number, sentence in enumerate(sentences):
            pos = normalized.find(sentence, cursor)
            if pos < 0:
                raise ValueError('LaTeX sentence source mapping failed')
            finish = pos + len(sentence)
            first_mask, last_mask = normalized_map[pos][0], normalized_map[finish - 1][1] - 1
            raw_start, raw_end = origin[first_mask][0], origin[last_mask][1]
            item = {'id': f'tex-{self.sequence:04d}-s{number + 1:03d}', 'kind': kind, 'section': self.section, 'page': 1,
                    'parent_id': parent, 'spans': [], 'locators': [{**locator, 'start': start + raw_start, 'end': start + raw_end}],
                    'source': sentence, 'raw_source': raw[raw_start:raw_end], 'source_format': 'latex',
                    'protected_tokens': {key: value for key, value in protected.items() if key in sentence},
                    'action': action, 'status': 'pending'}
            if extra:
                item.update(extra)
            output.append(item)
            self.items.append(item)
            cursor = finish
        self.regions.append({'region_id': region_id, 'locator': locator, 'role': kind, 'raw_text': raw,
                             'normalized_text': normalized, 'disposition': action, 'unit_ids': [item['id'] for item in output]})
        return output

    def parse_file(self, path):
        path = Path(path).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError(f'LaTeX include leaves project root: {path}')
        if path in self.active:
            raise ValueError(f'Cyclic LaTeX include: {path}')
        if path in self.files:
            self.warn('repeated_include_requires_review', path, 0)
            return
        if not path.is_file():
            raise FileNotFoundError(path)
        self.active.add(path)
        self.files.add(path)
        text = path.read_text(encoding='utf-8-sig')
        begin = re.search(r'\\begin\s*\{document\}', text)
        if begin:
            # Preamble is preserved; title and author display metadata remain explicit.
            for match in re.finditer(r'\\(title|author)\b', text[:begin.start()]):
                name, end, args = command_at(text, match.start())
                req = _required(args)
                if req:
                    self.emit(path, text, req[0][1], req[0][2], 'title' if name == 'title' else 'text',
                              'translate' if name == 'title' else 'preserve', split=False)
            self.parse_range(path, text, begin.end(), text.rfind('\\end{document}') if '\\end{document}' in text else len(text))
        else:
            self.parse_range(path, text, 0, len(text))
        # Copy local class/style/bibliography dependencies; unknown generated resources remain explicit.
        for match in re.finditer(r'\\(bibliography|addbibresource|documentclass|usepackage)\b', text):
            name, end, args = command_at(text, match.start())
            req = _required(args)
            if not req:
                continue
            for value in text[req[-1][1]:req[-1][2]].split(','):
                value = value.strip()
                suffix = '.bib' if name in ('bibliography', 'addbibresource') else '.cls' if name == 'documentclass' else '.sty'
                dependency = path.parent / (value if Path(value).suffix else value + suffix)
                if dependency.is_file():
                    self.files.add(dependency.resolve())
                    if suffix == '.bib':
                        self.parse_bibliography(dependency.resolve())
                elif suffix == '.bib':
                    self.warn('missing_bibliography', path, match.start(), dependency=value)
        self.active.remove(path)

    def parse_bibliography(self, path):
        text = path.read_text(encoding='utf-8-sig')
        if any(r.get('locator', {}).get('file') == path.relative_to(self.root).as_posix() for r in self.regions):
            return
        cursor = 0
        while cursor < len(text):
            match = re.search(r'@(\w+)\s*\{', text[cursor:])
            if not match:
                break
            start = cursor + match.start()
            opening = cursor + match.end() - 1
            end = group_end(text, opening)
            comma = text.find(',', opening + 1, end)
            if comma < 0 or match.group(1).casefold() in ('string', 'preamble'):
                self.warn('bibliography_expression_requires_review', path, start)
                cursor = end
                continue
            key = text[opening + 1:comma].strip()
            field_cursor = comma + 1
            while field_cursor < end - 1:
                field = re.match(r'\s*,?\s*(\w+)\s*=\s*', text[field_cursor:end - 1])
                if not field:
                    if text[field_cursor:end - 1].strip(' \r\n\t,'):
                        self.warn('bibliography_expression_requires_review', path, field_cursor)
                    break
                value_start = field_cursor + field.end()
                if text[value_start] == '{':
                    value_end = group_end(text, value_start)
                    inner_start, inner_end = value_start + 1, value_end - 1
                elif text[value_start] == '"':
                    value_end = value_start + 1
                    while value_end < end and (text[value_end] != '"' or text[value_end - 1] == '\\'):
                        value_end += 1
                    if value_end >= end:
                        raise ValueError('Unclosed bibliography quoted field')
                    inner_start, inner_end = value_start + 1, value_end
                    value_end += 1
                else:
                    value_end = text.find(',', value_start, end - 1)
                    value_end = end - 1 if value_end < 0 else value_end
                    inner_start, inner_end = value_start, value_end
                if field.group(1).casefold() == 'title':
                    self.emit(path, text, inner_start, inner_end, 'reference', split=False,
                              extra={'bibliography_key': key, 'bibliography_field': 'title'})
                field_cursor = value_end
            cursor = end

    def parse_range(self, path, text, start, end, reference=False):
        index, plain_start = start, start
        def flush(stop):
            nonlocal plain_start
            fragment = text[plain_start:stop]
            for match in re.finditer(r'\S(?:.*?\S)?(?=\n\s*\n|\s*\Z)', fragment, re.S):
                self.emit(path, text, plain_start + match.start(), plain_start + match.end(), 'reference' if reference else 'sentence')
            plain_start = stop
        while index < end:
            if text[index] == '%':
                newline = text.find('\n', index)
                index = end if newline < 0 else newline + 1
                continue
            if text.startswith('$$', index) or text.startswith('\\[', index):
                flush(index)
                closing = '$$' if text.startswith('$$', index) else '\\]'
                stop = text.find(closing, index + 2, end)
                if stop < 0:
                    raise ValueError('Unclosed display math')
                stop += 2
                self.emit(path, text, index, stop, 'equation', 'preserve', {'math_format': 'latex'}, split=False)
                index = plain_start = stop
                continue
            if text[index] != '\\':
                index += 1
                continue
            cmd = command_at(text, index)
            if not cmd:
                index += 1
                continue
            name, stop, args = cmd
            required = _required(args)
            simple = name.rstrip('*')
            if simple in TEXT_COMMANDS and required:
                flush(index)
                arg = required[-1]
                kind = 'heading' if simple in ('section', 'subsection', 'subsubsection', 'chapter', 'paragraph', 'title') else simple
                extra = {}
                if kind == 'heading':
                    self.section_count += 1
                    self.section = text[arg[1]:arg[2]]
                elif kind == 'caption':
                    if self.current_figure:
                        kind = 'figure'
                        extra = dict(self.current_figure)
                    else:
                        kind = 'table' if self.current_table else 'caption'
                        if self.current_table:
                            extra['table_id'] = self.current_table
                emitted = self.emit(path, text, arg[1], arg[2], kind, extra=extra, split=False)
                if kind == 'figure' and emitted and emitted[0].get('asset_id'):
                    asset = next(a for a in self.assets if a['asset_id'] == emitted[0]['asset_id'])
                    asset['owner_ids'].append(emitted[0]['id'])
                index = plain_start = stop
            elif simple in ('input', 'include') and required:
                flush(index)
                include = text[required[-1][1]:required[-1][2]]
                child = path.parent / (include if Path(include).suffix else include + '.tex')
                self.parse_file(child)
                index = plain_start = stop
            elif simple == 'begin' and required:
                env = text[required[0][1]:required[0][2]]
                # begin may consume environment arguments; body starts after them.
                close_start, close_end = _environment_end(text, index, env, stop)
                if close_end > end:
                    raise ValueError(f'Environment {env} exceeds parent range')
                flush(index)
                if env in MATH_ENVIRONMENTS:
                    self.emit(path, text, index, close_end, 'equation', 'preserve', {'math_format': 'latex'}, split=False)
                elif env in ('tabular', 'tabular*', 'longtable'):
                    self.table_cells(path, text, stop, close_start)
                elif env in ('figure', 'figure*'):
                    previous = self.current_figure
                    self.figure_count += 1
                    self.current_figure = {'figure_no': str(self.figure_count)}
                    images = list(re.finditer(r'\\includegraphics\b', text[stop:close_start]))
                    if len(images) == 1:
                        image_at = stop + images[0].start()
                        _, _, image_args = command_at(text, image_at)
                        image_req = _required(image_args)
                        if image_req:
                            value = text[image_req[-1][1]:image_req[-1][2]]
                            image_path = path.parent / value
                            options = [image_path] if image_path.suffix else [image_path.with_suffix(s) for s in ('.pdf', '.png', '.jpg', '.jpeg', '.eps')]
                            available = [p for p in options if p.is_file()]
                            if len(available) == 1:
                                image_path = available[0].resolve()
                                if not image_path.is_relative_to(self.root):
                                    raise ValueError('Figure leaves project root')
                                self.files.add(image_path)
                                asset_id = f'tex-figure-{self.figure_count}'
                                self.assets.append({'asset_id': asset_id, 'kind': 'figure', 'path': str(image_path), 'sha256': digest(image_path),
                                                    'representation': 'original', 'owner_ids': []})
                                self.current_figure.update(asset_id=asset_id, image_path=str(image_path))
                            else:
                                self.warn('missing_or_ambiguous_figure_asset', path, image_at, path_value=value)
                    else:
                        self.warn('compound_or_missing_figure', path, index, image_count=len(images))
                    self.parse_range(path, text, stop, close_start)
                    self.current_figure = previous
                elif env in ('table', 'table*'):
                    previous = self.current_table
                    self.table_count += 1
                    self.current_table = f'tex-table-{self.table_count}'
                    self.parse_range(path, text, stop, close_start)
                    self.current_table = previous
                elif env == 'thebibliography':
                    self.parse_range(path, text, stop, close_start, reference=True)
                elif env in ('document', 'abstract', 'itemize', 'enumerate', 'description', 'quote', 'center', 'minipage'):
                    self.parse_range(path, text, stop, close_start, reference=reference)
                elif env in ('verbatim', 'lstlisting', 'minted'):
                    self.emit(path, text, index, close_end, 'code', 'preserve', split=False)
                else:
                    self.emit(path, text, index, close_end, 'code', 'preserve', split=False)
                    self.warn('unsupported_environment', path, index, environment=env)
                index = plain_start = close_end
            elif simple == 'bibitem':
                flush(index)
                self.reference_count += 1
                next_item = re.search(r'\\bibitem\b', text[stop:end])
                finish = stop + next_item.start() if next_item else end
                self.emit(path, text, stop, finish, 'reference', split=False,
                          extra={'id': f'ref-{self.reference_count:03d}', 'bibliography_key': text[required[-1][1]:required[-1][2]] if required else ''})
                index = plain_start = finish
            elif simple in LAYOUT or simple in ('includegraphics', 'item', 'label', 'bibliography', 'addbibresource', 'bibliographystyle'):
                flush(index)
                # These commands remain intact in the copied source project.
                index = plain_start = stop
            else:
                index = max(index + 1, stop)
        flush(end)

    def table_cells(self, path, text, start, end):
        table_id = self.current_table
        if not table_id:
            self.table_count += 1
            table_id = f'tex-table-{self.table_count}'
        row, col, depth, index, cell_start = 0, 0, 0, start, start
        cells = []
        def emit_cell(stop):
            nonlocal cell_start
            fragment = text[cell_start:stop]
            # Rule commands are layout; their source project text is preserved.
            fragment_start = cell_start
            prefix = re.match(r'\s*(?:(?:\\(?:hline|toprule|midrule|bottomrule))\s*)*', fragment)
            fragment_start += prefix.end()
            if text[fragment_start:stop].strip():
                emitted = self.emit(path, text, fragment_start, stop, 'table_cell', split=False,
                                    extra={'id': f'{table_id}-cell-r{row + 1}c{col + 1}', 'table_id': table_id,
                                           'parent_id': table_id, 'row': row, 'col': col, 'rowspan': 1, 'colspan': 1})
                cells.extend(emitted)
            else:
                locator = {'file': path.relative_to(self.root).as_posix(), 'start': stop, 'end': stop}
                empty = {'id': f'{table_id}-cell-r{row + 1}c{col + 1}', 'kind': 'table_cell', 'table_id': table_id,
                         'parent_id': table_id, 'section': self.section, 'page': 1, 'spans': [], 'locators': [locator],
                         'row': row, 'col': col, 'rowspan': 1, 'colspan': 1, 'source': '', 'action': 'preserve',
                         'empty_cell': True, 'status': 'pending'}
                self.items.append(empty)
                cells.append(empty)
        while index < end:
            if text[index] == '\\':
                if text.startswith('\\\\', index) and depth == 0:
                    emit_cell(index)
                    row, col = row + 1, 0
                    index += 2
                    cell_start = index
                else:
                    index += 2
            elif text[index] == '{':
                depth += 1
                index += 1
            elif text[index] == '}':
                depth -= 1
                index += 1
            elif text[index] == '&' and depth == 0:
                emit_cell(index)
                col += 1
                index += 1
                cell_start = index
            else:
                index += 1
        if text[cell_start:end].strip() and not re.fullmatch(r'\s*(?:\\(?:hline|bottomrule)\s*)*', text[cell_start:end]):
            emit_cell(end)
        for cell in cells:
            cell['table_rows'] = max(c['row'] for c in cells) + 1
            cell['table_cols'] = max(c['col'] for c in cells) + 1


def discover_main(path, main_file=None):
    path = Path(path).resolve()
    root = path if path.is_dir() else path.parent
    if main_file:
        main = (root / main_file).resolve()
        if not main.is_relative_to(root) or not main.is_file():
            raise ValueError('Invalid main LaTeX file')
        return root, main
    if path.is_file():
        return root, path
    candidates = [p for p in root.rglob('*.tex') if '\\documentclass' in p.read_text(encoding='utf-8-sig') and '\\begin{document}' in p.read_text(encoding='utf-8-sig')]
    if len(candidates) != 1:
        raise ValueError('Ambiguous LaTeX project; specify --main-file: ' + ', '.join(p.relative_to(root).as_posix() for p in candidates))
    return root, candidates[0]


def extract_project(path, main_file=None):
    root, main = discover_main(path, main_file)
    parser = LatexParser(root)
    parser.parse_file(main)
    for source_path in sorted(parser.files):
        if source_path.suffix.lower() in ('.tex', '.latex', '.bib', '.sty', '.cls'):
            source_text = source_path.read_text(encoding='utf-8-sig')
            register_syntax_gaps(parser.regions, source_path, source_text, source_path.relative_to(root).as_posix())
    return {'items': parser.items, 'regions': parser.regions, 'assets': parser.assets, 'warnings': parser.warnings,
            'document': {'source_format': 'latex', 'source_root': str(root), 'main_file': main.relative_to(root).as_posix(),
                         'input_files': [str(p) for p in sorted(parser.files)], 'extractor': {'name': 'bounded-latex', 'version': '1'},
                         'limitations': ['Macro expansion is not interpreted', 'Unknown macros/environments and expression-based bibliography fields require review']}}


def restore_translation(item, translation):
    tokens = item.get('protected_tokens', {})
    for token in tokens:
        if translation.count(token) != item['source'].count(token):
            raise ValueError(f'Changed protected LaTeX token {token}')
    escape = {'\\': r'\textbackslash{}', '&': r'\&', '%': r'\%', '$': r'\$', '#': r'\#', '_': r'\_', '{': r'\{', '}': r'\}', '~': r'\textasciitilde{}', '^': r'\textasciicircum{}'}
    output = ''.join(escape.get(c, c) for c in translation)
    for token, value in tokens.items():
        output = output.replace(token, value)
    if '⟦TEX' in output:
        raise ValueError('Unknown protected token in translated source')
    return output


def export_project(job, destination):
    """Apply current merged translations to a new source copy; never overwrite inputs."""
    destination = Path(destination).resolve()
    root = Path(job['root']).resolve()
    if destination.exists():
        raise ValueError('LaTeX export destination already exists')
    shutil.copytree(root / 'input', destination)
    edits = {}
    for item in job.get('merged', []):
        if item.get('action') == 'preserve':
            continue
        if not item.get('translation'):
            raise ValueError(f'Missing translation for {item["id"]}')
        locators = item.get('locators', [])
        if len(locators) != 1 or 'file' not in locators[0]:
            raise ValueError(f'LaTeX source locator unavailable for {item["id"]}')
        locator = locators[0]
        edits.setdefault(locator['file'], []).append((locator['start'], locator['end'], restore_translation(item, item['translation'])))
    for filename, changes in edits.items():
        path = (destination / filename).resolve()
        if not path.is_relative_to(destination):
            raise ValueError('Source path leaves export')
        source = path.read_text(encoding='utf-8-sig')
        cursor = len(source)
        for start, end, replacement in sorted(changes, reverse=True):
            if not (0 <= start <= end <= cursor):
                raise ValueError('Overlapping LaTeX translation edits')
            source = source[:start] + replacement + source[end:]
            cursor = start
        path.write_text(source, encoding='utf-8')
    return destination


def compile_project(directory, main_file, engine='xelatex', timeout=60):
    """Compile a prepared source copy with shell escape disabled and bounded runtime."""
    if engine not in ('xelatex', 'lualatex', 'pdflatex'):
        raise ValueError('Supported engines: xelatex, lualatex, pdflatex')
    executable = shutil.which(engine)
    if not executable:
        return {'status': 'NOT_RUN', 'exit_code': 4, 'error': f'Missing LaTeX engine: {engine}'}
    directory = Path(directory).resolve()
    main = (directory / main_file).resolve()
    if not main.is_relative_to(directory) or not main.is_file():
        raise ValueError('Invalid main file')
    command = [executable, '-no-shell-escape', '-interaction=nonstopmode', '-halt-on-error', str(main.relative_to(directory))]
    logs = []
    try:
        for _ in range(2):
            process = subprocess.run(command, cwd=directory, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout)
            logs.append(process.stdout + process.stderr)
            if process.returncode:
                break
    except subprocess.TimeoutExpired as exc:
        return {'status': 'FAIL', 'exit_code': 5, 'error': 'LaTeX compilation timed out', 'command': command}
    log = '\n'.join(logs)
    (directory / 'translation-compile.log').write_text(log, encoding='utf-8')
    pdf = directory / (main.stem + '.pdf')
    references_pending = bool(re.search(r'undefined references|Citation .+ undefined|Rerun to get', log, re.I))
    status = 'FAIL' if process.returncode or not pdf.is_file() else 'NEEDS_REVIEW' if references_pending else 'PASS'
    return {'status': status, 'exit_code': 0 if status == 'PASS' else 3 if status == 'NEEDS_REVIEW' else 5,
            'command': command, 'engine': engine, 'pdf': str(pdf) if pdf.is_file() else None,
            'log': str(directory / 'translation-compile.log'), 'references_pending': references_pending}


def main():
    parser = argparse.ArgumentParser(description='Export a validated translated LaTeX project and optionally compile it')
    parser.add_argument('--job-dir', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--engine', choices=['xelatex', 'lualatex', 'pdflatex'], help='Compile with this installed engine; omission only exports sources')
    args = parser.parse_args()
    try:
        from ..document_ir import load_merged_job, atomic_write_json
    except ImportError:
        from scripts.document_ir import load_merged_job, atomic_write_json
    try:
        job = load_merged_job(args.job_dir)
        if job['document'].get('source_format') not in ('tex', 'latex'):
            raise ValueError('This job is not a LaTeX source project')
        output = export_project(job, args.out)
        main_file = job['document']['primary_input'].removeprefix('input/')
        result = compile_project(output, main_file, args.engine) if args.engine else {'status': 'NOT_RUN', 'exit_code': 0, 'reason': 'Source export only; PDF compilation not requested'}
        result['source_output'] = str(output)
        result['cjk_note'] = 'The original project must support Chinese text/fonts; engine and package changes require their own compilation evidence.'
        atomic_write_json(output / 'export-report.json', result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return result['exit_code']
    except (ValueError, OSError) as exc:
        print(json.dumps({'status': 'FAIL', 'error': str(exc)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
