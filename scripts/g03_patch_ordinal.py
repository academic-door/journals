from pathlib import Path

path = Path("scripts/translate_issue.py")
text = path.read_text(encoding="utf-8")
marker = "_EN_TEMPORAL_ORDINAL_RE = re.compile("
if marker in text:
    raise SystemExit(0)

insertion_point = text.index("_EN_NUMBER_UNIT_RE = re.compile(")
block = r'''_EN_ORDINAL_BASE_VALUES = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
    "eleventh": 11, "twelfth": 12, "thirteenth": 13, "fourteenth": 14,
    "fifteenth": 15, "sixteenth": 16, "seventeenth": 17,
    "eighteenth": 18, "nineteenth": 19,
    "twentieth": 20, "thirtieth": 30, "fortieth": 40, "fiftieth": 50,
    "sixtieth": 60, "seventieth": 70, "eightieth": 80, "ninetieth": 90,
}
_EN_ORDINAL_TENS_VALUES = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
_EN_ORDINAL_ONES_VALUES = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9,
}
_EN_TEMPORAL_ORDINAL_RE = re.compile(
    r"(?i)(?<![A-Za-z])"
    r"(?P<ordinal>(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|"
    r"tenth|eleventh|twelfth|thirteenth|fourteenth|fifteenth|sixteenth|"
    r"seventeenth|eighteenth|nineteenth|twentieth|thirtieth|fortieth|"
    r"fiftieth|sixtieth|seventieth|eightieth|ninetieth|"
    r"(?:twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)[- ]"
    r"(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth)))"
    r"\s+(?:years?|months?|weeks?|days?|decades?|centuries?)\b"
)


def _english_temporal_ordinal_value(word: str) -> int | None:
    normalized = word.lower().replace("-", " ").strip()
    if normalized in _EN_ORDINAL_BASE_VALUES:
        return _EN_ORDINAL_BASE_VALUES[normalized]
    parts = normalized.split()
    if len(parts) == 2:
        tens = _EN_ORDINAL_TENS_VALUES.get(parts[0])
        ones = _EN_ORDINAL_ONES_VALUES.get(parts[1])
        if tens is not None and ones is not None:
            return tens + ones
    return None


'''
text = text[:insertion_point] + block + text[insertion_point:]

semantic_marker = '    # Mathematical "unity" denotes numeric 1 only in explicit comparator/relation contexts.\n'
semantic_block = '''    # Temporal ordinals are quantitative horizons. Canonicalize only when an
    # English ordinal directly modifies an explicit time unit, so a natural Chinese
    # rendering such as "第30年" remains numerically auditable rather than exempt.
    for match in _EN_TEMPORAL_ORDINAL_RE.finditer(value):
        span = match.span("ordinal")
        if overlaps(span):
            continue
        ordinal_value = _english_temporal_ordinal_value(match.group("ordinal"))
        if ordinal_value is not None:
            record(_quantity_token(ordinal_value), span)

'''
if text.count(semantic_marker) != 1:
    raise SystemExit("semantic insertion marker mismatch")
text = text.replace(semantic_marker, semantic_block + semantic_marker, 1)
path.write_text(text, encoding="utf-8")
