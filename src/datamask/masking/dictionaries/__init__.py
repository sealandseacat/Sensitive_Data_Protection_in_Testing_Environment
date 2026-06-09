"""Bundled value dictionaries for realistic fake replacements.

These small starter lists ship with the package so fake-value masking works out
of the box. They are intentionally short — extend them, or register your own
named dictionaries at runtime via :func:`register_dictionary`. The requirement
example ("replace a US city with another US city") is satisfied by the
``us_cities`` dictionary plus the consistent (seeded) chooser in ``rules.py``.
"""
from __future__ import annotations

from functools import lru_cache
from importlib import resources
from typing import Sequence

_DATA_PACKAGE = "datamask.masking.dictionaries.data"
_CUSTOM: dict[str, list[str]] = {}


@lru_cache(maxsize=None)
def _load_bundled(name: str) -> tuple[str, ...]:
    try:
        text = resources.files(_DATA_PACKAGE).joinpath(f"{name}.txt").read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError):
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
