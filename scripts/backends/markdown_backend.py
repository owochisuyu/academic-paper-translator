"""Markdown-it based source extraction with explicit table cells and protected code."""
from __future__ import annotations

from pathlib import Path
import importlib.metadata
import re

from .common import digest, normalize_with_map, sentence_ranges, register_syntax_gaps
try:
    from ..align_bilingual import split_en_sentences
except ImportError:
    from align_bilingual import split_en_sentences


def extract_markdown(path):
    try:
        from markdown_it import MarkdownIt
    except ImportError as exc:
        raise ImportError('Markdown input requires markdown-it-py') from exc
    path = Path(path).resolve()
    raw = path.read_text(encoding='utf-8-sig')
    lines = raw.splitlines(keepends=True)
    starts, offset = [], 0
    for line in lines:
        starts.append(offset)
        offset += len(line)
    starts.append(offset)
    parser = MarkdownIt('commonmark').enable('table')
    tokens = parser.parse(raw)
    items, regions, assets, warnings = [], [], [], []
    section, sec_idx, paragraph_idx, table_idx = 'Title & Abstract', 0, 0, 0
    stack, table_id, row, col = [], None, -1, -1
    region_counter = 0
    cell_cursor = 0
    current_row_map = None
    heading_level = 1
    used_spans = set()

    def locate(token, text=None):
        span = token.map or current_row_map or [0, len(lines)]
        start, end = starts[span[0]], starts[span[1]]
        if table_id and col >= 0 and token.type == 'inline':
            line = raw[start:end].rstrip('\r\n')
            boundaries = [-1] + [m.start() for m in re.finditer(r'(?<!\\)\|', line)] + [len(line)]
            ranges = [(a + 1, b) for a, b in zip(boundaries, boundaries[1:])]
            if ranges and not line[ranges[0][0]:ranges[0][1]].strip():
                ranges.pop(0)
            if ranges and not line[ranges[-1][0]:ranges[-1][1]].strip():
                ranges.pop()
            if col < len(ranges):
                a, b = ranges[col]
                cell = line[a:b]
                left = len(cell) - len(cell.lstrip())
                right = len(cell.rstrip())
                return {'file': path.name, 'start': start + a + left, 'end': start + a + max(left, right),
                        'line_start': span[0] + 1, 'line_end': span[1], 'transform': 'markdown_table_cell'}
        if text:
            pos = raw.find(text, start, end)
            while pos >= 0 and (pos, pos + len(text)) in used_spans:
                pos = raw.find(text, pos + 1, end)
            if pos >= 0:
                start, end = pos, pos + len(text)
                used_spans.add((start, end))
        return {'file': path.name, 'start': start, 'end': end, 'line_start': span[0] + 1, 'line_end': span[1]}

    def emit(text, kind='sentence', token=None, extra=None, split=True, action='translate'):
        nonlocal region_counter, paragraph_idx
        region_counter += 1
        region_id = f'md-region-{region_counter:04d}'
        paragraph_idx += 1
        locator = locate(token, text)
        locator['region_id'] = region_id
        region = {'region_id': region_id, 'locator': locator, 'role': kind, 'raw_text': raw[locator['start']:locator['end']],
                  'normalized_text': normalize_with_map(text)[0], 'disposition': action, 'unit_ids': [],
                  'evidence': {'parser': 'markdown-it-py', 'transform': 'markdown_token'}}
        units = sentence_ranges(text, split_en_sentences) if split and text.strip() else [(text, 0, len(text), [])]
        for index, (sentence, start, end, mapping) in enumerate(units):
            item_id = f'sec{sec_idx:02d}-p001-b{paragraph_idx:03d}-s{index + 1:03d}'
            exact = raw[locator['start']:locator['end']] == text
            unit_locator = {**locator, 'token_start': start, 'token_end': end}
            if exact:
                unit_locator.update(start=locator['start'] + start, end=locator['start'] + end)
            item = {'id': item_id, 'kind': kind, 'section': section, 'page': 1, 'spans': [],
                    'parent_id': region_id, 'locators': [unit_locator],
                    'source': sentence, 'raw_source': raw[unit_locator['start']:unit_locator['end']], 'normalization_map': mapping,
                    'status': 'pending', 'action': action if sentence.strip() else 'preserve'}
            if extra:
                item.update(extra)
            items.append(item)
            region['unit_ids'].append(item['id'])
        regions.append(region)
        return items[-len(units):]

    for index, token in enumerate(tokens):
        typ = token.type
        if typ == 'heading_open':
            heading_level = int(token.tag[1:])
        if typ == 'table_open':
            table_idx += 1
            table_id, row, col = f'md-table-{table_idx:03d}', -1, -1
        elif typ == 'tr_open':
            row += 1
            col = -1
            current_row_map = token.map
            cell_cursor = 0
        elif typ in ('th_open', 'td_open'):
            col += 1
        if token.nesting == 1:
            stack.append(typ)
        if typ in ('fence', 'code_block'):
            emit(token.content, 'code', token, {'language': token.info.strip()}, split=False, action='preserve')
        elif typ in ('html_block',):
            emit(token.content, 'code', token, split=False, action='preserve')
            warnings.append({'code': 'markdown_html_requires_review', 'line': (token.map or [0])[0] + 1})
        elif typ == 'inline':
            if 'heading_open' in stack:
                sec_idx += 1
                section = token.content
                emit(token.content, 'heading', token, {'id': f'sec{sec_idx:02d}-head', 'heading_level': heading_level}, split=False)
            elif 'th_open' in stack or 'td_open' in stack:
                emit(token.content, 'table_cell', token,
                     {'id': f'{table_id}-cell-r{row + 1}c{col + 1}', 'table_id': table_id, 'row': row, 'col': col,
                      'rowspan': 1, 'colspan': 1, 'parent_id': table_id, 'empty_cell': not token.content.strip()}, split=False)
            elif token.content.strip().startswith('$$') and token.content.strip().endswith('$$'):
                emit(token.content, 'equation', token, {'math_format': 'latex'}, split=False, action='preserve')
            elif len(token.children or []) == 1 and token.children[0].type == 'image':
                image_token = token.children[0]
                image = image_token.attrGet('src') or ''
                alt = image_token.content
                figure = emit(alt, 'figure', token, {'figure_no': str(len(assets) + 1)}, split=False)[0]
                image_path = path.parent / image
                if re.match(r'^[A-Za-z]+://', image):
                    warnings.append({'code': 'remote_image_not_downloaded', 'id': figure['id'], 'url': image})
                elif image_path.is_file():
                    asset_id = f'{digest(path)[:16]}:md-image:{len(assets) + 1}'
                    figure.update(asset_id=asset_id, image_path=str(image_path.resolve()))
                    assets.append({'asset_id': asset_id, 'doc_id': digest(path), 'kind': 'figure',
                                   'representation': 'original', 'path': str(image_path.resolve()), 'sha256': digest(image_path),
                                   'owner_ids': [figure['id']], 'locators': figure['locators']})
                    warnings.append({'code': 'image_text_unverified', 'id': figure['id'], 'asset_id': asset_id})
                else:
                    warnings.append({'code': 'missing_figure_asset', 'id': figure['id'], 'path': image})
            else:
                kind = 'reference' if 'reference' in section.casefold() or 'bibliography' in section.casefold() else 'sentence'
                emitted = emit(token.content, kind, token, {'role': 'list_item' if 'list_item_open' in stack else 'paragraph'})
                # Store link targets separately so consumers can preserve/review them without interpreting them as commands.
                links = [child.attrGet('href') for child in token.children or [] if child.type == 'link_open']
                for item in emitted:
                    item['links'] = links
                if any(child.type == 'html_inline' for child in token.children or []):
                    warnings.append({'code': 'markdown_html_requires_review', 'id': emitted[0]['id']})
                if any(child.type == 'image' for child in token.children or []):
                    warnings.append({'code': 'inline_image_requires_review', 'id': emitted[0]['id']})
        if token.nesting == -1:
            if stack:
                stack.pop()
            if typ == 'table_close':
                table_id = None
                current_row_map = None
    for table_number in range(1, table_idx + 1):
        ident = f'md-table-{table_number:03d}'
        cells = [item for item in items if item.get('table_id') == ident]
        for item in cells:
            item['table_rows'] = max(c['row'] for c in cells) + 1
            item['table_cols'] = max(c['col'] for c in cells) + 1
    register_syntax_gaps(regions, path, raw, path.name)
    return {'items': items, 'regions': regions, 'assets': assets, 'warnings': warnings,
            'document': {'source_format': 'markdown', 'extractor': {'name': 'markdown-it-py', 'version': importlib.metadata.version('markdown-it-py')}}}
