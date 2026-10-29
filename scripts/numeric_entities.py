"""Exact, occurrence-aware numeric quantities for translation validation.

This lexer does not infer all natural-language entity bindings. Unknown compact
unit suffixes are retained as uncertainty, never silently discarded.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re

# Case is meaningful: mV and MV must not be conflated. Longest forms win.
_UNIT_ALIASES = {
    'km/h': ('km/h', '公里/小时', '千米/小时'),
    'm/s^2': ('m/s^2', 'm/s²', 'm/s2', '米/秒²', '米/秒^2'),
    'm/s': ('m/s', '米/秒'), 'm^2': ('m^2', 'm²', '平方米'),
    'm^3': ('m^3', 'm³', '立方米'), 'rad/s': ('rad/s', '弧度/秒'),
    'kHz': ('kHz', '千赫兹', '千赫'), 'MHz': ('MHz', '兆赫兹', '兆赫'),
    'GHz': ('GHz', '吉赫兹', '吉赫'), 'Hz': ('Hz', '赫兹', '赫'),
    'dBm': ('dBm',), 'dB': ('dB', '分贝'),
    'deg': ('deg', 'degree', 'degrees', '°', '度'), 'rad': ('rad', '弧度'),
    'mm': ('mm', '毫米'), 'cm': ('cm', '厘米'), 'km': ('km', '千米', '公里'),
    'um': ('um', 'µm', 'μm', '微米'), 'nm': ('nm', '纳米'), 'm': ('m', '米'),
    'ns': ('ns', '纳秒'), 'us': ('us', 'µs', 'μs', '微秒'),
    'ms': ('ms', '毫秒'), 's': ('s', '秒'), 'min': ('min', '分钟'),
    'h': ('h', 'hr', '小时'), 'mV': ('mV', '毫伏'), 'V': ('V', '伏特', '伏'),
    'mA': ('mA', '毫安'), 'A': ('A', '安培', '安'),
    'kW': ('kW', '千瓦'), 'mW': ('mW', '毫瓦'), 'W': ('W', '瓦特', '瓦'),
    'mg': ('mg', '毫克'), 'kg': ('kg', '千克', '公斤'), 'g': ('g', '克'),
    'K': ('K', '开尔文'), '°C': ('°C', '摄氏度'),
    'Pa': ('Pa', '帕'), 'kPa': ('kPa', '千帕'), 'MPa': ('MPa', '兆帕'),
    '%': ('%', '％', '个百分点'), 'ppm': ('ppm',),
}
_ALIAS_MAP = {alias: canon for canon, aliases in _UNIT_ALIASES.items() for alias in aliases}
_UNIT_PATTERN = '|'.join(re.escape(a) for a in sorted(_ALIAS_MAP, key=len, reverse=True))
# A number must not be a substring of a variable, identifier, or decimal.
_MANTISSA = r'[+\-−]?(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\.\d+)'
_NUMBER = (
    rf'(?P<number>(?P<scientific_base>{_MANTISSA})\s*(?:\\times|×|x|\*)\s*10\s*\^?\s*\{{?(?P<scientific_exp>[+\-−]?\d+)\}}?'
    rf'|(?P<exponential_base>{_MANTISSA})[eE](?P<exponential_exp>[+\-−]?\d+)'
    rf'|(?P<plain>{_MANTISSA}))'
)
NUM_ENTITY_REGEX = re.compile(
    r'(?<![A-Za-z0-9_.])' + _NUMBER
    + rf'(?P<unit_gap>\s*)(?:(?P<unit>{_UNIT_PATTERN})(?![A-Za-z/\^²³])|(?P<unknown_unit>[A-Za-zµμΩ]+(?:[/^][A-Za-z0-9²³]+)*))?'
)
# Historical exported name; the named fields are intentional.
NUM_RX_COMBINED = NUM_ENTITY_REGEX
_MASKS = [
    re.compile(r'\$\$[\s\S]*?\$\$|(?<!\\)\$[^$]+\$'),
    re.compile(r'\\\([\s\S]*?\\\)|\\\[[\s\S]*?\\\]'),
    re.compile(r'\[\s*\d+(?:\s*(?:,|[-–—])\s*\d+)*\s*\]'),
    re.compile(r'\([A-Z][^()]*?,\s*(?:19|20)\d{2}[a-z]?\)'),
    re.compile(r'(?:(?:Eq\.|Equation|Formula|Eqs\.|式)\s*)?[（(]\d+[a-z]?[)）]'),
    re.compile(r'⟦[^⟧]*⟧|\{\{(?:MATH|CITE|PROTECTED)[^}]*\}\}'),
]


def _mask_non_quantities(text: str) -> str:
    for pattern in _MASKS:
        text = pattern.sub(lambda m: ' ' * len(m.group()), text)
    return text


def extract_numeric_entities(text: str) -> list[dict]:
    """Return Decimal-valued quantities with exact source offsets and occurrences."""
    if not isinstance(text, str):
        raise TypeError('Numeric extraction requires text')
    clean = _mask_non_quantities(text)
    entities = []
    for match in NUM_ENTITY_REGEX.finditer(clean):
        number = match.group('number')
        base = match.group('scientific_base') or match.group('exponential_base')
        exponent = match.group('scientific_exp') or match.group('exponential_exp')
        token = number.replace('−', '-').replace(',', '')
        try:
            value = Decimal(f"{base.replace('−', '-').replace(',', '')}e{exponent.replace('−', '-')}" if base is not None else token)
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f'Invalid numerical expression: {number}') from exc
        raw_unit = match.group('unit')
        unknown = match.group('unknown_unit')
        # An ordinary following word ("100 samples") is not a unit claim.
        # Compact forms ("10furlong") and compound forms are explicit uncertainty.
        unknown = unknown if unknown and (not match.group('unit_gap') or '/' in unknown or '^' in unknown or unknown.isupper()) else None
        unit = _ALIAS_MAP.get(raw_unit) if raw_unit else None
        if clean[max(0, match.start() - 3):match.start()] == '百分之':
            unit = '%'
        end = match.end('unit') if raw_unit else match.end('unknown_unit') if unknown else match.end('number')
        prefix = clean[max(0, match.start() - 40):match.start()]
        binding = re.search(r'\b([A-Za-z][A-Za-z0-9_]*)\s*=\s*$', prefix)
        entities.append({
            'value': value, 'unit': unit, 'token': number.strip(),
            'raw': text[match.start():end], 'start': match.start(), 'end': end,
            'occurrence': len(entities), 'unknown_unit': unknown,
            'binding': binding.group(1) if binding else None,
        })
    return entities


def compare_numeric_entities(source: str, target: str) -> dict:
    """Compare exact quantities and multiplicity; keep formatting separate."""
    src = extract_numeric_entities(source)
    tgt = extract_numeric_entities(target)
    remaining = list(tgt)
    issues, warnings, formatting = [], [], []
    for ent in src:
        matching = next((i for i, candidate in enumerate(remaining)
                         if (ent['value'], ent['unit'], ent['unknown_unit']) ==
                            (candidate['value'], candidate['unit'], candidate['unknown_unit'])
                         and (not ent['value'].is_zero() or ent['value'].is_signed() == candidate['value'].is_signed())
                         and (not ent['binding'] or candidate['binding'] == ent['binding'])), None)
        if matching is None:
            issues.append({'type': 'missing_or_changed_quantity', 'numeric_base': ent['token'],
                           'expected_entity': ent['raw'], 'unit': ent['unit'],
                           'source_offset': [ent['start'], ent['end']],
                           'issue': 'Exact quantity, unit, occurrence, or explicit variable binding changed'})
        else:
            candidate = remaining.pop(matching)
            if re.sub(r'\s+', '', ent['token']) != re.sub(r'\s+', '', candidate['token']):
                formatting.append({'type': 'numeric_display_changed', 'source': ent['token'],
                                   'target': candidate['token'], 'value': str(ent['value'])})
    for ent in remaining:
        issues.append({'type': 'added_quantity', 'numeric_base': ent['token'],
                       'actual_entity': ent['raw'], 'unit': ent['unit'],
                       'target_offset': [ent['start'], ent['end']],
                       'issue': 'Translation contains an unmatched numeric occurrence'})
    for side, entities in [('source', src), ('target', tgt)]:
        for ent in entities:
            if ent['unknown_unit']:
                warnings.append({'type': 'unknown_unit', 'side': side, 'raw': ent['raw'],
                                 'unit': ent['unknown_unit'], 'offset': [ent['start'], ent['end']]})
    return {'issues': issues, 'unknown_units': warnings, 'formatting': formatting,
            'source_entities': len(src), 'target_entities': len(tgt)}
