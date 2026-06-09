"""Heuristics for ignoring obvious test/placeholder values.

Ported from the original validation script. These values (single characters,
repeated characters, keywords like ``test``/``n/a``) commonly appear identically
in both source and target for legitimate reasons, so they would create noise in
the masking-completeness check. They are filtered out before the expensive
row-level comparison.
"""
from __future__ import annotations

_TEST_KEYWORDS = {
    "test", "dummy", "sample", "example", "xxx", "yyy", "zzz", "n/a", "na", "null", "none",
}


def is_test_data(value) -> bool:
    """Return True if ``value`` looks like placeholder/test data."""
    if value is None:
        return False
    s = str(value).strip()
    if not s:
        return True
    if len(s) == 1:
        return True
    # Repeated single character: 'aaaa', '1111', '----', '@@@@'.
    if len(s) <= 10 and len(set(s)) == 1:
        return True
    if s.lower() in _TEST_KEYWORDS:
        return True
    return False
