"""Offset-preserving lexical boundaries for the bounded LaTeX source backend.

These routines recognize tokens and declared argument shapes, not TeX expansion.
Unknown commands retain conservative argument capture and must be reported by the
caller. No input text is rewritten while computing source locations.
"""
from __future__ import annotations

import re


LITERAL_ENVIRONMENTS = {'verbatim', 'verbatim*', 'lstlisting', 'minted'}
TEXT_FORMAT_COMMANDS = frozenset({'textbf', 'textit', 'texttt', 'textsc', 'textup',
                                 'textnormal', 'textrm', 'textsf', 'textmd', 'textsl',
                                 'emph', 'underline'})
COMMAND_SIGNATURES = {
    **{name: 'om' for name in ('section', 'subsection', 'subsubsection', 'chapter',
                              'paragraph', 'subparagraph', 'title', 'author',
                              'caption', 'footnote', 'footnotetext')},
    **{name: 'm' for name in TEXT_FORMAT_COMMANDS},
    **{name: 'm' for name in ('text', 'mbox', 'intertext',
                             'shortintertext', 'underline', 'ref', 'eqref',
                             'pageref', 'label', 'url', 'ensuremath', 'input', 'include',
                             'bibliography', 'bibliographystyle')},
    **{name: 'oom' for name in ('cite', 'citep', 'citet', 'citealt', 'citealp',
                               'citeauthor', 'citeyear', 'citeyearpar', 'autocite',
                               'parencite', 'textcite')},
    'nocite': 'm', 'href': 'omm', 'includegraphics': 'om', 'bibitem': 'om',
    'addbibresource': 'om', 'documentclass': 'om', 'usepackage': 'om',
    'RequirePackage': 'om', 'multicolumn': 'mmm', 'footnotemark': 'o',
    'item': 'o', 'end': 'm', 'newcommand': 'coom', 'renewcommand': 'coom',
    'providecommand': 'coom', 'DeclareRobustCommand': 'coom',
    'newenvironment': 'moomm', 'renewenvironment': 'moomm',
    'DeclareMathOperator': 'cm',
    **{name: '' for name in ('centering', 'hline', 'toprule', 'midrule', 'bottomrule',
                             'small', 'footnotesize', 'noindent', 'maketitle',
                             'clearpage', 'newpage', 'relax', 'newblock')},
}
ENVIRONMENT_SIGNATURES = {
    **{name: '' for name in ('document', 'abstract', 'itemize', 'enumerate',
                              'description', 'quote', 'center', 'subequations',
                              'appendices', 'equation', 'equation*', 'align',
                              'align*', 'gather', 'gather*', 'multline', 'multline*',
                              'displaymath', 'math', 'verbatim', 'verbatim*')},
    'figure': 'o', 'figure*': 'o', 'table': 'o', 'table*': 'o',
    'tabular': 'om', 'tabular*': 'mom', 'longtable': 'om',
    'minipage': 'ooom', 'thebibliography': 'm', 'alignat': 'm', 'alignat*': 'm',
    'lstlisting': 'o', 'minted': 'om',
}


def skip_space_comments(text, index, limit=None):
    limit = len(text) if limit is None else limit
    while index < limit:
        if text[index].isspace():
            index += 1
        elif text[index] == '%':
            end = text.find('\n', index, limit)
            index = limit if end < 0 else end + 1
        else:
            break
    return index


def group_end(text, start, opener='{', closer='}'):
    if start >= len(text) or text[start] != opener:
        raise ValueError(f'Expected {opener} at offset {start}')
    depth, index = 1, start + 1
    while index < len(text):
        if text[index] == '\\':
            index += 2
            continue
        if text[index] == '%':
            newline = text.find('\n', index)
            index = len(text) if newline < 0 else newline + 1
            continue
        # A closing bracket inside a braced optional argument is literal data.
        if opener == '[' and text[index] == '{':
            index = group_end(text, index)
            continue
        if text[index] == opener:
            depth += 1
        elif text[index] == closer:
            depth -= 1
            if not depth:
                return index + 1
        index += 1
    raise ValueError(f'Unbalanced {opener}{closer} starting at {start}')


def control_sequence(text, index):
    match = re.match(r'\\([A-Za-z@]+\*?|.)', text[index:], re.S)
    return (match.group(1), index + len(match.group())) if match else None


def _arguments(text, cursor, signature):
    args = []
    for kind in signature:
        scan = skip_space_comments(text, cursor)
        if kind == 'o':
            if scan >= len(text) or text[scan] != '[':
                continue
            stop = group_end(text, scan, '[', ']')
            args.append(('[', scan + 1, stop - 1))
        elif scan < len(text) and text[scan] == '{':
            stop = group_end(text, scan)
            args.append(('{', scan + 1, stop - 1))
        elif kind == 'c' and scan < len(text) and text[scan] == '\\':
            token = control_sequence(text, scan)
            stop = token[1]
            args.append(('c', scan, stop))
        else:
            break
        cursor = stop
    return cursor, args


def literal_command_end(text, index):
    token = control_sequence(text, index)
    if not token or token[0].rstrip('*') != 'verb':
        return None
    cursor = token[1]
    if cursor >= len(text) or text[cursor].isspace():
        raise ValueError(f'Missing verb delimiter at {index}')
    stop = text.find(text[cursor], cursor + 1)
    newline = text.find('\n', cursor + 1)
    if stop < 0 or (newline >= 0 and newline < stop):
        raise ValueError(f'Unclosed verb command at {index}')
    return stop + 1


