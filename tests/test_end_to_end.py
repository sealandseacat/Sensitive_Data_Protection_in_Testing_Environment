"""End-to-end smoke tests using an in-memory/temp SQLite database.

These run without any external database or LLM, exercising the connector,
detection pipeline, history store and masking engine together.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from datamask.config import (
    Config,
    DatabaseConfig,
    DetectionConfig,
    HistoryConfig,
    LLMConfig,
    MaskingConfig,
)
from datamask.masking.format import format_preserving_random, seeded_rng
from datamask.masking.rules import MaskContext, get_strategy
from datamask.runner import Runner


@pytest.fixture()
def sqlite_db(tmp_path: Path) -> str:
    db = tmp_path / "sample.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE customers (
            id INTEGER PRIMARY KEY,
            full_name TEXT,
            email TEXT,
            city TEXT,
            status_code TEXT
        );
        INSERT INTO customers (full_name, email, city, status_code) VALUES
            ('Mary Johnson', 'mary.johnson@example.com', 'New York', 'A'),
            ('Robert Smith', 'robert.smith@test.org', 'Chicago', 'B'),
            ('Linda Davis', 'linda.davis@mail.net', 'Dallas', 'A');
        """
    )
    conn.commit()
    conn.close()
    return str(db)


def _config(db_path: str, tmp_path: Path, dry_run: bool = True) -> Config:
    return Config(
        database=DatabaseConfig(url=f"sqlite:///{db_path}", name="sample"),
        detection=DetectionConfig(sample_size=10, use_history=True),
        history=HistoryConfig(enabled=True, url=f"sqlite:///{tmp_path / 'hist.db'}"),
        llm=LLMConfig(enabled=False),
        masking=MaskingConfig(dry_run=dry_run, seed="test"),
    )


def test_scan_detects_email_and_name(sqlite_db, tmp_path):
    with Runner(_config(sqlite_db, tmp_path)) as runner:
        report = runner.scan()
    by_col = {d.column: d for d in report.decisions}
    assert by_col["email"].is_sensitive
    assert by_col["email"].rule == "email"
    assert by_col["full_name"].is_sensitive


def test_history_reuse(sqlite_db, tmp_path):
    cfg = _config(sqlite_db, tmp_path)
    with Runner(cfg) as runner:
        runner.scan()
    # Second run should serve decisions from history.
    with Runner(cfg) as runner:
        report = runner.scan()
        assert runner.pipeline.stats.by_source.get("history", 0) > 0
    assert any(d.is_sensitive for d in report.decisions)


def test_mask_dry_run_does_not_write(sqlite_db, tmp_path):
    with Runner(_config(sqlite_db, tmp_path, dry_run=True)) as runner:
        results = runner.mask()
    assert results
    assert all(r.rows_written == 0 for r in results)
    # original data intact
    conn = sqlite3.connect(sqlite_db)
    emails = [r[0] for r in conn.execute("SELECT email FROM customers")]
    conn.close()
    assert "mary.johnson@example.com" in emails


def test_mask_apply_writes_and_is_consistent(sqlite_db, tmp_path):
    with Runner(_config(sqlite_db, tmp_path, dry_run=False)) as runner:
        runner.mask()
    conn = sqlite3.connect(sqlite_db)
    emails = [r[0] for r in conn.execute("SELECT email FROM customers")]
    conn.close()
    assert "mary.johnson@example.com" not in emails
    assert all("@" in e for e in emails)  # still email-shaped


def test_format_preserving_keeps_shape():
    rng = seeded_rng("Ab3-9z", "seed")
    out = format_preserving_random("Ab3-9z", rng)
    assert len(out) == 6
    assert out[3] == "-"          # separator preserved
    assert out[0].isupper()
    assert out[2].isdigit()


def test_consistency_same_input_same_output():
    ctx = MaskContext(column="name", rule="full_name", seed="seed")
    fn = get_strategy("fake_name")
    assert fn("Mary Johnson", ctx) == fn("Mary Johnson", ctx)


def test_null_and_blank_strategies():
    ctx = MaskContext(column="x", rule=None, seed="s")
    assert get_strategy("null")("anything", ctx) is None
    assert get_strategy("blank")("anything", ctx) == ""
