"""Independent source coverage checks using positions and exact occurrences.

A coverage result addresses discovered source text and suspicious visible regions;
it is not a proof that a parser found every semantic element on a page.
"""
from __future__ import annotations

from pathlib import Path
import re
import unicodedata
import pymupdf


def normalize_source(text: str) -> str:
    # Whitespace has no coverage weight. NFC preserves math symbols and case.
    return re.sub(r'\s+', '', unicodedata.normalize('NFC', text))


def _pdf_raw_parts(item: dict, witnessed_words: set[str]) -> list[dict]:
    """Validate the readable source before claiming its retained native fragments.

    A complete ``raw_source`` is evidence of coverage only when the source sent to
    the translator still contains that content. Recorded line-wrap edits are
    replayed under a small allowlist, never treated as arbitrary replacements.
    """
    source = item.get('source', '')
    raw = item.get('raw_source', source)
    if not isinstance(source, str) or not isinstance(raw, str):
        raise ValueError('Source and raw_source must be strings')
    parts = item.get('source_parts')
    if parts is not None:
        if not isinstance(parts, list) or not parts:
            raise ValueError('source_parts must contain native fragments')
        for part in parts:
            if not isinstance(part, dict) or not isinstance(part.get('raw_source'), str):
                raise ValueError('Invalid native source part')
            if normalize_source(part.get('source', '')) != normalize_source(part['raw_source']):
                raise ValueError('Native source part differs from its raw text')
            if len(_pages(part)) != 1 or not _locations(part):
                raise ValueError('Every native source part needs one page and its locator')
        if raw != '\n'.join(part['raw_source'] for part in parts):
            raise ValueError('Merged raw_source differs from its ordered source_parts')
        part_locations = [loc for part in parts for loc in _locations(part)]
        if _locations(item) != part_locations:
            raise ValueError('Merged locators differ from source_parts locators')
    else:
        parts = [{**item, 'raw_source': raw}]
    edits = item.get('normalizations', [])
    if not isinstance(edits, list):
        raise ValueError('normalizations must be a list')
    cursor, output = 0, []
    for edit in edits:
        if not isinstance(edit, dict):
            raise ValueError('Invalid source normalization')
        start, end = edit.get('start'), edit.get('end')
        if (not isinstance(start, int) or not isinstance(end, int)
                or not cursor <= start < end <= len(raw)
                or edit.get('original') != raw[start:end] or edit.get('replacement') != ''):
            raise ValueError('Source normalization does not match its raw interval')
        method = edit.get('method')
        before, removed, after = raw[:start], raw[start:end], raw[end:]
        if method == 'same_document_spelling':
            left, right = re.search(r'([A-Za-z]{2,})$', before), re.match(r'([a-z]{2,})', after)
            if (not re.fullmatch(r'-\r?\n[ \t]*', removed) or not left or not right
                    or (left.group(1) + right.group(1)).casefold() not in witnessed_words
                    or edit.get('joined_spelling') != left.group(1) + right.group(1)):
                raise ValueError('Unwitnessed discretionary-hyphen normalization')
        elif method == 'retain_printed_hyphen':
            left, right = re.search(r'([A-Za-z]{2,})-$', before), re.match(r'([a-z]{2,})', after)
            if (not re.fullmatch(r'\r?\n[ \t]*', removed) or not left or not right
                    or edit.get('joined_spelling') != left.group(1) + '-' + right.group(1)):
                raise ValueError('Invalid printed-hyphen continuation')
        elif method == 'doi_suffix_continuation':
            if (not re.fullmatch(r'\r?\n[ \t]*', removed)
                    or not re.search(r'https?://(?:dx\.)?doi\.org/10\.\d{4,9}/$', before)
                    or not re.match(r'[A-Za-z0-9]', after)):
                raise ValueError('Invalid DOI continuation')
        else:
            raise ValueError(f'Unsupported source normalization: {method}')
        output.append(raw[cursor:start])
        cursor = end
    output.append(raw[cursor:])
    if normalize_source(source) != normalize_source(''.join(output)):
        raise ValueError('Readable source omits or changes retained native source text')
    return parts


