"""Structural, source, and factual checks for traceable paper translation.

The legacy entry point reports deterministic checks only. The v3 job entry point
also requires independent semantic review, source provenance, and current hashes.
Neither result claims that rules can prove full semantic correctness.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys

try:
    from .numeric_entities import NUM_ENTITY_REGEX, NUM_RX_COMBINED, extract_numeric_entities, compare_numeric_entities
    from .source_coverage import validate_source_coverage
except ImportError:
    from numeric_entities import NUM_ENTITY_REGEX, NUM_RX_COMBINED, extract_numeric_entities, compare_numeric_entities
    from source_coverage import validate_source_coverage

BRACKET_CITATION_REGEX = re.compile(r'\[\s*\d+(?:\s*(?:,\s*|[-–—])\s*\d+)*\s*\]')
AUTHOR_YEAR_CITATION_REGEX = re.compile(r'\([A-Z][A-Za-z\s&.\-]+,\s*(?:19|20)\d{2}[a-z]?(?:;[^)]+)?\)')
EQUATION_REF_REGEX = re.compile(r'(?:(?:Eq\.|Equation|Formula|Eqs\.|式)\s*)?[（(](\d+[a-z]?)[)）]')
MATH_FORMULA_REGEX = re.compile(r'\$\$([\s\S]*?)\$\$|(?<!\\)\$([^$]+)\$|\\\(([\s\S]*?)\\\)|\\\[([\s\S]*?)\\\]')
NEGATION_WORDS_EN = re.compile(r"\b(not|never|cannot|can't|neither|nor|fails|failed|without)\b", re.I)
NEGATION_WORDS_ZH = ('不', '未', '无法', '不能', '没有', '无', '非', '免', '否')
PROTECTED_TOKEN_REGEX = re.compile(r'⟦[^⟧]+⟧')


def _load_data(path):
    """Strict legacy import through the shared canonical JSON loaders."""
    try:
        from .document_ir import read_json, read_jsonl
    except ImportError:
        from document_ir import read_json, read_jsonl
    path = Path(path)
    if path.suffix.lower() == '.jsonl':
        return read_jsonl(path)
    if path.suffix.lower() == '.json':
        return read_json(path)
    text = path.read_text(encoding='utf-8-sig')
    if not text.strip():
        return []
    # Unknown extension: a multi-object line stream is explicitly imported as JSONL.
    if text.lstrip().startswith('[') or len([x for x in text.splitlines() if x.strip()]) == 1:
        return read_json(path)
    return read_jsonl(path)


def _check_source_items(items):
    if not isinstance(items, list):
        raise ValueError('Source manifest must contain a list of objects')
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not item['id'].strip():
            raise ValueError(f'Source item {index} requires a non-empty string id')
        if not isinstance(item.get('source'), str):
            raise ValueError(f'Source item {item["id"]} requires string source')
    return items


def load_manifest(source_path) -> list[dict]:
    data = _load_data(source_path)
    if isinstance(data, dict) and 'id' in data:
        data = [data]
    return _check_source_items(data)


def _translation_map(data):
    result, duplicates = {}, []
    if isinstance(data, dict) and 'id' not in data:
        for key, value in data.items():
            if isinstance(value, dict) and value.get('id', key) != key:
                raise ValueError(f'Translation map key {key!r} conflicts with nested id')
        data = [{'id': key, **(value if isinstance(value, dict) else {'translation': value})} for key, value in data.items()]
    elif isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise ValueError('Translations must be objects or a list of objects')
    for index, item in enumerate(data, 1):
        if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not item['id'].strip():
            raise ValueError(f'Translation item {index} requires a non-empty string id')
        values = [item[key] for key in ('translation', 'target', 'translated') if key in item]
        if not values:
            raise ValueError(f'Translation item {item["id"]} is missing translation text')
        if not all(isinstance(value, str) for value in values) or any(value != values[0] for value in values[1:]):
            raise ValueError(f'Translation item {item["id"]} has non-string or conflicting translation fields')
        if item['id'] in result:
            duplicates.append(item['id'])
        result[item['id']] = values[0]
    return result, duplicates


def load_translations(translated_path) -> tuple[dict[str, str], list[str]]:
    return _translation_map(_load_data(translated_path))


def _compact(text):
    return re.sub(r'\s+', '', text)


def _formulae(text):
    return [_compact(next((part for part in match if part), '')) for match in MATH_FORMULA_REGEX.findall(text)]


def _aggregate_status(checks):
    statuses = [check.get('status', 'NOT_RUN') for check in checks.values()]
    if 'FAIL' in statuses:
        return 'FAIL'
    if any(status not in {'PASS', 'NOT_APPLICABLE'} for status in statuses):
        return 'NEEDS_REVIEW'
    return 'PASS'


class TranslationValidator:
    def __init__(self, strict: bool = True):
        self.strict = strict

    def validate_tier1_coverage(self, source_items, doc_path=None):
        report = validate_source_coverage(source_items, doc_path)
        # Retain legacy binary FAIL semantics for incomplete source coverage.
        if report['status'] == 'NEEDS_REVIEW':
            report['status'] = 'FAIL'
        return report

    def validate_tier2_integrity(self, source_items, translation_map, duplicate_ids):
        _check_source_items(source_items)
        translation_map, input_duplicates = _translation_map(translation_map)
        duplicates = Counter(item['id'] for item in source_items)
        source_ids = set(duplicates)
        translated_ids = set(translation_map)
        missing = sorted(source_ids - translated_ids)
        extra = sorted(translated_ids - source_ids)
        duplicate_ids = sorted(set(duplicate_ids + input_duplicates + [key for key, count in duplicates.items() if count > 1]))
        empty, numerical, citations, formulae, unknown, formatting, polarity = [], [], [], [], [], [], []
        numeric_total = 0
        for item in source_items:
            ident, src = item['id'], item['source'].strip()
            if ident not in translation_map:
                continue
            tgt = translation_map[ident].strip()
            if src and not tgt:
                empty.append({'id': ident, 'kind': item.get('kind'), 'source': src})
                continue
            comparison = compare_numeric_entities(src, tgt)
            numeric_total += comparison['source_entities']
            numerical.extend(dict(issue, id=ident, source=src, translation=tgt) for issue in comparison['issues'])
            unknown.extend(dict(issue, id=ident) for issue in comparison['unknown_units'])
            formatting.extend(dict(issue, id=ident) for issue in comparison['formatting'])
            for label, pattern in [('bracket', BRACKET_CITATION_REGEX), ('author_year', AUTHOR_YEAR_CITATION_REGEX)]:
                source_cits = Counter(_compact(m.group()) for m in pattern.finditer(src))
                target_cits = Counter(_compact(m.group()) for m in pattern.finditer(tgt))
                if source_cits != target_cits:
                    citations.append({'id': ident, 'type': label, 'source': src, 'translation': tgt,
                                      'missing': list((source_cits - target_cits).elements()),
                                      'extra': list((target_cits - source_cits).elements()),
                                      'issue': 'Citation content or occurrence count changed'})
            source_refs = Counter(EQUATION_REF_REGEX.findall(src))
            target_refs = Counter(EQUATION_REF_REGEX.findall(tgt))
            if source_refs != target_refs:
                formulae.append({'id': ident, 'type': 'equation_reference', 'source': src, 'translation': tgt,
                                 'missing': list((source_refs - target_refs).elements()),
                                 'extra': list((target_refs - source_refs).elements()),
                                 'issue': 'Equation/reference label occurrence changed'})
            source_math, target_math = Counter(_formulae(src)), Counter(_formulae(tgt))
            if source_math != target_math:
                formulae.append({'id': ident, 'type': 'math_expression', 'source': src, 'translation': tgt,
                                 'issue': 'Mathematical expression or occurrence count changed'})
            source_tokens = PROTECTED_TOKEN_REGEX.findall(src)
            target_tokens = PROTECTED_TOKEN_REGEX.findall(tgt)
            if source_tokens != target_tokens:
                formulae.append({'id': ident, 'type': 'protected_tokens', 'source': source_tokens,
                                 'translation': target_tokens, 'issue': 'Protected tokens changed, reordered, added, or omitted'})
            if NEGATION_WORDS_EN.search(src) and not (any(marker in tgt for marker in NEGATION_WORDS_ZH) or NEGATION_WORDS_EN.search(tgt)):
                polarity.append({'id': ident, 'type': 'polarity_discrepancy', 'source': src, 'translation': tgt,
                                 'issue': 'Possible negation omission; requires semantic review'})
        factual_failures = numerical or citations or formulae or polarity
        status = 'FAIL' if missing or extra or duplicate_ids or empty or (self.strict and factual_failures) else 'PASS'
        if status == 'PASS' and unknown and self.strict:
            status = 'NEEDS_REVIEW'
        return {'status': status, 'missing_ids': missing, 'duplicate_ids': duplicate_ids,
                'extra_ids': extra, 'empty_translations': empty, 'numerical_issues': numerical,
                'citation_issues': citations, 'formula_issues': [*formulae, *polarity],
                'polarity_issues': polarity, 'unknown_units': unknown, 'formatting_issues': formatting,
                'numeric_entities_checked': numeric_total,
                'semantic_review': 'NOT_RUN', 'limitations': ['Rules do not prove within-item semantic completeness or all natural-language quantity bindings.']}

    def validate(self, source_path_or_items, translated_path_or_items, doc_path=None, report_path=None):
        source = load_manifest(source_path_or_items) if isinstance(source_path_or_items, (str, Path)) else _check_source_items(source_path_or_items)
        target, duplicates = load_translations(translated_path_or_items) if isinstance(translated_path_or_items, (str, Path)) else _translation_map(translated_path_or_items)
        tier1 = self.validate_tier1_coverage(source, doc_path)
        tier2 = self.validate_tier2_integrity(source, target, duplicates)
        report = {'status': 'PASS' if tier1['status'] == tier2['status'] == 'PASS' else 'FAIL',
                  'timestamp': datetime.now(timezone.utc).isoformat(), 'strict_mode': self.strict,
                  'validation_scope': 'legacy deterministic checks; semantic and final-rendering review not performed',
                  'summary': {'tier1_status': tier1['status'], 'tier2_status': tier2['status'],
                              'total_source_items': len(source), 'total_translated_items': len(target),
                              'missing_ids_count': len(tier2['missing_ids']),
                              'duplicate_ids_count': len(tier2['duplicate_ids']),
                              'extra_ids_count': len(tier2['extra_ids']),
                              'empty_translations_count': len(tier2['empty_translations']),
                              'numerical_discrepancies_count': len(tier2['numerical_issues']),
                              'citation_discrepancies_count': len(tier2['citation_issues']),
                              'formula_discrepancies_count': len(tier2['formula_issues'])},
                  'tier1_source_coverage': tier1, 'tier2_factual_integrity': tier2,
                  'semantic': {'status': 'NOT_RUN'}, 'rendering': {'status': 'NOT_RUN'}}
        if report_path:
            _write_report(report_path, report)
        return report


def _write_report(path, report):
    try:
        from .document_ir import atomic_write_json
    except ImportError:
        from document_ir import atomic_write_json
    atomic_write_json(path, report)


def validate_translation(source_path, translated_path, doc_path=None, report_path='coverage_report.json', strict=True):
    return TranslationValidator(strict=strict).validate(source_path, translated_path, doc_path, report_path)


def validate_job(job_dir, report_path=None):
    """Validate canonical job provenance plus independent semantic review."""
    if __package__:
        from .document_ir import (load_job, load_translation_records, validate_assets, job_bindings,
                                  validate_semantic_reviews, reviewed_regions, ContractError)
    else:
        from document_ir import (load_job, load_translation_records, validate_assets, job_bindings,
                                 validate_semantic_reviews, reviewed_regions, ContractError)
    root = Path(job_dir).resolve()
    output = Path(report_path) if report_path else root / 'checks' / 'preflight.json'
    try:
        job = load_job(root)
        translations = load_translation_records(job)
        source = job['source']
        to_translate = [item for item in source if item.get('action', 'translate') == 'translate']
        translation_map, duplicates = _translation_map(translations)
        deterministic = TranslationValidator(strict=True).validate_tier2_integrity(to_translate, translation_map, duplicates)
        structural = {'status': 'PASS', 'expected': len(to_translate), 'received': len(translation_map),
                      **{key: deterministic[key] for key in ('missing_ids', 'extra_ids', 'duplicate_ids', 'empty_translations')}}
        if any(structural[key] for key in ('missing_ids', 'extra_ids', 'duplicate_ids', 'empty_translations')):
            structural['status'] = 'FAIL'
        factual = {'status': 'PASS', **{key: deterministic[key] for key in (
            'numerical_issues', 'citation_issues', 'formula_issues', 'unknown_units', 'formatting_issues', 'numeric_entities_checked')}}
        factual['heuristic_warnings'] = deterministic['polarity_issues']
        factual['formula_issues'] = [issue for issue in factual['formula_issues'] if issue.get('type') != 'polarity_discrepancy']
        if any(factual[key] for key in ('numerical_issues', 'citation_issues', 'formula_issues')):
            factual['status'] = 'FAIL'
        elif factual['unknown_units']:
            factual['status'] = 'NEEDS_REVIEW'
        document = job['document']
        primary = document.get('primary_input')
        if not primary and document.get('input_files'):
            primary = document['input_files'][0]['path']
        source_path = root / primary if primary else None
        regions = reviewed_regions(job)
        extraction = validate_source_coverage(source, source_path, regions, job.get('scope'),
                                              source_root=root / 'input', input_files=document.get('input_files'),
                                              project_dependencies=document.get('project_dependencies'))
        if not primary:
            extraction['status'] = 'FAIL'
            extraction['details'].append('Canonical document has no primary source input')
        pending = [region.get('region_id') for region in regions if region.get('disposition') == 'needs_review']
        if pending and extraction['status'] == 'PASS':
            extraction['status'] = 'NEEDS_REVIEW'
        extraction['unresolved_region_ids'] = pending
        all_warnings = document.get('summary', {}).get('warnings', [])
        resolved_regions = {r['region_id'] for r in regions if isinstance(r.get('evidence'), dict) and r['evidence'].get('reviewed') is True}
        prefix = str(document.get('doc_id', ''))[:16] + ':'
        warnings = [w for w in all_warnings if not (isinstance(w, dict) and (w.get('region_id') in resolved_regions or prefix + str(w.get('region_id', '')) in resolved_regions))]
        extraction['resolved_parser_warnings'] = len(all_warnings) - len(warnings)
        extraction['parser_warnings'] = warnings
        if warnings and extraction['status'] == 'PASS':
            extraction['status'] = 'NEEDS_REVIEW'
        asset_findings = validate_assets(job)
        assets = {'status': 'FAIL' if asset_findings else 'PASS', 'findings': asset_findings,
                  'expected': len(job.get('assets', []))}
        semantic = validate_semantic_reviews(job, translations)
        if factual['status'] != 'FAIL' and (factual['unknown_units'] or factual['heuristic_warnings']):
            factual['status'] = 'PASS' if semantic.get('status') == 'PASS' else 'NEEDS_REVIEW'
            factual['uncertainty_resolution'] = 'independent current semantic review' if factual['status'] == 'PASS' else 'pending independent semantic review'
        checks = {'extraction': extraction, 'structural': structural, 'factual': factual,
                  'assets': assets, 'semantic': semantic}
        report = {'schema_version': 3, 'stage': 'preflight', 'status': _aggregate_status(checks),
                  'timestamp': datetime.now(timezone.utc).isoformat(), 'doc_id': document.get('doc_id'),
                  'bindings': job_bindings(job), 'checks': checks,
                  'rendering': {'status': 'NOT_RUN'},
                  'limitations': ['Preflight PASS permits a candidate render; accepted delivery requires independent output verification.']}
    except (ContractError, ValueError, OSError, KeyError, TypeError) as exc:
        report = {'schema_version': 3, 'stage': 'preflight', 'status': 'FAIL',
                  'timestamp': datetime.now(timezone.utc).isoformat(),
                  'checks': {'contract': {'status': 'FAIL', 'error': str(exc)}},
                  'rendering': {'status': 'NOT_RUN'}}
    _write_report(output, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job-dir', help='Canonical v3 job directory')
    parser.add_argument('--source', help='Legacy source JSON/JSONL')
    parser.add_argument('--translated', help='Legacy translated JSON/JSONL')
    parser.add_argument('--doc', '--pdf', '--source-doc', dest='doc')
    parser.add_argument('--report', '--out', dest='report')
    parser.add_argument('--no-strict', dest='strict', action='store_false')
    parser.set_defaults(strict=True)
    args = parser.parse_args()
    if args.job_dir:
        if not args.strict:
            parser.error('--no-strict is not allowed for canonical jobs')
        report = validate_job(args.job_dir, args.report)
        print(json.dumps({'status': report['status'], 'checks': {k: v['status'] for k, v in report['checks'].items()}}, ensure_ascii=False))
        return 0 if report['status'] == 'PASS' else 3 if report['status'] == 'NEEDS_REVIEW' else 2
    if not args.source or not args.translated:
        parser.error('Specify --job-dir or both --source and --translated')
    try:
        report = validate_translation(args.source, args.translated, args.doc,
                                      args.report or 'coverage_report.json', args.strict)
    except (ValueError, OSError, TypeError) as exc:
        report = {'status': 'FAIL', 'error': str(exc), 'validation_scope': 'legacy deterministic checks'}
        _write_report(args.report or 'coverage_report.json', report)
    print(json.dumps(report.get('summary', report), ensure_ascii=False))
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main())
