"""Regression tests for bundled dictionary loading.

The loader used to anchor ``importlib.resources.files()`` on the ``data``
directory itself. ``data/`` has no ``__init__.py``, so on Python 3.9 it imports
as a namespace package whose ``spec.origin`` is ``None`` and ``files()`` blows
up with ``TypeError`` — every ``fake_*`` strategy crashed on 3.9, the oldest
version the package claims to support. The fix anchors on this regular package
and walks into ``data/`` from there, which works on every supported Python.

These tests fail loudly if bundled dictionaries ever come back empty (or the
loader regresses), on whichever interpreter the suite runs under.
"""
from __future__ import annotations

from dbmask.masking.dictionaries import available, get_dictionary
from dbmask.masking.rules import MaskContext, strat_fake_city, strat_fake_name

BUNDLED = ("first_names", "last_names", "us_cities")


def test_bundled_dictionaries_load_and_are_nonempty():
    for name in BUNDLED:
        values = get_dictionary(name)
        assert len(values) > 10, f"bundled dictionary '{name}' loaded empty/short"
        assert all(isinstance(v, str) and v.strip() for v in values)


def test_available_lists_bundled_dictionaries():
    assert set(BUNDLED) <= set(available())


def test_fake_values_vary_by_input():
    """With healthy dictionaries, distinct inputs get distinct fakes.

    When loading silently failed, every full name collapsed to the same
    'Alex Doe' fallback — realistic-looking output was the whole point of the
    dictionary strategies, so pin it down.
    """
    ctx = MaskContext(column="full_name", rule="full_name", seed="t")
    names = {strat_fake_name(f"Person Number{i}", ctx) for i in range(20)}
    assert len(names) > 10

    ctx = MaskContext(column="city", rule="city", seed="t")
    cities = {strat_fake_city(f"City{i}", ctx) for i in range(20)}
    assert len(cities) > 10
    assert cities <= set(get_dictionary("us_cities"))


def test_unknown_dictionary_is_a_soft_miss():
    """Unknown names return empty so strategies fall back to format_random."""
    assert get_dictionary("definitely_not_a_bundled_dictionary") == []
