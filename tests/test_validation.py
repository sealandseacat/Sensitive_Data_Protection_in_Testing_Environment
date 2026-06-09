"""Validation tests using two SQLite databases (source vs. target).

These prove the three checks, with special attention to the tricky row-based
masking-completeness logic (the Tesla/Apple dictionary-swap case).
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
    ValidationConfig,
)
from datamask.connectors.sql import SQLConnector
from datamask.validation.result import Status
from datamask.validation.validator import Validator


def _make(path: Path, rows):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE accounts (
            id INTEGER PRIMARY KEY,
            company TEXT,
            city TEXT
        );
        """
    )
    conn.executemany("INSERT INTO accounts (id, company, city) VALUES (?, ?, ?)", rows)
    conn.commit()
    conn.close()


@pytest.fixture()
def dbs(tmp_path: Path):
    # Source = original data.
    source_rows = [
        (1, "Tesla", "Austin"),
        (2, "Apple", "Cupertino"),
        (3, "Google", "Mountain View"),
    ]
    # Target = masked. Rows 1 & 2 are a dictionary SWAP (company values still
    # appear in the column, but each row as a whole changed) -> properly masked.
    # Row 3 was NOT masked: identical to source -> a transformation failure.
    target_rows = [
        (1, "Apple", "Austin"),    # company swapped Tesla->Apple, row differs from source row 1
        (2, "Tesla", "Cupertino"), # company swapped Apple->Tesla
        (3, "Google", "Mountain View"),  # identical to source row 3 -> UNMASKED
    ]
    src = tmp_path / "source.db"
    tgt = tmp_path / "target.db"
    _make(src, source_rows)
    _make(tgt, target_rows)
    return str(src), str(tgt)


def _validator(check_masking=True):
    cfg = ValidationConfig(
        enabled=True,
        check_row_counts=True,
        check_schema_elements=True,
        check_masking_completeness=check_masking,
        ignore_test_data=True,
    )
    return Validator(cfg)


def _run(src_path, tgt_path, sensitive=None, **kwargs):
    source = SQLConnector(DatabaseConfig(url=f"sqlite:///{src_path}", name="source"))
    target = SQLConnector(DatabaseConfig(url=f"sqlite:///{tgt_path}", name="target"))
    source.connect()
    target.connect()
    try:
        return _validator(**kwargs).validate(
            source, target, schemas=["main"], sensitive_columns=sensitive
        )
    finally:
        source.close()
        target.close()


def test_row_counts_match(dbs):
    src, tgt = dbs
    report = _run(src, tgt, sensitive=[])
    rc = [i for i in report.issues if i.check == "row_count"]
    assert rc and all(i.status == Status.PASS for i in rc)


def test_schema_elements_match(dbs):
    src, tgt = dbs
    report = _run(src, tgt, sensitive=[])
    se = [i for i in report.issues if i.check == "schema_elements"]
    assert se and all(i.status == Status.PASS for i in se)


def test_masking_completeness_detects_unmasked_row(dbs):
    src, tgt = dbs
    report = _run(src, tgt, sensitive=[("main", "accounts", "company")])
    mc = [i for i in report.issues if i.check == "masking_completeness"]
    assert len(mc) == 1
    # Row 3 (Google) is identical on both sides -> must FAIL.
    assert mc[0].status == Status.FAIL
    assert "Google" in mc[0].detail.get("sample_values", [])


def test_swap_is_not_flagged_as_unmasked(tmp_path: Path):
    # Only the swapped rows exist; no identical full row -> should PASS even
    # though 'Tesla' and 'Apple' still appear in the target column.
    _make(tmp_path / "s.db", [(1, "Tesla", "Austin"), (2, "Apple", "Cupertino")])
    _make(tmp_path / "t.db", [(1, "Apple", "Austin"), (2, "Tesla", "Cupertino")])
    report = _run(
        str(tmp_path / "s.db"),
        str(tmp_path / "t.db"),
        sensitive=[("main", "accounts", "company")],
    )
    mc = [i for i in report.issues if i.check == "masking_completeness"]
    assert mc[0].status == Status.PASS


def test_row_count_mismatch_fails(tmp_path: Path):
    _make(tmp_path / "s.db", [(1, "Tesla", "Austin"), (2, "Apple", "Cupertino")])
    _make(tmp_path / "t.db", [(1, "Apple", "Austin")])  # one row missing
    report = _run(str(tmp_path / "s.db"), str(tmp_path / "t.db"), sensitive=[])
    rc = [i for i in report.issues if i.check == "row_count"]
    assert rc[0].status == Status.FAIL
    assert not report.passed


def test_runner_validate_end_to_end(tmp_path: Path, dbs):
    src, tgt = dbs
    config = Config(
        database=DatabaseConfig(url=f"sqlite:///{tgt}", name="target"),
        source_database=DatabaseConfig(url=f"sqlite:///{src}", name="source"),
        detection=DetectionConfig(),
        history=HistoryConfig(enabled=False),
        llm=LLMConfig(enabled=False),
        masking=MaskingConfig(),
        validation=ValidationConfig(
            enabled=True,
            columns=["main.accounts.company"],
        ),
    )
    from datamask.runner import Runner

    with Runner(config) as runner:
        report = runner.validate()
    assert not report.passed  # Google row is unmasked
