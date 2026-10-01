"""Deterministic Chinese/English routing; caller may override ambiguous labels."""
import re

FIELD_LINE = re.compile(r"^\s*(?:field|字段)\s*[:：]\s*(.+)$", re.IGNORECASE | re.MULTILINE)
HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\U00020000-\U0003134f]")


def resolve_language(text, requested="auto"):
    if requested in ("zh", "en"):
        return requested
    if requested != "auto":
        raise ValueError("language must be auto, zh or en")
    match = FIELD_LINE.search(text)
    label = match.group(1) if match else text
    return "zh" if HAN.search(label) else "en"
