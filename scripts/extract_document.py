# -*- coding: utf-8 -*-
"""Extract source units and a positional region ledger before any translation."""
from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import re
import sys
from pathlib import Path
import pymupdf

try:
    from .align_bilingual import split_en_sentences
    from .backends.common import normalize_with_map, sentence_ranges, scoped_out
except ImportError:
    from align_bilingual import split_en_sentences
    from backends.common import normalize_with_map, sentence_ranges, scoped_out

FIGURE_CAPTION_REGEX = re.compile(r'^(?:Fig\.|Figure|Supplementary\s+Figure)\s*([A-Za-z0-9_.-]+)', re.I)
TABLE_CAPTION_REGEX = re.compile(r'^(?:Table|Tab\.)\s*([A-Za-z0-9_.-]+)', re.I)
SECTION_HEADING_REGEX = re.compile(r'^(?:[0-9]+(?:\.[0-9]+)*\.?\s+[A-Z]|[A-Z]\.\s+[A-Z]|Abstract|Introduction|Related\s+Work|Method|Background|Results|Discussion|Conclusion|Acknowledg|References|Bibliography|Appendix)', re.I)
REFERENCE_LINE_REGEX = re.compile(r'^\[\s*\d+\s*\]\s+')
EQUATION_LINE_REGEX = re.compile(r'\(\s*([0-9]+[a-z]?)\s*\)\s*$')


def clean_text(text):
    return normalize_with_map(text or '')[0]


def sanitize_id_part(text):
    return re.sub(r'[^A-Za-z0-9_.-]', '_', text.strip()) or '001'


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _load_assets(path, doc_id):
    try:
        from .document_ir import read_json
    except ImportError:
        from document_ir import read_json
    data = read_json(path)
    assets = data.get('assets', []) if isinstance(data, dict) else data
    if not isinstance(assets, list):
        raise ValueError('Asset manifest must be a list or an object containing assets')
    seen, result = set(), []
    for original in assets:
        item = dict(original)
        if item.get('doc_id') != doc_id:
            raise ValueError('Asset belongs to a different or unspecified document')
        key = item.get('asset_id')
        if not key or key in seen:
            raise ValueError('Missing or duplicate asset_id')
        seen.add(key)
        raw_path = item.get('path') or item.get('image_path')
        if not raw_path:
            raise ValueError(f'Missing path for asset {key}')
        image = Path(raw_path)
        if not image.is_absolute():
            image = Path(path).resolve().parent / image
        if not image.is_file() or _sha(image) != item.get('sha256'):
            raise ValueError(f'Missing or modified asset {key}')
        item.update(path=str(image.resolve()), image_path=str(image.resolve()))
        result.append(item)
    return result