def _restored_source(item: dict) -> str:
    source = item.get('source', '')
    if not isinstance(source, str):
        raise ValueError('Source must be a string')
    protected = item.get('protected_tokens', {})
    if not isinstance(protected, dict) or any(not isinstance(value, str) for value in protected.values()):
        raise ValueError('Invalid protected-token definitions')
    return re.sub(r'⟦TEX\d+⟧', lambda m: protected.get(m.group(), m.group()), source)


def _text_matches_raw(item: dict, raw: str, suffix: str) -> bool:
    source = _restored_source(item)
    if normalize_source(source) == normalize_source(raw):
        return True
    if suffix not in ('.md', '.markdown'):
        return False
    # Markdown-it unescapes punctuation inside table cells. It also strips list,
    # blockquote and heading markers; prose characters are never removed here.
    if item.get('kind') == 'table_cell':
        raw = re.sub(r'\\([!"#$%&\'()*+,\-./:;<=>?@\[\]\\^_`{|}~])', r'\1', raw)
    raw = re.sub(r'(?m)^\s{0,3}(?:>\s?)+', '', raw)
    if item.get('role') == 'list_item':
        raw = re.sub(r'(?m)^\s*(?:[-+*]|\d+[.)])\s+', '', raw)
    if item.get('kind') in ('heading', 'title'):
        raw = re.sub(r'^\s{0,3}#{1,6}\s+', '', raw)
    return normalize_source(source) == normalize_source(raw)


def _syntax_has_unverified_prose(raw: str, suffix: str) -> bool:
    """Conservatively reject prose relabelled as unowned source syntax."""
    if suffix in ('.md', '.markdown'):
        raw = re.sub(r'(?m)^\s*(?:`{3,}|~{3,})[^\n]*$', '', raw)
        raw = re.sub(r'(?m)^\s{0,3}\[[^\]\n]+\]:\s*\S+[^\n]*$', '', raw)
        raw = re.sub(r'(?m)^\s*\d+[.)]\s*$', '', raw)
    elif suffix in ('.tex', '.latex'):
        raw = re.sub(r'(?m)(?<!\\)%[^\n]*', '', raw)
        # These complete commands are project metadata or non-prose structure.
        # Text-bearing commands such as section/title/caption are deliberately
        # absent: their payload must be assigned to a source unit.
        metadata = ('documentclass|usepackage|input|include|includegraphics|label|'
                    'bibliography|addbibresource|bibliographystyle|begin|end|'
                    'setlength|newlength|setcounter|pagestyle|thispagestyle|'
                    'vspace|hspace|fontsize|selectfont|rule|cline')
        raw = re.sub(r'\\(?:' + metadata + r')\*?\s*(?:\[[^\]\n]*\]\s*)?(?:\{[^{}]*\}\s*)*', '', raw)
        raw = re.sub(r'\\[A-Za-z]+\*?', '', raw)
    elif suffix in ('.bib', '.bbl'):
        # Bibliographic metadata is intentionally retained, but must not hide a
        # title field which is part of the requested translation scope.
        return bool(re.search(r'\btitle\s*=\s*[{"][^}"]*[A-Za-z\u4e00-\u9fff]', raw, re.I))
    return any(char.isalnum() for char in raw)


def _locations(item: dict) -> list[dict]:
    values = item.get('locators') or item.get('spans') or []
    if isinstance(values, dict):
        values = [values]
    if item.get('locator'):
        values = [item['locator'], *values]
    return [loc for loc in values if isinstance(loc, dict)]


def _pages(item: dict) -> set[int]:
    pages = {loc.get('page') for loc in _locations(item) if isinstance(loc.get('page'), int)}
    if isinstance(item.get('page'), int):
        pages.add(item['page'])
    return pages


def _boxes(item: dict, page: int) -> list:
    return [pymupdf.Rect(loc['bbox']) for loc in _locations(item)
            if loc.get('page', item.get('page', page)) == page and
            isinstance(loc.get('bbox'), (list, tuple)) and len(loc['bbox']) == 4]


def _geometric_candidate(item: dict, block: dict, page: int) -> bool:
    boxes = _boxes(item, page)
    if not boxes:
        return True
    target = pymupdf.Rect(block['bbox'])
    return any((box + (-1, -1, 1, 1)).intersects(target) for box in boxes)


