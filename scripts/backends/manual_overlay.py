"""Apply source-bound, reviewed transcriptions before freezing a new extraction."""
from __future__ import annotations

import math
import copy
from pathlib import Path
import pymupdf

from .common import digest, normalize_with_map
try:
    from ..document_ir import read_json, canonical_hash, load_job, resolve_job_path
except ImportError:
    from document_ir import read_json, canonical_hash, load_job, resolve_job_path


KINDS = {'figure_label', 'sentence', 'heading', 'footnote', 'caption', 'reference', 'table_cell', 'figure'}
OCR_BINDINGS = ('doc_id', 'source_manifest_hash', 'source_sha256', 'region_hash', 'crop_sha256')
OCR_CONTRACT = 'reviewed_source_transcription_v1'
OCR_METHOD = 'current vision agent transcription with independent source-image assessment'


def _identity(value, label):
    if (not isinstance(value, dict) or not isinstance(value.get('name'), str)
            or not value['name'].strip() or value.get('method') not in ('agent', 'human')):
        raise ValueError(f'{label} requires a real named agent/human identity')


def validate_ocr_response(request, response, review):
    """Verify the original full response, never a projection of reviewed fields."""
    if not isinstance(response, dict) or not isinstance(review, dict):
        raise ValueError('OCR overlay requires its complete response and independent review')
    for row, label in ((response, 'Transcription'), (review, 'Source review')):
        if any(row.get(k) != request.get(k) for k in ('region_id',) + OCR_BINDINGS):
            raise ValueError(f'{label} has stale/missing source or image binding')
    if response.get('status') != 'transcribed' or response.get('complete') is not True:
        raise ValueError('Transcription is NOT_RUN, partial or needs review')
    if response.get('uncertainties') != []:
        raise ValueError('Unresolved source uncertainties prevent complete import')
    _identity(response.get('producer'), 'Transcriber')
    validate_units(response.get('units'), pymupdf.Rect(request['bbox']), strict=True)
    if review.get('transcription_hash') != canonical_hash(response):
        raise ValueError('Source review belongs to a different transcription')
    _identity(review.get('reviewer'), 'Independent reviewer')
    if review.get('independent') is not True or review.get('status') != 'PASS' or review.get('findings') != []:
        raise ValueError('Independent source review is incomplete or has unresolved findings')
    if review['reviewer']['name'].strip().casefold() == response['producer']['name'].strip().casefold():
        raise ValueError('A separate source reviewer is required')
    if not isinstance(review.get('reason'), str) or not review['reason'].strip():
        raise ValueError('Independent source review needs its actual assessment reason')


def validate_ocr_request(job, source_id, pointer, region):
    """Recheck the task packet and image bytes against the frozen source job."""
    root, document = job['root'], job['document']
    request = read_json(resolve_job_path(root, pointer['request']))
    if pointer.get('request_hash') != canonical_hash(request):
        raise ValueError('OCR request changed after preparation')
    if request.get('region_id') != region['region_id'] or request.get('region_hash') != canonical_hash(region):
        raise ValueError('OCR request does not identify the frozen source region')
    if request.get('bbox') != region['locator']['bbox'] or request.get('page') != region['locator']['page']:
        raise ValueError('OCR request geometry differs from source')
    if request.get('coordinate_system') != 'unrotated_top_left_pt':
        raise ValueError('Unsupported OCR coordinate system')
    for key, expected in (('doc_id', document['doc_id']),
                          ('source_manifest_hash', document['source_manifest_hash']),
                          ('source_sha256', source_id)):
        if request.get(key) != expected:
            raise ValueError(f'OCR request has stale {key}')
    for image_key in ('crop_image', 'page_image'):
        image = request[image_key]
        if digest(resolve_job_path(root, image['path'])) != image['sha256']:
            raise ValueError('OCR image content changed after preparation')
    if request['crop_image']['sha256'] != request['crop_sha256']:
        raise ValueError('OCR crop identity mismatch')
    if request['crop_image'].get('bbox_pt') != request['bbox']:
        raise ValueError('OCR crop geometry differs from source')
    with pymupdf.open(resolve_job_path(root, document['primary_input'])) as source:
        page = source[request['page'] - 1]
        if page.rotation or request['page_image'].get('bbox_pt') != list(page.rect):
            raise ValueError('OCR full-page geometry differs from source')
        for image_key, box in (('page_image', page.rect), ('crop_image', pymupdf.Rect(request['bbox']))):
            image = request[image_key]
            dpi = image.get('dpi')
            if type(dpi) is not int or not 144 <= dpi <= 400:
                raise ValueError('Invalid OCR image resolution')
            pixels = ((box & page.rect) * pymupdf.Matrix(dpi / 72, dpi / 72)).irect
            if (image.get('width_px'), image.get('height_px')) != (pixels.width, pixels.height):
                raise ValueError('OCR image dimensions differ from source geometry')
            if image_key == 'crop_image' and image.get('pixel_to_page_points') != {
                    'scale': 72 / dpi, 'x_origin_px': pixels.x0, 'y_origin_px': pixels.y0}:
                raise ValueError('OCR pixel coordinates differ from source geometry')
    return request


