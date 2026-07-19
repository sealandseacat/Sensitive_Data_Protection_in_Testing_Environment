"""Tests for the seed map — durable (original -> masked) pair tracking.

The point of these tests is the guarantee the seed map exists to provide:
once a value has been masked, it keeps masking to the *same* replacement even
when the things the old recompute-only approach depended on (dictionary
contents/order, the global seed) change underneath it.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from datamask.config import MaskingConfig, SeedMapConfig
from datamask.masking import dictionaries as dicts
from datamask.masking.engine import ColumnPlan, MaskingEngine
from datamask.masking.seed_store import SeedStore


@pytest.fixture()
def seed_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'seedmap.db'}"


@pytest.fixture()
def restore_cities():
    """Snapshot/restore the bundled city list (tests mutate it deliberately)."""
    original = list(dicts.get_dictionary("us_cities"))
    yield original
    dicts.register_dictionary("us_cities", original)


def _engine(seed_url: str, enabled: bool = True, seed: str = "datamask") -> MaskingEngine:
    return MaskingEngine(
        MaskingConfig(
            seed=seed,
            seed_map=SeedMapConfig(enabled=enabled, url=seed_url),
        )
    )


def _plan(strategy: str = "fake_city", column: str = "company") -> ColumnPlan:
    return ColumnPlan(
        schema="public", table="customers", column=column, rule="city",
        strategy_name=strategy,
    )


# -- the core guarantee ------------------------------------------------------

def test_pair_survives_dictionary_reorder(seed_url, restore_cities):
    """Sorting a dictionary file must NOT change an already-tracked mapping."""
    engine = _engine(seed_url)
    first = engine.mask_value("Tesla", _plan())
    engine.close()

    # Someone sorts us_cities.txt — this changed the mapping before the seed map.
    dicts.register_dictionary("us_cities", sorted(restore_cities))

    engine = _engine(seed_url)
    second = engine.mask_value("Tesla", _plan())
    engine.close()

    assert first == second


def test_pair_survives_dictionary_growth(seed_url, restore_cities):
    engine = _engine(seed_url)
    first = engine.mask_value("Tesla", _plan())
    engine.close()

    dicts.register_dictionary("us_cities", restore_cities + ["Ann Arbor", "Boulder"])

    engine = _engine(seed_url)
    assert engine.mask_value("Tesla", _plan()) == first
    engine.close()


def test_pair_survives_global_seed_change(seed_url):
    """A changed masking.seed must not re-map values that are already tracked."""
    engine = _engine(seed_url, seed="datamask")
    first = engine.mask_value("Tesla", _plan())
    engine.close()

    engine = _engine(seed_url, seed="a-completely-different-seed")
    second = engine.mask_value("Tesla", _plan())
    engine.close()

    assert first == second


def test_same_value_same_mask_across_columns_and_tables(seed_url):
    """Scope is per strategy, so joins across tables stay intact."""
    engine = _engine(seed_url)
    a = engine.mask_value("Tesla", _plan(column="company"))
    b = engine.mask_value(
        "Tesla",
        ColumnPlan(schema="sales", table="orders", column="vendor",
                   rule="city", strategy_name="fake_city"),
    )
    engine.close()
    assert a == b


def test_different_values_keep_their_own_pairs(seed_url):
    engine = _engine(seed_url)
    tesla = engine.mask_value("Tesla", _plan())
    ford = engine.mask_value("Ford", _plan())
    # Re-reading must return each value's own recorded pair, not the latest one.
    assert engine.mask_value("Tesla", _plan()) == tesla
    assert engine.mask_value("Ford", _plan()) == ford
    engine.close()


# -- the disable switch ------------------------------------------------------

def test_disabled_seed_map_writes_nothing(seed_url, tmp_path, restore_cities):
    engine = _engine(seed_url, enabled=False)
    assert engine.seed_store() is None
    first = engine.mask_value("Tesla", _plan())
    engine.close()

    # No store file is created ...
    assert not (tmp_path / "seedmap.db").exists()

    # ... and the old drift behaviour is back: reordering changes the mapping.
    dicts.register_dictionary("us_cities", sorted(restore_cities))
    engine = _engine(seed_url, enabled=False)
    second = engine.mask_value("Tesla", _plan())
    engine.close()
    assert first != second, "expected recompute-only drift when tracking is off"


def test_disabled_still_masks_deterministically_within_a_run(seed_url):
    engine = _engine(seed_url, enabled=False)
    assert engine.mask_value("Tesla", _plan()) == engine.mask_value("Tesla", _plan())
    engine.close()


# -- privacy -----------------------------------------------------------------

def test_original_values_are_never_stored(seed_url):
    engine = _engine(seed_url)
    engine.mask_value("Tesla", _plan())
    engine.close()

    with SeedStore(url=seed_url) as store:
        pairs = store.all_pairs()
        assert pairs, "expected a tracked pair"
        blob = repr(pairs)
    assert "Tesla" not in blob
    assert all("original" not in key for key in pairs[0])


def test_untracked_strategies_are_not_recorded(seed_url):
    engine = _engine(seed_url)
    engine.mask_value("Tesla", _plan(strategy="redact"))
    engine.mask_value("Tesla", _plan(strategy="blank"))
    store = engine.seed_store()
    assert store is not None
    assert store.count() == 0
    engine.close()


def test_null_values_are_not_tracked(seed_url):
    """NULLs bypass the seed map — there is no pair to keep consistent.

    Note: what the *strategy* then does with a NULL is a separate, pre-existing
    concern. ``fake_city``/``fake_first_name``/``fake_last_name`` currently
    fabricate a value from a NULL (unlike ``fake_name``/``fake_email``, which
    preserve it). This test only pins the seed map's own behaviour: nothing is
    recorded either way.
    """
    engine = _engine(seed_url)
    engine.mask_value(None, _plan())
    store = engine.seed_store()
    assert store is not None and store.count() == 0
    engine.close()


# -- store mechanics ---------------------------------------------------------

def test_seed_is_stable_and_scoped(seed_url):
    with SeedStore(url=seed_url) as store:
        _, seed_a = store.fingerprint("fake_city", "Tesla")
        _, seed_b = store.fingerprint("fake_city", "Tesla")
        _, seed_other_scope = store.fingerprint("fake_name", "Tesla")
        assert seed_a == seed_b                    # stable
        assert seed_a != seed_other_scope          # namespaced per strategy


def test_record_is_idempotent(seed_url):
    with SeedStore(url=seed_url) as store:
        seed1 = store.record("fake_city", "Tesla", "Apple")
        seed2 = store.record("fake_city", "Tesla", "Ford")  # loses the race
        assert seed1 == seed2
        assert store.lookup("fake_city", "Tesla") == "Apple"  # first write wins
        assert store.count() == 1


def test_lookup_miss_returns_none(seed_url):
    with SeedStore(url=seed_url) as store:
        assert store.lookup("fake_city", "NeverSeen") is None
