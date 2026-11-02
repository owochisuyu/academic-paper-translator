"""Recover numbered bibliography boundaries from ordered native PDF regions.

A PDF text block may contain the end of one entry and the beginning of the next.
Use the independently retained native line/character intervals, not sentence
splitting or the physical PDF block as the bibliography entry boundary.
"""
from __future__ import annotations

import copy
import re

from .common import normalize_with_map

HEADINGS = re.compile(r'^(?:references|bibliography|literature\s+cited)\s*$', re.I)
NUMBERED = re.compile(r'(?m)^[ \t]*\[([0-9]+)\][ \t]+(?=\S)')
SECTION_START = re.compile(r'^(?:appendix|appendices|acknowledg(?:e)?ments?|supplementary\s+material)\b', re.I)
ALLOWED_KINDS = {'sentence', 'reference', 'heading', 'title', 'text'}


def _native_region(region, units):
    loc = region.get('locator', {})
    owners = region.get('unit_ids', [])
    return (region.get('role') == 'text' and region.get('disposition') in ('translate', 'preserve')
            and region.get('provenance', 'native') == 'native' and loc.get('method', 'native') == 'native'
            and 'block_id' in loc and isinstance(region.get('raw_text'), str)
            and loc.get('start') == 0 and loc.get('end') == len(region['raw_text'])
            and owners and all(key in units and units[key].get('kind') in ALLOWED_KINDS
                              and units[key].get('provenance', 'native') == 'native' for key in owners))


def _font_size(region):
    return max((span.get('size', 0) for span in region.get('spans', [])), default=0)


def _part(region, start, end):
    raw = region['raw_text'][start:end]
    locator = {**region['locator'], 'region_id': region['region_id'], 'start': start, 'end': end}
    return {'source': normalize_with_map(raw)[0], 'raw_source': raw, 'locators': [locator],
            'spans': [copy.deepcopy(span) for span in region.get('spans', [])
                      if span.get('end', 0) > start and span.get('start', len(raw)) < end]}


def _entry(ident, label, heading, parts):
    raw = '\n'.join(part['raw_source'] for part in parts)
    # These are shared objects deliberately: create_job scopes both views of
    # the same locators together when it deep-copies a source unit.
    locators = [loc for part in parts for loc in part['locators']]
    return {'id': ident, 'kind': 'reference', 'reference_no': label,
            'source': normalize_with_map(raw)[0], 'raw_source': raw,
            'source_parts': parts, 'locators': locators,
            'spans': [span for part in parts for span in part['spans']],
            'normalization_map': [], 'page': locators[0]['page'],
            'section': heading, 'section_path': [heading], 'parent_id': ident,
            'provenance': 'native', 'action': 'translate', 'status': 'pending'}


