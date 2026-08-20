"""Bundled value dictionaries for realistic fake replacements.

These small starter lists ship with the package so fake-value masking works out
of the box. They are intentionally short — extend them, or register your own
named dictionaries at runtime via :func:`register_dictionary`. The classic
example — replace a US city with another US city — is handled by the
``us_cities`` dictionary plus the consistent (seeded) chooser in ``rules.py``.
"""
from __future__ import annotations

from functools import lru_cache
from importlib import resources
from typing import Sequence

# Anchor resource lookups on THIS package (it has an __init__.py, so it is a
# regular package on every supported Python) and walk into data/ from there.
# Anchoring on "…dictionaries.data" directly breaks on Python 3.9: data/ has no
# __init__.py, so it imports as a namespace package whose spec.origin is None,
# and 3.9's importlib.resources.files() fallback does pathlib.Path(None) ->
# TypeError. Namespace-package support for files() only arrived in 3.10.
_ANCHOR = __name__
_CUSTOM: dict[str, list[str]] = {}


@lru_cache(maxsize=None)
def _load_bundled(name: str) -> tuple[str, ...]:
    try:
        text = (
            resources.files(_ANCHOR)
            .joinpath("data")
            .joinpath(f"{name}.txt")
            .read_text(encoding="utf-8")
        )
    except FileNotFoundError:
        # Unknown dictionary name: strategies treat an empty pool as "fall back
        # to format-preserving random", so this is a soft miss by design.
        return tuple()
    return tuple(line.strip() for line in text.splitlines() if line.strip())


def register_dictionary(name: str, values: Sequence[str]) -> None:
    """Register (or override) a named dictionary at runtime."""
    _CUSTOM[name] = list(values)


def get_dictionary(name: str) -> list[str]:
    """Return the values for a named dictionary (custom takes precedence)."""
    if name in _CUSTOM:
        return _CUSTOM[name]
    return list(_load_bundled(name))


def available() -> list[str]:
    bundled = {"first_names", "last_names", "us_cities"}
    return sorted(bundled | set(_CUSTOM))
