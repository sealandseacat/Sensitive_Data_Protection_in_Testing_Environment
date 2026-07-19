"""Seed map — durable tracking of every (original -> masked) pair.

Why this exists
---------------
``masking.seed`` alone makes masking *deterministic*: the same input recomputes
to the same output because the RNG is derived from ``sha256(seed + value)``.
But nothing is ever *recorded*, so that consistency is only as stable as the
inputs to that computation. In practice it silently breaks:

  * reordering a dictionary file (e.g. sorting ``us_cities.txt``) changes the
    replacement every value maps to,
  * appending a new dictionary entry shifts some mappings,
  * changing ``masking.seed`` changes all of them.

Any of those de-synchronises a new run from every previous run: a customer
masked as "Apple" last month becomes "Ford" today, and referential consistency
across tables and databases is lost.

The seed map fixes that by *persisting the decision instead of recomputing it*.
The first time a value is masked, the pair is written down and assigned a
**seed** — a short, stable token that identifies that pair forever. Every later
run looks the pair up and reuses it, so the mapping survives dictionary edits,
seed changes and code upgrades.

Privacy
-------
**Original values are never stored.** The lookup key is a salted SHA-256 hash
of the original value, so the store maps ``hash(Tesla) -> "Apple"``, not
``"Tesla" -> "Apple"``. You cannot read the store to discover what a value
became — only look up a value you already hold — so it is not a reversal table.

One limit worth being clear about: by default the salt is generated once and
kept *inside the store*, which makes it stable with zero configuration but means
anyone holding the store also holds the salt. Because masked columns often draw
on small, guessable value sets (city names, company names), such a holder could
hash candidate values to test whether a specific one is present. If that matters
in your threat model, set ``masking.seed_map.salt`` to an external secret (e.g.
``${DATAMASK_SEED_SALT}``); it is then never written to disk and the store alone
cannot be tested against.

Changing the salt orphans every existing pair — they can no longer be found, and
values start mapping afresh. Choose it once, keep it with your backups.

The store is an ordinary database (SQLite by default, any SQLAlchemy URL works),
so it is easy to back up, inspect and share between environments.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    select,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

DEFAULT_SEED_MAP_URL = "sqlite:///datamask_seedmap.db"

_metadata = MetaData()

seed_map_table = Table(
    "datamask_seed_map",
    _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    # Short stable token identifying this pair — this is "the seed" for the pair.
    Column("seed", String(64), nullable=False),
    # Namespace the pair lives in (the strategy name, e.g. "fake_city").
    Column("scope", String(128), nullable=False),
    # Salted SHA-256 of the original value. The original itself is NEVER stored.
    Column("value_hash", String(64), nullable=False),
    Column("masked_value", Text),
    Column("strategy", String(64)),
    Column("created_at", DateTime),
    UniqueConstraint("scope", "value_hash", name="uq_datamask_seed_pair"),
)

# Small key/value side table. Holds the hashing salt so it stays constant for
# the life of the store — if the salt ever changed, every existing pair would
# become unfindable and masking would silently start over.
seed_meta_table = Table(
    "datamask_seed_meta",
    _metadata,
    Column("key", String(64), primary_key=True),
    Column("value", Text),
)


class SeedStore:
    """Persistent map of ``hash(original) -> masked`` pairs, keyed by a seed.

    Usage::

        store = SeedStore(url="sqlite:///seedmap.db", secret="datamask")
        store.connect()
        store.lookup("fake_city", "Tesla")            # -> None on first sight
        store.record("fake_city", "Tesla", "Apple")   # -> returns the pair seed
        store.lookup("fake_city", "Tesla")            # -> "Apple", forever
        store.close()
    """

    def __init__(
        self,
        url: str = DEFAULT_SEED_MAP_URL,
        salt: Optional[str] = None,
        cache_size: int = 100_000,
    ):
        self.url = url
        # Salt for the value hash. Deliberately NOT masking.seed: the salt has
        # to stay fixed for pairs to remain findable, and the seed is meant to
        # be changeable. When no salt is configured, a random one is generated
        # once and kept inside the store itself (see _resolve_salt).
        self._configured_salt = salt
        self.salt: str = salt or ""
        self.engine: Optional[Engine] = None
        self._cache: dict[tuple[str, str], str] = {}
        self._cache_size = cache_size

    # -- lifecycle ------------------------------------------------------------
    def connect(self) -> None:
        self.engine = create_engine(self.url)
        _metadata.create_all(self.engine)
        self.salt = self._resolve_salt()

    def _resolve_salt(self) -> str:
        """Return the salt for this store, creating and persisting one if needed.

        An explicitly configured salt always wins and is never written to disk —
        that is the mode where the salt is a real external secret and the store
        alone cannot be brute-forced. Otherwise a random salt is generated once
        and stored alongside the pairs so it survives restarts.
        """
        if self._configured_salt:
            return self._configured_salt

        engine = self._require()
        with engine.begin() as conn:
            row = conn.execute(
                select(seed_meta_table.c.value).where(seed_meta_table.c.key == "salt")
            ).first()
            if row is not None:
                return row[0]
            generated = secrets.token_hex(16)
            try:
                conn.execute(
                    seed_meta_table.insert().values(key="salt", value=generated)
                )
            except IntegrityError:  # another process created it first
                generated = conn.execute(
                    select(seed_meta_table.c.value).where(
                        seed_meta_table.c.key == "salt"
                    )
                ).scalar()
            return generated

    def close(self) -> None:
        if self.engine is not None:
            self.engine.dispose()
            self.engine = None
        self._cache.clear()

    def _require(self) -> Engine:
        if self.engine is None:
            raise RuntimeError("SeedStore not connected. Call connect() first.")
        return self.engine

    def __enter__(self) -> "SeedStore":
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    # -- identity -------------------------------------------------------------
    def fingerprint(self, scope: str, value: str) -> tuple[str, str]:
        """Return ``(value_hash, seed)`` for a value inside a scope.

        ``value_hash`` (full SHA-256) is the lookup key; ``seed`` is its short
        prefix, used as the human-quotable tracking token for the pair. Both are
        deterministic, so the same value in the same scope always fingerprints
        identically — that is what lets an existing pair be found again.
        """
        digest = hashlib.sha256(
            f"{self.salt}|{scope}|{value}".encode("utf-8")
        ).hexdigest()
        return digest, digest[:16]

    # -- reads ----------------------------------------------------------------
    def lookup(self, scope: str, value: str) -> Optional[str]:
        """Return the masked value previously assigned to ``value``, if any."""
        value_hash, _ = self.fingerprint(scope, value)
        cache_key = (scope, value_hash)
        if cache_key in self._cache:
            return self._cache[cache_key]

        stmt = select(seed_map_table.c.masked_value).where(
            seed_map_table.c.scope == scope,
            seed_map_table.c.value_hash == value_hash,
        )
        with self._require().connect() as conn:
            row = conn.execute(stmt).first()
        if row is None:
            return None

        masked = row[0]
        self._remember(cache_key, masked)
        return masked

    def count(self) -> int:
        """Total number of tracked pairs."""
        from sqlalchemy import func

        stmt = select(func.count()).select_from(seed_map_table)
        with self._require().connect() as conn:
            return int(conn.execute(stmt).scalar() or 0)

    def all_pairs(self, limit: Optional[int] = None) -> list[dict]:
        """Return tracked pairs for inspection/auditing.

        Originals are not included because they are not stored — each row shows
        the pair's ``seed``, its ``scope``/``strategy`` and the masked value.
        """
        stmt = select(seed_map_table).order_by(seed_map_table.c.id)
        if limit is not None:
            stmt = stmt.limit(limit)
        with self._require().connect() as conn:
            rows = conn.execute(stmt).mappings().all()
        return [
            {
                "seed": r["seed"],
                "scope": r["scope"],
                "strategy": r["strategy"],
                "masked_value": r["masked_value"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    # -- writes ---------------------------------------------------------------
    def record(
        self,
        scope: str,
        value: str,
        masked: str,
        strategy: Optional[str] = None,
    ) -> str:
        """Persist a new pair and return its seed.

        If the pair already exists (another process wrote it first), the stored
        masked value wins and is returned via the cache, so concurrent runs stay
        consistent with each other rather than overwriting one another.
        """
        value_hash, seed = self.fingerprint(scope, value)
        payload = {
            "seed": seed,
            "scope": scope,
            "value_hash": value_hash,
            "masked_value": masked,
            "strategy": strategy or scope,
            "created_at": datetime.now(timezone.utc),
        }
        engine = self._require()
        try:
            with engine.begin() as conn:
                conn.execute(seed_map_table.insert().values(**payload))
            self._remember((scope, value_hash), masked)
        except IntegrityError:
            # Lost a race — adopt whatever is already stored.
            existing = self._fetch(scope, value_hash)
            if existing is not None:
                self._remember((scope, value_hash), existing)
        return seed

    # -- internals ------------------------------------------------------------
    def _fetch(self, scope: str, value_hash: str) -> Optional[str]:
        stmt = select(seed_map_table.c.masked_value).where(
            seed_map_table.c.scope == scope,
            seed_map_table.c.value_hash == value_hash,
        )
        with self._require().connect() as conn:
            row = conn.execute(stmt).first()
        return None if row is None else row[0]

    def _remember(self, key: tuple[str, str], masked: str) -> None:
        """Cache a pair in memory to avoid a DB round-trip per masked row."""
        if len(self._cache) >= self._cache_size:
            self._cache.clear()  # simple bounded cache; correctness is in the DB
        self._cache[key] = masked