class DocumentExtractor:
    def __init__(self, scope='full', assets_manifest=None, asset_dir=None,
                 backend='native', mineru_result=None, main_file=None, source_overlay=None,
                 equation_overlay=None):
        if scope not in ('full', 'body'):
            raise ValueError(f'Invalid scope: {scope}')
        self.scope = scope
        self.assets_manifest = assets_manifest
        self.asset_dir = Path(asset_dir).resolve() if asset_dir else None
        self.backend = backend
        self.mineru_result = mineru_result
        self.main_file = main_file
        self.source_overlay = source_overlay
        self.equation_overlay = equation_overlay
        self.regions, self.assets, self.warnings = [], [], []
        self.extra_document = {}

    def extract(self, input_path):
        path = Path(input_path).resolve()
        if not path.exists():
            raise FileNotFoundError(path)
        self.regions, self.assets, self.warnings = [], [], []
        self.extra_document = {}
        suffix = path.suffix.lower()
        if path.is_dir() or suffix in ('.tex', '.latex'):
            try:
                from .backends.latex_backend import extract_project
            except ImportError:
                from backends.latex_backend import extract_project
            parsed = extract_project(path, main_file=self.main_file)
            items = parsed['items']
            self._adopt(parsed)
            total_pages = 1
        elif suffix == '.pdf':
            if self.backend == 'mineru':
                try:
                    from .backends.mineru_backend import extract_mineru
                except ImportError:
                    from backends.mineru_backend import extract_mineru
                parsed = extract_mineru(path, result_path=self.mineru_result)
                items = parsed['items']
                self._adopt(parsed)
                total_pages = parsed['pages']
            else:
                items = self._extract_pdf(path)
                with pymupdf.open(path) as doc:
                    total_pages = len(doc)
                self.extra_document.update(extractor={'name': 'pymupdf', 'version': pymupdf.VersionBind},
                                           source_format='pdf', processed_pages=list(range(1, total_pages + 1)))
                table_signature = inspect.signature(pymupdf.table.find_tables).parameters
                layout_parameter = table_signature.get('use_layout')
                self.extra_document['table_detection'] = {
                    'method': 'pymupdf.Page.find_tables',
                    'use_layout_default': layout_parameter.default if layout_parameter else 'not available in this version',
                    'failure_policy': 'NEEDS_REVIEW; no silent fallback to incomplete tables',
                }
        elif suffix in ('.md', '.markdown'):
            try:
                from .backends.markdown_backend import extract_markdown
            except ImportError:
                from backends.markdown_backend import extract_markdown
            parsed = extract_markdown(path)
            items = parsed['items']
            self._adopt(parsed)
            total_pages = 1
        else:
            raise ValueError('Supported inputs: PDF, Markdown, .tex, or a LaTeX project directory')
        if self.equation_overlay:
            if suffix != '.pdf' or self.backend != 'native':
                raise ValueError('Equation overlays require native PDF extraction')
            try:
                from .backends.pdf_equation_overlay import apply_equation_overlay
            except ImportError:
                from backends.pdf_equation_overlay import apply_equation_overlay
            self.warnings, overlay_info = apply_equation_overlay(
                path, self.equation_overlay, items, self.regions, self.assets, self.warnings)
            self.extra_document['equation_overlay'] = overlay_info
        if self.source_overlay:
            if suffix != '.pdf':
                raise ValueError('Manual source overlays currently support PDF regions only')
            try:
                from .backends.manual_overlay import apply_overlay
            except ImportError:
                from backends.manual_overlay import apply_overlay
            self.warnings, overlay_info = apply_overlay(path, self.source_overlay, items, self.regions, self.assets, self.warnings)
            self.extra_document['source_overlay'] = overlay_info
        if suffix == '.pdf':
            try:
                from .backends.pdf_order import order_pdf_units
            except ImportError:
                from backends.pdf_order import order_pdf_units
            items = order_pdf_units(items, self.assets)
            if self.backend == 'native':
                try:
                    from .backends.pdf_references import reconcile_numbered_references
                except ImportError:
                    from backends.pdf_references import reconcile_numbered_references
                items, reference_info = reconcile_numbered_references(items, self.regions, self.warnings)
                self.extra_document['reference_reconciliation'] = reference_info
            try:
                from .backends.pdf_structure import merge_hanging_references, merge_page_continuations, normalize_line_wraps
            except ImportError:
                from backends.pdf_structure import merge_hanging_references, merge_page_continuations, normalize_line_wraps
            items = merge_hanging_references(items, self.regions)
            items = merge_page_continuations(items, self.regions)
            items = normalize_line_wraps(items, self.regions)
            self.extra_document['reading_order_method'] = 'geometry: paragraph/table/figure groups; two-column bands when both columns are detected; requires source visual review'
        ids = [i['id'] for i in items]
        if len(ids) != len(set(ids)):
            raise ValueError('Extractor generated duplicate source IDs')
        excluded = [i for i in items if self.scope == 'body' and scoped_out(i)]
        excluded_ids = {i['id'] for i in excluded}
        for region in self.regions:
            owners = region.get('unit_ids', [])
            if owners and all(i in excluded_ids for i in owners):
                region['disposition'] = 'excluded'
                region['evidence'] = {'rule': 'scope=body', 'reason': 'References, footnotes, acknowledgements and appendices excluded'}
        items = [i for i in items if i['id'] not in excluded_ids]
        for order, item in enumerate(items):
            item.setdefault('status', 'pending')
            item.setdefault('action', 'preserve' if item['kind'] in ('equation', 'code') or not item.get('source', '').strip() else 'translate')
            item.setdefault('parent_id', item['id'])
            item.setdefault('section_path', [item.get('section', '')])
            item.setdefault('spans', [])
            item['order'] = order
        self.extra_document['extraction_warnings'] = self.warnings
        self.extra_document['excluded_items'] = [{'id': i['id'], 'kind': i['kind'], 'source': i['source'], 'locators': i.get('locators', []), 'reason': 'scope=body'} for i in excluded]
        stats = self._compute_summary(items, total_pages)
        stats.update(warnings=self.warnings, excluded_items=len(excluded), backend=self.backend,
                     excluded_regions=sum(r.get('disposition') == 'excluded' for r in self.regions),
                     running_matter_regions=sum(r.get('role') == 'running_matter' for r in self.regions),
                     extraction_status='NEEDS_REVIEW' if self.warnings else 'PASS')
        return items, stats

    def _adopt(self, result):
        self.regions = result.get('regions', [])
        self.assets = result.get('assets', [])
        self.warnings = result.get('warnings', [])
        self.extra_document = result.get('document', {})

    def _extract_markdown(self, path):
        from importlib import import_module
        module = import_module(f'{__package__}.backends.markdown_backend' if __package__ else 'backends.markdown_backend')
        result = module.extract_markdown(path)
        self._adopt(result)
        return result['items']

    def _extract_latex(self, path):
        from importlib import import_module
        module = import_module(f'{__package__}.backends.latex_backend' if __package__ else 'backends.latex_backend')
        result = module.extract_project(path, main_file=self.main_file)
        self._adopt(result)
        return result['items']

    def _extract_pdf(self, pdf_path):
        doc_id = _sha(pdf_path)
        if self.assets_manifest:
            self.assets = _load_assets(self.assets_manifest, doc_id)
        elif self.asset_dir:
            try:
                from .extract_figures import extract_figures
            except ImportError:
                from extract_figures import extract_figures
            self.assets = extract_figures(str(pdf_path), str(self.asset_dir), dpi=250)
        items, figure_counts = [], {}
        section, sec_idx, ref_idx = 'Title & Abstract', 0, 0
        try:
            from .backends.pdf_structure import refine_table, repeated_running_matter
        except ImportError:
            from backends.pdf_structure import refine_table, repeated_running_matter
        with pymupdf.open(pdf_path) as doc:
            running_matter = repeated_running_matter(doc)
            for page_idx, page in enumerate(doc):
                page_no = page_idx + 1
                page_meta = {'page': page_no, 'width': page.rect.width, 'height': page.rect.height,
                             'rotation': page.rotation, 'coordinate_system': 'unrotated_top_left_pt'}
                tables = []
                try:
                    tables = list(page.find_tables().tables)
                except Exception as exc:
                    self.warnings.append({'code': 'table_detection_failed', 'page': page_no, 'message': str(exc)})
                table_info = []
                for index, table in enumerate(tables):
                    table_id = f'p{page_no}-tbl{index + 1}'
                    cell_specs, table_rect, table_warnings = refine_table(table, page, table_id)
                    self.warnings.extend({**warning, 'page': page_no} for warning in table_warnings)
                    cell_items = []
                    for cell in cell_specs:
                        row_no, col_no = cell['row'], cell['col']
                        bbox = cell['bbox']
                        cell_id = f'{table_id}-cell-r{row_no + 1}c{col_no + 1}'
                        raw = cell['source']
                        normalized, mapping = normalize_with_map(raw)
                        region_id = f'p{page_no}:table{index + 1}:r{row_no}c{col_no}'
                        locator = {**page_meta, 'region_id': region_id, 'bbox': bbox, 'start': 0, 'end': len(raw)}
                        item = {**cell, 'id': cell_id, 'kind': 'table_cell', 'table_id': table_id, 'parent_id': table_id,
                                'section': section, 'page': page_no,
                                'spans': cell.get('native_spans', [{'bbox': bbox}]),
                                'locators': cell.get('native_locators', [locator]),
                                'source': normalized, 'raw_source': raw, 'normalization_map': mapping,
                                'action': 'translate' if normalized else 'preserve', 'empty_cell': not normalized, 'status': 'pending'}
                        item.pop('bbox', None)
                        item.pop('native_spans', None)
                        item.pop('native_locators', None)
                        cell_items.append(item)
                        self.regions.append({'region_id': region_id, 'locator': locator, 'role': 'table_cell',
                                             'raw_text': raw, 'disposition': item['action'], 'unit_ids': [cell_id]})
                    items.extend(cell_items)
                    table_info.append((table_id, table_rect, cell_items))
                blocks = page.get_text('dict').get('blocks', [])
                for block_index, block in enumerate(blocks):
                    bbox = list(block.get('bbox', (0, 0, 0, 0)))
                    block_id = block.get('number', block_index)
                    region_id = f'p{page_no}:b{block_id}'
                    locator = {**page_meta, 'block_id': block_id, 'region_id': region_id, 'bbox': bbox}
                    if block.get('type') != 0:
                        bound_assets = [a for a in self.assets if a.get('page') == page_no and pymupdf.Rect(a['bbox']).contains(pymupdf.Rect(bbox))]
                        region = {'region_id': region_id, 'locator': locator, 'role': 'image', 'raw_text': '',
                                  'disposition': 'needs_review', 'unit_ids': [], 'asset_ids': [a['asset_id'] for a in bound_assets],
                                  'evidence': {'reason': 'Image text cannot be verified by native extraction'}}
                        self.regions.append(region)
                        area = pymupdf.Rect(bbox).get_area() / max(page.rect.get_area(), 1)
                        self.warnings.append({'code': 'image_text_unverified', 'page': page_no, 'region_id': region_id,
                                              'bbox': bbox, 'area_fraction': area, 'message': 'OCR or source visual review required, even when native page headers exist'})
                        continue
                    spans, pieces = [], []
                    cursor = 0
                    for line_index, line in enumerate(block.get('lines', [])):
                        if pieces:
                            pieces.append('\n')
                            cursor += 1
                        for span_index, span in enumerate(line.get('spans', [])):
                            text = span.get('text', '')
                            spans.append({'span_id': f'{region_id}:l{line_index}:s{span_index}', 'bbox': list(span.get('bbox', bbox)),
                                          'font': span.get('font', ''), 'size': span.get('size', 10), 'flags': span.get('flags', 0),
                                          'text': text, 'start': cursor, 'end': cursor + len(text)})
                            pieces.append(text)
                            cursor += len(text)
                    raw = ''.join(pieces)
                    if not raw.strip():
                        continue
                    locator.update(start=0, end=len(raw))
                    normalized, mapping = normalize_with_map(raw)
                    region = {'region_id': region_id, 'locator': locator, 'role': 'text', 'raw_text': raw,
                              'spans': spans, 'normalized_text': normalized, 'normalization_map': mapping,
                              'disposition': 'translate', 'unit_ids': []}
                    self.regions.append(region)
                    running_key = (page_no, tuple(round(v, 2) for v in bbox))
                    if running_key in running_matter:
                        region.update(role='running_matter', disposition='excluded',
                                      evidence=running_matter[running_key])
                        continue
                    rect = pymupdf.Rect(bbox)
                    containing_table = next((t for t in table_info if (rect & t[1]).get_area() >= .98 * max(rect.get_area(), 1)), None)
                    if containing_table:
                        owners = [c['id'] for c in containing_table[2] if (rect & pymupdf.Rect(c['spans'][0]['bbox'])).get_area() > 0]
                        for cell in containing_table[2]:
                            if cell['id'] in owners and not cell.get('source_parts'):
                                cell['locators'][0]['block_id'] = min(block_id, cell['locators'][0].get('block_id', block_id))
                        region.update(role='table_text', disposition='preserve', unit_ids=owners,
                                      evidence={'reason': 'Represented by positioned table cells', 'table_id': containing_table[0]})
                        continue
                    if any((rect & table_rect).get_area() > .35 * max(rect.get_area(), 1) for _, table_rect, _ in table_info):
                        self.warnings.append({'code': 'mixed_table_text_block', 'page': page_no, 'region_id': region_id,
                                              'message': 'Text block partly intersects a table; manual structure review required'})
                    max_font = max((s['size'] for s in spans), default=10)
                    all_bold = bool(spans) and all('bold' in span['font'].casefold() for span in spans if span['text'].strip())
                    heading = len(normalized) < 140 and ((SECTION_HEADING_REGEX.match(normalized) and max_font >= 11.5) or (all_bold and max_font >= 9 and not normalized.endswith('.')))
                    base = {'section': section, 'page': page_no, 'spans': spans, 'locators': [locator],
                            'parent_id': region_id, 'raw_source': raw, 'normalization_map': mapping, 'status': 'pending'}
                    figure_match = FIGURE_CAPTION_REGEX.match(normalized)
                    table_match = TABLE_CAPTION_REGEX.match(normalized)
                    if page_no == 1 and max_font >= 15 and len(normalized) < 220 and not any(i['kind'] == 'title' for i in items):
                        group = [{**base, 'id': 'document-title', 'kind': 'title', 'source': normalized}]
                    elif heading:
                        sec_idx += 1
                        section = normalized
                        group = [{**base, 'id': f'sec{sec_idx:02d}-head', 'kind': 'heading', 'section': section, 'source': normalized,
                                  'heading_font_size': round(max_font, 2)}]
                    elif figure_match:
                        number = figure_match.group(1).rstrip('.:,;-')
                        figure_counts[number] = figure_counts.get(number, 0) + 1
                        fig_id = f'fig-{number}-cap' + (f'-p{page_no}-{figure_counts[number]}' if figure_counts[number] > 1 else '')
                        candidates = [a for a in self.assets if str(a.get('figure_id')) == number and a.get('page') == page_no]
                        candidates = [a for a in candidates if not a.get('caption_bbox') or (pymupdf.Rect(a['caption_bbox']) & rect).get_area() > 0]
                        if len(candidates) > 1:
                            raise ValueError(f'Ambiguous exact figure association: page {page_no} figure {number}')
                        asset = candidates[0] if candidates else None
                        item = {**base, 'id': fig_id, 'kind': 'figure', 'source': normalized, 'figure_no': number}
                        if asset:
                            item.update(asset_id=asset['asset_id'], image_path=asset['path'])
                            asset.setdefault('owner_ids', []).append(fig_id)
                        else:
                            self.warnings.append({'code': 'missing_figure_asset', 'page': page_no, 'id': fig_id, 'figure_no': number})
                        group = [item]
                    elif table_match:
                        number = table_match.group(1).rstrip('.:,;-')
                        ordered = sorted(table_info, key=lambda t: min(abs(t[1].y0 - rect.y1), abs(rect.y0 - t[1].y1)))
                        table_id = ordered[0][0] if ordered else f'p{page_no}-unparsed-table-{number}'
                        group = [{**base, 'id': f'{table_id}-cap', 'kind': 'table', 'table_id': table_id, 'table_no': number, 'source': normalized}]
                        if not ordered:
                            self.warnings.append({'code': 'table_structure_unavailable', 'id': group[0]['id'], 'page': page_no})
                    elif 'reference' in section.casefold() or 'bibliography' in section.casefold() or REFERENCE_LINE_REGEX.match(normalized):
                        ref_idx += 1
                        group = [{**base, 'id': f'ref-{ref_idx:03d}', 'kind': 'reference', 'source': normalized}]
                    elif EQUATION_LINE_REGEX.search(normalized) and ('=' in normalized or '∑' in normalized) and len(normalized) < 300:
                        equation_id = f'p{page_no}-b{block_id}-equation'
                        item = {**base, 'id': equation_id, 'kind': 'equation', 'source': normalized, 'action': 'preserve', 'math_format': 'graphic'}
                        if self.asset_dir:
                            self.asset_dir.mkdir(parents=True, exist_ok=True)
                            image_path = self.asset_dir / f'{equation_id}.png'
                            page.get_pixmap(clip=rect + (-2, -2, 2, 2), dpi=300).save(image_path)
                            asset_id = f'{doc_id[:16]}:{equation_id}'
                            self.assets.append({'asset_id': asset_id, 'doc_id': doc_id, 'kind': 'equation', 'representation': 'raster',
                                                'page': page_no, 'bbox': bbox, 'path': str(image_path), 'sha256': _sha(image_path),
                                                'owner_ids': [equation_id], 'locators': [locator]})
                            item.update(asset_id=asset_id, image_path=str(image_path))
                        group = [item]
                        region['disposition'] = 'preserve'
                    else:
                        containing_figures = [a for a in self.assets if a.get('page') == page_no and a.get('kind') == 'figure' and pymupdf.Rect(a['bbox']).contains(rect)]
                        group = []
                        bullet_starts = [m.start() for m in re.finditer(r'(?m)^[•●▪◦]\s+', raw)]
                        ranges = []
                        if bullet_starts:
                            boundaries = sorted(set([0, *bullet_starts, len(raw)]))
                            for first, last in zip(boundaries, boundaries[1:]):
                                ranges.extend((sent, first + a, first + b, [[first + x, first + y] for x, y in mapping])
                                              for sent, a, b, mapping in sentence_ranges(raw[first:last], split_en_sentences))
                        else:
                            ranges = sentence_ranges(raw, split_en_sentences)
                        for sentence_index, (sentence, start, end, sentence_map) in enumerate(ranges):
                            item_id = f'sec{sec_idx:02d}-p{page_no:03d}-b{block_index + 1:03d}-s{sentence_index + 1:03d}'
                            item = {**base, 'id': item_id, 'kind': 'figure_label' if containing_figures else 'list_item' if bullet_starts else 'sentence',
                                    'source': sentence, 'raw_source': raw[start:end], 'normalization_map': sentence_map,
                                    'locators': [{**locator, 'start': start, 'end': end}]}
                            if containing_figures:
                                item['figure_asset_id'] = containing_figures[0]['asset_id']
                            group.append(item)
                    items.extend(group)
                    region['unit_ids'] = [i['id'] for i in group]
                if not any(r['locator'].get('page') == page_no and r.get('raw_text', '').strip() for r in self.regions):
                    self.warnings.append({'code': 'page_has_no_native_text', 'page': page_no})
                try:
                    clusters = page.cluster_drawings()
                except Exception as exc:
                    clusters = []
                    self.warnings.append({'code': 'drawing_detection_failed', 'page': page_no, 'message': str(exc)})
                for drawing_index, graphic in enumerate(clusters):
                    graphic = pymupdf.Rect(graphic)
                    if graphic.width < 15 or graphic.height < 15:
                        continue
                    represented = any((graphic & rect).get_area() >= .95 * graphic.get_area() for _, rect, _ in table_info)
                    represented = represented or any(a.get('page') == page_no and (graphic & pymupdf.Rect(a['bbox'])).get_area() >= .95 * graphic.get_area() for a in self.assets if a.get('bbox'))
                    if not represented:
                        region_id = f'p{page_no}:drawing{drawing_index}'
                        self.regions.append({'region_id': region_id, 'locator': {**page_meta, 'region_id': region_id, 'bbox': list(graphic)},
                                             'role': 'graphic', 'raw_text': '', 'disposition': 'needs_review', 'unit_ids': [],
                                             'evidence': {'reason': 'Vector graphic has no confirmed figure/table association'}})
                        self.warnings.append({'code': 'unbound_graphic', 'page': page_no, 'region_id': region_id})
        heading_sizes = sorted({i['heading_font_size'] for i in items if i.get('kind') == 'heading'}, reverse=True)
        for item in items:
            if item.get('kind') == 'heading':
                item['heading_level'] = min(6, heading_sizes.index(item['heading_font_size']) + 1)
        return items

    def _compute_summary(self, items, total_pages):
        kinds = [i['kind'] for i in items]
        return {'pages': total_pages, 'sections': len({i.get('section', '') for i in items}),
                'sentence_count': kinds.count('sentence'), 'figure_count': kinds.count('figure'),
                'table_count': len({i.get('table_id', i['id']) for i in items if i['kind'] in ('table', 'table_cell')}),
                'reference_count': kinds.count('reference'), 'equation_count': kinds.count('equation'), 'total_items': len(items)}


