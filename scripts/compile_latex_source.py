"""Compile an immutable LaTeX project snapshot with an explicitly selected Tectonic.

The result attests to a local source compilation, never translation completeness.
The engine is not installed by this script. Downloads require --allow-network;
without it Tectonic can only use its existing local package cache.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

import pymupdf

try:
    from .document_ir import atomic_write_json, canonical_hash, file_hash
except ImportError:
    from document_ir import atomic_write_json, canonical_hash, file_hash


EXCLUDED_DIRECTORIES = {'.git', '.venv', '__pycache__'}


def snapshot_project(project, destination):
    """Copy regular project files; never follow a link outside the selected tree."""
    files = []
    for directory, names, filenames in os.walk(project, followlinks=False):
        directory = Path(directory)
        for name in names:
            if (directory / name).is_symlink():
                raise ValueError('Project directory symlinks require an explicit normalized copy')
        names[:] = sorted(n for n in names if n not in EXCLUDED_DIRECTORIES)
        for name in sorted(filenames):
            source = directory / name
            if source.is_symlink() or not source.is_file():
                raise ValueError('Project inputs must be regular files')
            relative = source.relative_to(project)
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            files.append({'path': relative.as_posix(), 'sha256': file_hash(target)})
    files.sort(key=lambda item: item['path'])
    if not files:
        raise ValueError('LaTeX project has no regular input files')
    return {'root': destination.name, 'files': files, 'input_set_hash': canonical_hash(files)}


def classify_log(text):
    """Final-pass issues that make a generated PDF unsuitable as complete source."""
    patterns = [
        ('undefined_references', r'(?:Reference|Citation)\s+[^\r\n]*undefined|There were undefined (?:references|citations)', True),
        ('missing_character', r'Missing character:[^\r\n]*', True),
        ('missing_source_file', r'No file [^\r\n]*\.(?:tex|latex|sty|cls|bib|bbl|bst|png|pdf|jpe?g|eps)\.', True),
        ('font_substitution', r'LaTeX Font Warning: (?:Font shape [^\r\n]* undefined|Some font shapes were not available[^\r\n]*)', True),
        ('unresolved_rerun', r'Label\(s\) may have changed|Rerun to get cross-references right|Please \(re\)run (?:Biber|BibTeX)', True),
        ('overfull_box', r'Overfull \\[hv]box[^\r\n]*', False),
    ]
    result = []
    for code, pattern, blocking in patterns:
        matches = [match.group(0) for match in re.finditer(pattern, text, flags=re.I)]
        if matches:
            result.append({'code': code, 'blocking': blocking, 'count': len(matches),
                           'examples': list(dict.fromkeys(matches))[:10]})
    return result


def audit_project_dependencies(root, snapshot):
    """Bind Tectonic's recorded local input names to the frozen project tree.

    Tectonic writes one dependency per continuation line. With --outdir it
    prefixes relative local inputs by the output directory in the Makefile even
    though the engine read them in its working directory. Package-bundle inputs
    are managed by Tectonic and are not listed as local project dependencies.
    """
    rules = root / 'build/dependencies.mk'
    report = {'status': 'NEEDS_REVIEW', 'files': [], 'external_or_unbound': [],
              'basis': 'Tectonic local-file dependency record; package-bundle resources are engine dependencies'}
    if not rules.is_file():
        report['external_or_unbound'].append('No dependency record was produced')
        return report
    lines = rules.read_text(encoding='utf-8', errors='strict').splitlines()
    if not lines or ' : ' not in lines[0]:
        report['external_or_unbound'].append('Unsupported dependency record syntax')
        return report
    tokens = [lines[0].split(' : ', 1)[1], *lines[1:]]
    declared = {entry['path']: entry for entry in snapshot['files']}
    observed = set()
    for token in tokens:
        token = token.strip()
        if token.endswith(' \\'):
            token = token[:-2].rstrip()
        if not token:
            continue
        candidate = Path(token)
        if candidate.is_absolute():
            candidate = candidate.resolve()
            if candidate.is_relative_to(root / 'build'):
                relative = candidate.relative_to(root / 'build')
            elif candidate.is_relative_to(root / 'run'):
                relative = candidate.relative_to(root / 'run')
            else:
                report['external_or_unbound'].append(token)
                continue
        else:
            candidate = (root / 'run' / candidate).resolve()
            if not candidate.is_relative_to(root / 'run'):
                report['external_or_unbound'].append(token)
                continue
            relative = candidate.relative_to(root / 'run')
        name = relative.as_posix()
        if name in declared:
            observed.add(name)
        elif (root / 'build' / relative).is_file():
            # A freshly generated auxiliary file is preserved in generated_files.
            continue
        else:
            report['external_or_unbound'].append(token)
    report['files'] = [declared[name] for name in sorted(observed)]
    report['status'] = 'NEEDS_REVIEW' if report['external_or_unbound'] else 'PASS'
    report['record'] = {'path': 'build/dependencies.mk', 'sha256': file_hash(rules)}
    return report


def _adaptations(original, actual, root, reason):
    before = {row['path']: row['sha256'] for row in original['files']}
    after = {row['path']: row['sha256'] for row in actual['files']}
    changed = []
    for name in sorted(set(before) | set(after)):
        if before.get(name) == after.get(name):
            continue
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError('Changed source requires an explicit --adaptation-reason')
        entry = {'file': name, 'before_sha256': before.get(name), 'after_sha256': after.get(name),
                 'reason': reason.strip()}
        try:
            old = (root / original['root'] / name).read_text(encoding='utf-8-sig') if name in before else ''
            new = (root / actual['root'] / name).read_text(encoding='utf-8-sig') if name in after else ''
            patch = ''.join(difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                                              fromfile='original/' + name, tofile='source/' + name))
            patch_path = root / 'adaptations' / f'{len(changed) + 1:04d}.patch'
            patch_path.parent.mkdir(exist_ok=True)
            patch_path.write_text(patch, encoding='utf-8')
            entry.update(patch_path=patch_path.relative_to(root).as_posix(), patch_sha256=file_hash(patch_path))
        except UnicodeError:
            entry['representation'] = 'binary difference; inspect original and actual files'
        changed.append(entry)
    return changed


def compile_project(project, main_file, out_dir, engine_path=None, timeout=180,
                    allow_network=False, cache_dir=None, original_project=None, adaptation_reason=None):
    project, root = Path(project).resolve(), Path(out_dir).resolve()
    if not project.is_dir() or not isinstance(main_file, str):
        raise ValueError('Select an existing source project and a relative main file')
    main = (project / main_file).resolve()
    if Path(main_file).is_absolute() or not main.is_relative_to(project) or not main.is_file():
        raise ValueError('Main file must be a regular file within the source project')
    if main.suffix.lower() not in ('.tex', '.latex'):
        raise ValueError('Main file must have a TeX source extension')
    if root.exists() or root.is_relative_to(project) or project.is_relative_to(root):
        raise ValueError('Use a new output directory outside the source project')
    if type(timeout) not in (int, float) or not 1 <= timeout <= 1800:
        raise ValueError('Compilation timeout must be between 1 and 1800 seconds')
    original = Path(original_project).resolve() if original_project else None
    if original and (not original.is_dir() or root.is_relative_to(original) or original.is_relative_to(root)):
        raise ValueError('Original project must be an existing directory separate from output')
    root.mkdir(parents=True)
    source_snapshot = snapshot_project(project, root / 'source')
    report = {'schema_version': 1, 'type': 'latex_source_compile', 'status': 'NOT_RUN',
              'compile_complete': False, 'exit_code': None, 'main_file': main.relative_to(project).as_posix(),
              'source_snapshot': source_snapshot, 'input_set_hash': source_snapshot['input_set_hash'],
              'logs': [], 'warnings': [], 'pdf': None, 'adaptations': [],
              'limitations': ['This is a source compilation result, not translation or final layout acceptance.',
                              'Engine/package behavior and visual source completeness still require review.']}
    if original:
        report['original_source_snapshot'] = snapshot_project(original, root / 'original-source')
        report['adaptations'] = _adaptations(report['original_source_snapshot'], source_snapshot, root, adaptation_reason)
    shutil.copytree(root / 'source', root / 'run')
    build = root / 'build'
    build.mkdir()
    executable = str(Path(engine_path).resolve()) if engine_path else shutil.which('tectonic')
    if not executable or not Path(executable).is_file():
        report['warnings'] = [{'code': 'engine_missing', 'blocking': True,
                               'message': 'Select an installed/portable Tectonic executable with --engine-path.'}]
        atomic_write_json(root / 'compile_result.json', report)
        return report
    env = os.environ.copy()
    for key in ('TEXINPUTS', 'BIBINPUTS', 'BIBSTYLES', 'TEXMFOUTPUT'):
        env.pop(key, None)
    cache = Path(cache_dir).resolve() if cache_dir else root / 'cache'
    cache.mkdir(parents=True, exist_ok=True)
    env.update(TECTONIC_CACHE_DIR=str(cache), TECTONIC_UNTRUSTED_MODE='1')
    command = [executable, '-X', 'compile', '--untrusted', '--keep-logs', '--keep-intermediates',
               '--synctex', '--makefile-rules', str(build / 'dependencies.mk'), '--outdir', str(build)]
    if not allow_network:
        command.append('--only-cached')
    command.append(report['main_file'])
    report['engine'] = {'path': executable, 'sha256': file_hash(executable), 'version': '', 'command': command}
    report['configuration'] = {'allow_network_package_download': bool(allow_network), 'cache_dir': str(cache),
                               'timeout_seconds': timeout, 'shell_escape': False, 'working_directory': 'run',
                               'snapshot_excluded_directories': sorted(EXCLUDED_DIRECTORIES)}
    started = time.monotonic()
    try:
        version = subprocess.run([executable, '--version'], cwd=root / 'run', env=env,
                                 capture_output=True, timeout=min(10, timeout), check=False)
        (root / 'engine-version.log').write_bytes(version.stdout + version.stderr)
        report['engine']['version'] = (version.stdout + version.stderr).decode('utf-8', errors='replace').strip()
        if version.returncode != 0 or not report['engine']['version'].startswith('Tectonic '):
            raise ValueError('Engine is not an operational Tectonic executable')
        result = subprocess.run(command, cwd=root / 'run', env=env, capture_output=True,
                                timeout=timeout, check=False)
        stdout, stderr = result.stdout, result.stderr
        report['exit_code'] = result.returncode
        report['status'] = 'FAIL'
    except subprocess.TimeoutExpired as exc:
        stdout, stderr = exc.stdout or b'', exc.stderr or b''
        report['status'] = 'FAIL'
        report['warnings'].append({'code': 'compile_timeout', 'blocking': True,
                                   'message': f'Compiler exceeded {timeout} seconds; partial output is not accepted.'})
    except (OSError, ValueError) as exc:
        stdout, stderr = b'', str(exc).encode('utf-8')
        report['status'] = 'FAIL'
        report['warnings'].append({'code': 'engine_error', 'blocking': True, 'message': str(exc)})
    (root / 'stdout.log').write_bytes(stdout)
    (root / 'stderr.log').write_bytes(stderr)
    report['elapsed_seconds'] = round(time.monotonic() - started, 3)
    tex_log = build / Path(main_file).with_suffix('.log').name
    if tex_log.is_file():
        report['warnings'].extend(classify_log(tex_log.read_text(encoding='utf-8', errors='replace')))
    else:
        report['warnings'].append({'code': 'tex_log_missing', 'blocking': True,
                                   'message': 'No final TeX log was produced.'})
    for snapshot_name in ('source_snapshot', 'original_source_snapshot'):
        snapshot = report.get(snapshot_name)
        if snapshot and any(not (root / snapshot['root'] / row['path']).is_file()
                            or file_hash(root / snapshot['root'] / row['path']) != row['sha256'] for row in snapshot['files']):
            report['warnings'].append({'code': 'source_snapshot_changed', 'blocking': True,
                                       'message': 'A frozen input changed during compilation.'})
    if any(not (root / 'run' / row['path']).is_file()
           or file_hash(root / 'run' / row['path']) != row['sha256'] for row in source_snapshot['files']):
        report['warnings'].append({'code': 'compiled_source_changed', 'blocking': True,
                                   'message': 'A compiler-side input was changed or removed during the run.'})
    report['dependency_audit'] = audit_project_dependencies(root, source_snapshot)
    if report['dependency_audit']['status'] != 'PASS':
        report['warnings'].append({'code': 'unbound_local_inputs', 'blocking': True,
                                   'message': 'Local compiler inputs are not fully bound to the frozen project or generated output.'})
    pdf = build / Path(main_file).with_suffix('.pdf').name
    if report['exit_code'] == 0 and pdf.is_file():
        try:
            with pymupdf.open(pdf) as document:
                if not len(document) or document.is_encrypted:
                    raise ValueError('Empty or encrypted output PDF')
                report['pdf'] = {'path': pdf.relative_to(root).as_posix(), 'sha256': file_hash(pdf), 'pages': len(document)}
            report['compile_complete'] = True
            report['status'] = 'NEEDS_REVIEW' if any(w['blocking'] for w in report['warnings']) else 'PASS'
        except (ValueError, RuntimeError) as exc:
            report['warnings'].append({'code': 'invalid_output_pdf', 'blocking': True, 'message': str(exc)})
    elif report['exit_code'] == 0:
        report['warnings'].append({'code': 'output_pdf_missing', 'blocking': True,
                                   'message': 'Exit code zero without a fresh output PDF is not successful compilation.'})
    for log in [root / 'engine-version.log', root / 'stdout.log', root / 'stderr.log', tex_log]:
        if log.is_file():
            report['logs'].append({'path': log.relative_to(root).as_posix(), 'sha256': file_hash(log)})
    report['generated_files'] = [{'path': p.relative_to(root).as_posix(), 'sha256': file_hash(p)}
                                 for p in sorted(build.rglob('*')) if p.is_file()]
    atomic_write_json(root / 'compile_result.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True)
    parser.add_argument('--main-file', required=True)
    parser.add_argument('--out-dir', required=True)
    parser.add_argument('--engine-path')
    parser.add_argument('--timeout', type=float, default=180)
    parser.add_argument('--allow-network', action='store_true')
    parser.add_argument('--cache-dir')
    parser.add_argument('--original-project', help='Preserve and diff the original when compiling an explicitly adapted copy')
    parser.add_argument('--adaptation-reason', help='Actual reason for changed input files; required when the original differs')
    args = parser.parse_args()
    try:
        report = compile_project(args.project, args.main_file, args.out_dir, args.engine_path,
                                 args.timeout, args.allow_network, args.cache_dir, args.original_project, args.adaptation_reason)
    except (ValueError, OSError) as exc:
        print(json.dumps({'status': 'FAIL', 'error': str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({'status': report['status'], 'compile_complete': report['compile_complete'],
                      'exit_code': report['exit_code'], 'pdf': report['pdf'],
                      'report': str(Path(args.out_dir).resolve() / 'compile_result.json')}, ensure_ascii=False))
    return 0 if report['status'] == 'PASS' else 3 if report['status'] == 'NEEDS_REVIEW' else 2


if __name__ == '__main__':
    raise SystemExit(main())