def _validate_reviewed_overlay(value, overlay_path, source_id, current_regions):
    if value.get('schema_version') != 2 or value.get('contract') != OCR_CONTRACT or value.get('method') != OCR_METHOD:
        raise ValueError('Reviewed OCR overlay requires its explicit schema and contract')
    origin = value.get('source_job')
    if not isinstance(origin, str) or not origin:
        raise ValueError('Reviewed OCR overlay requires the original frozen source job')
    origin = Path(origin)
    job = load_job(origin if origin.is_absolute() else Path(overlay_path).resolve().parent / origin)
    if (job['document']['doc_id'] != source_id
            or digest(resolve_job_path(job['root'], job['document']['primary_input'])) != source_id
            or value.get('source_job_manifest_hash') != job['document']['source_manifest_hash']):
        raise ValueError('OCR overlay is not bound to the original frozen source job')
    manifest = read_json(job['root'] / 'ocr/manifest.json')
    for key in ('doc_id', 'source_manifest_hash', 'source_sha256'):
        expected = job['document']['source_manifest_hash'] if key == 'source_manifest_hash' else source_id
        if manifest.get(key) != expected:
            raise ValueError(f'OCR manifest has stale {key}')
    frozen = {r['region_id']: r for r in job['regions']
              if r.get('role') in ('image', 'graphic') and r.get('disposition') == 'needs_review'}
    pointers = manifest.get('regions', [])
    requested = {r['region_id']: r for r in pointers}
    supplied = value['regions']
    ids = [r.get('region_id') for r in supplied]
    if (len(requested) != len(pointers) or len(ids) != len(set(ids))
            or set(ids) != set(frozen) or set(ids) != set(requested)):
        raise ValueError('OCR overlay must retain exactly every reviewed source region')
    prefix = source_id[:16] + ':'
    reviewer_names, reviewer_methods = [], []
    for entry in supplied:
        ident = entry['region_id']
        original = current_regions.get(ident.removeprefix(prefix))
        if original is None:
            raise ValueError('Reviewed OCR region is absent from the current extraction')
        # create_job scopes these fields; compare the entire same-source region.
        current = copy.deepcopy(original)
        current['region_id'] = ident
        current['doc_id'] = source_id
        current['unit_ids'] = [v if v.startswith(prefix) else prefix + v for v in current.get('unit_ids', [])]
        if current != frozen[ident]:
            raise ValueError('Reviewed OCR geometry or region differs from current source extraction')
        request = validate_ocr_request(job, source_id, requested[ident], frozen[ident])
        if entry.get('request') != request or entry.get('source_image') != request['crop_image']:
            raise ValueError('OCR overlay request or source image differs from the original packet')
        response, review = entry.get('response'), entry.get('review')
        validate_ocr_response(request, response, review)
        for field in ('complete', 'units', 'producer'):
            if entry.get(field) != response.get(field):
                raise ValueError(f'OCR overlay {field} differs from the reviewed response')
        if (entry.get('transcription_hash') != canonical_hash(response)
                or entry.get('evidence') != review['reason']):
            raise ValueError('OCR overlay evidence differs from the reviewed response')
        reviewer_names.append(review['reviewer']['name'])
        reviewer_methods.append(review['reviewer']['method'])
    expected_reviewer = {'type': 'agent' if 'agent' in reviewer_methods else 'human',
                         'id': '; '.join(dict.fromkeys(reviewer_names))}
    if value['reviewer'] != expected_reviewer:
        raise ValueError('OCR overlay reviewer differs from the independent review')


