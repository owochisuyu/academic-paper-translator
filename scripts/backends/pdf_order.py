"""Deterministic block ordering; geometry remains available for independent review."""
from __future__ import annotations

import pymupdf


def order_pdf_units(items, assets):
    asset_lookup = {a['asset_id']: a for a in assets}
    result = []
    for page in sorted({item['page'] for item in items}):
        groups = {}
        for item in (i for i in items if i['page'] == page):
            if item.get('provenance') == 'manual' and item.get('source_region_id'):
                # The independently assessed reading order applies inside this
                # exact source image only. Native content and other regions still
                # use page geometry, avoiding cross-page/global manual reordering.
                key = ('manual-source-region', item['source_region_id'])
            else:
                key = item.get('table_id') or item.get('figure_asset_id') or item.get('asset_id') or item.get('parent_id') or item['id']
            groups.setdefault(key, []).append(item)
        positioned = []
        for key, members in groups.items():
            boxes = [pymupdf.Rect(loc['bbox']) for i in members for loc in i.get('locators', []) if loc.get('bbox')]
            asset = asset_lookup.get(key)
            if asset and asset.get('bbox'):
                boxes.append(pymupdf.Rect(asset['bbox']))
            rect = pymupdf.Rect(boxes[0]) if boxes else pymupdf.Rect(0, 0, 0, 0)
            for box in boxes[1:]:
                rect |= box
            if isinstance(key, tuple) and key[0] == 'manual-source-region':
                positions = [i.get('source_region_order') for i in members]
                if any(type(n) is not int for n in positions) or sorted(positions) != list(range(len(members))):
                    raise ValueError('Incomplete or duplicated source-image reading order')
                members.sort(key=lambda i: i['source_region_order'])
            elif any(i.get('table_id') for i in members):
                members.sort(key=lambda i: (-1, -1) if i['kind'] in ('table', 'table_caption') else (i.get('row', 0), i.get('col', 0)))
            elif asset:
                members.sort(key=lambda i: (0 if i['kind'] in ('figure', 'equation') else 1,
                                            i.get('locators', [{}])[0].get('bbox', [0, 0])[1]))
            positioned.append((rect, members))
        width = next((loc['width'] for i in items if i['page'] == page for loc in i.get('locators', []) if 'width' in loc), 595)
        midpoint = width / 2
        left = [g for g in positioned if g[0].width < .6 * width and g[0].x1 <= midpoint + 12]
        right = [g for g in positioned if g[0].width < .6 * width and g[0].x0 >= midpoint - 12]
        sidebar = [g for g in positioned if g[0].x1 < .3 * width and g[0].width < .25 * width]
        wide_body = [g for g in positioned if g[0].x0 > .25 * width and g[0].width > .55 * width]
        if len(sidebar) >= 2 and len(wide_body) >= 2:
            # A narrow metadata sidebar is not an equal-width text column. Native
            # block order usually encodes title/metadata/body relationships here.
            ordered = sorted(positioned, key=lambda g: min((loc.get('block_id', 10**9) for i in g[1] for loc in i.get('locators', [])), default=10**9))
        elif len(left) >= 2 and len(right) >= 2:
            columns = {id(g) for g in [*left, *right]}
            spanning = sorted((g for g in positioned if id(g) not in columns), key=lambda g: (g[0].y0, g[0].x0))
            remaining = [g for g in positioned if id(g) in columns]
            ordered = []
            for boundary in spanning:
                before = [g for g in remaining if (g[0].y0 + g[0].y1) / 2 < (boundary[0].y0 + boundary[0].y1) / 2]
                ordered.extend(sorted(before, key=lambda g: (0 if g in left else 1, g[0].y0, g[0].x0)))
                remaining = [g for g in remaining if g not in before]
                ordered.append(boundary)
            ordered.extend(sorted(remaining, key=lambda g: (0 if g in left else 1, g[0].y0, g[0].x0)))
        else:
            ordered = sorted(positioned, key=lambda g: (g[0].y0, g[0].x0))
        for _, members in ordered:
            result.extend(members)
    return result
