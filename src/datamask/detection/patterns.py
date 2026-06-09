"""Pattern-based sensitive-data detection.

This layer is the open-source replacement for the company-specific regexes in
the original script. Instead of guessing from *column names only*, each
:class:`Pattern` inspects a sample of the actual values and reports how many of
them match. A column is flagged when a high enough fraction matches.

The classic example from the requirements: values containing an ``@`` are very
likely email addresses. Patterns are data-driven, easy to read, and trivial to
extend — add your own by appending to ``DEFAULT_PATTERNS`` or passing custom
patterns to :class:`PatternMatcher`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Optional, Sequence


@dataclass
class Pattern:
    """A named heuristic that recognizes one kind of sensitive value.

    Attributes
    ----------
    name:
        Human-readable identifier, also used as the default masking *rule*
        (e.g. ``"email"`` -> the engine looks up a strategy for ``email``).
    test:
        Callable returning ``True`` if a single value looks like this kind.
    min_ratio:
        Fraction of sampled values that must match before the column is flagged.
    weight:
        Tie-breaker / confidence contribution when several patterns match.
    """

    name: str
    test: Callable[[str], bool]
    min_ratio: float = 0.6
    weight: float = 1.0


def _regex_test(pattern: str, flags: int = re.IGNORECASE) -> Callable[[str], bool]:
    compiled = re.compile(pattern, flags)
    return lambda value: bool(compiled.search(value or ""))


# --- Reusable building-block regexes ---------------------------------------
_EMAIL = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
_URL = r"^(https?://|www\.)[^\s]+$"
_IPV4 = r"^(\d{1,3}\.){3}\d{1,3}$"
_PHONE = r"^\+?[\d\s().-]{7,}$"
_SSN_US = r"^\d{3}-?\d{2}-?\d{4}$"
_CREDIT_CARD = r"^(?:\d[ -]?){13,19}$"
_ZIP_US = r"^\d{5}(?:-\d{4})?$"
_UUID = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
_DATE = r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}([ T]\d{1,2}:\d{2}(:\d{2})?)?$"


def _looks_like_full_name(value: str) -> bool:
    """Two-to-three capitalized words, letters/hyphens/apostrophes only."""
    value = (value or "").strip()
    parts = value.split()
    if not (2 <= len(parts) <= 3):
        return False
    return all(re.fullmatch(r"[A-Z][a-zA-Z'’-]+\.?", p) for p in parts)


def _luhn_valid(value: str) -> bool:
    digits = [int(c) for c in re.sub(r"\D", "", value)]
    if not (13 <= len(digits) <= 19):
        return False
    checksum = 0
    parity = len(digits) % 2
    for i, d in enumerate(digits):
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


# --- The default catalogue --------------------------------------------------
DEFAULT_PATTERNS: list[Pattern] = [
    Pattern("email", _regex_test(_EMAIL), min_ratio=0.6),
    Pattern("url", _regex_test(_URL), min_ratio=0.6),
    Pattern("ip_address", _regex_test(_IPV4), min_ratio=0.7),
    Pattern("uuid", _regex_test(_UUID), min_ratio=0.8),
    Pattern("ssn", _regex_test(_SSN_US), min_ratio=0.6),
    Pattern("credit_card", _luhn_valid, min_ratio=0.6, weight=1.2),
    Pattern("zip_code", _regex_test(_ZIP_US), min_ratio=0.7),
    Pattern("phone", _regex_test(_PHONE), min_ratio=0.7, weight=0.8),
    Pattern("full_name", _looks_like_full_name, min_ratio=0.5, weight=0.9),
    Pattern("date", _regex_test(_DATE), min_ratio=0.8, weight=0.5),
]


@dataclass
class PatternMatch:
    name: str
    ratio: float
    confidence: float


class PatternMatcher:
    """Runs a catalogue of :class:`Pattern` objects over sampled values."""

    def __init__(self, patterns: Optional[Sequence[Pattern]] = None):
        self.patterns = list(patterns if patterns is not None else DEFAULT_PATTERNS)

    def match(self, values: Sequence[str]) -> Optional[PatternMatch]:
        """Return the best-matching pattern for ``values`` or ``None``.

        ``None`` means "patterns are inconclusive" — the caller may then fall
        back to history or an LLM.
        """
        clean = [v for v in (values or []) if v not in (None, "")]
        if not clean:
            return None

        best: Optional[PatternMatch] = None
        for pat in self.patterns:
            hits = sum(1 for v in clean if pat.test(v))
            ratio = hits / len(clean)
            if ratio >= pat.min_ratio:
                confidence = min(1.0, ratio * pat.weight)
                if best is None or confidence > best.confidence:
                    best = PatternMatch(pat.name, ratio, confidence)
        return best
