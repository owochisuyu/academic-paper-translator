"""Versioned document jobs and strict, content-bound interchange.

These hashes detect stale/mixed artifacts, not hostile modification of every file.
Translation and review remain model/human work; this module never invents either.
"""
from __future__ import annotations

import copy
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import shutil
import tempfile
from typing import Any

SCHEMA_VERSION = 3
SEMANTIC_CHECKS = (
    'omission', 'addition', 'conditions', 'negation', 'causality',
    'comparison', 'references', 'quantities', 'terminology',
)


class ContractError(ValueError):
    """An invalid, incomplete, or stale interchange artifact."""


def canonical_hash(data: Any) -> str:
    value = json.dumps(data, ensure_ascii=False, sort_keys=True,
                       separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def file_hash(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def _unique_pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ContractError(f'Duplicate JSON key: {key!r}')
        value[key] = item
    return value


def _decode(text: str, location: str):
    def reject_constant(value):
        raise ContractError(f'Non-finite JSON number: {value}')
    try:
        return json.loads(text, object_pairs_hook=_unique_pairs,
                          parse_constant=reject_constant)
    except (json.JSONDecodeError, ContractError) as exc:
        raise ContractError(f'{location}: {exc}') from exc


def read_json(path: str | Path):
    path = Path(path)
    try:
        return _decode(path.read_text(encoding='utf-8-sig'), str(path))
    except OSError as exc:
        raise ContractError(f'Cannot read {path}: {exc}') from exc


def read_jsonl(path: str | Path) -> list[dict]:
    path = Path(path)
    try:
        text = path.read_text(encoding='utf-8-sig')
    except OSError as exc:
        raise ContractError(f'Cannot read {path}: {exc}') from exc
    if not text.strip():
        return []
    if text.lstrip().startswith('['):
        records = _decode(text, str(path))
    else:
        records = [_decode(line, f'{path}:{n}')
                   for n, line in enumerate(text.splitlines(), 1) if line.strip()]
    if not isinstance(records, list) or any(not isinstance(x, dict) for x in records):
        raise ContractError(f'{path}: expected records containing JSON objects')
    return records


def _atomic_write(path: str | Path, content: str):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f'.{path.name}.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def atomic_write_json(path: str | Path, data: Any):
    _atomic_write(path, json.dumps(data, ensure_ascii=False, sort_keys=True,
                                  indent=2, allow_nan=False) + '\n')


def atomic_write_jsonl(path: str | Path, records):
    _atomic_write(path, ''.join(json.dumps(record, ensure_ascii=False,
                  sort_keys=True, allow_nan=False) + '\n' for record in records))


def resolve_job_path(root: str | Path, relative: str) -> Path:
    root = Path(root).resolve()
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ContractError(f'Expected task-relative path, got {relative!r}')
    result = (root / relative).resolve()
    if not result.is_relative_to(root):
        raise ContractError(f'Path escapes job directory: {relative}')
    return result


def source_hash(item: dict) -> str:
    return canonical_hash({k: v for k, v in item.items()
                           if k not in ('source_hash', 'status', 'translation')})


def translation_hash(record: dict | str) -> str:
    return canonical_hash(record if isinstance(record, str) else record['translation'])


def translation_bindings(job) -> dict:
    root = job['root']
    glossary = read_json(root / 'glossary.json') if (root / 'glossary.json').is_file() else {'version': 1, 'entries': []}
    policy = read_json(root / 'translation_policy.json') if (root / 'translation_policy.json').is_file() else None
    return {'glossary_hash': canonical_hash(glossary), 'generation_hash': canonical_hash(policy)}


def _unique_ids(records: list[dict], label: str, key='id') -> dict[str, dict]:
    result = {}
    for n, row in enumerate(records, 1):
        ident = row.get(key)
        if not isinstance(ident, str) or not ident:
            raise ContractError(f'{label} record {n}: missing/non-string {key}')
        if ident in result:
            raise ContractError(f'{label}: duplicate {key} {ident}')
        result[ident] = row
    return result


def create_job(input_path, job_dir, items, regions=None, assets=None,
               summary=None, scope='full', extra_document=None) -> dict:
    """Freeze an extraction. Existing jobs must be explicitly migrated, not overwritten."""
    original = Path(input_path).resolve()
    root = Path(job_dir).resolve()
    if (root / 'document.json').exists():
        raise ContractError(f'Job already exists: {root}; choose a new extraction directory')
    if scope not in ('full', 'body'):
        raise ContractError(f'Unknown scope: {scope}')
    extra = copy.deepcopy(extra_document or {})
    summary = copy.deepcopy(summary or {})
    supplied = extra.pop('input_files', None)
    source_root = Path(extra.pop('source_root', original if original.is_dir() else original.parent)).resolve()
    if supplied is not None:
        files = [Path(x['path'] if isinstance(x, dict) else x).resolve() for x in supplied]
    elif original.is_dir():
        files = sorted(p for p in original.rglob('*') if p.is_file()
                       and not any(part.startswith('.') or part in ('__pycache__', 'node_modules')
                                   for part in p.relative_to(original).parts)
                       and not p.resolve().is_relative_to(root))
    else:
        files = [original]
    if not files or any(not p.is_file() for p in files):
        raise ContractError('Input file set is empty or contains unavailable files')
    if not original.is_dir() and original not in files:
        files.append(original)
    inputs = []
    for path in sorted(set(files)):
        if not path.is_relative_to(source_root):
            raise ContractError(f'Input dependency outside source root: {path}')
        inputs.append({'path': 'input/' + path.relative_to(source_root).as_posix(),
                       'sha256': file_hash(path)})
    evidence_copies = []
    equation_overlay = extra.get('equation_overlay')
    if equation_overlay:
        manifest_path = Path(equation_overlay['path']).resolve()
        manifest_bytes = manifest_path.read_bytes()
        digest = hashlib.sha256(manifest_bytes).hexdigest()
        if digest != equation_overlay.get('sha256'):
            raise ContractError('Equation overlay changed after extraction')
        relative = 'evidence/equation-overlay-' + digest + '.json'
        inputs.append({'path': relative, 'sha256': digest})
        evidence_copies.append((relative, manifest_bytes))
        equation_overlay['path'] = relative
    doc_id = inputs[0]['sha256'] if len(inputs) == 1 else canonical_hash(inputs)
    prefix = doc_id[:16] + ':'
    root.mkdir(parents=True, exist_ok=True)
    for path, info in zip(sorted(set(files)), inputs):
        destination = resolve_job_path(root, info['path'])
        destination.parent.mkdir(parents=True, exist_ok=True)
        if path != destination:
            shutil.copy2(path, destination)
    for relative, content in evidence_copies:
        destination = resolve_job_path(root, relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    main_file = extra.pop('main_file', None)
    if original.is_dir():
        if main_file:
            primary = 'input/' + Path(main_file).as_posix().removeprefix('input/')
        else:
            primary = inputs[0]['path']
    else:
        primary = 'input/' + original.relative_to(source_root).as_posix()
    if primary not in {x['path'] for x in inputs}:
        raise ContractError(f'Main file is absent from input manifest: {primary}')

    units = copy.deepcopy(list(items))
    source_map = _unique_ids(units, 'source')
    id_map = {key: key if key.startswith(prefix) else prefix + key for key in source_map}
    def scoped(value):
        return value if value.startswith(prefix) else prefix + value

    raw_assets = copy.deepcopy(assets or [])
    if isinstance(raw_assets, dict):
        raw_assets = raw_assets.get('assets', [])
    if not isinstance(raw_assets, list):
        raise ContractError('Assets must be a list')
    asset_map, path_map, asset_records = {}, {}, []
    for n, row in enumerate(raw_assets):
        old_id = str(row.get('asset_id') or f'asset-{n + 1:04d}')
        if old_id in asset_map:
            raise ContractError(f'Duplicate asset_id: {old_id}')
        asset_id = scoped(old_id)
        origin = row.get('path') or row.get('image_path')
        if not origin:
            raise ContractError(f'Asset {old_id} lacks a file path')
        path = Path(origin)
        if not path.is_absolute():
            candidates = [source_root / path, root / path]
            available = [p.resolve() for p in candidates if p.is_file()]
            if len(set(available)) != 1:
                raise ContractError(f'Asset path is missing or ambiguous: {origin}')
            path = available[0]
        path = path.resolve()
        if not path.is_file():
            raise ContractError(f'Missing asset: {path}')
        digest = file_hash(path)
        if row.get('sha256') and row['sha256'] != digest:
            raise ContractError(f'Asset hash mismatch: {old_id}')
        relative = 'assets/' + digest[:16] + '-' + path.name
        destination = resolve_job_path(root, relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if path != destination:
            shutil.copy2(path, destination)
        row.update(asset_id=asset_id, doc_id=doc_id, path=relative, sha256=digest)
        row.pop('image_path', None)
        row.setdefault('kind', 'figure')
        row.setdefault('locators', [])
        row['owner_ids'] = [id_map.get(x, scoped(x)) for x in row.get('owner_ids', [])]
        row.setdefault('representation', path.suffix.lstrip('.').lower())
        asset_map[old_id] = asset_id
        path_map[str(path)] = row
        asset_records.append(row)

    region_records = copy.deepcopy(regions) if regions is not None else []
    for row in region_records:
        row['region_id'] = scoped(str(row['region_id']))
        row['unit_ids'] = [id_map.get(x, scoped(x)) for x in row.get('unit_ids', [])]
        row['doc_id'] = doc_id
    for n, item in enumerate(units):
        old_id = item['id']
        item.update(id=id_map[old_id], doc_id=doc_id, schema_version=SCHEMA_VERSION)
        item.setdefault('order', n)
        item.setdefault('parent_id', old_id.rsplit('-s', 1)[0])
        for key in ('parent_id', 'table_id', 'caption_of'):
            if isinstance(item.get(key), str) and item[key]:
                item[key] = id_map.get(item[key], scoped(item[key]))
        item.setdefault('section_path', [item.get('section', '')])
        item.setdefault('action', 'preserve' if item.get('kind') in ('equation', 'code')
                        or item.get('source') == '' else 'translate')
        item.setdefault('locators', [{'page': item.get('page', 1), **span}
                                     for span in item.get('spans', [])])
        for locator in item['locators']:
            if locator.get('region_id'):
                locator['region_id'] = scoped(str(locator['region_id']))
        if item.get('asset_id'):
            if item['asset_id'] not in asset_map and item['asset_id'] not in asset_map.values():
                raise ContractError(f'Unknown asset {item["asset_id"]} for {old_id}')
            item['asset_id'] = asset_map.get(item['asset_id'], item['asset_id'])
        elif item.get('image_path'):
            path = Path(item['image_path'])
            path = path.resolve() if path.is_absolute() else (source_root / path).resolve()
            if str(path) not in path_map:
                raise ContractError(f'Unregistered image_path for {old_id}: {path}')
            item['asset_id'] = path_map[str(path)]['asset_id']
        if item.get('asset_id'):
            row = next(a for a in asset_records if a['asset_id'] == item['asset_id'])
            item['image_path'] = row['path']
            if item['id'] not in row['owner_ids']:
                row['owner_ids'].append(item['id'])
        if item.get('kind') == 'equation':
            item.setdefault('math_format', 'latex' if '\\' in item.get('source', '') else 'typst_math')
        item['source_hash'] = source_hash(item)
    if regions is None:
        # Only a fallback for explicit callers; independent source checks remain required.
        region_records = [{'region_id': scoped(f'unit-region-{n}'), 'doc_id': doc_id,
                           'locator': item['locators'][0] if item['locators'] else {},
                           'raw_text': item.get('source', ''), 'role': item.get('kind'),
                           'disposition': item['action'], 'unit_ids': [item['id']],
                           'evidence': {'method': 'unit-derived; independent coverage required'}}
                          for n, item in enumerate(units)]
    _unique_ids(region_records, 'regions', 'region_id')
    counts = {}
    for row in units:
        counts[row.get('kind', 'unknown')] = counts.get(row.get('kind', 'unknown'), 0) + 1
    scope_record = {'scope': scope, 'counts': counts,
                    'excluded': [x for x in region_records if x.get('disposition') == 'excluded'],
                    'policy': summary.pop('scope_policy', {})}
    atomic_write_jsonl(root / 'source.jsonl', units)
    atomic_write_jsonl(root / 'regions.jsonl', region_records)
    atomic_write_json(root / 'assets.json', asset_records)
    atomic_write_json(root / 'scope_manifest.json', scope_record)
    source_format = original.suffix.lower().lstrip('.') if original.is_file() else 'latex'
    document = {'schema_version': SCHEMA_VERSION, 'doc_id': doc_id, 'source_format': source_format,
                'primary_input': primary, 'input_files': inputs, 'scope': scope,
                'extractor': summary.get('extractor', 'native'), 'summary': summary,
                'extraction_config_hash': canonical_hash({'scope': scope, 'extra': extra}),
                **extra}
    # Identity and digests cannot be overridden by extra metadata.
    document.update(schema_version=SCHEMA_VERSION, doc_id=doc_id, primary_input=primary,
                    input_files=inputs, scope=scope)
    document['hashes'] = {name: file_hash(root / name) for name in
                          ('source.jsonl', 'regions.jsonl', 'assets.json', 'scope_manifest.json')}
    document['source_manifest_hash'] = document['hashes']['source.jsonl']
    atomic_write_json(root / 'document.json', document)
    return load_job(root)


def load_job(job_dir, verify=True) -> dict:
    root = Path(job_dir).resolve()
    document = read_json(root / 'document.json')
    if not isinstance(document, dict) or document.get('schema_version') != SCHEMA_VERSION:
        raise ContractError('Expected a schema_version=3 document job; import legacy data explicitly')
    for name in ('source.jsonl', 'regions.jsonl', 'assets.json', 'scope_manifest.json'):
        expected = document.get('hashes', {}).get(name)
        if not expected or (verify and file_hash(root / name) != expected):
            raise ContractError(f'Frozen extraction hash mismatch: {name}')
    if document.get('source_manifest_hash') != document['hashes']['source.jsonl']:
        raise ContractError('Source manifest identity mismatch')
    inputs = document.get('input_files')
    if not isinstance(inputs, list) or not inputs:
        raise ContractError('Document has no input provenance')
    for entry in inputs:
        path = resolve_job_path(root, entry['path'])
        if verify and (not path.is_file() or file_hash(path) != entry.get('sha256')):
            raise ContractError(f'Input hash mismatch: {entry["path"]}')
    if document.get('primary_input') not in {x['path'] for x in inputs}:
        raise ContractError('Primary input is not in document input files')
    source = read_jsonl(root / 'source.jsonl')
    _unique_ids(source, 'source')
    for item in source:
        if item.get('doc_id') != document['doc_id']:
            raise ContractError(f'Cross-document source item: {item["id"]}')
        if not isinstance(item.get('source'), str) or not isinstance(item.get('kind'), str):
            raise ContractError(f'Invalid source/kind: {item["id"]}')
        if item.get('action') not in ('translate', 'preserve'):
            raise ContractError(f'Invalid action: {item["id"]}')
        if item.get('source_hash') != source_hash(item):
            raise ContractError(f'Source unit hash mismatch: {item["id"]}')
    assets = read_json(root / 'assets.json')
    if not isinstance(assets, list) or any(not isinstance(x, dict) for x in assets):
        raise ContractError('assets.json must contain an array of objects')
    _unique_ids(assets, 'assets', 'asset_id')
    regions = read_jsonl(root / 'regions.jsonl')
    _unique_ids(regions, 'regions', 'region_id')
    return {'root': root, 'document': document, 'source': source, 'assets': assets,
            'regions': regions, 'scope': read_json(root / 'scope_manifest.json')}


def load_translation_records(job, path=None) -> list[dict]:
    path = Path(path) if path is not None else job['root'] / 'translations.jsonl'
    records = read_jsonl(path)
    sources = {x['id']: x for x in job['source'] if x['action'] == 'translate'}
    seen = _unique_ids(records, 'translations')
    extra = set(seen) - set(sources)
    if extra:
        raise ContractError(f'Unknown or preserve-only translation IDs: {sorted(extra)}')
    for row in records:
        item = sources[row['id']]
        for key, expected in (('doc_id', job['document']['doc_id']),
                              ('source_hash', item['source_hash']),
                              ('source_manifest_hash', job['document']['source_manifest_hash']),
                              *translation_bindings(job).items()):
            if row.get(key) != expected:
                raise ContractError(f'Translation {row["id"]}: stale/missing {key}')
        if not isinstance(row.get('translation'), str) or not row['translation'].strip():
            raise ContractError(f'Translation {row["id"]}: empty or non-string translation')
        if row.get('status') not in ('translated', 'needs_review'):
            raise ContractError(f'Translation {row["id"]}: invalid status')
        for key in ('source', 'kind', 'page', 'asset_id', 'image_path', 'table_id', 'locators'):
            if key in row and row[key] != item.get(key):
                raise ContractError(f'Translation {row["id"]} conflicts with canonical {key}')
    return records


def validate_assets(job) -> list[dict]:
    findings = []
    assets = {x['asset_id']: x for x in job['assets']}
    source_ids = {x['id'] for x in job['source']}
    for asset in assets.values():
        try:
            if asset.get('doc_id') != job['document']['doc_id']:
                raise ContractError('asset belongs to another document')
            path = resolve_job_path(job['root'], asset['path'])
            if not path.is_file() or file_hash(path) != asset.get('sha256'):
                raise ContractError('missing asset or content hash mismatch')
            if not set(asset.get('owner_ids', [])) <= source_ids:
                raise ContractError('asset references unknown owner IDs')
        except (KeyError, OSError, ContractError) as exc:
            findings.append({'asset_id': asset.get('asset_id'), 'issue': str(exc)})
    for item in job['source']:
        asset_id = item.get('asset_id')
        if asset_id and asset_id not in assets:
            findings.append({'id': item['id'], 'issue': 'unregistered asset_id'})
        elif asset_id and item['id'] not in assets[asset_id].get('owner_ids', []):
            findings.append({'id': item['id'], 'issue': 'asset ownership mismatch'})
        elif item.get('kind') == 'figure' and not asset_id:
            findings.append({'id': item['id'], 'issue': 'figure has no exact asset binding'})
    return findings


def implementation_bindings() -> dict:
    """Make a saved approval specific to the checks and renderer that produced it."""
    directory = Path(__file__).resolve().parent
    packages = {}
    for name in ('PyMuPDF', 'typst', 'Pillow', 'markdown-it-py'):
        try:
            packages[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            packages[name] = None
    return {
        'python': platform.python_version(), 'packages': packages,
        'scripts': {path.relative_to(directory).as_posix(): file_hash(path)
                    for path in sorted(directory.rglob('*.py'))},
    }


def job_bindings(job) -> dict:
    root = job['root']
    names = ('document.json', 'source.jsonl', 'regions.jsonl', 'assets.json',
             'scope_manifest.json', 'translations.jsonl', 'reviews.jsonl', 'glossary.json',
             'merged.jsonl', 'source_reviews.jsonl', 'translation_policy.json')
    result = {name: file_hash(root / name) if (root / name).is_file() else None for name in names}
    for row in job['assets']:
        path = resolve_job_path(root, row['path'])
        result[row['path']] = file_hash(path) if path.is_file() else None
    for row in job['document']['input_files']:
        path = resolve_job_path(root, row['path'])
        result[row['path']] = file_hash(path) if path.is_file() else None
    result['implementation'] = implementation_bindings()
    return result


def validate_semantic_reviews(job, translations, records=None) -> dict:
    if isinstance(translations, dict):
        translations = [{'id': key, 'translation': value} for key, value in translations.items()]
    targets = {row['id']: row for row in translations}
    expected = {x['id']: x for x in job['source'] if x['action'] == 'translate'}
    review_path = job['root'] / 'reviews.jsonl'
    report = {'status': 'NOT_RUN', 'required': len(expected), 'reviewed': 0,
              'missing_ids': sorted(expected), 'findings': [], 'methods': [],
              'limitations': ['Review records document an assessment; they do not prove semantic correctness.']}
    if not expected:
        report.update(status='NOT_APPLICABLE', missing_ids=[])
        return report
    if records is None and not review_path.is_file():
        return report
    reviews = _unique_ids(read_jsonl(review_path) if records is None else records, 'reviews')
    if set(reviews) - set(expected):
        raise ContractError(f'Extra review IDs: {sorted(set(reviews) - set(expected))}')
    for ident, row in reviews.items():
        item = expected[ident]
        problems = []
        for key in ('doc_id', 'source_manifest_hash'):
            if row.get(key) != job['document'][key]:
                problems.append(f'stale/missing {key}')
        if row.get('source_hash') != item['source_hash']:
            problems.append('stale source_hash')
        if ident not in targets or row.get('translation_hash') != translation_hash(targets[ident]):
            problems.append('stale/missing translation_hash')
        for key, digest in translation_bindings(job).items():
            if row.get(key) != digest:
                problems.append(f'stale/missing {key}')
        reviewer = row.get('reviewer', {})
        if not isinstance(reviewer, dict) or not reviewer.get('name') or reviewer.get('method') not in ('agent', 'human'):
            problems.append('reviewer name and method (agent/human) are required')
        if row.get('independent') is not True:
            problems.append('independent review is not recorded')
        checks = row.get('checks', {})
        if not isinstance(checks, dict) or any(checks.get(key) not in ('PASS', 'FAIL', 'NEEDS_REVIEW') for key in SEMANTIC_CHECKS):
            problems.append('all semantic checks must be explicitly assessed')
        elif any(checks[key] != 'PASS' for key in SEMANTIC_CHECKS):
            problems.append('one or more semantic checks remain unresolved')
        findings = row.get('findings', [])
        if not isinstance(findings, list):
            problems.append('findings must be an array')
        else:
            for finding in findings:
                quote = finding.get('source_quote', '')
                if not quote or quote not in item.get('raw_source', item['source']):
                    problems.append('finding source_quote is absent from actual source')
                if finding.get('resolved') is not True:
                    problems.append('unresolved semantic finding')
        if row.get('status') != 'PASS':
            problems.append('review is not PASS')
        if ident in targets and targets[ident].get('status') == 'needs_review':
            problems.append('translation explicitly needs review')
        if problems:
            report['findings'].append({'id': ident, 'issues': problems})
        else:
            report['reviewed'] += 1
            method = reviewer['method']
            if method not in report['methods']:
                report['methods'].append(method)
    report['missing_ids'] = sorted(set(expected) - set(reviews))
    report['status'] = 'PASS' if report['reviewed'] == len(expected) else 'NEEDS_REVIEW'
    return report


def assert_report_current(job, report):
    if report.get('status') != 'PASS':
        raise ContractError(f'Preflight is not PASS: {report.get("status", "missing")}')
    if report.get('bindings') != job_bindings(job):
        raise ContractError('Preflight is stale: regenerate checks after input, translation, review, asset, or implementation changes')
    required = ('extraction', 'structural', 'factual', 'assets', 'semantic')
    checks = report.get('checks', {})
    if any(checks.get(key, {}).get('status') not in ('PASS', 'NOT_APPLICABLE') for key in required):
        raise ContractError('Preflight lacks a completed required check')


def load_merged_job(job_dir, require_preflight=True):
    job = load_job(job_dir)
    targets = load_translation_records(job)
    translations = {x['id']: x for x in targets}
    merged = read_jsonl(job['root'] / 'merged.jsonl')
    _unique_ids(merged, 'merged')
    if len(merged) != len(job['source']):
        raise ContractError('Merged item count differs from canonical source')
    expected = []
    for item in job['source']:
        row = copy.deepcopy(item)
        if item['action'] == 'translate':
            if item['id'] not in translations:
                raise ContractError(f'Missing translation: {item["id"]}')
            row['translation'] = translations[item['id']]['translation']
        else:
            row['translation'] = item['source']
        expected.append(row)
    if merged != expected:
        raise ContractError('Merged content is not the canonical source/translation join; regenerate merge')
    issues = validate_assets(job)
    if issues:
        raise ContractError(f'Invalid assets: {issues}')
    job['merged'] = merged
    path = job['root'] / 'checks' / 'preflight.json'
    job['preflight'] = read_json(path) if path.is_file() else {}
    if require_preflight:
        assert_report_current(job, job['preflight'])
    return job


def status_exit_code(status):
    return {'PASS': 0, 'NOT_APPLICABLE': 0, 'accepted': 0, 'draft': 0,
            'FAIL': 2, 'NEEDS_REVIEW': 3, 'NOT_RUN': 3,
            'DEPENDENCY_MISSING': 4, 'ERROR': 5}.get(status, 5)


def reviewed_regions(job) -> list[dict]:
    """Apply content-bound source assessments without changing the frozen extraction."""
    regions = copy.deepcopy(job['regions'])
    review_path = job['root'] / 'source_reviews.jsonl'
    if not review_path.is_file():
        return regions
    index = {r['region_id']: r for r in regions}
    records = _unique_ids(read_jsonl(review_path), 'source reviews', 'region_id')
    unit_ids = {r['id'] for r in job['source']}
    units = {r['id']: r for r in job['source']}
    for ident, record in records.items():
        if ident not in index:
            raise ContractError(f'Source review references unknown region: {ident}')
        region = index[ident]
        if record.get('doc_id') != job['document']['doc_id'] or record.get('region_hash') != canonical_hash(region):
            raise ContractError(f'Stale source region review: {ident}')
        reviewer = record.get('reviewer', {})
        if not isinstance(reviewer, dict) or not reviewer.get('name') or reviewer.get('method') not in ('agent', 'human'):
            raise ContractError(f'Source review {ident}: reviewer identity required')
        if record.get('status') != 'PASS':
            continue
        decision = record.get('decision')
        if decision not in ('no_translatable_text', 'labels_covered', 'ocr_verified', 'excluded'):
            raise ContractError(f'Source review {ident}: unsupported decision')
        if not isinstance(record.get('reason'), str) or not record['reason'].strip():
            raise ContractError(f'Source review {ident}: reason required')
        mapped = record.get('unit_ids', [])
        if not isinstance(mapped, list) or not set(mapped) <= unit_ids:
            raise ContractError(f'Source review {ident}: unknown mapped units')
        if decision in ('labels_covered', 'ocr_verified') and not mapped:
            raise ContractError(f'Source review {ident}: mapped units required')
        if decision == 'excluded':
            permitted = {'header', 'footer', 'page_number'}
            if job['document']['scope'] == 'body':
                permitted |= {'reference', 'footnote', 'acknowledgement'}
            if region.get('role') not in permitted:
                raise ContractError(f'Source review {ident}: full-scope content cannot be excluded by a review')
        else:
            if region.get('role') not in ('image', 'figure', 'graphic', 'scan', 'unknown'):
                raise ContractError(f'Source review {ident}: image/OCR decisions require a visual source region')
            region_locator = region.get('locator', {})
            box = region_locator.get('bbox')
            for unit_id in mapped:
                valid_locator = False
                for locator in units[unit_id].get('locators', []):
                    if locator.get('page') != region_locator.get('page'):
                        continue
                    candidate = locator.get('bbox')
                    if not (isinstance(box, list) and len(box) == 4 and isinstance(candidate, list) and len(candidate) == 4):
                        continue
                    x0, y0 = max(box[0], candidate[0]), max(box[1], candidate[1])
                    x1, y1 = min(box[2], candidate[2]), min(box[3], candidate[3])
                    area = max(0, candidate[2] - candidate[0]) * max(0, candidate[3] - candidate[1])
                    inside = max(0, x1 - x0) * max(0, y1 - y0) >= .95 * area if area > 0 else False
                    method = locator.get('method', units[unit_id].get('provenance', 'native'))
                    if inside and (decision != 'ocr_verified' or method in ('ocr', 'manual_transcription', 'manual')):
                        valid_locator = True
                if not valid_locator:
                    raise ContractError(f'Source review {ident}: mapped unit is not supported within this visual region: {unit_id}')
        region.setdefault('evidence', {}).update(reviewed=True, source_review=record)
        if decision == 'ocr_verified':
            region['evidence']['ocr_complete'] = True
        region['unit_ids'] = sorted(set(region.get('unit_ids', [])) | set(mapped))
        region['disposition'] = ('excluded' if decision == 'excluded' else
                                 'preserve' if decision == 'no_translatable_text' else 'translate')
    return regions
