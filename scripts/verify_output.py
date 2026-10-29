# -*- coding: utf-8 -*-
"""Reopen compiled PDFs and require a current, explicit full-page visual review."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import sys
import unicodedata


def _ir():
    try:
        from . import document_ir
    except ImportError:
        import document_ir
    return document_ir


def _compiler():
    try:
        from . import compile_typst
    except ImportError:
        import compile_typst
    return compile_typst


def normalize_text(text):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', text)).replace('\u00ad', '')


def _pixel_key(data):
    from PIL import Image
    with Image.open(io.BytesIO(data)) as source:
        image = source.convert('RGBA')
        background = Image.new('RGBA', image.size, 'white')
        background.alpha_composite(image)
        rgb = background.convert('RGB')
        # Embedded image data are normally lossless. Quantization avoids harmless color rounding.
        pixels = rgb.resize((32, 32), Image.Resampling.BOX)
        signature = bytes((value // 8) * 8 for value in pixels.tobytes())
        return (round(rgb.width / rgb.height, 3), hashlib.sha256(signature).hexdigest())


def inspect_pdf(pdf_path, segments, graphics=None, assets=None, root=None, preview_dir=None, mode=None):
    import pymupdf
    root = Path(root or Path(pdf_path).parent)
    findings, positions = [], []
    expected_graphics = Counter(g['asset_id'] for g in (graphics or []))
    asset_map = {a['asset_id']: a for a in (assets or [])}
    actual_images = Counter()
    image_errors = []
    streams = {'all': [], 'left': [], 'right': []}
    character_pages = {}
    character_index = 0
    with pymupdf.open(pdf_path) as doc:
        if len(doc) == 0:
            findings.append({'type': 'empty_document', 'severity': 'error'})
        for number, page in enumerate(doc, 1):
            for block in page.get_text('dict').get('blocks', []):
                for line in block.get('lines', []):
                    for span in line.get('spans', []):
                        bbox = pymupdf.Rect(span['bbox'])
                        normalized = normalize_text(span['text'])
                        # The controlled renderer has one page-number footer. Exclude only that
                        # exact text in its margin; do not strip numbers from article content.
                        is_footer = normalized == str(number) and bbox.y0 > page.rect.height - 45
                        if not is_footer:
                            column = 'left' if (bbox.x0 + bbox.x1) / 2 < page.rect.width / 2 else 'right'
                            for char in normalized:
                                character_pages[character_index] = number
                                streams['all'].append((char, character_index))
                                streams[column].append((char, character_index))
                                character_index += 1
                            if '\ufffd' in normalized or '\u25a1' in normalized:
                                findings.append({'type': 'unrecognized_glyph', 'page': number, 'severity': 'review'})
                        if span['text'].strip() and not (page.rect + (-1, -1, 1, 1)).contains(bbox):
                            findings.append({'type': 'text_outside_page', 'page': number, 'bbox': list(bbox), 'text': span['text'], 'severity': 'error'})
            for span in page.get_texttrace():
                if span.get('type') == 3 or span.get('opacity', 1) == 0:
                    findings.append({'type': 'invisible_text', 'page': number, 'severity': 'error'})
            for occurrence in page.get_image_info(xrefs=True):
                xref = occurrence.get('xref', 0)
                if not xref:
                    image_errors.append({'page': number, 'reason': 'inline image has no extractable object'})
                    continue
                try:
                    extracted = doc.extract_image(xref)
                    pixels = extracted['image']
                    if extracted.get('smask'):
                        # PDF stores PNG alpha as a separate soft-mask image object.
                        # Compare the visible composite, not the opaque RGB payload alone.
                        base = pymupdf.Pixmap(doc, xref)
                        mask = pymupdf.Pixmap(doc, extracted['smask'])
                        pixels = pymupdf.Pixmap(base, mask).tobytes('png')
                    actual_images[_pixel_key(pixels)] += 1
                except Exception as exc:
                    image_errors.append({'page': number, 'reason': str(exc)})
            if preview_dir:
                previews = Path(preview_dir)
                previews.mkdir(parents=True, exist_ok=True)
                page.get_pixmap(dpi=120).save(previews / f'page-{number:04}.png')
        page_count = len(doc)
    combined = {key: ''.join(char for char, _ in stream) for key, stream in streams.items()}
    used = set()
    for segment in segments:
        expected = normalize_text(segment['text'])
        if not expected:
            continue
        candidates = ['all']
        if mode == 'D':
            candidates.append('left' if segment.get('language') == 'source' else 'right')
        found = None
        for stream_key in candidates:
            start = 0
            while True:
                start = combined[stream_key].find(expected, start)
                if start < 0:
                    break
                ids = {ident for _, ident in streams[stream_key][start:start + len(expected)]}
                if not ids & used:
                    found = (stream_key, start, ids)
                    used.update(ids)
                    break
                start += 1
            if found:
                break
        if found is None:
            findings.append({'type': 'missing_math_glyphs' if segment.get('verification') == 'math_glyphs' else 'missing_text_occurrence', 'id': segment.get('id'),
                             'language': segment.get('language'), 'text': segment['text'], 'severity': 'error'})
        else:
            stream_key, start, ids = found
            pages = sorted({character_pages[ident] for ident in ids})
            positions.append({'id': segment.get('id'), 'language': segment.get('language'), 'pages': pages,
                              'stream': stream_key, 'normalized_offset': start, 'normalized_length': len(expected),
                              'verification': segment.get('verification', 'text')})
    expected_pixels = Counter()
    asset_pixel_ids = {}
    for asset_id, count in expected_graphics.items():
        asset = asset_map.get(asset_id)
        if not asset:
            findings.append({'type': 'missing_asset_record', 'asset_id': asset_id, 'severity': 'error'})
            continue
        path = root / asset['path']
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != asset['sha256']:
            findings.append({'type': 'asset_changed', 'asset_id': asset_id, 'severity': 'error'})
            continue
        try:
            key = _pixel_key(path.read_bytes())
            expected_pixels[key] += count
            asset_pixel_ids.setdefault(key, []).append(asset_id)
        except Exception:
            findings.append({'type': 'graphic_requires_visual_review', 'asset_id': asset_id, 'severity': 'review'})
    for key, count in expected_pixels.items():
        if actual_images[key] < count:
            findings.append({'type': 'missing_or_wrong_graphic', 'asset_ids': asset_pixel_ids[key],
                             'expected': count, 'observed': actual_images[key], 'severity': 'error'})
    for error in image_errors:
        findings.append({'type': 'uninspectable_image', **error, 'severity': 'review'})
    status = 'FAIL' if any(f['severity'] == 'error' for f in findings) else ('NEEDS_REVIEW' if findings else 'PASS')
    return {'status': status, 'page_count': page_count, 'segments_expected': len(segments),
            'segments_found': len(positions), 'positions': positions,
            'graphics_expected': sum(expected_graphics.values()), 'findings': findings,
            'limitations': ['Text extraction cannot establish visibility under overlaid graphics.',
                            'Pixel fingerprints are an identity check, not proof of correct source cropping.',
                            'Mathematical glyphs and layout require an explicit full-page visual review.']}


def validate_visual_review(review, pdf_hash, page_count):
    if not isinstance(review, dict):
        return {'status': 'NOT_RUN', 'reason': 'No explicit visual review supplied'}
    if review.get('pdf_hash') != pdf_hash or review.get('page_count') != page_count:
        return {'status': 'FAIL', 'reason': 'Visual review refers to a different PDF or page count'}
    pages = review.get('reviewed_pages')
    if not isinstance(pages, list) or not all(type(p) is int for p in pages) or len(pages) != len(set(pages)) or set(pages) != set(range(1, page_count + 1)):
        return {'status': 'NEEDS_REVIEW', 'reason': 'Review must cover every actual output page exactly once'}
    if not isinstance(review.get('reviewer'), (str, dict)) or not review['reviewer']:
        return {'status': 'NEEDS_REVIEW', 'reason': 'Reviewer identity/method missing'}
    required = {'layout', 'graphics', 'math', 'text_visibility'}
    checks = review.get('checks')
    if not isinstance(checks, list) or not all(isinstance(c, str) for c in checks) or not required.issubset(set(checks)):
        return {'status': 'NEEDS_REVIEW', 'reason': 'Visual checks incomplete'}
    if review.get('status') != 'PASS' or review.get('findings') != []:
        return {'status': 'NEEDS_REVIEW', 'reason': 'Visual review has unresolved findings or is not PASS'}
    return {'status': 'PASS', 'reviewer': review['reviewer'], 'reviewed_pages': pages}


def verify_job(job_dir, visual_review_path=None, promote=True):
    ir = _ir()
    root = Path(job_dir).resolve()
    job = ir.load_merged_job(root, require_preflight=True)
    mapping = ir.read_json(root / 'checks' / 'render_map.json')
    if mapping.get('status') != 'candidate':
        raise ir.ContractError('Only a candidate can be verified for acceptance')
    if mapping.get('bindings') != ir.job_bindings(job):
        raise ir.ContractError('Candidate belongs to stale job contents')
    for name in ('pdf', 'typ', 'markdown'):
        path = (root / mapping[name + '_path']).resolve()
        if not path.is_relative_to(root):
            raise ir.ContractError('Candidate path leaves the job directory')
        if not path.is_file() or ir.file_hash(path) != mapping[name + '_hash']:
            raise ir.ContractError(f'{name} candidate changed after compilation')
    # Reconstruct expectations from the trusted records, never from renderer self-report alone.
    segments, graphics, math_ids = _compiler().expected_content(job['merged'], mapping['mode'])
    if segments != mapping.get('segments') or graphics != mapping.get('graphics') or math_ids != mapping.get('math_ids'):
        raise ir.ContractError('Render map does not match canonical merged contents')
    pdf_path = root / mapping['pdf_path']
    # The Markdown and Typst files depend on the portable copies, not the original job assets.
    # Derive their paths from the canonical asset hashes instead of trusting renderer metadata.
    expected_assets, markdown_assets = [], {}
    for asset in job['assets']:
        source = ir.resolve_job_path(root, asset['path'])
        copied = pdf_path.parent / 'assets' / (asset['sha256'] + source.suffix.lower())
        if not copied.is_file() or ir.file_hash(copied) != asset['sha256']:
            raise ir.ContractError(f'Portable candidate asset changed or missing: {asset["asset_id"]}')
        expected_assets.append({**asset, 'path': copied.relative_to(root).as_posix()})
        markdown_assets[asset['asset_id']] = copied.relative_to(pdf_path.parent).as_posix()
    if expected_assets != mapping.get('assets'):
        raise ir.ContractError('Portable asset map does not match canonical assets')
    expected_markdown = _compiler().export_markdown(job['merged'], mapping['mode'], markdown_assets)
    if (root / mapping['markdown_path']).read_text(encoding='utf-8') != expected_markdown:
        raise ir.ContractError('Markdown does not match canonical merged contents')
    preview_dir = root / 'checks' / 'previews' / mapping['pdf_hash']
    inspection = inspect_pdf(pdf_path, segments, graphics, job['assets'], root, preview_dir, mode=mapping['mode'])
    review_path = Path(visual_review_path) if visual_review_path else root / 'reviews' / 'visual.json'
    visual = validate_visual_review(ir.read_json(review_path) if review_path.is_file() else None,
                                    mapping['pdf_hash'], inspection['page_count'])
    if inspection['status'] == 'FAIL' or visual['status'] == 'FAIL':
        status = 'FAIL'
    elif visual['status'] != 'PASS':
        status = 'NEEDS_REVIEW'
    else:
        # Review-only findings (e.g. vector graphics) are covered by the explicit visual review.
        status = 'PASS'
    report = {'schema_version': 3, 'status': status,
              'delivery_status': 'accepted' if status == 'PASS' else 'needs_review' if status == 'NEEDS_REVIEW' else 'failed',
              'bindings': ir.job_bindings(job), 'pdf_hash': mapping['pdf_hash'], 'pdf_path': mapping['pdf_path'],
              'preview_dir': preview_dir.relative_to(root).as_posix(),
              'automatic': inspection, 'visual': visual,
              'scope': job['document'].get('scope'), 'semantic': job.get('preflight', {}).get('checks', {}).get('semantic')}
    if status == 'PASS' and promote:
        target = root / 'deliverables'
        target.mkdir(exist_ok=True)
        for name in ('pdf', 'typ', 'markdown'):
            source = root / mapping[name + '_path']
            shutil.copy2(source, target / source.name)
        candidate_assets = pdf_path.parent / 'assets'
        if candidate_assets.is_dir():
            shutil.copytree(candidate_assets, target / 'assets', dirs_exist_ok=True)
        report['deliverables'] = [str((target / Path(mapping[name + '_path']).name).relative_to(root).as_posix()) for name in ('pdf', 'typ', 'markdown')]
        ir.atomic_write_json(target / 'acceptance.json', report)
    ir.atomic_write_json(root / 'checks' / 'rendering.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description='Verify actual candidate PDF and optionally promote an explicitly reviewed output')
    parser.add_argument('--job-dir', required=True)
    parser.add_argument('--visual-review')
    parser.add_argument('--no-promote', action='store_true')
    args = parser.parse_args()
    try:
        report = verify_job(args.job_dir, args.visual_review, not args.no_promote)
        print(json.dumps({'status': report['status'], 'delivery_status': report['delivery_status']}, ensure_ascii=False))
        return 0 if report['status'] == 'PASS' else 2 if report['status'] == 'FAIL' else 3
    except (ValueError, OSError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