def _claim(text: str, blocks: list[dict], candidate_indices: list[int]) -> bool:
    """Claim one unused exact occurrence, including an occurrence across blocks."""
    if not text or not candidate_indices:
        return False
    combined = ''.join(blocks[i]['normalized'] for i in candidate_indices)
    positions = [(i, j) for i in candidate_indices for j in range(len(blocks[i]['normalized']))]
    start = combined.find(text)
    while start >= 0:
        matched = positions[start:start + len(text)]
        if all(not blocks[i]['covered'][j] for i, j in matched):
            for i, j in matched:
                blocks[i]['covered'][j] = True
            return True
        start = combined.find(text, start + 1)
    return False


def _claim_located(item: dict, blocks: list[dict], page: int):
    """Claim an exact native block interval; never fall back on a false locator.

    A small glyph such as ``1`` can overlap a neighbouring mathematical block.
    Geometric substring matching must not consume that neighbour's occurrence
    when the extractor already supplied the actual block and character offset.
    Legacy/third-party units without native intervals retain the geometric route.
    """
    locations = _locations(item)
    explicit = [loc for loc in locations if loc.get('page', page) == page
                and 'block_id' in loc and ('start' in loc or 'end' in loc)]
    if not explicit:
        return None
    if len(locations) != 1 or len(explicit) != 1:
        return False
    locator = explicit[0]
    number, start, end = locator.get('block_id'), locator.get('start'), locator.get('end')
    if type(number) is not int or type(start) is not int or type(end) is not int:
        return False
    candidates = [block for block in blocks if block['block_id'] == number]
    if len(candidates) != 1:
        return False
    block = candidates[0]
    raw = block['canonical_text']
    if (not 0 <= start < end <= len(raw)
            or raw[start:end] != item.get('raw_source', item.get('source', ''))
            or not _geometric_candidate(item, block, page)):
        return False
    before, selected, after = (normalize_source(raw[:start]), normalize_source(raw[start:end]),
                               normalize_source(raw[end:]))
    if before + selected + after != block['normalized']:
        return False
    first, last = len(before), len(before) + len(selected)
    if not selected or any(block['covered'][first:last]):
        return False
    block['covered'][first:last] = [True] * len(selected)
    return True


def scan_candidates(page, page_num: int, source_items: list[dict], regions: list[dict] | None = None) -> list[dict]:
    """Report image regions needing OCR/label review, including header-only pages."""
    candidates = []
    page_area = max(page.rect.get_area(), 1)
    native_blocks = [b for b in page.get_text('blocks') if b[6] == 0 and b[4].strip()]
    image_boxes = [pymupdf.Rect(info['bbox']) for info in page.get_image_info()]
    for index, box in enumerate(image_boxes):
        if box.get_area() <= 0:
            continue
        contained_text = sum(len(normalize_source(b[4])) for b in native_blocks
                             if (box & pymupdf.Rect(b[:4])).get_area() > 0.5 * pymupdf.Rect(b[:4]).get_area())
        evidence = []
        resolved = False
        for region in regions or []:
            if page_num not in _pages(region):
                continue
            if not any((box & rect).get_area() >= 0.8 * box.get_area() for rect in _boxes(region, page_num)):
                continue
            ev = region.get('evidence') or {}
            # A renderer's preserve flag alone is not evidence that image text was read.
            reviewed = ev.get('reviewed') is True if isinstance(ev, dict) else False
            ocr = ev.get('ocr_complete') is True if isinstance(ev, dict) else False
            mapped = [item for item in source_items if item.get('id') in region.get('unit_ids', [])]
            manual = (isinstance(ev, dict) and ev.get('method') == 'manual_transcription'
                      and ev.get('complete') is True and bool(mapped)
                      and all(item.get('provenance') == 'manual' and any(
                          (rect & box).get_area() >= 0.8 * max(rect.get_area(), 0.001)
                          for rect in _boxes(item, page_num)) for item in mapped))
            if (reviewed or ocr or manual) and region.get('disposition') != 'needs_review':
                resolved = True
                evidence.append(region.get('region_id'))
        if not resolved:
            candidates.append({'page': page_num, 'bbox': list(box), 'type': 'image_text_unverified',
                               'native_chars_inside': contained_text,
                               'area_ratio': round(box.get_area() / page_area, 4),
                               'issue': 'Image region requires OCR or documented visual/label review; a native header does not establish body coverage'})
    # A page with drawings only still contains content requiring explicit handling.
    if not native_blocks and not image_boxes and page.get_drawings():
        handled = any(page_num in _pages(r) and r.get('disposition') == 'preserve'
                      and isinstance(r.get('evidence'), dict) and r['evidence'].get('reviewed') is True
                      for r in regions or [])
        if not handled:
            candidates.append({'page': page_num, 'bbox': list(page.rect), 'type': 'vector_page_unverified',
                               'issue': 'Visible drawing content without text needs explicit source review'})
    return candidates