def extract_document(input_path, output_jsonl='source.jsonl', scope='full', summary_path=None,
                     job_dir=None, assets_manifest=None, backend='native', mineru_result=None, main_file=None, source_overlay=None,
                     equation_overlay=None):
    extractor = DocumentExtractor(scope, assets_manifest=assets_manifest,
                                  asset_dir=Path(job_dir) / 'extracted-assets' if job_dir else None,
                                  backend='native' if backend == 'auto' else backend,
                                  mineru_result=mineru_result, main_file=main_file, source_overlay=source_overlay,
                                  equation_overlay=equation_overlay)
    items, stats = extractor.extract(input_path)
    if job_dir:
        try:
            from .document_ir import create_job
        except ImportError:
            from document_ir import create_job
        create_job(input_path, job_dir, items, regions=extractor.regions, assets=extractor.assets,
                   summary=stats, scope=scope, extra_document=extractor.extra_document)
        stats['job_dir'] = str(Path(job_dir).resolve())
    else:
        Path(output_jsonl).resolve().parent.mkdir(parents=True, exist_ok=True)
        with open(output_jsonl, 'w', encoding='utf-8') as stream:
            for item in items:
                stream.write(json.dumps(item, ensure_ascii=False) + '\n')
    if summary_path:
        Path(summary_path).resolve().parent.mkdir(parents=True, exist_ok=True)
        Path(summary_path).write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding='utf-8')
    return stats


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input')
    parser.add_argument('--out', default='source.jsonl')
    parser.add_argument('--scope', choices=['full', 'body'], default='full')
    parser.add_argument('--summary')
    parser.add_argument('--job-dir')
    parser.add_argument('--assets-manifest', help='Explicit document-bound figure/asset manifest; never discovered from cwd')
    parser.add_argument('--backend', choices=['native', 'auto', 'mineru'], default='native')
    parser.add_argument('--mineru-result', help='Complete, source-bound normalized MinerU result JSON')
    parser.add_argument('--main-file', help='LaTeX main file relative to project directory')
    parser.add_argument('--source-overlay', help='Source-hash-bound reviewed image text transcriptions; creates additional source IDs before freezing')
    parser.add_argument('--equation-overlay', help='Reviewed source-bound native equation groups, preserved as complete PDF crops')
    args = parser.parse_args()
    try:
        result = extract_document(args.input, args.out, args.scope, args.summary, args.job_dir,
                                  args.assets_manifest, args.backend, args.mineru_result, args.main_file, args.source_overlay,
                                  args.equation_overlay)
    except (ValueError, OSError, ImportError) as exc:
        print(f'Extraction failed: {exc}', file=sys.stderr)
        return 4 if isinstance(exc, ImportError) else 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
