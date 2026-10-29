"""Small shared helpers for source parsers; offsets always refer to raw input."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def normalize_with_map(raw):
    """Collapse whitespace without throwing away the original character ranges."""
    chars, mapping = [], []
    for match in re.finditer(r"\S|\s+", raw):
        token = match.group()
        value = " " if token.isspace() else token
        if not chars and value == " ":
            continue
        chars.append(value)
        mapping.append([match.start(), match.end()])
    if chars and chars[-1] == " ":
        chars.pop()
        mapping.pop()
    return "".join(chars), mapping


def sentence_ranges(raw, splitter):
    normalized, mapping = normalize_with_map(raw)
    cursor = 0
    result = []
    for sentence in splitter(normalized):
        start = normalized.find(sentence, cursor)
        if start < 0:
            # Never fabricate a source offset when the splitter rewrites content.
            raise ValueError("Sentence splitter changed source text; offset mapping unavailable")
        end = start + len(sentence)
        result.append((sentence, mapping[start][0], mapping[end - 1][1], mapping[start:end]))
        cursor = end
    return result


def scoped_out(item):
    section = item.get("section", "").casefold()
    return item.get("kind") in {"reference", "footnote"} or any(
        term in section for term in ("reference", "bibliography", "acknowledg", "appendix", "appendices")
    )


def register_syntax_gaps(regions, path, text, relative_file):
    """Account for non-semantic syntax retained by a structured parser.

    Call only after token/AST parsing. Unsupported constructs must separately create
    blocking parser warnings; this helper never asserts semantic coverage by itself.
    """
    intervals = sorted((r['locator']['start'], r['locator']['end']) for r in regions
                       if r.get('locator', {}).get('file') == relative_file)
    cursor = 0
    gaps = []
    for start, end in intervals + [(len(text), len(text))]:
        if start > cursor:
            gaps.append((cursor, start))
        cursor = max(cursor, end)
    for index, (start, end) in enumerate(gaps):
        region_id = f'syntax:{relative_file}:{start}:{end}'
        regions.append({'region_id': region_id, 'locator': {'file': relative_file, 'start': start, 'end': end, 'region_id': region_id},
                        'role': 'source_syntax', 'raw_text': text[start:end], 'disposition': 'preserve', 'unit_ids': [],
                        'evidence': {'method': 'structured-parser', 'reason': 'Source syntax/layout or protected project metadata preserved in input copy'}})
