"""Import a complete, source-bound MinerU 4 MiddleJson result package.

Supported upstream contract: https://opendatalab.github.io/MinerU/reference/output_files/
This adapter deliberately does not install/start MinerU, download models, or upload
papers. The envelope is ours, not a claimed MinerU API response:
  {"source_sha256": ..., "coordinate_system": "unrotated_top_left_pt",
   "result": <saved middle_json.json>, "producer_run": {...}}
Use the actual saved package, including images, and record its producer version.
"""
from __future__ import annotations

from pathlib import Path
import importlib.util
import math
import re
import pymupdf

from .common import digest, normalize_with_map, sentence_ranges
try:
    from ..align_bilingual import split_en_sentences
    from ..document_ir import read_json
except ImportError:
    from align_bilingual import split_en_sentences
    from document_ir import read_json


def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for span in content:
            if not isinstance(span, dict) or span.get('type') not in ('text', 'inline_equation', 'equation'):
                raise ValueError('Unsupported MinerU inline span; retain and review the original result')
            value = span.get('content', '')
            if not isinstance(value, str):
                raise ValueError('MinerU text span content must be a string')
            parts.append('$' + value + '$' if span.get('type') != 'text' else value)
        return ''.join(parts)
    raise ValueError('Unsupported MinerU block content')


