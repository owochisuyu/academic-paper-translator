"""Import a verified LaTeX compilation into the existing PDF translation workflow.

The TeX engine decides what is printed, including macros and active branches.
Compilation provenance is frozen alongside the PDF. Source/visual coverage and
translation acceptance remain separate checks; a compiler success is not a waiver.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import pymupdf

try:
    from .document_ir import (ContractError, canonical_hash, create_job, file_hash,
                              read_json, resolve_job_path)
    from .extract_document import DocumentExtractor
except ImportError:
    from document_ir import (ContractError, canonical_hash, create_job, file_hash,
                             read_json, resolve_job_path)
    from extract_document import DocumentExtractor


def validate_compilation(report_path):
    """Validate saved evidence without executing commands contained in it."""
    report_path = Path(report_path).resolve()
    root, report = report_path.parent, read_json(report_path)
    if not isinstance(report, dict) or report.get('schema_version') != 1 or report.get('type') != 'latex_source_compile':
        raise ContractError('Expected a version-1 latex_source_compile report')
    if report.get('status') != 'PASS' or report.get('compile_complete') is not True or report.get('exit_code') != 0:
        raise ContractError('LaTeX compilation did not complete successfully; resolve its report first')
    warnings = report.get('warnings')
    if (not isinstance(warnings, list) or any(not isinstance(w, dict)
            or w.get('blocking') is not False or w.get('code') not in ('overfull_box', 'underfull_box')
            for w in warnings)):
        raise ContractError('Blocking or unclassified compilation warnings require resolution before import')
    engine = report.get('engine')
    if (not isinstance(engine, dict) or not isinstance(engine.get('version'), str)
            or not engine['version'].strip() or not isinstance(engine.get('command'), list)
            or not engine['command'] or any(not isinstance(x, str) for x in engine['command'])):
        raise ContractError('Compilation must record the actual engine version and command')
    files = {report_path}

    def checked_file(parent, row, label):
        if not isinstance(row, dict) or not isinstance(row.get('sha256'), str):
            raise ContractError(f'{label} needs a path and SHA256')
        path = resolve_job_path(parent, row.get('path'))
        if not path.is_file() or file_hash(path) != row['sha256']:
            raise ContractError(f'{label} hash mismatch: {row.get("path")}')
        if not path.is_relative_to(root):
            raise ContractError(f'{label} leaves the compilation directory')
        files.add(path)
        return path

    def checked_snapshot(snapshot, label):
        if not isinstance(snapshot, dict):
            raise ContractError(f'Missing {label}')
        directory = resolve_job_path(root, snapshot.get('root'))
        rows = snapshot.get('files')
        if not isinstance(rows, list) or not rows:
            raise ContractError(f'{label} has no source files')
        if snapshot.get('input_set_hash') != canonical_hash(rows):
            raise ContractError(f'{label} input-set hash mismatch')
        paths = [checked_file(directory, row, label) for row in rows]
        if len(paths) != len(set(paths)):
            raise ContractError(f'{label} contains duplicate source paths')
        return directory, paths

    source_root, source_files = checked_snapshot(report.get('source_snapshot'), 'compiled source snapshot')
    main = resolve_job_path(source_root, report.get('main_file'))
    if main not in source_files or main.suffix.lower() not in ('.tex', '.latex'):
        raise ContractError('Main TeX file is missing from the compiled source snapshot')
    audit = report.get('dependency_audit')
    if (not isinstance(audit, dict) or audit.get('status') != 'PASS'
            or audit.get('external_or_unbound') != [] or not isinstance(audit.get('files'), list)):
        raise ContractError('Compilation must account for all actually read local source dependencies')
    checked_file(root, audit.get('record'), 'compiler dependency record')
    declared_inputs = {row['path']: row['sha256'] for row in report['source_snapshot']['files']}
    consumed = set()
    for entry in audit['files']:
        if (not isinstance(entry, dict) or not isinstance(entry.get('path'), str)
                or entry['path'] in consumed or declared_inputs.get(entry['path']) != entry.get('sha256')):
            raise ContractError('Consumed source dependency is absent or differs from the frozen snapshot')
        consumed.add(entry['path'])
    if report['main_file'] not in consumed:
        raise ContractError('Dependency record does not identify the main file actually compiled')
    original = report.get('original_source_snapshot')
    original_files = None
    if original is not None:
        checked_snapshot(original, 'original source snapshot')
        original_files = {row['path']: row['sha256'] for row in original['files']}
    adaptations = report.get('adaptations', [])
    if not isinstance(adaptations, list):
        raise ContractError('Compilation adaptations must be a list')
    if adaptations and original is None:
        raise ContractError('Adapted compilation must retain its original source snapshot')
    adapted_files = {row['path']: row['sha256'] for row in report['source_snapshot']['files']}
    changed = ({path for path in set(original_files) | set(adapted_files)
                if original_files.get(path) != adapted_files.get(path)} if original_files is not None else set())
    declared = set()
    for adaptation in adaptations:
        if not isinstance(adaptation, dict) or not adaptation.get('reason'):
            raise ContractError('A compilation adaptation requires a recorded reason')
        name = adaptation.get('file')
        if (not isinstance(name, str) or name in declared or name not in changed
                or adaptation.get('before_sha256') != original_files.get(name)
                or adaptation.get('after_sha256') != adapted_files.get(name)):
            raise ContractError('Adaptation does not match the original and compiled source bytes')
        declared.add(name)
        if adaptation.get('patch_path'):
            checked_file(root, {'path': adaptation['patch_path'],
                               'sha256': adaptation.get('patch_sha256')}, 'compilation adaptation patch')
    if declared != changed:
        raise ContractError('Every changed compilation input must have an explicit adaptation record')
    logs = report.get('logs')
    if not isinstance(logs, list) or not logs:
        raise ContractError('Compilation must retain its actual execution log')
    for log in logs:
        checked_file(root, log, 'compilation log')
    generated = report.get('generated_files', [])
    if not isinstance(generated, list):
        raise ContractError('Generated compilation files must be a list')
    for generated_file in generated:
        checked_file(root, generated_file, 'generated compilation file')
    pdf = checked_file(root, report.get('pdf'), 'compiled PDF')
    if pdf.suffix.lower() != '.pdf':
        raise ContractError('Compiled output must be a PDF')
    with pymupdf.open(pdf) as document:
        if document.needs_pass or len(document) < 1:
            raise ContractError('Compiled PDF is encrypted or empty')
        if type(report['pdf'].get('pages')) is not int or report['pdf']['pages'] != len(document):
            raise ContractError('Compiled PDF page count differs from the report')
    return root, report, pdf, sorted(files)


def import_compilation(report_path, job_dir, scope='full', source_overlay=None,
                       assets_manifest=None, backend='native', mineru_result=None,
                       equation_overlay=None):
    root, report, pdf, files = validate_compilation(report_path)
    job_dir = Path(job_dir).resolve()
    if (job_dir / 'document.json').exists():
        raise ContractError('Job already exists; choose a new extraction directory')
    if any(p.is_relative_to(job_dir) for p in files):
        raise ContractError('The new job must not contain or overwrite its compilation inputs')
    extractor = DocumentExtractor(scope, asset_dir=job_dir / 'extracted-assets',
        source_overlay=source_overlay, assets_manifest=assets_manifest,
        backend=backend, mineru_result=mineru_result, equation_overlay=equation_overlay)
    items, stats = extractor.extract(pdf)
    metadata = {**extractor.extra_document, 'source_root': str(root),
        'input_files': [str(path) for path in files],
        'source_format': 'pdf', 'origin_format': 'latex', 'extraction_route': 'compiled_latex_pdf',
        'latex_compilation': {
            'report': 'input/' + Path(report_path).resolve().relative_to(root).as_posix(),
            'report_sha256': file_hash(report_path),
            'input_set_hash': report['source_snapshot']['input_set_hash'],
            'main_file': report['main_file'], 'engine': report['engine'],
            'pdf_sha256': report['pdf']['sha256'], 'pages': report['pdf']['pages'],
            'adaptations': report.get('adaptations', []),
            'layout_warnings': report['warnings'],
            'dependency_audit': report['dependency_audit'],
            'meaning': 'Engine output is the source for PDF extraction. Source/visual review, translation and final PDF acceptance are still required.'}}
    job = create_job(pdf, job_dir, items, regions=extractor.regions, assets=extractor.assets,
                     summary=stats, scope=scope, extra_document=metadata)
    return {**stats, 'job_dir': str(job_dir), 'doc_id': job['document']['doc_id'],
            'route': 'compiled_latex_pdf', 'translation_status': 'NOT_RUN',
            'meaning': metadata['latex_compilation']['meaning']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compilation', required=True, help='Saved compile_result.json')
    parser.add_argument('--job-dir', required=True)
    parser.add_argument('--scope', choices=['full', 'body'], default='full')
    parser.add_argument('--source-overlay')
    parser.add_argument('--equation-overlay')
    parser.add_argument('--assets-manifest')
    parser.add_argument('--backend', choices=['native', 'mineru'], default='native')
    parser.add_argument('--mineru-result')
    args = parser.parse_args()
    try:
        result = import_compilation(args.compilation, args.job_dir, args.scope,
            args.source_overlay, args.assets_manifest, args.backend, args.mineru_result,
            args.equation_overlay)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError, ImportError) as exc:
        print(f'Compiled LaTeX import failed: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
