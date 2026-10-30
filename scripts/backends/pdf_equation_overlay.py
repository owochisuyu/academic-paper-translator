"""Group explicitly reviewed native PDF regions into source-bound equation crops.

This is a pre-freeze structural import, not equation detection or a visual-review
substitute. Version 1 accepts only a review declaring no translatable labels.
The retained native fragments still undergo the ordinary exact coverage check.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import unicodedata
from pathlib import Path

import pymupdf

from .common import normalize_with_map
try:
    from ..document_ir import canonical_hash
    from ..source_coverage import _pdf_raw_parts
except ImportError:
    from document_ir import canonical_hash
    from source_coverage import _pdf_raw_parts


CONTRACT = 'reviewed_native_pdf_equation_v1'
COORDINATES = 'unrotated_top_left_pt'
DPI = 300
ALLOWED_KINDS = {'sentence', 'paragraph', 'text', 'equation'}
# Deliberately small: an unfamiliar multi-letter identifier needs a text-aware
# workflow even when a reviewer has declared the whole region non-translatable.
MATH_WORDS = frozenset({'sin', 'cos', 'tan', 'cot', 'sec', 'csc', 'sinh', 'cosh',
                        'tanh', 'arcsin', 'arccos', 'arctan', 'lim', 'log', 'ln',
                        'exp', 'min', 'max', 'inf', 'sup', 'det', 'gcd', 'mod'})


def _reject_text_labels(raw):
    # NFKC exposes ligatures/styled Latin letters without changing any retained
    # source text or positions. A font change within a native word is already
    # joined in raw_text; whitespace remains meaningful between variables.
    words = re.findall(r'[^\W\d_]+', unicodedata.normalize('NFKC', raw))
    unsupported = list(dict.fromkeys(word for word in words if len(word) > 1 and word not in MATH_WORDS))
    if unsupported:
        raise ValueError('Equation contains potential translatable text or unverified identifiers '
                         f'({", ".join(unsupported[:8])}); NEEDS_REVIEW: use a text-aware workflow '
                         'instead of declaring no_translatable_text')


def _box(value, label):
    if (not isinstance(value, list) or len(value) != 4
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in value)):
        raise ValueError(f'{label} needs four finite PDF-point coordinates')
    result = pymupdf.Rect(value)
    if result.is_empty or result.is_infinite:
        raise ValueError(f'{label} must have positive area')
    return result


def _index(rows, key, label):
    result = {}
    for row in rows:
        ident = row.get(key) if isinstance(row, dict) else None
        if not isinstance(ident, str) or not ident or ident in result:
            raise ValueError(f'Missing or duplicate {label} identity')
        result[ident] = row
    return result


def _native_blocks(page):
    result = {}
    for index, block in enumerate(page.get_text('dict').get('blocks', [])):
        number = block.get('number', index)
        result[number] = {**block, 'raw_text': '\n'.join(
            ''.join(span.get('text', '') for span in line.get('spans', []))
            for line in block.get('lines', []))}
    return result


def _native_region(region, page_number, page, native, crop):
    locator = region.get('locator', {})
    block_number = locator.get('block_id')
    block = native.get(block_number)
    expected_id = f'p{page_number}:b{block_number}'
    if ('doc_id' in region or region.get('region_id') != expected_id
            or locator.get('region_id') != expected_id or region.get('role') != 'text'
            or region.get('disposition') not in ('translate', 'preserve')
            or region.get('provenance', 'native') != 'native'
            or locator.get('method', 'native') != 'native'
            or locator.get('page') != page_number or locator.get('rotation') != 0
            or locator.get('coordinate_system') != COORDINATES
            or block is None or block.get('type') != 0):
        raise ValueError('Equation overlay requires current unfrozen native text regions on one page')
    raw = region.get('raw_text')
    if (not isinstance(raw, str) or not raw.strip() or raw != block['raw_text']
            or locator.get('start') != 0 or locator.get('end') != len(raw)
            or locator.get('width') != page.rect.width or locator.get('height') != page.rect.height):
        raise ValueError('Equation region text or geometry differs from the native source PDF')
    box = _box(locator.get('bbox'), 'Native region bbox')
    if any(abs(a - b) > .001 for a, b in zip(box, block['bbox'])):
        raise ValueError('Equation region bbox differs from the native source PDF')
    if not (crop + (-.001, -.001, .001, .001)).contains(box):
        raise ValueError('Equation crop does not contain the whole selected region')
    return raw


def _group_parts(selected, selected_ids, items, item_index, all_regions):
    """Require exact bidirectional ownership and full native character coverage."""
    owners = set()
    for region in selected:
        ids = region.get('unit_ids')
        if (not isinstance(ids, list) or not ids or len(ids) != len(set(ids))
                or any(ident not in item_index for ident in ids)):
            raise ValueError('Every selected region needs all of its real source units')
        owners.update(ids)
    for region in all_regions:
        if region['region_id'] not in selected_ids and owners.intersection(region.get('unit_ids', [])):
            raise ValueError('Equation units are also owned by an unselected region')
    for item in items:
        locations = item.get('locators', [])
        intersects = any(loc.get('region_id') in selected_ids for loc in locations)
        if intersects != (item['id'] in owners):
            raise ValueError('Equation ownership is incomplete or differs from source unit locators')
    grouped = [item for item in items if item['id'] in owners]
    region_index = {region['region_id']: region for region in selected}
    covered = {ident: [False] * len(region_index[ident]['raw_text']) for ident in selected_ids}
    parts = []
    for item in grouped:
        if (item.get('kind') not in ALLOWED_KINDS or 'doc_id' in item or 'source_hash' in item
                or item.get('provenance', 'native') != 'native'
                or item.get('extraction_method', 'native') != 'native'
                or any(item.get(k) for k in ('table_id', 'figure_asset_id', 'caption_of'))
                or item.get('normalizations')):
            raise ValueError('Only unfrozen native text/equation units may be grouped; no headings, tables, figures, references or footnotes')
        for part in _pdf_raw_parts(item, set()):
            locations = part.get('locators', [])
            if len(locations) != 1 or locations[0].get('region_id') not in selected_ids:
                raise ValueError('Equation source units cannot cross unselected regions')
            locator = locations[0]
            region = region_index[locator['region_id']]
            start, end = locator.get('start'), locator.get('end')
            if (type(start) is not int or type(end) is not int
                    or not 0 <= start < end <= len(region['raw_text'])):
                raise ValueError('Equation fragment has an invalid native character interval')
            expected_locator = {**region['locator'], 'start': start, 'end': end}
            if locator != expected_locator or part['raw_source'] != region['raw_text'][start:end]:
                raise ValueError('Equation source fragment differs from its native text interval')
            if item['id'] not in region['unit_ids']:
                raise ValueError('Equation region omits a source unit owner')
            marks = covered[region['region_id']]
            if any(marks[start:end]):
                raise ValueError('Equation fragments duplicate native source coverage')
            marks[start:end] = [True] * (end - start)
            parts.append(copy.deepcopy({'source': part['source'], 'raw_source': part['raw_source'],
                'locators': locations, 'spans': part.get('spans', []),
                'original_unit_id': item['id'], 'original_unit_hash': canonical_hash(item)}))
    for ident, marks in covered.items():
        if any(not present and not char.isspace()
               for char, present in zip(region_index[ident]['raw_text'], marks)):
            raise ValueError('Equation overlay cannot hide partial native character coverage')
    return grouped, owners, parts


def apply_equation_overlay(source_path, manifest_path, items, regions, assets, warnings):
    """Validate all records, then update items/regions/assets in place.

    Return ``(unchanged_warnings_copy, overlay_info)``. ``overlay_info.path`` and
    ``sha256`` identify the manifest for freezing with the new job. PNG assets
    use the existing asset path/hash/owner_ids contract. No frozen job is edited,
    no extraction warning is waived, and no visual-review PASS is manufactured.

    Required manifest fields: source_sha256, reviewer {type, id}, equations.
    Each equation needs id, page (1-based), bbox (unrotated top-left PDF points),
    region_ids, complete=true, no_translatable_text=true, and evidence. Labels
    must be absent or empty in v1. Multi-letter text outside the small standard
    operator allowlist is rejected despite the declaration; it needs a
    text-aware workflow or separate review/OCR.
    """
    source_path, manifest_path = Path(source_path).resolve(), Path(manifest_path).resolve()
    source_bytes, manifest_bytes = source_path.read_bytes(), manifest_path.read_bytes()
    source_id = hashlib.sha256(source_bytes).hexdigest()
    manifest_id = hashlib.sha256(manifest_bytes).hexdigest()
    value = json.loads(manifest_bytes.decode('utf-8-sig'))
    if not isinstance(value, dict) or value.get('source_sha256') != source_id:
        raise ValueError('Equation overlay is not bound to the input PDF hash')
    if value.get('schema_version', 1) != 1 or value.get('contract', CONTRACT) != CONTRACT:
        raise ValueError('Unsupported equation overlay schema or contract')
    reviewer = value.get('reviewer', {})
    if (not isinstance(reviewer, dict) or reviewer.get('type') not in ('agent', 'human')
            or not isinstance(reviewer.get('id'), str) or not reviewer['id'].strip()):
        raise ValueError('Equation overlay requires a named agent or human reviewer')
    entries = value.get('equations')
    if not isinstance(entries, list) or not entries:
        raise ValueError('Equation overlay requires nonempty equation records')
    region_index = _index(regions, 'region_id', 'source region')
    item_index = _index(items, 'id', 'source unit')
    asset_index = _index(assets, 'asset_id', 'asset')
    group_ids, used_regions, used_units, removed_assets, plans = set(), set(), set(), set(), []
    with pymupdf.open(stream=source_bytes, filetype='pdf') as document:
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError('Equation record must be an object')
            ident = entry.get('id')
            if (not isinstance(ident, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}', ident)
                    or ident in group_ids or ident in item_index or ident + ':asset' in asset_index):
                raise ValueError('Equation id must be unique, new and path-safe')
            group_ids.add(ident)
            if (entry.get('complete') is not True or not isinstance(entry.get('evidence'), str)
                    or not entry['evidence'].strip()):
                raise ValueError('Equation overlay requires complete=true and the actual review reason')
            if entry.get('no_translatable_text') is not True or entry.get('labels', []) != []:
                raise ValueError('Text-bearing equations remain NEEDS_REVIEW: reviewed labels/OCR are not supported by equation overlay v1')
            page_number = entry.get('page')
            if type(page_number) is not int or not 1 <= page_number <= len(document):
                raise ValueError('Equation page must identify an existing PDF page')
            page = document[page_number - 1]
            if page.rotation or entry.get('coordinate_system', COORDINATES) != COORDINATES:
                raise ValueError('Equation overlay requires unrotated top-left PDF coordinates')
            crop = _box(entry.get('bbox'), 'Equation bbox')
            if not page.rect.contains(crop):
                raise ValueError('Equation bbox is outside the PDF page')
            if any(plan['page'] == page_number and (crop & plan['crop']).get_area() > .001 for plan in plans):
                raise ValueError('Equation crops must not overlap or duplicate a group')
            ids = entry.get('region_ids')
            if (not isinstance(ids, list) or not ids or any(not isinstance(v, str) for v in ids)
                    or len(ids) != len(set(ids)) or any(v not in region_index for v in ids)
                    or used_regions.intersection(ids)):
                raise ValueError('Equation region_ids must be known, nonempty, unique and ungrouped')
            selected_ids = set(ids)
            selected = [region_index[r] for r in ids]
            native = _native_blocks(page)
            for region in selected:
                _native_region(region, page_number, page, native, crop)
            # A crop cannot silently include an unselected source block, even if
            # a damaged caller ledger omitted that block altogether.
            selected_blocks = {r['locator']['block_id'] for r in selected}
            for number, block in native.items():
                if (number not in selected_blocks and (block.get('raw_text', '').strip() or block.get('type') != 0)
                        and (crop & pymupdf.Rect(block['bbox'])).get_area() > .001):
                    raise ValueError('Equation bbox intersects unselected native content')
            for region in regions:
                loc = region.get('locator', {})
                if (region['region_id'] not in selected_ids and loc.get('page') == page_number
                        and loc.get('bbox') and (crop & pymupdf.Rect(loc['bbox'])).get_area() > .001):
                    raise ValueError('Equation bbox intersects an unselected source region')
            grouped, owners, parts = _group_parts(selected, selected_ids, items, item_index, regions)
            if used_units.intersection(owners):
                raise ValueError('A native source unit cannot belong to two equations')
            for asset in assets:
                old_owners = set(asset.get('owner_ids', []))
                if old_owners.intersection(owners):
                    if asset.get('kind') != 'equation' or not old_owners <= owners:
                        raise ValueError('Equation grouping would remove a shared or non-equation asset owner')
                    removed_assets.add(asset['asset_id'])
            for item in grouped:
                if item.get('asset_id') and item['asset_id'] not in removed_assets:
                    raise ValueError('Existing equation asset ownership is missing or inconsistent')
            used_regions.update(ids)
            used_units.update(owners)
            raw = '\n'.join(part['raw_source'] for part in parts)
            _reject_text_labels(raw)
            source, _ = normalize_with_map(raw)
            # Share locator objects with source_parts: create_job scopes them
            # together when it deep-copies and freezes the complete unit.
            locators = [loc for part in parts for loc in part['locators']]
            evidence = {'contract': CONTRACT, 'validation': 'structure_only',
                        'source_sha256': source_id, 'manifest_sha256': manifest_id,
                        'reviewer': copy.deepcopy(reviewer), 'reason': entry['evidence'],
                        'complete': True, 'no_translatable_text': True,
                        'region_hashes': {r['region_id']: canonical_hash(r) for r in selected},
                        'replaced_unit_ids': [i['id'] for i in grouped]}
            equation = {'id': ident, 'kind': 'equation', 'action': 'preserve', 'math_format': 'graphic',
                        'section': grouped[0].get('section', ''), 'section_path': grouped[0].get('section_path', []),
                        'page': page_number, 'parent_id': ident, 'source': source, 'raw_source': raw,
                        'source_parts': parts, 'locators': locators,
                        'spans': [span for part in parts for span in part['spans']],
                        'normalization_map': [], 'provenance': 'native', 'status': 'pending',
                        'equation_overlay': evidence}
            # Exercise the same provenance checks as independent PDF coverage.
            _pdf_raw_parts(equation, set())
            plans.append({'entry': entry, 'page': page_number, 'crop': crop, 'owners': owners,
                          'equation': equation, 'evidence': evidence})
        # All structural validation precedes image writes and caller mutations.
        for plan in plans:
            pixmap = document[plan['page'] - 1].get_pixmap(clip=plan['crop'], dpi=DPI, alpha=False)
            plan['png'] = pixmap.tobytes('png')
            plan['pixels'] = {'width_px': pixmap.width, 'height_px': pixmap.height,
                              'x_origin_px': pixmap.x, 'y_origin_px': pixmap.y}
    folder = manifest_path.parent / 'equation-overlay-assets'
    folder.mkdir(parents=True, exist_ok=True)
    additions = []
    for plan in plans:
        image_hash = hashlib.sha256(plan['png']).hexdigest()
        image_path = folder / (image_hash + '.png')
        if image_path.exists() and image_path.read_bytes() != plan['png']:
            raise ValueError('Existing equation asset content does not match its identity')
        if not image_path.exists():
            image_path.write_bytes(plan['png'])
        equation = plan['equation']
        asset_id = equation['id'] + ':asset'
        equation.update(asset_id=asset_id, image_path=str(image_path))
        additions.append({'asset_id': asset_id, 'doc_id': source_id, 'source_sha256': source_id,
                          'kind': 'equation', 'representation': 'png', 'provenance': 'reviewed_native_pdf_crop',
                          'path': str(image_path), 'sha256': image_hash, 'page': plan['page'],
                          'bbox': list(plan['crop']), 'dpi': DPI, **plan['pixels'],
                          'owner_ids': [equation['id']], 'locators': copy.deepcopy(equation['locators']),
                          'manifest_sha256': manifest_id})
    by_owner = {owner: plan for plan in plans for owner in plan['owners']}
    new_items, inserted = [], set()
    for item in items:
        plan = by_owner.get(item['id'])
        if plan is None:
            new_items.append(item)
        elif plan['equation']['id'] not in inserted:
            new_items.append(plan['equation'])
            inserted.add(plan['equation']['id'])
    new_regions = copy.deepcopy(regions)
    region_groups = {ident: plan for plan in plans for ident in plan['entry']['region_ids']}
    for region in new_regions:
        plan = region_groups.get(region['region_id'])
        if plan:
            region.update(unit_ids=[plan['equation']['id']], disposition='preserve')
            region['evidence'] = {**(region.get('evidence') or {}),
                                  'equation_overlay': copy.deepcopy(plan['evidence'])}
    items[:] = new_items
    regions[:] = new_regions
    assets[:] = [asset for asset in assets if asset['asset_id'] not in removed_assets] + additions
    return copy.deepcopy(warnings), {'path': str(manifest_path), 'path_name': manifest_path.name,
        'sha256': manifest_id, 'source_sha256': source_id, 'reviewer': copy.deepcopy(reviewer),
        'contract': CONTRACT, 'validation': 'structure_only', 'equations': copy.deepcopy(entries),
        'grouped_regions': sorted(used_regions), 'replaced_unit_ids': sorted(used_units),
        'equation_ids': [plan['equation']['id'] for plan in plans],
        'limitations': 'Only explicitly reviewed equations with no translatable text; visual review and final rendered-math review remain required.'}
