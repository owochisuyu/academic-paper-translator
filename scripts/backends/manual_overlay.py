"""Apply source-bound, reviewed transcriptions before freezing a new extraction."""
from __future__ import annotations

import math
from pathlib import Path
import pymupdf

from .common import digest, normalize_with_map
try:
    from ..document_ir import read_json
except ImportError:
    from document_ir import read_json


def apply_overlay(source_path, overlay_path, items, regions, assets, warnings):
    value = read_json(overlay_path)
    source_id = digest(source_path)
    if not isinstance(value, dict) or value.get('source_sha256') != source_id:
        raise ValueError('Source overlay is not bound to the input PDF hash')
    reviewer = value.get('reviewer', {})
    if not isinstance(reviewer, dict) or reviewer.get('type') not in ('agent', 'human') or not isinstance(reviewer.get('id'), str) or not reviewer['id'].strip():
        raise ValueError('Source overlay requires a named agent or human reviewer')
    supplied = value.get('regions')
    if not isinstance(supplied, list) or not supplied:
        raise ValueError('Source overlay requires nonempty region records')
    region_lookup = {r['region_id']: r for r in regions}
    seen, resolved = set(), set()
    for entry in supplied:
        if not isinstance(entry, dict):
            raise ValueError('Overlay region must be an object')
        region_id = entry.get('region_id', '')
        if region_id.startswith(source_id[:16] + ':'):
            region_id = region_id[len(source_id[:16]) + 1:]
        if region_id in seen or region_id not in region_lookup:
            raise ValueError('Duplicate or unknown overlay region: ' + region_id)
        seen.add(region_id)
        original = region_lookup[region_id]
        if original.get('role') not in ('image', 'graphic') or original.get('disposition') != 'needs_review':
            raise ValueError('Overlay can only transcribe an unresolved image/graphic region')
        if type(entry.get('complete')) is not bool or not isinstance(entry.get('evidence'), str) or not entry['evidence'].strip():
            raise ValueError('Overlay requires an explicit complete flag and review evidence')
        units = entry.get('units')
        if not isinstance(units, list) or not units:
            raise ValueError('Image text must become real source units; an empty waiver is not an overlay')
        locator = original['locator']
        page = locator['page']
        parent_box = pymupdf.Rect(locator['bbox'])
        related = [a for a in assets if a.get('page') == page and a.get('bbox') and pymupdf.Rect(a['bbox']).contains(parent_box)]
        additions = []
        for index, row in enumerate(units):
            if not isinstance(row, dict) or not isinstance(row.get('source'), str) or not row['source'].strip():
                raise ValueError('Transcribed source must be nonempty text')
            if row.get('kind', 'figure_label') not in ('figure_label', 'sentence', 'heading', 'footnote'):
                raise ValueError('Unsupported overlay unit kind')
            bbox = row.get('bbox')
            if not isinstance(bbox, list) or len(bbox) != 4 or not all(type(v) in (int, float) and math.isfinite(v) for v in bbox):
                raise ValueError('Overlay units require finite source bbox coordinates')
            rect = pymupdf.Rect(bbox)
            if rect.is_empty or not (parent_box + (-.5, -.5, .5, .5)).contains(rect):
                raise ValueError('Overlay text bbox is outside its source image region')
            item_id = f'{region_id}:manual{index + 1}'
            manual_region = f'{item_id}:region'
            source_text, mapping = normalize_with_map(row['source'])
            unit_locator = {**locator, 'region_id': manual_region, 'parent_region_id': region_id,
                            'bbox': bbox, 'start': 0, 'end': len(row['source']), 'method': 'manual_transcription'}
            item = {'id': item_id, 'kind': row.get('kind', 'figure_label'), 'section': 'Source image labels', 'page': page,
                    'parent_id': region_id, 'source': source_text, 'raw_source': row['source'], 'normalization_map': mapping,
                    'locators': [unit_locator], 'spans': [{'bbox': bbox}], 'provenance': 'manual',
                    'reviewer': reviewer, 'action': 'translate', 'status': 'pending'}
            if len(related) == 1:
                item['figure_asset_id'] = related[0]['asset_id']
            items.append(item)
            additions.append(item_id)
            regions.append({'region_id': manual_region, 'locator': unit_locator, 'role': item['kind'], 'raw_text': row['source'],
                            'normalized_text': source_text, 'disposition': 'translate', 'unit_ids': [item_id],
                            'evidence': {'method': 'manual_transcription', 'reviewer': reviewer, 'source_sha256': source_id}})
        original['unit_ids'] = list(dict.fromkeys(original.get('unit_ids', []) + additions))
        original['evidence'] = {'method': 'manual_transcription', 'reviewer': reviewer, 'complete': entry['complete'],
                                'description': entry['evidence'], 'source_sha256': source_id, 'overlay_sha256': digest(overlay_path)}
        if entry['complete'] and (original['role'] == 'image' or related):
            original['disposition'] = 'preserve'
            resolved.add(region_id)
    complete_pages = set()
    for page in {r['locator']['page'] for r in regions if r.get('role') in ('image', 'graphic')}:
        visible = [r for r in regions if r.get('role') in ('image', 'graphic') and r['locator']['page'] == page]
        if visible and all(r['region_id'] in resolved for r in visible):
            complete_pages.add(page)
    remaining = [w for w in warnings if w.get('region_id') not in resolved
                 and not (w.get('code') == 'page_has_no_native_text' and w.get('page') in complete_pages)]
    return remaining, {'path_name': Path(overlay_path).name, 'sha256': digest(overlay_path), 'reviewer': reviewer,
                       'source_sha256': source_id, 'regions': supplied, 'resolved_regions': sorted(resolved)}