def command_at(text, index, signature=None):
    token = control_sequence(text, index)
    if token is None:
        return None
    name, cursor = token
    simple = name.rstrip('*')
    if simple == 'verb':
        return name, literal_command_end(text, index), []
    if simple == 'begin' and signature is None:
        cursor, args = _arguments(text, cursor, 'm')
        if args:
            environment = text[args[0][1]:args[0][2]]
            cursor, rest = _arguments(text, cursor, ENVIRONMENT_SIGNATURES.get(environment, ''))
            args.extend(rest)
        return name, cursor, args
    spec = signature if signature is not None else COMMAND_SIGNATURES.get(simple)
    if spec is not None:
        cursor, args = _arguments(text, cursor, spec)
        return name, cursor, args
    if not name[0].isalpha():
        return name, cursor, []
    # Unknown arity is not guessed: retain the old conservative source span.
    # The caller must block acceptance for this unsupported command.
    args = []
    while cursor < len(text):
        scan = skip_space_comments(text, cursor)
        if scan >= len(text) or text[scan] not in '[{':
            break
        left = text[scan]
        stop = group_end(text, scan, left, ']' if left == '[' else '}')
        args.append((left, scan + 1, stop - 1))
        cursor = stop
    return name, cursor, args


def math_span_end(text, start, limit=None):
    r"""Return an entire $...$, $$...$$, \(...\) or \[...\] span end.

    Escaped delimiters and delimiters in comments cannot end the math span.
    Call only at a lexically active position (not the second char of an escape).
    """
    limit = len(text) if limit is None else min(limit, len(text))
    opener = next((v for v in ('$$', '$', r'\(', r'\[') if text.startswith(v, start)), None)
    if opener is None:
        return None
    closer = {'$$': '$$', '$': '$', r'\(': r'\)', r'\[': r'\]'}[opener]
    index = start + len(opener)
    while index < limit:
        if text[index] == '%':
            newline = text.find('\n', index, limit)
            index = limit if newline < 0 else newline + 1
        elif text.startswith(closer, index):
            return index + len(closer)
        elif text[index] == '\\':
            token = control_sequence(text, index)
            index = token[1] if token else index + 1
        else:
            index += 1
    raise ValueError(f'Unclosed math delimiter {opener} at {start}')


def environment_end(text, start, name, body_start):
    if name in LITERAL_ENVIRONMENTS:
        match = re.search(r'\\end\s*\{' + re.escape(name) + r'\}', text[body_start:])
        if match:
            return body_start + match.start(), body_start + match.end()
        raise ValueError(f'Unclosed LaTeX environment {name} at {start}')
    depth, index = 1, body_start
    while index < len(text):
        if text[index] == '%':
            newline = text.find('\n', index)
            index = len(text) if newline < 0 else newline + 1
            continue
        if text[index] != '\\':
            index += 1
            continue
        token = control_sequence(text, index)
        if token is None:
            index += 1
            continue
        command, after = token
        if command.rstrip('*') == 'verb':
            index = literal_command_end(text, index)
            continue
        if command not in ('begin', 'end'):
            index = after
            continue
        stop, args = _arguments(text, after, 'm')
        if not args:
            index = after
            continue
        environment = text[args[0][1]:args[0][2]]
        if environment == name:
            depth += 1 if command == 'begin' else -1
            if depth == 0:
                return index, stop
        elif command == 'begin' and environment in LITERAL_ENVIRONMENTS:
            _, stop = environment_end(text, index, environment, stop)
        index = stop
    raise ValueError(f'Unclosed LaTeX environment {name} at {start}')


def find_command_start(text, start, end, name):
    """Find a structural command without matching comments or literal tokens.

    Command arguments follow the same bounded grammar as ``command_at``. Their
    contents are not sibling structural commands; unsupported macro expansion
    still requires a caller warning. Source offsets are never rewritten.
    """
    index = start
    while index < end:
        if text[index] == '%':
            newline = text.find('\n', index, end)
            index = end if newline < 0 else newline + 1
            continue
        if text[index] != '\\':
            index += 1
            continue
        command = command_at(text, index)
        if command is None:
            index += 1
            continue
        current, stop, args = command
        if current == name:
            return index
        if current == 'begin' and args:
            environment = text[args[0][1]:args[0][2]]
            if environment in LITERAL_ENVIRONMENTS:
                _, stop = environment_end(text, index, environment, stop)
        index = stop
    return None


def glue_command_end(text, index):
    """Bound ordinary literal TeX glue; dynamic dimensions remain unsupported."""
    token = control_sequence(text, index)
    if not token or token[0] not in ('hskip', 'vskip', 'kern', 'mskip'):
        return None
    number = r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)\s*'
    dimension = number + r'(?:pt|pc|in|bp|cm|mm|dd|cc|sp|em|ex|mu|fil(?:l)?)\b'
    match = re.match(r'\s*' + dimension + r'(?:\s+plus\s+' + dimension + r')?'
                     + r'(?:\s+minus\s+' + dimension + r')?(?:\s*\\relax\b)?', text[token[1]:])
    return token[1] + match.end() if match else None