def extract_mineru(input_path, result_path=None):
    if not result_path:
        installed = importlib.util.find_spec('mineru') is not None
        raise ImportError('MinerU ' + ('is installed but live execution is not configured' if installed else 'is not installed') +
                          '; supply --mineru-result with a complete source-bound saved MiddleJson package. No remote fallback is performed.')
    source = Path(input_path).resolve()
    package = Path(result_path).resolve()
    envelope = read_json(package)
    if not isinstance(envelope, dict) or envelope.get('source_sha256') != digest(source):
        raise ValueError('MinerU package is not bound to the input PDF hash')
    payload = envelope.get('result')
    if not isinstance(payload, dict) or payload.get('schema') != 'docvortex.middle' or payload.get('schema_version') != '2.0':
        raise ValueError('Expected MinerU 4 docvortex.middle protocol 2.0; CLI --json is not MiddleJson')
    producer = payload.get('metadata', {}).get('producer', {})
    if producer.get('name') != 'mineru' or not str(producer.get('version', '')).startswith('4.'):
        raise ValueError('Unsupported or unspecified MinerU producer version')
    if payload.get('is_full_document') is not True or envelope.get('next_request') or envelope.get('errors'):
        raise ValueError('MinerU result is partial or failed; all pages must finish before import')
    with pymupdf.open(source) as pdf:
        page_sizes = [(p.rect.width, p.rect.height) for p in pdf]
    pages = payload.get('pages')
    if not isinstance(pages, list):
        raise ValueError('MinerU result lacks pages')
    indices = [p.get('page_idx') for p in pages if isinstance(p, dict)]
    if len(indices) != len(pages) or any(type(i) is not int for i in indices) or sorted(indices) != list(range(len(page_sizes))):
        raise ValueError('MinerU page set is incomplete, duplicated, or outside the input PDF')
    coordinate_system = envelope.get('coordinate_system')
    if coordinate_system not in ('unrotated_top_left_pt', 'normalized_1000', None):
        raise ValueError('Unsupported MinerU coordinate declaration')
    items, regions, assets, warnings = [], [], [], []
    section = 'Title & Abstract'
    for page in sorted(pages, key=lambda x: x['page_idx']):
        page_idx = page['page_idx']
        blocks = page.get('blocks')
        if not isinstance(blocks, list):
            raise ValueError('MinerU page lacks block list')
        seen = set()
        for sequence, block in enumerate(blocks):
            if not isinstance(block, dict):
                raise ValueError('MinerU block must be an object')
            block_id = block.get('index', sequence)
            if block_id in seen:
                raise ValueError('Duplicate MinerU block index')
            seen.add(block_id)
            region_id = f'mineru:p{page_idx + 1}:b{block_id}'
            locator = {'page': page_idx + 1, 'block_id': block_id, 'region_id': region_id, 'method': 'ocr',
                       'width': page_sizes[page_idx][0], 'height': page_sizes[page_idx][1]}
            box = block.get('bbox')
            if box is not None:
                if not isinstance(box, list) or len(box) != 4 or not all(type(v) in (int, float) and math.isfinite(v) for v in box):
                    raise ValueError('Invalid MinerU bbox')
                locator['original_bbox'] = box
                if coordinate_system == 'normalized_1000':
                    box = [box[0] * locator['width'] / 1000, box[1] * locator['height'] / 1000,
                           box[2] * locator['width'] / 1000, box[3] * locator['height'] / 1000]
                if coordinate_system:
                    if not (0 <= box[0] <= box[2] <= locator['width'] + 1 and 0 <= box[1] <= box[3] <= locator['height'] + 1):
                        raise ValueError('MinerU bbox is outside source page')
                    locator['bbox'] = box
            if 'bbox' not in locator:
                warnings.append({'code': 'ocr_geometry_unavailable', 'region_id': region_id, 'page': page_idx + 1})
            confidence = block.get('confidence')
            if confidence is None or not isinstance(confidence, (int, float)) or confidence < .95:
                warnings.append({'code': 'ocr_confidence_requires_review', 'region_id': region_id, 'confidence': confidence})
            kind = block.get('type')
            source_text = ''
            try:
                source_text = _text(block.get('content', ''))
            except ValueError:
                warnings.append({'code': 'unsupported_mineru_content', 'region_id': region_id, 'block_type': kind})
            region = {'region_id': region_id, 'locator': locator, 'role': kind or 'unknown', 'raw_text': source_text,
                      'disposition': 'translate', 'unit_ids': [], 'confidence': confidence,
                      'evidence': {'producer': producer, 'method': 'ocr', 'source_sha256': envelope['source_sha256']}}
            if kind not in ('text', 'paragraph', 'title', 'heading', 'equation', 'code', 'caption', 'footnote') or not source_text.strip():
                region['disposition'] = 'needs_review'
                region['backend_block'] = block
                warnings.append({'code': 'unsupported_mineru_block', 'region_id': region_id, 'block_type': kind})
                regions.append(region)
                continue
            mapped_kind = {'text': 'sentence', 'paragraph': 'sentence', 'title': 'heading'}.get(kind, kind)
            if mapped_kind == 'heading':
                section = source_text
            action = 'preserve' if kind in ('equation', 'code') else 'translate'
            region['disposition'] = action
            splits = sentence_ranges(source_text, split_en_sentences) if mapped_kind == 'sentence' else [(normalize_with_map(source_text)[0], 0, len(source_text), [])]
            for n, (sentence, start, end, mapping) in enumerate(splits):
                unit_id = f'{region_id}:s{n + 1}'
                item = {'id': unit_id, 'kind': mapped_kind, 'section': section, 'page': page_idx + 1,
                        'source': sentence, 'raw_source': source_text[start:end], 'normalization_map': mapping,
                        'locators': [{**locator, 'start': start, 'end': end}], 'spans': [{'bbox': locator['bbox']}] if 'bbox' in locator else [],
                        'parent_id': region_id, 'action': action, 'status': 'pending', 'provenance': 'ocr', 'confidence': confidence}
                if kind == 'equation':
                    item['math_format'] = 'latex'
                items.append(item)
                region['unit_ids'].append(unit_id)
            regions.append(region)
    return {'items': items, 'regions': regions, 'assets': assets, 'warnings': warnings, 'pages': len(page_sizes),
            'document': {'source_format': 'pdf', 'extractor': producer, 'processed_pages': list(range(1, len(page_sizes) + 1)),
                         'backend_contract': 'docvortex.middle/2.0', 'backend_run': envelope.get('producer_run', {}),
                         'limitations': ['Saved-result importer only; live MinerU execution not validated', 'Visual/table blocks require explicit review']}}