def _validate_text_regions(source_items, path, regions, source_root=None, input_files=None, scope=None,
                           project_dependencies=None):
    report = {'status': 'PASS', 'source_items_count': len(source_items), 'details': [],
              'coverage_basis': 'independent character intervals in decoded source files',
              'source_characters': 0, 'covered_characters': 0,
              'missing_pages': [], 'unextracted_blocks': [], 'scan_candidates': []}
    if regions is None:
        report.update(status='NOT_RUN', document_verification='NOT_RUN')
        report['details'].append('Text-source region ledger not supplied; no independent extraction coverage check.')
        return report
    source_root = Path(source_root).resolve() if source_root else path.parent.resolve()
    files = {}
    expected = input_files or [{'path': path.name}]
    expected_paths = {str(item['path'] if isinstance(item, dict) else item).replace('\\', '/').removeprefix('input/')
                      for item in expected}
    dependencies = {}
    dependency_extensions = {'style': '.sty', 'class': '.cls', 'bibliography_database': '.bib'}
    for declaration in project_dependencies or []:
        if not isinstance(declaration, dict):
            raise ValueError('Project dependency declarations must be objects')
        relative = str(declaration.get('file', '')).replace('\\', '/').removeprefix('input/')
        role = declaration.get('role')
        if (relative not in expected_paths or relative in dependencies
                or role not in dependency_extensions
                or Path(relative).suffix.lower() != dependency_extensions[role]
                or not isinstance(declaration.get('reason'), str) or not declaration['reason'].strip()):
            raise ValueError(f'Invalid project dependency declaration: {relative}')
        dependencies[relative] = declaration
    report['project_dependencies'] = list(dependencies.values())
    report['unverified_dependency_expansion'] = [name for name, entry in dependencies.items()
                                                if entry['role'] in ('style', 'class')]
    if report['unverified_dependency_expansion']:
        report['status'] = 'NEEDS_REVIEW'
        report['details'].append('Copied style/class files may expand visible content; source compilation and review remain necessary.')
    for item in expected:
        relative = item['path'] if isinstance(item, dict) else str(item)
        relative = relative.replace('\\', '/').removeprefix('input/')
        if Path(relative).suffix.lower() not in ('.md', '.markdown', '.tex', '.latex', '.bib', '.bbl') and relative not in dependencies:
            continue
        file_path = (source_root / relative).resolve()
        if not file_path.is_relative_to(source_root):
            raise ValueError(f'Source locator is outside input root: {relative}')
        text = file_path.read_text(encoding='utf-8-sig')
        files[relative.replace('\\', '/')] = {'text': text, 'covered': [False] * len(text)}
    if not files:
        report.update(status='FAIL', document_verification='NOT_RUN', char_coverage_ratio=0.0)
        report['details'].append('No supported textual source files in input provenance.')
        return report
    ids = {item['id'] for item in source_items}
    unit_intervals = {}
    grouped_intervals = {}
    for item in source_items:
        locations = _locations(item)
        if not locations:
            report['status'] = 'FAIL'
            report['details'].append(f'Source unit has no input interval: {item.get("id")}')
        for loc in locations:
            relative = str(loc.get('file', '')).replace('\\', '/').removeprefix('input/')
            start, end = loc.get('start'), loc.get('end')
            if (relative not in files or not isinstance(start, int) or not isinstance(end, int)
                    or not 0 <= start <= end <= len(files[relative]['text'])):
                report['status'] = 'FAIL'
                report['details'].append(f'Invalid source-unit interval: {item.get("id")}')
                continue
            actual = files[relative]['text'][start:end]
            if item.get('raw_source', actual) != actual:
                report['status'] = 'FAIL'
                report['details'].append(f'Source-unit raw text differs from input: {item.get("id")}')
                continue
            key = relative, start, end
            grouped_intervals.setdefault(key, []).append(item)
            unit_intervals.setdefault(item['id'], []).append(key)
    for (relative, start, end), units in grouped_intervals.items():
        try:
            restored = ' '.join(_restored_source(item) for item in units)
            combined = {**units[0], 'source': restored, 'protected_tokens': {}}
            valid = _text_matches_raw(combined, files[relative]['text'][start:end], Path(relative).suffix.lower())
        except ValueError:
            valid = False
        if not valid:
            report['status'] = 'FAIL'
            report['details'].append(f'Readable source omits or changes input at {relative}:{start}-{end}')
            for item in units:
                unit_intervals[item['id']] = [key for key in unit_intervals[item['id']]
                                              if key != (relative, start, end)]
    located = 0
    for region in regions:
        disposition = region.get('disposition')
        if disposition not in ('translate', 'preserve', 'excluded', 'needs_review'):
            report['details'].append(f'Invalid region disposition: {region.get("region_id")}')
            report['status'] = 'FAIL'
        if disposition == 'needs_review' and report['status'] != 'FAIL':
            report['status'] = 'NEEDS_REVIEW'
        if disposition == 'excluded' and not region.get('evidence'):
            report['details'].append(f'Unexplained excluded region: {region.get("region_id")}')
            report['status'] = 'FAIL'
        scope_mode = scope.get('scope', 'full') if isinstance(scope, dict) else scope or 'full'
        if disposition == 'excluded' and scope_mode != 'body':
            report['details'].append(f'Full-scope text content cannot be excluded: {region.get("region_id")}')
            report['status'] = 'FAIL'
        references = region.get('unit_ids', [])
        if (disposition == 'translate' and not references) or (disposition != 'excluded' and not set(references) <= ids):
            report['details'].append(f'Region has missing unit mappings: {region.get("region_id")}')
            report['status'] = 'FAIL'
        for loc in _locations(region):
            relative = str(loc.get('file', '')).replace('\\', '/').removeprefix('input/')
            if not relative:
                continue
            if relative not in files:
                report['details'].append(f'Unknown source file in region: {relative}')
                report['status'] = 'FAIL'
                continue
            start, end = loc.get('start'), loc.get('end')
            data = files[relative]
            if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start <= end <= len(data['text']):
                report['details'].append(f'Invalid source interval: {region.get("region_id")}')
                report['status'] = 'FAIL'
                continue
            if region.get('raw_text') is not None and region['raw_text'] != data['text'][start:end]:
                report['details'].append(f'Region source text differs at {relative}:{start}-{end}')
                report['status'] = 'FAIL'
                continue
            if disposition in ('translate', 'preserve') and references:
                represented = [False] * (end - start)
                for unit_id in references:
                    for unit_file, unit_start, unit_end in unit_intervals.get(unit_id, []):
                        if unit_file == relative and start <= unit_start <= unit_end <= end:
                            represented[unit_start - start:unit_end - start] = [True] * (unit_end - unit_start)
                omitted = ''.join(char for char, present in zip(data['text'][start:end], represented) if not present)
                if _syntax_has_unverified_prose(omitted, Path(relative).suffix.lower()):
                    report['status'] = 'FAIL'
                    report['details'].append(f'Region payload is not represented by its source units: {region.get("region_id")}')
                    continue
            elif disposition == 'preserve' and not references:
                if region.get('role') == 'project_dependency':
                    evidence = region.get('evidence', {})
                    if (relative not in dependencies or not isinstance(evidence, dict)
                            or evidence.get('method') != 'project-dependency'
                            or not isinstance(evidence.get('reason'), str) or not evidence['reason'].strip()):
                        report['status'] = 'FAIL'
                        report['details'].append(f'Unverified project dependency region: {region.get("region_id")}')
                        continue
                elif region.get('role') != 'source_syntax' or _syntax_has_unverified_prose(data['text'][start:end], Path(relative).suffix.lower()):
                    if report['status'] != 'FAIL':
                        report['status'] = 'NEEDS_REVIEW'
                    report['details'].append(f'Unowned source syntax may contain untranslated prose: {region.get("region_id")}')
            located += 1
            data['covered'][start:end] = [True] * (end - start)
    for relative, data in files.items():
        total = sum(not char.isspace() for char in data['text'])
        covered = sum(bool(covered) and not char.isspace() for char, covered in zip(data['text'], data['covered']))
        report['source_characters'] += total
        report['covered_characters'] += covered
        if total != covered:
            report['unextracted_blocks'].append({'file': relative, 'uncovered_characters': total - covered})
    if report['unextracted_blocks']:
        report['status'] = 'FAIL' if located or report['status'] == 'FAIL' else 'NEEDS_REVIEW'
        report['details'].append('Unaccounted source character intervals remain.')
    report['char_coverage_ratio'] = round(report['covered_characters'] / report['source_characters'], 6) if report['source_characters'] else 0
    report['document_verification'] = 'RUN' if located else 'NOT_RUN'
    if not report['source_characters'] and not any(item.get('source', '').strip() for item in source_items):
        report['status'] = 'FAIL'
        report['details'].append('No source content to verify.')
    return report


