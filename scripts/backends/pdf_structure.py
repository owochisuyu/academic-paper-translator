"""Native text geometry helpers for table fidelity, running matter and references."""
from __future__ import annotations

from collections import defaultdict
import re
import pymupdf

from .common import normalize_with_map


def repeated_running_matter(document):
    candidates = defaultdict(list)
    single_page_numbers = {}
    for page_index, page in enumerate(document):
        for block in page.get_text('blocks'):
            if block[6] != 0:
                continue
            text = block[4].strip()
            if not text:
                continue
            placement = 'footer' if block[1] > .9 * page.rect.height else 'header' if block[3] < .09 * page.rect.height else None
            if placement is None:
                continue
            if (placement == 'footer' and text == str(page_index + 1)
                    and abs((block[0] + block[2]) / 2 - page.rect.width / 2) <= .05 * page.rect.width):
                key = (page_index + 1, tuple(round(v, 2) for v in block[:4]))
                single_page_numbers[key] = {
                    'role': 'page_number', 'reason': 'Standalone centered physical page number outside the body area',
                    'pages': [page_index + 1], 'signature': '<page-number>'}
            lines = text.splitlines()
            if lines and re.fullmatch(r'\s*\d+\s*', lines[-1]):
                lines = lines[:-1]
            signature = re.sub(r'\s+', ' ', '\n'.join(lines)).strip()
            if not signature and text.strip() == str(page_index + 1):
                signature = '<page-number>'
            if signature:
                candidates[(placement, signature)].append((page_index + 1, tuple(round(v, 2) for v in block[:4])))
    repeated = {key: {'role': placement, 'reason': 'Repeated running matter outside body area',
                  'pages': sorted({p for p, _ in occurrences}), 'signature': signature}
            for (placement, signature), occurrences in candidates.items()
            if len({p for p, _ in occurrences}) >= 2
            for key in occurrences}
    return {**single_page_numbers, **repeated}


def native_lines(page):
    result = []
    for block in page.get_text('dict')['blocks']:
        if block.get('type') != 0:
            continue
        for line in block.get('lines', []):
            text = ''.join(s.get('text', '') for s in line.get('spans', []))
            if text.strip():
                result.append({'text': text, 'bbox': pymupdf.Rect(line['bbox']),
                               'baseline': line['spans'][0].get('origin', [0, line['bbox'][3]])[1]})
    return result