def checked_box(value, parent, label='text'):
    if not isinstance(value, list) or len(value) != 4 or not all(type(v) in (int, float) and math.isfinite(v) for v in value):
        raise ValueError(f'Overlay {label} requires finite source bbox coordinates')
    rect = pymupdf.Rect(value)
    if rect.is_empty or not (parent + (-.5, -.5, .5, .5)).contains(rect):
        raise ValueError(f'Overlay {label} bbox is outside its source image region')
    return rect


def validate_units(units, parent_box, strict=False):
    """Check structure, never assert that a human/agent actually read the pixels."""
    if not isinstance(units, list) or not units:
        raise ValueError('Image text must become real source units; an empty waiver is not an overlay')
    tables = {}
    for index, row in enumerate(units):
        if not isinstance(row, dict) or not isinstance(row.get('source'), str):
            raise ValueError('Transcribed source must be text')
        kind = row.get('kind', 'figure_label')
        if kind not in KINDS:
            raise ValueError(f'Unsupported overlay unit kind: {kind}')
        if not row['source'].strip() and kind != 'table_cell':
            raise ValueError('Transcribed source must be nonempty except an explicit empty table cell')
        if row.get('uncertain') is True or (strict and row.get('uncertain') is not False):
            raise ValueError('Uncertain transcription cannot be imported as complete')
        if 'order' in row and (type(row['order']) is not int or row['order'] != index):
            raise ValueError('Unit order must be its zero-based position in the region')
        checked_box(row.get('bbox'), parent_box)
        if kind == 'heading' and ('heading_level' in row or strict):
            if type(row.get('heading_level')) is not int or not 1 <= row['heading_level'] <= 6:
                raise ValueError('Heading level must be an integer from 1 to 6')
        if kind == 'figure':
            checked_box(row.get('figure_bbox'), parent_box, 'figure')
        if kind != 'table_cell':
            continue
        table = row.get('table_id')
        if not isinstance(table, str) or not table.strip():
            raise ValueError('Table cells need a nonempty table_id')
        values = [row.get(k) for k in ('row', 'col', 'table_rows', 'table_cols')]
        rs, cs = row.get('rowspan', 1), row.get('colspan', 1)
        if any(type(v) is not int for v in values + [rs, cs]):
            raise ValueError('Table coordinates and dimensions must be integers')
        r, c, rows, cols = values
        if min(r, c) < 0 or min(rows, cols, rs, cs) < 1 or r + rs > rows or c + cs > cols or rows * cols > 100000:
            raise ValueError('Invalid or unsupported table dimensions')
        state = tables.setdefault(table, {'shape': (rows, cols), 'occupied': set()})
        area = {(rr, cc) for rr in range(r, r + rs) for cc in range(c, c + cs)}
        if state['shape'] != (rows, cols) or state['occupied'] & area:
            raise ValueError('Conflicting table shape or duplicate/overlapping cells')
        state['occupied'].update(area)
    for table, state in tables.items():
        rows, cols = state['shape']
        if len(state['occupied']) != rows * cols:
            raise ValueError(f'Incomplete table grid: {table}; register empty cells explicitly')


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
    if any(not isinstance(entry, dict) for entry in supplied):
        raise ValueError('Overlay region must be an object')
    region_lookup = {r['region_id']: r for r in regions}
    reviewed_ocr = (value.get('schema_version') == 2 or 'contract' in value or value.get('method') == OCR_METHOD
                    or any(any(k in entry for k in ('response', 'review', 'transcription_hash', 'request', 'source_image'))
                           for entry in supplied))
    if reviewed_ocr:
        # Verify every region before appending units, changing dispositions, or writing figure crops.
        _validate_reviewed_overlay(value, overlay_path, source_id, region_lookup)
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
        locator = original['locator']
        page = locator['page']
        parent_box = pymupdf.Rect(locator['bbox'])
        validate_units(units, parent_box, strict=reviewed_ocr)
        related = [a for a in assets if a.get('page') == page and a.get('bbox') and pymupdf.Rect(a['bbox']).contains(parent_box)]
        additions = []
        for index, row in enumerate(units):
            bbox = row.get('bbox')
            item_id = f'{region_id}:manual{index + 1}'
            manual_region = f'{item_id}:region'
            source_text, mapping = normalize_with_map(row['source'])
            unit_locator = {**locator, 'region_id': manual_region, 'parent_region_id': region_id,
                            'bbox': bbox, 'start': 0, 'end': len(row['source']), 'method': 'manual_transcription'}
            item = {'id': item_id, 'kind': row.get('kind', 'figure_label'), 'section': row.get('section', 'Transcribed source'), 'page': page,
                    'parent_id': f'{region_id}:paragraph:{row.get("paragraph_id", index + 1)}',
                    'source': source_text, 'raw_source': row['source'], 'normalization_map': mapping,
                    'locators': [unit_locator], 'spans': [{'bbox': bbox}], 'provenance': 'manual',
                    'source_region_id': region_id, 'source_region_order': index,
                    'reviewer': reviewer, 'action': 'translate' if source_text else 'preserve', 'status': 'pending'}
            for field in ('heading_level', 'row', 'col', 'table_rows', 'table_cols', 'rowspan', 'colspan'):
                if field in row:
                    item[field] = row[field]
            if 'table_id' in row:
                item['table_id'] = f'{region_id}:table:{row["table_id"]}'
            if item['kind'] == 'figure':
                crop_box = checked_box(row['figure_bbox'], parent_box, 'figure')
                asset_id = f'{item_id}:figure'
                stamp = canonical_hash({'page': page, 'bbox': list(crop_box)})[:16]
                folder = Path(overlay_path).resolve().parent / 'overlay-assets'
                folder.mkdir(parents=True, exist_ok=True)
                image_path = folder / f'{source_id[:16]}-{stamp}.png'
                with pymupdf.open(source_path) as document:
                    source_page = document[page - 1]
                    if source_page.rotation:
                        raise ValueError('Rotated figure crops require explicit coordinate normalization')
                    source_page.get_pixmap(clip=crop_box, dpi=250).save(image_path)
                asset = {'asset_id': asset_id, 'doc_id': source_id, 'path': str(image_path),
                         'sha256': digest(image_path), 'source_sha256': source_id, 'page': page,
                         'bbox': list(crop_box), 'caption_bbox': bbox, 'owner_ids': [item_id],
                         'kind': 'figure', 'representation': 'png', 'provenance': 'reviewed_scan_crop'}
                assets.append(asset)
                item.update(asset_id=asset_id, image_path=str(image_path), figure_bbox=list(crop_box))
            elif len(related) == 1 and item['kind'] == 'figure_label':
                item['figure_asset_id'] = related[0]['asset_id']
            items.append(item)
            additions.append(item_id)
            regions.append({'region_id': manual_region, 'locator': unit_locator, 'role': item['kind'], 'raw_text': row['source'],
                            'normalized_text': source_text, 'disposition': item['action'], 'unit_ids': [item_id],
                            'evidence': {'method': 'manual_transcription', 'reviewer': reviewer, 'source_sha256': source_id}})
        for item in (i for i in items if i['id'] in additions and i['kind'] == 'figure_label'):
            label_box = pymupdf.Rect(item['locators'][0]['bbox'])
            owners = [a for a in assets if a.get('page') == page and a.get('kind') == 'figure'
                      and a.get('bbox') and pymupdf.Rect(a['bbox']).contains(label_box)]
            if len(owners) == 1:
                item['figure_asset_id'] = owners[0]['asset_id']
        original['unit_ids'] = list(dict.fromkeys(original.get('unit_ids', []) + additions))
        original['evidence'] = {'method': 'manual_transcription', 'reviewer': reviewer, 'complete': entry['complete'],
                                'description': entry['evidence'], 'source_sha256': source_id, 'overlay_sha256': digest(overlay_path)}
        if reviewed_ocr:
            original['evidence'].update(review_contract=OCR_CONTRACT, transcription_hash=entry['transcription_hash'])
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
                       'source_sha256': source_id, 'regions': supplied, 'resolved_regions': sorted(resolved),
                       'review_contract': OCR_CONTRACT if reviewed_ocr else 'legacy_manual_overlay'}