def validate_source_coverage(source_items: list[dict], doc_path=None, regions=None, scope=None, source_root=None, input_files=None,
                             project_dependencies=None) -> dict:
    pages = sorted({p for item in source_items for p in _pages(item)})
    report = {'status': 'PASS', 'source_items_count': len(source_items), 'manifest_pages': pages,
              'missing_pages': [], 'char_coverage_ratio': 1.0, 'unextracted_blocks': [],
              'unmatched_source_items': [], 'scan_candidates': [], 'details': [],
              'coverage_basis': 'exact native-text occurrences with source positions when available'}
    if not source_items:
        report.update(status='FAIL', char_coverage_ratio=0.0)
        report['details'].append('Source manifest contains 0 items.')
        return report
    if not doc_path:
        report['document_verification'] = 'NOT_RUN'
        report['details'].append('No original document supplied; source extraction was not independently checked.')
        return report
    path = Path(doc_path)
    if not path.is_file():
        report.update(status='FAIL', char_coverage_ratio=0.0)
        report['details'].append(f'Document file does not exist: {path}')
        return report
    if path.suffix.lower() != '.pdf':
        return _validate_text_regions(source_items, path, regions, source_root, input_files, scope, project_dependencies)
    total, covered = 0, 0
    try:
        with pymupdf.open(path) as doc:
            report['doc_pages_count'] = len(doc)
            report['document_verification'] = 'RUN'
            if not len(doc):
                raise ValueError('PDF has no pages')
            native_pages = {index + 1: [b for b in page.get_text('blocks') if b[6] == 0 and b[4].strip()]
                            for index, page in enumerate(doc)}
            witnessed_words = {word.casefold() for blocks in native_pages.values() for block in blocks
                               for word in re.findall(r'[A-Za-z]+', block[4])}
            native_parts = []
            for item in source_items:
                try:
                    native_parts.extend({**part, 'id': item.get('id'),
                                         'provenance': item.get('provenance'),
                                         'extraction_method': item.get('extraction_method')}
                                        for part in _pdf_raw_parts(item, witnessed_words))
                except ValueError as exc:
                    report['status'] = 'FAIL'
                    report['details'].append(f'Invalid source provenance for {item.get("id")}: {exc}')
            for page_index, page in enumerate(doc):
                page_num = page_index + 1
                blocks = []
                canonical = {b.get('number', n): '\n'.join(''.join(s.get('text', '') for s in line.get('spans', []))
                             for line in b.get('lines', []))
                             for n, b in enumerate(page.get_text('dict').get('blocks', [])) if b.get('type') == 0}
                for raw in native_pages[page_num]:
                    text = normalize_source(raw[4])
                    blocks.append({'bbox': list(raw[:4]), 'text': raw[4], 'normalized': text,
                                   'block_id': raw[5], 'canonical_text': canonical.get(raw[5], raw[4].rstrip('\n')),
                                   'covered': [False] * len(text)})
                p_items = [i for i in native_parts if page_num in _pages(i)]
                # Explicit exclusions can cover repeated headers, never inferred by
                # length or vocabulary similarity. Reasons are required.
                excluded = []
                for region in regions or []:
                    if page_num not in _pages(region) or region.get('disposition') != 'excluded':
                        continue
                    if not region.get('evidence'):
                        report['details'].append(f'Exclusion {region.get("region_id")} lacks evidence')
                        report['status'] = 'FAIL'
                        continue
                    scope_mode = scope.get('scope', 'full') if isinstance(scope, dict) else scope or 'full'
                    if scope_mode != 'body':
                        role = region.get('role')
                        boxes = _boxes(region, page_num)
                        margin = boxes and all(box.y1 < .1 * page.rect.height or box.y0 > .89 * page.rect.height for box in boxes)
                        if role not in ('header', 'footer', 'page_number', 'running_matter') or not margin:
                            report['details'].append(f'Full-scope body text cannot be excluded: {region.get("region_id")}')
                            report['status'] = 'FAIL'
                            continue
                    excluded.append({**region, 'source': region.get('raw_text', '')})
                for item in [*p_items, *excluded]:
                    text = normalize_source(item.get('raw_source', item.get('source', '')))
                    if not text:
                        continue
                    is_transcribed = item.get('provenance') in ('ocr', 'manual') or item.get('extraction_method') in ('ocr', 'manual_transcription') or any(loc.get('method') in ('ocr', 'manual_transcription') for loc in _locations(item))
                    matched = None if is_transcribed else _claim_located(item, blocks, page_num)
                    if matched is None:
                        candidates = [i for i, block in enumerate(blocks) if _geometric_candidate(item, block, page_num)]
                        matched = _claim(text, blocks, candidates)
                    if not matched:
                        # OCR text exists in an image, not the native text layer.
                        # Its source provenance is checked in the extraction ledger.
                        if not is_transcribed:
                            report['unmatched_source_items'].append({'id': item.get('id'), 'page': page_num,
                                                                     'source': item.get('source', '')[:120]})
                for block in blocks:
                    total += len(block['normalized'])
                    covered += sum(block['covered'])
                    if not all(block['covered']):
                        missing_text = ''.join(ch for ch, was_covered in zip(block['normalized'], block['covered']) if not was_covered)
                        report['unextracted_blocks'].append({'page': page_num, 'bbox': block['bbox'],
                                                            'text': block['text'][:160], 'uncovered_text': missing_text,
                                                            'uncovered_characters': block['covered'].count(False)})
                scans = scan_candidates(page, page_num, p_items, regions)
                report['scan_candidates'].extend(scans)
                if page_num not in pages and (blocks or page.get_images() or page.get_drawings()):
                    report['missing_pages'].append(page_num)
            report['source_characters'] = total
            report['covered_characters'] = covered
            report['char_coverage_ratio'] = round(covered / total, 6) if total else 0.0
            if report['unextracted_blocks'] or report['unmatched_source_items'] or report['missing_pages']:
                report['status'] = 'FAIL'
            elif report['scan_candidates'] and report['status'] != 'FAIL':
                report['status'] = 'NEEDS_REVIEW'
            transcribed = [i for i in source_items if i.get('provenance') in ('ocr', 'manual') or i.get('extraction_method') in ('ocr', 'manual_transcription')]
            report['transcribed_items'] = len(transcribed)
            if not total and transcribed:
                report['native_text_check'] = 'NOT_APPLICABLE'
                report['char_coverage_ratio'] = None
            if not total and not transcribed:
                report['status'] = 'FAIL'
                report['details'].append('No native text coverage; OCR or independently reviewed preserved regions are required.')
            if report['unextracted_blocks']:
                report['details'].append(f'{len(report["unextracted_blocks"])} source text block(s) contain uncovered characters.')
    except Exception as exc:
        report['status'] = 'FAIL'
        report['details'].append(f'PDF coverage check error: {exc}')
    return report