def refine_table(table, page, table_id):
    """Retain native span text and split clearly aligned identifier rows.

    PyMuPDF's table text merger can move underscores and separate ligature pieces.
    Source glyph lines have the correct spelling. Split a detected multi-row band
    only when every column agrees on baselines and the key column contains separate
    identifier tokens; other multiline cells remain intact and are flagged.
    """
    lines = native_lines(page)
    old_grid = table.extract()
    output, warnings, logical_row = [], [], 0
    table_box = pymupdf.Rect(table.bbox)
    for row_index, row in enumerate(table.rows):
        columns = []
        for col_index, raw_box in enumerate(row.cells):
            if raw_box is None:
                columns.append(None)
                continue
            box = pymupdf.Rect(raw_box)
            assigned = []
            for line in lines:
                glyph_box = line['bbox']
                x = (glyph_box.x0 + glyph_box.x1) / 2
                y = (glyph_box.y0 + glyph_box.y1) / 2
                if box.x0 - .2 <= x <= box.x1 + .2 and box.y0 - .2 <= y <= box.y1 + .2:
                    assigned.append(line)
            assigned.sort(key=lambda x: (x['baseline'], x['bbox'].x0))
            columns.append((box, assigned))
        present = [c for c in columns if c]
        equal_lines = bool(present) and len({len(c[1]) for c in present}) == 1 and len(present[0][1]) > 1
        split = equal_lines and all(re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', line['text'].strip()) for line in present[0][1])
        if split:
            split = all(max(c[1][i]['baseline'] for c in present) - min(c[1][i]['baseline'] for c in present) <= 2.0 for i in range(len(present[0][1])))
        count = len(present[0][1]) if split else 1
        if equal_lines and not split:
            warnings.append({'code': 'ambiguous_multiline_table_row', 'table_id': table_id, 'detected_row': row_index})
        for subrow in range(count):
            for col_index, column in enumerate(columns):
                if column is None:
                    continue
                box, assigned = column
                selected = assigned[subrow:subrow + 1] if split else assigned
                if selected:
                    value = '\n'.join(line['text'] for line in selected)
                    glyph_box = pymupdf.Rect(selected[0]['bbox'])
                    for line in selected[1:]:
                        glyph_box |= line['bbox']
                    if split:
                        cell_box = pymupdf.Rect(box.x0, glyph_box.y0, box.x1, glyph_box.y1)
                    else:
                        cell_box = box | glyph_box
                    table_box |= cell_box
                else:
                    value = old_grid[row_index][col_index] or ''
                    cell_box = box
                    if value:
                        warnings.append({'code': 'table_text_geometry_unavailable', 'table_id': table_id, 'detected_row': row_index, 'col': col_index})
                output.append({'row': logical_row, 'col': col_index, 'source': value, 'bbox': list(cell_box),
                               'rowspan': 1, 'colspan': 1, 'recovered_row_split': split})
            logical_row += 1
    xs = sorted({round(v, 2) for row in table.rows for b in row.cells if b for v in (b[0], b[2])})
    for cell in output:
        cell['colspan'] = max(1, sum(cell['bbox'][0] + .1 < x <= cell['bbox'][2] + .1 for x in xs))
        cell['table_rows'] = logical_row
        cell['table_cols'] = max(c['col'] for c in output) + 1
    return output, table_box, warnings


def merge_hanging_references(items, regions):
    """Combine adjacent hanging-indented fragments, retaining each native source part."""
    result, redirects = [], {}
    for item in items:
        if item.get('kind') != 'reference':
            result.append(item)
            continue
        locator = (item.get('locators') or [{}])[0]
        box = locator.get('bbox')
        previous = result[-1] if result else None
        previous_box = (previous.get('locators') or [{}])[-1].get('bbox') if previous else None
        first_box = (previous.get('locators') or [{}])[0].get('bbox') if previous else None
        continuation = (previous and previous.get('kind') == 'reference' and previous.get('page') == item.get('page')
                        and box and previous_box and first_box and box[0] >= first_box[0] + 4
                        and -4 <= box[1] - previous_box[3] <= 18
                        and not re.match(r'^\[\d+\]', item['source']))
        if continuation:
            previous.setdefault('source_parts', [{'source': previous['source'], 'raw_source': previous.get('raw_source', previous['source']),
                                                   'locators': list(previous['locators']), 'spans': list(previous.get('spans', []))}])
            previous['source_parts'].append({'source': item['source'], 'raw_source': item.get('raw_source', item['source']),
                                             'locators': item['locators'], 'spans': item.get('spans', [])})
            previous['source'] += ' ' + item['source']
            previous['raw_source'] += '\n' + item.get('raw_source', item['source'])
            previous['locators'].extend(item['locators'])
            previous['spans'].extend(item.get('spans', []))
            previous['normalization_map'] = []
            redirects[item['id']] = previous['id']
        else:
            result.append(item)
    for region in regions:
        region['unit_ids'] = list(dict.fromkeys(redirects.get(i, i) for i in region.get('unit_ids', [])))
    return result


def merge_page_continuations(items, regions):
    """Join an unfinished sentence with a lowercase continuation on the next page."""
    by_page = defaultdict(list)
    for item in items:
        if item.get('kind') == 'sentence':
            by_page[item['page']].append(item)
    redirects = {}
    for page in sorted(by_page):
        if page + 1 not in by_page or not by_page[page] or not by_page[page + 1]:
            continue
        previous, following = by_page[page][-1], by_page[page + 1][0]
        if previous['id'] in redirects or following['id'] in redirects:
            continue
        if (previous.get('section') != following.get('section') or re.search(r'[.!?。！？:;][\"\')\]]?$', previous['source'].strip())
                or not re.match(r'^[a-z]', following['source'].lstrip())):
            continue
        previous.setdefault('source_parts', [{'source': previous['source'], 'raw_source': previous.get('raw_source', previous['source']),
                                               'locators': list(previous['locators']), 'spans': list(previous.get('spans', []))}])
        previous['source_parts'].append({'source': following['source'], 'raw_source': following.get('raw_source', following['source']),
                                         'locators': following['locators'], 'spans': following.get('spans', [])})
        previous['source'] += ' ' + following['source']
        previous['raw_source'] += '\n' + following.get('raw_source', following['source'])
        previous['locators'].extend(following['locators'])
        previous['spans'].extend(following.get('spans', []))
        previous['normalization_map'] = []
        previous['cross_page_continuation'] = True
        redirects[following['id']] = previous['id']
    for region in regions:
        region['unit_ids'] = list(dict.fromkeys(redirects.get(i, i) for i in region.get('unit_ids', [])))
    return [i for i in items if i['id'] not in redirects]


def normalize_line_wraps(items, regions):
    """Repair witnessed line wraps while keeping every native source character.

    A discretionary hyphen is removed only if the joined spelling occurs elsewhere
    in this document. Otherwise the printed hyphen remains. A DOI ending in its
    required suffix separator is joined to the following source line. All edits
    retain raw offsets and are separately recorded for source review.
    """
    words = {word.casefold() for region in regions for word in re.findall(r'[A-Za-z]+', region.get('raw_text', ''))}
    for item in items:
        if item.get('kind') not in ('sentence', 'reference', 'heading', 'title', 'list_item', 'figure', 'caption'):
            continue
        raw = item.get('raw_source', '')
        if normalize_with_map(raw)[0] != item.get('source'):
            continue
        edits = []
        for match in re.finditer(r'([A-Za-z]{2,})-\r?\n[ \t]*([a-z]{2,})', raw):
            fused = match.group(1) + match.group(2)
            remove_hyphen = fused.casefold() in words
            start = match.start(1) + len(match.group(1)) + (0 if remove_hyphen else 1)
            end = match.start(2)
            edits.append({'start': start, 'end': end, 'original': raw[start:end], 'replacement': '',
                          'method': 'same_document_spelling' if remove_hyphen else 'retain_printed_hyphen',
                          'joined_spelling': fused if remove_hyphen else match.group(1) + '-' + match.group(2)})
        for match in re.finditer(r'https?://(?:dx\.)?doi\.org/10\.\d{4,9}/(\r?\n[ \t]*)(?=[A-Za-z0-9])', raw):
            edits.append({'start': match.start(1), 'end': match.end(1), 'original': match.group(1),
                          'replacement': '', 'method': 'doi_suffix_continuation'})
        if not edits:
            continue
        edits.sort(key=lambda edit: edit['start'])
        chars, indices, cursor = [], [], 0
        for edit in edits:
            if edit['start'] < cursor:
                raise ValueError('Overlapping source normalization edits')
            chars.extend(raw[cursor:edit['start']])
            indices.extend(range(cursor, edit['start']))
            cursor = edit['end']
        chars.extend(raw[cursor:])
        indices.extend(range(cursor, len(raw)))
        normalized, mapping = normalize_with_map(''.join(chars))
        item['source'] = normalized
        item['normalization_map'] = [[indices[a], indices[b - 1] + 1] for a, b in mapping]
        item['normalizations'] = edits
    return items
