"""Masking strategies (the "how to transform a value" catalogue).

Every strategy is a callable ``(value, context) -> masked_value``. They are
deterministic: a seeded RNG derived from the input guarantees the same source
value always maps to the same masked value, which keeps referential consistency
(e.g. the same customer name masks identically everywhere).

Covered requirement #5 options:
  * fake-value replacement from dictionaries (names, cities, ...),
  * shuffle,
  * random characters (format-preserving),
  * null / blank,
  * plus redaction and a generic dictionary factory.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from datamask.masking import dictionaries as dicts
from datamask.masking.format import format_preserving_random, seeded_rng


@dataclass
class MaskContext:
    """Information available to a strategy when masking a single value."""

    column: str
    rule: Optional[str]
    seed: Optional[str]


Strategy = Callable[[str, MaskContext], object]

# ---------------------------------------------------------------------------
# Individual strategies
# ---------------------------------------------------------------------------

def strat_null(value, ctx: MaskContext):
    """Replace with SQL NULL."""
    return None


def strat_blank(value, ctx: MaskContext):
    """Replace with an empty string."""
    return ""


def strat_redact(value, ctx: MaskContext):
    """Keep length but hide content (e.g. ``****``), preserving separators."""
    if value is None:
        return None
    return "".join("*" if ch.isalnum() else ch for ch in str(value))


def strat_format_random(value, ctx: MaskContext):
    """Random characters, preserving length and digit/letter/separator layout."""
    if value is None:
        return None
    rng = seeded_rng(str(value), ctx.seed)
    return format_preserving_random(str(value), rng)


def strat_shuffle(value, ctx: MaskContext):
    """Deterministically shuffle the characters of the value in place.

    Letters/digits are permuted; separators keep their positions so the overall
    format is preserved.
    """
    if value is None:
        return None
    s = str(value)
    rng = seeded_rng(s, ctx.seed)
    movable_idx = [i for i, ch in enumerate(s) if ch.isalnum()]
    chars = [s[i] for i in movable_idx]
    rng.shuffle(chars)
    out = list(s)
    for idx, ch in zip(movable_idx, chars):
        out[idx] = ch
    return "".join(out)


def _pick(dictionary: str, value: str, ctx: MaskContext) -> Optional[str]:
    pool = dicts.get_dictionary(dictionary)
    if not pool:
        return None
    rng = seeded_rng(str(value), ctx.seed)
    return rng.choice(pool)


def strat_fake_first_name(value, ctx: MaskContext):
    if value is None:
        return None  # a missing value stays missing; masking never invents data
    pick = _pick("first_names", value, ctx)
    return pick if pick is not None else strat_format_random(value, ctx)


def strat_fake_last_name(value, ctx: MaskContext):
    if value is None:
        return None
    pick = _pick("last_names", value, ctx)
    return pick if pick is not None else strat_format_random(value, ctx)


def strat_fake_name(value, ctx: MaskContext):
    """Replace a full name with a consistent fake first + last name."""
    if value is None:
        return None
    first = _pick("first_names", value, ctx) or "Alex"
    last = _pick("last_names", str(value) + "_last", ctx) or "Doe"
    return f"{first} {last}"


def strat_fake_city(value, ctx: MaskContext):
    """Replace a US city with another US city (requirement example)."""
    if value is None:
        return None
    pick = _pick("us_cities", value, ctx)
    return pick if pick is not None else strat_format_random(value, ctx)


def strat_fake_email(value, ctx: MaskContext):
    """Generate a consistent fake email, preserving the domain if present."""
    if value is None:
        return None
    s = str(value)
    rng = seeded_rng(s, ctx.seed)
    domain = s.split("@", 1)[1] if "@" in s else "example.com"
    first = (_pick("first_names", s, ctx) or "user").lower()
    last = (_pick("last_names", s + "_l", ctx) or "anon").lower()
    suffix = rng.randint(1, 999)
    return f"{first}.{last}{suffix}@{domain}"


def make_dictionary_strategy(dictionary: str) -> Strategy:
    """Build a strategy that replaces values from an arbitrary named dictionary."""

    def _strategy(value, ctx: MaskContext):
        if value is None:
            return None
        pick = _pick(dictionary, value, ctx)
        return pick if pick is not None else strat_format_random(value, ctx)

    return _strategy


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
STRATEGIES: dict[str, Strategy] = {
    "null": strat_null,
    "blank": strat_blank,
    "redact": strat_redact,
    "format_random": strat_format_random,
    "shuffle": strat_shuffle,
    "fake_name": strat_fake_name,
    "fake_first_name": strat_fake_first_name,
    "fake_last_name": strat_fake_last_name,
    "fake_city": strat_fake_city,
    "fake_email": strat_fake_email,
}

# Sensible defaults mapping *detected rule* -> *strategy* when the user hasn't
# configured one. Anything not listed falls back to the engine's default.
DEFAULT_RULE_STRATEGIES: dict[str, str] = {
    "email": "fake_email",
    "full_name": "fake_name",
    "first_name": "fake_first_name",
    "last_name": "fake_last_name",
    "city": "fake_city",
    "phone": "format_random",
    "ssn": "format_random",
    "credit_card": "format_random",
    "zip_code": "format_random",
    "ip_address": "format_random",
    "uuid": "format_random",
    "date": "format_random",
    "address": "redact",
    "date_of_birth": "format_random",
}


def get_strategy(name: str) -> Strategy:
    if name not in STRATEGIES:
        raise KeyError(
            f"Unknown masking strategy '{name}'. Available: {sorted(STRATEGIES)}"
        )
    return STRATEGIES[name]


def register_strategy(name: str, strategy: Strategy) -> None:
    """Register a custom strategy at runtime."""
    STRATEGIES[name] = strategy