def reconcile_numbered_references(items, regions, warnings):
    """Return ``(ordered_items, info)``; update region owners and warnings in place.

    Call after native reading-order calculation and before reference/paragraph
    merges or line-wrap normalization. This only applies to an explicit separate
    bibliography heading followed by a continuous sequence of numbered entries.
    Ambiguous numbering or cross-region ownership retains the original source.
    No source or semantic review is fabricated.
    """
    units = {unit['id']: unit for unit in items}
    region_index = {region['region_id']: region for region in regions}
    ordered_ids, seen = [], set()
    for item in items:
        for loc in item.get('locators', []):
            ident = loc.get('region_id')
            if ident in region_index and ident not in seen:
                ordered_ids.append(ident)
                seen.add(ident)
    plans = []
    reserved = set(units)
    planned_regions = set()
    for index, ident in enumerate(ordered_ids):
        anchor = region_index[ident]
        if not _native_region(anchor, units) or not HEADINGS.fullmatch(anchor['raw_text'].strip()):
            continue
        heading = normalize_with_map(anchor['raw_text'])[0]
        selected, ref_font, started = [], 0, False
        for next_id in ordered_ids[index + 1:]:
            region = region_index[next_id]
            if region.get('disposition') == 'excluded':
                continue
            if not _native_region(region, units):
                break
            text = region['raw_text'].strip()
            if HEADINGS.fullmatch(text) or SECTION_START.match(text):
                break
            if any(units[key].get('kind') == 'heading' for key in region['unit_ids']):
                break
            if not started:
                first = NUMBERED.match(region['raw_text'])
                if first is None:
                    break
                ref_font, started = _font_size(region), True
            elif ref_font and _font_size(region) > ref_font * 1.18:
                # An unrecognised larger-font body/section after the bibliography
                # cannot be silently absorbed into its last entry.
                break
            selected.append(region)
        if not selected:
            continue
        selected_ids = {ident, *(region['region_id'] for region in selected)}
        owners = {owner for key in selected_ids for owner in region_index[key]['unit_ids']}
        if (planned_regions.intersection(selected_ids)
                or any(any(loc.get('region_id') not in selected_ids for loc in units[owner].get('locators', [])) for owner in owners)
                or any(set(region.get('unit_ids', [])).intersection(owners)
                       for key, region in region_index.items() if key not in selected_ids)):
            warnings.append({'code': 'ambiguous_reference_ownership', 'region_id': ident,
                             'page': anchor['locator']['page']})
            continue
        groups, labels = [], []
        malformed = False
        for region in selected:
            raw = region['raw_text']
            matches = list(NUMBERED.finditer(raw))
            positions = {match.start(): int(match.group(1)) for match in matches}
            boundaries = sorted({0, len(raw), *positions})
            for start, end in zip(boundaries, boundaries[1:]):
                if start in positions:
                    number = positions[start]
                    if number < 1 or labels and number != labels[-1] + 1:
                        malformed = True
                    labels.append(number)
                    groups.append([])
                if not raw[start:end].strip():
                    continue
                if not groups:
                    malformed = True
                    continue
                groups[-1].append(_part(region, start, end))
        if malformed or not groups or any(not group for group in groups):
            warnings.append({'code': 'ambiguous_reference_numbering', 'region_id': ident,
                             'page': anchor['locator']['page'], 'numbers': labels})
            continue
        loc = anchor['locator']
        prefix = f'pdf-bib-p{loc["page"]}-b{loc["block_id"]}'
        head_id = prefix + '-head'
        ids = [head_id, *(f'{prefix}-ref-{number:03d}' for number in labels)]
        if any(key in reserved for key in ids):
            warnings.append({'code': 'ambiguous_reference_identity', 'region_id': ident,
                             'page': loc['page']})
            continue
        heading_unit = {'id': head_id, 'kind': 'heading', 'source': heading,
            'raw_source': anchor['raw_text'], 'normalization_map': normalize_with_map(anchor['raw_text'])[1],
            'locators': [copy.deepcopy(loc)], 'spans': copy.deepcopy(anchor.get('spans', [])),
            'page': loc['page'], 'section': heading, 'section_path': [heading],
            'parent_id': head_id, 'heading_level': 1, 'heading_font_size': _font_size(anchor),
            'provenance': 'native', 'action': 'translate', 'status': 'pending'}
        heading_unit['locators'][0]['region_id'] = ident
        entries = [_entry(new_id, number, heading, parts)
                   for new_id, number, parts in zip(ids[1:], labels, groups)]
        region_owners = {key: [] for key in selected_ids}
        region_owners[ident].append(head_id)
        for entry in entries:
            for part_loc in entry['locators']:
                if entry['id'] not in region_owners[part_loc['region_id']]:
                    region_owners[part_loc['region_id']].append(entry['id'])
        plans.append({'owner_ids': owners, 'region_owners': region_owners,
                      'items': [heading_unit, *entries], 'anchor': ident, 'numbers': labels})
        reserved.update(ids)
        planned_regions.update(selected_ids)
    by_owner = {owner: plan for plan in plans for owner in plan['owner_ids']}
    output, inserted = [], set()
    for item in items:
        plan = by_owner.get(item['id'])
        if plan is None:
            output.append(item)
        elif plan['anchor'] not in inserted:
            output.extend(plan['items'])
            inserted.add(plan['anchor'])
    for plan in plans:
        for ident, owners in plan['region_owners'].items():
            region = region_index[ident]
            region['unit_ids'] = owners
            region.setdefault('evidence', {})['reference_boundaries'] = {
                'method': 'explicit bibliography heading and consecutive native line markers',
                'anchor': plan['anchor'], 'numbers': plan['numbers']}
    return output, {'method': 'native numbered reference boundaries',
        'sections': [{'anchor': plan['anchor'], 'numbers': plan['numbers'],
                      'entries': len(plan['numbers'])} for plan in plans],
        'entries': sum(len(plan['numbers']) for plan in plans)}
