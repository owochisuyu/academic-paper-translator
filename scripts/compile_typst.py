# -*- coding: utf-8 -*-
"""Render canonical translation records. Low-level rendering is a draft preview API."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


class RenderError(ValueError):
    pass


def _ir():
    try:
        from . import document_ir
    except ImportError:
        import document_ir
    return document_ir


def _literal(value):
    return json.dumps(str(value), ensure_ascii=False)


def escape_typst_content(text: str) -> str:
    """Compatibility helper; literal text is emitted as a Typst string, never code."""
    return '#text(' + _literal(text or '') + ')'


TYPST_HEADER_TEMPLATE = r'''#set page(paper: "a4", margin: (x: 20mm, y: 20mm),
  footer: context align(center, text(size: 8pt, counter(page).display())))
#set text(font: ("Times New Roman", "Microsoft YaHei", "SimSun", "Noto Serif CJK SC"), size: 10pt, lang: "zh")
#set par(justify: false, leading: 0.75em)
#let en(t) = t
#let zh(t) = text(fill: rgb("1a4b77"))[#t]
#let bip(a, b) = context layout(size => {
  let pair = [#en(a) #if b != a [#parbreak()#zh(b)]]
  let short = measure(pair, width: size.width).height <= 45mm
  block(breakable: not short, width: 100%, inset: (y: 2pt), pair)
})
#let zhp(t) = block(breakable: true, width: 100%, inset: (y: 2pt))[#zh(t)]
#let parallel(a, b) = table(columns: (1fr, 1fr), stroke: none, inset: 5pt,
  table.cell(breakable: true)[#en(a)], table.cell(breakable: true)[#zh(b)])
#let paper-table(body) = context layout(size => {
  let short = measure(body, width: size.width).height <= 70mm
  block(breakable: not short, width: 100%, body)
})
'''


def _texts(item, draft):
    source = item.get('source', '')
    if not isinstance(source, str):
        raise RenderError(f"Non-string source: {item.get('id')}")
    if item.get('action') == 'preserve':
        return source, source
    target = item.get('translation')
    if not isinstance(target, str) or (source.strip() and not target.strip()):
        if draft:
            return source, '[UNTRANSLATED] ' + source
        raise RenderError(f"Missing translation: {item.get('id')}")
    return source, target


def _heading_level(item):
    return max(1, min(6, int(item.get('heading_level', item.get('level', 1)))))


def _markdown_parts(text, item):
    """Parse delimited inline math only for Markdown provenance, outside code spans."""
    markdown = item.get('source_format') in ('markdown', 'md') or any(
        Path(locator.get('file', '')).suffix.lower() in ('.md', '.markdown', '.mdown')
        for locator in item.get('locators', []))
    if not markdown or item.get('kind') == 'code':
        return [('text', text)]
    parts, position, plain_start = [], 0, 0
    while position < len(text):
        if text[position] == '`':
            fence = re.match(r'`+', text[position:]).group()
            end = text.find(fence, position + len(fence))
            position = end + len(fence) if end >= 0 else len(text)
            continue
        if text.startswith(r'\(', position):
            end = text.find(r'\)', position + 2)
            if end < 0:
                raise RenderError('Unclosed Markdown inline math delimiter')
            closing = end + 2
        elif text[position] == '$' and (position == 0 or text[position - 1] != '\\'):
            # Double dollars belong to display-math units. A closing dollar followed
            # by a digit is currency, e.g. "$5 to $10", not an inline formula.
            if text.startswith('$$', position):
                raise RenderError('Display Markdown math must be a separate equation unit')
            match = re.match(r'\$(?=\S)([^\n$]*?\S)(?<!\\)\$(?![\d$])', text[position:])
            if not match:
                if position + 1 < len(text) and (text[position + 1].isalpha() or text[position + 1] in '\\_{'):
                    raise RenderError('Unclosed or ambiguous Markdown inline math delimiter')
                position += 1
                continue
            closing = position + match.end()
        else:
            position += 1
            continue
        if position > plain_start:
            parts.append(('text', text[plain_start:position]))
        parts.append(('math', latex_to_typst(text[position:closing])))
        plain_start = position = closing
    if plain_start < len(text):
        parts.append(('text', text[plain_start:]))
    return parts


def _display_parts(text, item=None):
    """Restore registered TeX tokens and bounded Markdown math without executing source."""
    item = item or {}
    tokens = item.get('protected_tokens', {})
    if not tokens:
        return _markdown_parts(text, item)
    parts = []
    for piece in re.split(r'(⟦TEX[0-9]+⟧)', text):
        if not piece:
            continue
        if piece not in tokens:
            if piece.startswith('⟦TEX'):
                raise RenderError('Unregistered protected token')
            parts.extend(_markdown_parts(piece, item))
            continue
        raw = tokens[piece]
        if raw.startswith('$') or raw.startswith(r'\(') or raw.startswith(r'\['):
            parts.append(('math', latex_to_typst(raw)))
        elif re.fullmatch(r'\\(?:textbf|textit|emph|textrm|texttt|textsf|underline)\{', raw) or raw == '}':
            continue
        elif re.fullmatch(r'\\cite[a-zA-Z]*\{[^{}]+\}', raw):
            parts.append(('text', '[' + raw[raw.index('{')+1:-1] + ']'))
        elif re.fullmatch(r'\\(?:eqref|ref|autoref)\{[^{}]+\}', raw):
            parts.append(('text', '[ref:' + raw[raw.index('{')+1:-1] + ']'))
        elif re.fullmatch(r'\\url\{[^{}]+\}', raw):
            parts.append(('text', raw[5:-1]))
        elif raw in (r'\%', r'\_', r'\&', r'\#', r'\$', r'\{', r'\}'):
            parts.append(('text', raw[1:]))
        else:
            raise RenderError(f'Unsupported protected TeX token: {raw}')
    return parts


def _content(text, item=None):
    # Whitespace immediately inside BOTH dollar delimiters makes Typst display math.
    # These parts belong inside prose; standalone equation units use display markup.
    return '[' + ''.join(escape_typst_content(value) if kind == 'text' else '$' + value.strip() + '$' for kind, value in _display_parts(text, item)) + ']'


def _pair(source, target, mode, preserve=False, item=None):
    if preserve:
        return '#block(breakable: true)' + _content(source, item)
    if mode == 'B':
        return f'#zhp({_content(target, item)})'
    if mode == 'D':
        return f'#parallel({_content(source, item)}, {_content(target, item)})'
    return f'#bip({_content(source, item)}, {_content(target, item)})'


def latex_to_typst(value: str) -> str:
    """A deliberately small converter. Unsupported syntax is an explicit error."""
    value = value.strip()
    value = re.sub(r'^\\begin\{equation\*?\}|\\end\{equation\*?\}$', '', value).strip()
    for left, right in [('$$', '$$'), ('\\[', '\\]'), ('\\(', '\\)'), ('$', '$')]:
        if value.startswith(left) and value.endswith(right):
            value = value[len(left):-len(right)].strip()
            break
    aliases = {'times': 'times', 'cdot': 'dot', 'pm': 'plus.minus', 'le': '<=', 'leq': '<=',
               'ge': '>=', 'geq': '>=', 'neq': '!=', 'approx': 'approx', 'infty': 'infinity'}
    names = set('alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi pi rho sigma tau phi chi psi omega Gamma Delta Theta Lambda Xi Pi Sigma Phi Psi Omega sin cos tan log ln exp'.split())
    pos = 0

    def group():
        nonlocal pos
        while pos < len(value) and value[pos].isspace():
            pos += 1
        if pos >= len(value) or value[pos] != '{':
            raise RenderError('LaTeX command requires a braced argument')
        pos += 1
        return parse('}')

    def parse(end=None):
        nonlocal pos
        out = []
        while pos < len(value):
            char = value[pos]
            if end and char == end:
                pos += 1
                return ''.join(out)
            if char == '\\':
                pos += 1
                match = re.match(r'[A-Za-z]+', value[pos:])
                if not match:
                    if pos < len(value) and value[pos] in ',;! ':
                        pos += 1
                        out.append(' ')
                        continue
                    raise RenderError('Unsupported LaTeX escape')
                command = match.group()
                pos += len(command)
                if command == 'frac':
                    numerator, denominator = group(), group()
                    out.append(f'frac({numerator}, {denominator})')
                elif command == 'sqrt':
                    out.append(f'sqrt({group()})')
                elif command in ('left', 'right'):
                    continue
                elif command in aliases:
                    out.append(' ' + aliases[command] + ' ')
                elif command in names:
                    out.append(' ' + command + ' ')
                else:
                    raise RenderError(f'Unsupported LaTeX command: \\{command}')
            elif char == '{':
                pos += 1
                out.append('(' + parse('}') + ')')
            elif char in '#@$&"' or char == '}':
                raise RenderError(f'Unsupported math character: {char}')
            else:
                pos += 1
                out.append(char)
        if end:
            raise RenderError('Unclosed LaTeX group')
        return ''.join(out)

    result = parse().strip()
    if not result:
        raise RenderError('Empty formula')
    return result


def _math(item):
    value = item.get('typst_math') or item.get('latex') or item.get('source', '')
    fmt = item.get('math_format') or item.get('format')
    if fmt is None:
        fmt = 'latex' if '\\' in value else 'typst_math'
    if fmt == 'latex':
        return latex_to_typst(value)
    if fmt != 'typst_math':
        raise RenderError(f'Unsupported mathematical representation: {fmt}')
    value = value.strip().strip('$').strip()
    if not value or any(char in value for char in '#[]{};"\\'):
        raise RenderError('Unsupported or unsafe Typst math expression')
    return value


def _asset_path(item, meta, typ_dir, draft):
    assets = meta.get('assets', [])
    if isinstance(assets, dict):
        assets = assets.get('assets', list(assets.values()))
    matches = [a for a in assets if a.get('asset_id') == item.get('asset_id')] if item.get('asset_id') else []
    if len(matches) > 1:
        raise RenderError(f"Duplicate asset ID: {item.get('asset_id')}")
    if matches:
        asset = matches[0]
        path = Path(meta.get('asset_root', typ_dir)) / asset['path']
        if not path.is_file():
            raise RenderError(f"Missing asset: {asset['asset_id']}")
        if asset.get('sha256') and hashlib.sha256(path.read_bytes()).hexdigest() != asset['sha256']:
            raise RenderError(f"Asset hash changed: {asset['asset_id']}")
        if item.get('doc_id') and asset.get('doc_id') != item['doc_id']:
            raise RenderError('Cross-document asset binding')
        return path.resolve()
    # Legacy preview accepts an explicit path, never directory/name discovery.
    if draft and item.get('image_path'):
        path = Path(item['image_path'])
        if not path.is_absolute():
            path = Path(meta.get('asset_root', typ_dir)) / path
        if path.is_file():
            return path.resolve()
    if draft:
        return None
    raise RenderError(f"Missing explicit asset binding for {item.get('id')}")


def _table_key(item):
    return item.get('table_id') or '__legacy_table__'


def _render_table(cells, captions, mode, draft):
    rows, cols, occupied = 0, 0, set()
    lines = []
    for cell in sorted(cells, key=lambda c: (c.get('row', 0), c.get('col', 0))):
        r, c = cell.get('row', 0), cell.get('col', 0)
        rs, cs = cell.get('rowspan', 1), cell.get('colspan', 1)
        if not all(isinstance(n, int) for n in (r, c, rs, cs)) or min(r, c) < 0 or min(rs, cs) < 1:
            raise RenderError('Invalid table cell coordinates')
        area = {(rr, cc) for rr in range(r, r + rs) for cc in range(c, c + cs)}
        if occupied & area:
            raise RenderError('Overlapping or duplicate table cells')
        occupied |= area
        rows, cols = max(rows, r + rs), max(cols, c + cs)
        en, zh = _texts(cell, draft)
        content = _pair(en, zh, 'B' if mode == 'B' else 'A', cell.get('action') == 'preserve', cell)
        lines.append(f' table.cell(x: {c}, y: {r}, rowspan: {rs}, colspan: {cs})[{content}],')
    if cells:
        # Missing cells cannot be silently invented in a canonical table.
        if not draft and occupied != {(r, c) for r in range(rows) for c in range(cols)}:
            raise RenderError('Incomplete table grid; register empty cells explicitly')
        result = [f'#paper-table(table(columns: {cols}, stroke: none, inset: 5pt,',
                  ' table.hline(y: 0, stroke: 1pt),', *lines,
                  f' table.hline(y: {rows}, stroke: 1pt),', '))']
    elif draft:
        result = ['#text(fill: red)[DRAFT: table cells unavailable]']
    else:
        raise RenderError('Table has a caption but no cells')
    for caption in captions:
        result.append(_pair(*_texts(caption, draft), mode, caption.get('action') == 'preserve', caption))
    return '\n'.join(result)


def render_typst_document(manifest_items: list[dict], mode='A', metadata=None, typ_dir='.') -> str:
    """Render a preview by default. Production callers must use compile_job()."""
    if mode not in 'ABCD' or len(mode) != 1:
        raise RenderError(f'Unknown mode: {mode}')
    meta = metadata or {}
    draft = meta.get('output_stage', 'draft') == 'draft'
    body = [TYPST_HEADER_TEMPLATE]
    if draft:
        body.append('#text(fill: red, weight: "bold")[DRAFT — not accepted]')
    tables = {}
    for item in manifest_items:
        if item.get('kind') in ('table', 'table_cell', 'table_caption'):
            bucket = tables.setdefault(_table_key(item), {'cells': [], 'captions': []})
            bucket['cells' if item['kind'] == 'table_cell' else 'captions'].append(item)
    rendered_tables = set()
    index = 0
    text_kinds = {'sentence', 'paragraph', 'text', 'heading', 'title', 'abstract', 'keyword',
                  'keywords', 'footnote', 'reference', 'list_item', 'figure_label', 'caption', 'figure_caption', 'code', 'preserved'}
    while index < len(manifest_items):
        item = manifest_items[index]
        kind = item.get('kind', 'sentence')
        index += 1
        if kind in ('table', 'table_cell', 'table_caption'):
            key = _table_key(item)
            if key not in rendered_tables:
                table = tables[key]
                body.append(_render_table(table['cells'], table['captions'], mode, draft))
                rendered_tables.add(key)
            continue
        if kind in ('figure', 'equation', 'math') and (kind == 'figure' or item.get('asset_id') or item.get('math_format') == 'graphic'):
            path = _asset_path(item, meta, typ_dir, draft)
            if path:
                relative = os.path.relpath(path, typ_dir).replace('\\', '/')
                body.append(f'#align(center)[#image({_literal(relative)}, width: 85%)]')
            else:
                body.append('#text(fill: red)[DRAFT: missing graphic]')
            if kind == 'figure' and item.get('source', '').strip():
                body.append(_pair(*_texts(item, draft), mode, item.get('action') == 'preserve', item))
            continue
        if kind in ('equation', 'math'):
            body.append('$ ' + _math(item) + ' $')
            if item.get('label'):
                body.append('#align(right)[' + escape_typst_content(item['label']) + ']')
            continue
        if kind not in text_kinds:
            raise RenderError(f'No renderer for kind {kind!r}: {item.get("id")}')
        en, zh = _texts(item, draft)
        preserve = item.get('action') == 'preserve'
        # C groups adjacent units by actual paragraph identity. D aligns each paragraph row.
        if mode in ('C', 'D') and kind in ('sentence', 'paragraph', 'text') and item.get('parent_id') and not item.get('protected_tokens'):
            while index < len(manifest_items):
                following = manifest_items[index]
                if following.get('kind') not in ('sentence', 'paragraph', 'text') or following.get('parent_id') != item['parent_id'] or following.get('protected_tokens') or (following.get('action') == 'preserve') != preserve:
                    break
                e2, z2 = _texts(following, draft)
                en, zh = en + ' ' + e2, zh + ' ' + z2
                index += 1
        pair = _pair(en, zh, mode, preserve, item)
        if kind in ('heading', 'title'):
            body.append('#heading(level: ' + str(_heading_level(item)) + ', outlined: false)[' + pair + ']')
        else:
            body.append(pair)
    return '\n\n'.join(body) + '\n'


def _math_glyph_text(value):
    """Linear glyph evidence for simple arithmetic; layout still needs visual review.

    Fractions, roots, named functions/symbols and complex attachment groups have no
    universal PDF extraction order, so those remain explicit visual-math checks.
    """
    if re.search(r'[A-Za-z]{2,}', value) or not re.fullmatch(r'[A-Za-z0-9\s_\^()+\-=<>.,]+', value):
        return None
    value = re.sub(r'[_^]\(([^()]*)\)', r'\1', value)
    if re.search(r'[_^]\(', value):
        return None
    value = value.replace('_', '').replace('^', '')
    return value.replace('<=', '≤').replace('>=', '≥').replace('-', '−')


def expected_content(items, mode):
    segments, graphics, math_ids = [], [], []
    for item in items:
        kind = item.get('kind', 'sentence')
        if item.get('asset_id'):
            graphics.append({'id': item['id'], 'asset_id': item['asset_id']})
        if kind in ('equation', 'math'):
            math_ids.append(item.get('id'))
            if not item.get('asset_id') and item.get('math_format') != 'graphic':
                glyphs = _math_glyph_text(_math(item))
                if glyphs:
                    segments.append({'id': item.get('id'), 'language': 'source', 'text': glyphs,
                                     'verification': 'math_glyphs'})
            continue
        en, zh = _texts(item, False)
        texts = [('source', en)] if item.get('action') == 'preserve' else ([('translation', zh)] if mode == 'B' else [('source', en)] + ([] if zh == en else [('translation', zh)]))
        for language, text in texts:
            if text.strip():
                for part_kind, part in _display_parts(text, item):
                    if part_kind == 'text' and part.strip():
                        segments.append({'id': item.get('id'), 'language': language, 'text': part})
                    elif part_kind == 'math':
                        if item.get('id') not in math_ids:
                            math_ids.append(item.get('id'))
                        glyphs = _math_glyph_text(part)
                        if glyphs:
                            segments.append({'id': item.get('id'), 'language': language, 'text': glyphs,
                                             'verification': 'math_glyphs'})
    return segments, graphics, math_ids


def export_markdown(items, mode='A', asset_paths=None):
    """Export the same records, including structured tables and portable asset links."""
    import html
    asset_paths = asset_paths or {}
    out, tables, rendered = [], {}, set()

    def values(item):
        en, zh = _texts(item, False)
        for token, raw in item.get('protected_tokens', {}).items():
            en, zh = en.replace(token, raw), zh.replace(token, raw)
        return en, zh

    def body(item):
        en, zh = values(item)
        if item.get('action') == 'preserve':
            return en
        return zh if mode == 'B' else en + ('<br>' + zh if zh != en else '')

    for item in items:
        if item.get('kind') in ('table', 'table_caption', 'table_cell'):
            group = tables.setdefault(_table_key(item), {'cells': [], 'captions': []})
            group['cells' if item['kind'] == 'table_cell' else 'captions'].append(item)
    for item in items:
        kind = item.get('kind', 'sentence')
        if kind in ('table', 'table_caption', 'table_cell'):
            key = _table_key(item)
            if key in rendered:
                continue
            rendered.add(key)
            group = tables[key]
            for caption in group['captions']:
                out.append(body(caption))
            cells = group['cells']
            if not cells:
                out.append('**DRAFT: table cells unavailable**')
                continue
            simple = all(c.get('rowspan', 1) == 1 and c.get('colspan', 1) == 1 for c in cells)
            if simple:
                grid = {(c.get('row', 0), c.get('col', 0)): c for c in cells}
                rows = max(r for r, _ in grid) + 1
                cols = max(c for _, c in grid) + 1
                lines = []
                for r in range(rows):
                    row = [body(grid[(r, c)]).replace('|', r'\|').replace('\n', '<br>') if (r, c) in grid else '' for c in range(cols)]
                    lines.append('| ' + ' | '.join(row) + ' |')
                    if r == 0:
                        lines.append('| ' + ' | '.join(['---'] * cols) + ' |')
                out.append('\n'.join(lines))
            else:
                lines = ['<table>']
                for r in sorted({c.get('row', 0) for c in cells}):
                    lines.append('<tr>')
                    for c in sorted((c for c in cells if c.get('row', 0) == r), key=lambda c: c.get('col', 0)):
                        text = html.escape(body(c)).replace('&lt;br&gt;', '<br>')
                        lines.append(f'<td rowspan="{c.get("rowspan", 1)}" colspan="{c.get("colspan", 1)}">{text}</td>')
                    lines.append('</tr>')
                lines.append('</table>')
                out.append('\n'.join(lines))
            continue
        if kind in ('equation', 'math'):
            if item.get('asset_id'):
                path = asset_paths.get(item['asset_id'])
                out.append(f'![Formula]({path})' if path else '**DRAFT: missing formula asset**')
            elif item.get('math_format') == 'typst_math':
                out.append('```typst\n$ ' + _math(item) + ' $\n```')
            else:
                raw = re.sub(r'^\\begin\{equation\*?\}|\\end\{equation\*?\}$', '', item.get('source', '')).strip().strip('$')
                out.append('$$\n' + raw + '\n$$')
            continue
        if item.get('asset_id'):
            path = asset_paths.get(item['asset_id'])
            out.append(f'![{item["asset_id"]}]({path})' if path else '**DRAFT: missing graphic asset**')
        if kind == 'code':
            code = item.get('source', '')
            longest = max((len(match.group()) for match in re.finditer(r'`+', code)), default=0)
            fence = '`' * max(3, longest + 1)
            out.append(fence + '\n' + code + '\n' + fence)
            continue
        prefix = '#' * _heading_level(item) + ' ' if kind in ('heading', 'title') else ''
        out.append(prefix + body(item).replace('<br>', '\n\n'))
    return '\n\n'.join(out) + '\n'


def compile_typst(typst_file, output_pdf=None, generate_previews=False, ppi=150) -> bool:
    pdf_out = output_pdf or str(Path(typst_file).with_suffix('.pdf'))
    try:
        try:
            import typst
        except ImportError:
            subprocess.run(['typst', 'compile', str(typst_file), str(pdf_out)], check=True, capture_output=True, text=True)
        else:
            typst.compile(str(typst_file), output=str(pdf_out))
        if generate_previews:
            import pymupdf
            with pymupdf.open(pdf_out) as doc:
                for number, page in enumerate(doc, 1):
                    page.get_pixmap(dpi=ppi).save(str(Path(pdf_out).with_suffix('')) + f'_page_{number}.png')
        return True
    except Exception as exc:
        print(f'Typst compilation failed: {exc}', file=sys.stderr)
        return False


def compile_job(job_dir, mode='A', output_stage='candidate', previews=False):
    ir = _ir()
    if output_stage not in ('candidate', 'draft'):
        raise RenderError('Only candidate or draft can be compiled; acceptance follows PDF verification')
    root = Path(job_dir).resolve()
    if output_stage == 'draft':
        job = ir.load_job(root)
        records = ir.load_translation_records(job) if (root / 'translations.jsonl').is_file() else []
        targets = {r['id']: r['translation'] for r in records}
        items = [{**row, 'translation': row['source'] if row['action'] == 'preserve' else targets.get(row['id'], '[UNTRANSLATED] ' + row['source'])} for row in job['source']]
    else:
        job = ir.load_merged_job(root, require_preflight=True)
        items = job['merged']
    bindings = ir.job_bindings(job)
    directory = root / output_stage
    directory.mkdir(parents=True, exist_ok=True)
    materialized = []
    asset_paths = {}
    for asset in job['assets']:
        source = ir.resolve_job_path(root, asset['path'])
        if not source.is_file():
            if output_stage == 'draft':
                continue
            raise RenderError(f'Missing asset: {source}')
        destination = directory / 'assets' / (asset['sha256'] + source.suffix.lower())
        destination.parent.mkdir(exist_ok=True)
        shutil.copy2(source, destination)
        relative = destination.relative_to(directory).as_posix()
        materialized.append({**asset, 'path': relative})
        asset_paths[asset['asset_id']] = relative
    typ_path = directory / f'paper.{mode}.typ'
    pdf_path = directory / f'paper.{mode}.pdf'
    typ_path.write_text(render_typst_document(items, mode, {'output_stage': output_stage, 'assets': materialized, 'asset_root': str(directory)}, str(directory)), encoding='utf-8')
    markdown_path = directory / f'paper.{mode}.md'
    if output_stage == 'draft':
        draft_items = [{**i, 'translation': _texts(i, True)[1]} for i in items]
        markdown_path.write_text('DRAFT — not accepted\n\n' + export_markdown(draft_items, mode, asset_paths), encoding='utf-8')
    else:
        markdown_path.write_text(export_markdown(items, mode, asset_paths), encoding='utf-8')
    if not compile_typst(typ_path, pdf_path, previews):
        raise RenderError('Compilation failed; no candidate can be accepted')
    segments, graphics, math_ids = expected_content(items, mode) if output_stage != 'draft' else ([], [], [])
    report = {'schema_version': 3, 'status': output_stage, 'mode': mode, 'bindings': bindings,
              'pdf_path': pdf_path.relative_to(root).as_posix(), 'pdf_hash': ir.file_hash(pdf_path),
              'typ_path': typ_path.relative_to(root).as_posix(), 'typ_hash': ir.file_hash(typ_path),
              'markdown_path': markdown_path.relative_to(root).as_posix(), 'markdown_hash': ir.file_hash(markdown_path),
              'segments': segments, 'graphics': graphics, 'math_ids': math_ids,
              'assets': [{**a, 'path': (directory / a['path']).relative_to(root).as_posix()} for a in materialized]}
    ir.atomic_write_json(root / 'checks' / 'render_map.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description='Compile a verified job to a PDF candidate, or an explicit legacy draft')
    parser.add_argument('manifest', nargs='?')
    parser.add_argument('--job-dir')
    parser.add_argument('--mode', choices=list('ABCD'), default='A')
    parser.add_argument('--output-stage', choices=['candidate', 'draft'], default='candidate')
    parser.add_argument('--out-typ', default='output.typ')
    parser.add_argument('--out-pdf', default='output.pdf')
    parser.add_argument('--previews', action='store_true')
    args = parser.parse_args()
    try:
        if args.job_dir:
            if args.manifest:
                raise RenderError('Choose --job-dir or a legacy manifest, not both')
            report = compile_job(args.job_dir, args.mode, args.output_stage, args.previews)
            print(json.dumps({'status': report['status'], 'pdf_path': report['pdf_path']}, ensure_ascii=False))
        else:
            if not args.manifest or args.output_stage != 'draft':
                raise RenderError('Production compilation requires --job-dir; legacy files require --output-stage draft')
            items = _ir().read_jsonl(args.manifest)
            out = Path(args.out_typ).resolve()
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(render_typst_document(items, args.mode, {'output_stage': 'draft', 'asset_root': str(Path(args.manifest).resolve().parent)}, str(out.parent)), encoding='utf-8')
            if not compile_typst(out, args.out_pdf, args.previews):
                return 5
        return 0
    except (ValueError, OSError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
