"""Join agent translations onto the frozen source; never accept model-owned metadata."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys

try:
    from .document_ir import (ContractError, atomic_write_json, atomic_write_jsonl,
        canonical_hash, file_hash, load_job, load_translation_records, read_json, read_jsonl,
        resolve_job_path, validate_semantic_reviews, translation_bindings)
except ImportError:
    from document_ir import (ContractError, atomic_write_json, atomic_write_jsonl,
        canonical_hash, file_hash, load_job, load_translation_records, read_json, read_jsonl,
        resolve_job_path, validate_semantic_reviews, translation_bindings)


def _batch_records(job, review=False):
    manifest_path = job['root'] / 'batches' / ('review_manifest.json' if review else 'translation_manifest.json')
    manifest = read_json(manifest_path)
    if manifest.get('doc_id') != job['document']['doc_id'] or manifest.get('source_manifest_hash') != job['document']['source_manifest_hash']:
        raise ContractError('Batch manifest belongs to another extraction')
    glossary_path = job['root'] / 'glossary.json'
    if not glossary_path.is_file() or manifest.get('glossary_hash') != canonical_hash(read_json(glossary_path)):
        raise ContractError('Glossary changed after preparation; prepare current packets before merging')
    records, assigned = [], []
    for batch in manifest['batches']:
        request = read_json(resolve_job_path(job['root'], batch['request']))
        raw = {k: v for k, v in request.items() if k not in ('batch_id', 'cache_key', 'estimated_input_token_upper_bound')}
        if canonical_hash(raw) != batch['cache_key'] or request.get('cache_key') != batch['cache_key']:
            raise ContractError(f'Changed request packet: {batch["batch_id"]}')
        if request.get('assigned_ids') != batch['assigned_ids'] or request.get('generation') != manifest['config']:
            raise ContractError(f'Packet/manifest configuration mismatch: {batch["batch_id"]}')
        values = read_jsonl(resolve_job_path(job['root'], batch['response']))
        ids = [x.get('id') for x in values]
        if len(ids) != len(set(ids)) or set(ids) != set(batch['assigned_ids']):
            raise ContractError(f'Incomplete/duplicate/extra response IDs in {batch["batch_id"]}')
        assigned.extend(batch['assigned_ids'])
        records.extend(values)
    required = {x['id'] for x in job['source'] if x['action'] == 'translate'}
    if len(assigned) != len(set(assigned)) or set(assigned) != required:
        raise ContractError('Batch assignments do not cover the current source exactly')
    return records


def import_legacy(job, path):
    """Explicit migration attaches provenance only after source metadata consistency checks."""
    try:
        data = read_json(path)
    except ContractError:
        data = read_jsonl(path)
    if isinstance(data, dict) and 'id' not in data:
        data = [{'id': key, 'translation': value} for key, value in data.items()]
    elif isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise ContractError('Legacy translations must be a map or record array/JSONL')
    aliases = {}
    for item in job['source']:
        if item['action'] == 'translate':
            aliases[item['id']] = item
            aliases[item['id'].split(':', 1)[-1]] = item
    result = []
    for row in data:
        item = aliases.get(row.get('id'))
        if item is None:
            raise ContractError(f'Unknown legacy ID: {row.get("id")}')
        for key in ('source', 'kind', 'page', 'table_id', 'asset_id'):
            if key in row and row[key] != item.get(key):
                raise ContractError(f'Legacy source metadata conflict for {row["id"]}: {key}')
        target = row.get('translation', row.get('target'))
        if not isinstance(target, str) or not target.strip():
            raise ContractError(f'Invalid legacy translation: {row["id"]}')
        result.append({'id': item['id'], 'doc_id': job['document']['doc_id'],
                       'source_hash': item['source_hash'], 'source_manifest_hash': job['document']['source_manifest_hash'],
                       'translation': target, 'status': 'translated',
                       **translation_bindings(job),
                       'producer': {'type': 'legacy-import', 'origin': str(Path(path).name)}})
    return result


def merge_job(job_dir, translated_path=None, reviews_path=None, *, legacy=False, review_batches=False):
    job = load_job(job_dir)
    if translated_path:
        records = import_legacy(job, translated_path) if legacy else read_jsonl(translated_path)
    elif review_batches or reviews_path:
        records = load_translation_records(job)
    else:
        records = _batch_records(job)
    # Validate an isolated temporary input before replacing the saved canonical file.
    temporary = job['root'] / 'batches' / '.merge-candidate.jsonl'
    atomic_write_jsonl(temporary, records)
    try:
        records = load_translation_records(job, temporary)
    finally:
        temporary.unlink(missing_ok=True)
    targets = {x['id']: x for x in records}
    required = {x['id'] for x in job['source'] if x['action'] == 'translate'}
    if set(targets) != required:
        raise ContractError(f'Missing translation IDs: {sorted(required - set(targets))}')
    ordered = [targets[x['id']] for x in job['source'] if x['action'] == 'translate']
    merged = []
    for item in job['source']:
        row = copy.deepcopy(item)
        row['translation'] = targets[item['id']]['translation'] if item['action'] == 'translate' else item['source']
        merged.append(row)
    reviews = None
    if reviews_path or review_batches:
        reviews = read_jsonl(reviews_path) if reviews_path else _batch_records(job, review=True)
    review = validate_semantic_reviews(job, ordered, records=reviews)
    atomic_write_jsonl(job['root'] / 'translations.jsonl', ordered)
    atomic_write_jsonl(job['root'] / 'merged.jsonl', merged)
    if reviews is not None:
        atomic_write_jsonl(job['root'] / 'reviews.jsonl', reviews)
    report = {'status': 'PASS', 'meaning': 'canonical join completed; preflight and PDF verification still required',
              'source_manifest_hash': job['document']['source_manifest_hash'],
              'translations_hash': file_hash(job['root'] / 'translations.jsonl'),
              'merged_hash': file_hash(job['root'] / 'merged.jsonl'),
              'translated_items': len(ordered), 'preserved_items': len(merged) - len(ordered),
              'semantic_status': review['status']}
    atomic_write_json(job['root'] / 'checks' / 'merge.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job-dir', required=True)
    parser.add_argument('--translated', help='Explicit canonical JSONL input instead of current batch responses')
    parser.add_argument('--reviews', help='Independent semantic review JSONL')
    parser.add_argument('--review-batches', action='store_true')
    parser.add_argument('--import-legacy', action='store_true')
    args = parser.parse_args()
    try:
        if args.import_legacy and not args.translated:
            raise ContractError('--import-legacy requires --translated')
        report = merge_job(args.job_dir, args.translated, args.reviews,
                           legacy=args.import_legacy, review_batches=args.review_batches)
        print(json.dumps(report, ensure_ascii=False))
        return 0
    except (ContractError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
