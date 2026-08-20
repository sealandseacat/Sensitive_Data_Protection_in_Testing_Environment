"""Regression tests for applying masks to tables larger than one batch.

The engine used to stream-read a table (holding the read connection open) and
write batches back to the same table mid-stream on a second connection. On
SQLite the in-flight SELECT holds a SHARED lock, the writer's COMMIT needs an
EXCLUSIVE one, and the run died with ``sqlite3.OperationalError: database is
locked`` as soon as a table exceeded one batch. Small tables never triggered it
because their only write happened after the read finished — which is exactly
why the bug hid until the first big table.

The fix reads key-ordered pages (keyset pagination) on short-lived connections
that are closed before each write. These tests pin down both the lock fix and
the pagination correctness (page boundaries, composite keys, keyless tables).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from dbmask.config import DatabaseConfig, MaskingConfig, SeedMapConfig
from dbmask.connectors.sql import SQLConnector
from dbmask.detection.result import Decision, Sensitivity
from dbmask.masking.engine import MaskingEngine


ROWS = 650          # spans several pages ...
BATCH_SIZE = 200    # ... at this batch size (4 pages: 200/200/200/50)


def _connector(db_path: Path) -> SQLConnector:
    # Short busy timeout so a reintroduced lock fails the test in ~1s instead
    # of hanging for SQLite's 5s default.
    cfg = DatabaseConfig(
        url=f"sqlite:///{db_path}", name="paging", connect_args={"timeout": 1}
    )
    c = SQLConnector(cfg)
    c.connect()
    return c


def _engine() -> MaskingEngine:
    return MaskingEngine(
        MaskingConfig(dry_run=False, seed="paging", seed_map=SeedMapConfig(enabled=False))
    )


def _email_decision(table: str = "users") -> Decision:
    return Decision(
        database="paging", schema="main", table=table, column="email",
        sensitivity=Sensitivity.SENSITIVE, rule="email",
        source="pattern", confidence=1.0,
    )


@pytest.fixture()
def big_db(tmp_path: Path) -> Path:
    db = tmp_path / "big.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT)")
    conn.executemany(
        "INSERT INTO users (email) VALUES (?)",
        [(f"user{i}@example.com",) for i in range(ROWS)],
    )
    conn.commit()
    conn.close()
    return db


# -- the lock regression ------------------------------------------------------

def test_apply_on_table_larger_than_one_batch(big_db):
    """Masking a multi-page SQLite table must complete and mask every row."""
    connector = _connector(big_db)
    try:
        result = _engine().mask_table(
            connector, "main", "users", [_email_decision()], batch_size=BATCH_SIZE
        )
    finally:
        connector.close()

    assert result.rows_scanned == ROWS
    assert result.rows_written == ROWS

    conn = sqlite3.connect(big_db)
    leftovers = conn.execute(
        "SELECT COUNT(*) FROM users WHERE email LIKE 'user%@example.com'"
    ).fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    conn.close()
    assert total == ROWS
    assert leftovers == 0, f"{leftovers} rows kept their original email"


def test_apply_is_deterministic_across_pages(big_db):
    """The same value must mask identically no matter which page it lands on."""
    conn = sqlite3.connect(big_db)
    conn.execute("UPDATE users SET email = 'dup@example.com' WHERE id IN (1, ?)", (ROWS,))
    conn.commit()
    conn.close()

    connector = _connector(big_db)
    try:
        _engine().mask_table(
            connector, "main", "users", [_email_decision()], batch_size=BATCH_SIZE
        )
    finally:
        connector.close()

    conn = sqlite3.connect(big_db)
    first, last = (
        conn.execute("SELECT email FROM users WHERE id IN (1, ?) ORDER BY id", (ROWS,))
        .fetchall()
    )
    conn.close()
    assert first == last  # id 1 is on page one, id ROWS on the final page


# -- keyset pagination correctness ---------------------------------------------

def test_iter_pages_covers_every_row_exactly_once(big_db):
    connector = _connector(big_db)
    try:
        seen: list[int] = []
        for page in connector.iter_pages("main", "users", ["id"], batch_size=BATCH_SIZE):
            assert 0 < len(page) <= BATCH_SIZE
            seen.extend(row["id"] for row in page)
    finally:
        connector.close()

    assert len(seen) == ROWS
    assert seen == sorted(set(seen)), "pages must be disjoint and key-ordered"


def test_iter_pages_composite_key_across_page_boundary(tmp_path):
    """A page boundary inside a run of equal first-key values must not skip rows.

    This is the classic keyset mistake (``WHERE a > last_a`` alone would jump
    over the rest of the run). The expanded row-value comparison must advance
    on the second key column.
    """
    db = tmp_path / "composite.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE events (day TEXT, seq INTEGER, payload TEXT,"
        " PRIMARY KEY (day, seq))"
    )
    rows = [
        (day, seq, f"{day}-{seq}")
        for day in ("2026-01-01", "2026-01-02")
        for seq in range(5)
    ]
    conn.executemany("INSERT INTO events VALUES (?, ?, ?)", rows)
    conn.commit()
    conn.close()

    connector = _connector(db)
    try:
        seen = [
            (row["day"], row["seq"])
            for page in connector.iter_pages(
                "main", "events", ["day", "seq"], batch_size=3
            )  # boundary lands mid-run: (day1: 0,1,2) (day1: 3,4 + day2: 0) ...
            for row in page
        ]
    finally:
        connector.close()

    assert seen == [(day, seq) for day, seq, _ in rows]


def test_iter_pages_without_key_columns_raises(big_db):
    connector = _connector(big_db)
    try:
        with pytest.raises(ValueError, match="no key columns"):
            next(iter(connector.iter_pages("main", "users", [], batch_size=10)))
    finally:
        connector.close()


# -- keyless tables -------------------------------------------------------------

@pytest.fixture()
def keyless_db(tmp_path: Path) -> Path:
    db = tmp_path / "keyless.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE notes (email TEXT)")  # no primary key
    conn.executemany(
        "INSERT INTO notes (email) VALUES (?)",
        [(f"person{i}@example.com",) for i in range(5)],
    )
    conn.commit()
    conn.close()
    return db


def test_dry_run_still_works_without_a_primary_key(keyless_db):
    connector = _connector(keyless_db)
    engine = MaskingEngine(
        MaskingConfig(dry_run=True, seed="paging", seed_map=SeedMapConfig(enabled=False))
    )
    try:
        result = engine.mask_table(
            connector, "main", "notes", [_email_decision("notes")], batch_size=2
        )
    finally:
        connector.close()
    assert result.rows_scanned == 5
    assert result.rows_written == 0
    assert result.preview  # previewing must not require keys


def test_apply_without_a_primary_key_fails_cleanly(keyless_db):
    connector = _connector(keyless_db)
    try:
        with pytest.raises(ValueError, match="no key columns"):
            _engine().mask_table(
                connector, "main", "notes", [_email_decision("notes")], batch_size=2
            )
    finally:
        connector.close()
