"""End-to-end demo of datamask against a throwaway SQLite database.

Run it with:  python examples/quickstart.py

It creates a small table, scans it, shows a masking preview, then applies the
masking and prints the before/after — all locally, no external services.
"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from datamask.config import (
    Config,
    DatabaseConfig,
    DetectionConfig,
    HistoryConfig,
    LLMConfig,
    MaskingConfig,
)
from datamask.runner import Runner


def build_sample_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        DROP TABLE IF EXISTS people;
        CREATE TABLE people (
            id INTEGER PRIMARY KEY,
            full_name TEXT,
            email TEXT,
            city TEXT,
            status_code TEXT
        );
        INSERT INTO people (full_name, email, city, status_code) VALUES
            ('Mary Johnson', 'mary.johnson@example.com', 'New York', 'A'),
            ('Robert Smith', 'robert.smith@test.org',   'Chicago',  'B'),
            ('Linda Davis',  'linda.davis@mail.net',    'Dallas',   'A');
        """
    )
    conn.commit()
    conn.close()


def main() -> None:
    workdir = Path(tempfile.mkdtemp(prefix="datamask_demo_"))
    db_path = workdir / "demo.db"
    build_sample_db(db_path)

    config = Config(
        database=DatabaseConfig(url=f"sqlite:///{db_path}", name="demo"),
        detection=DetectionConfig(sample_size=10),
        history=HistoryConfig(enabled=True, url=f"sqlite:///{workdir / 'history.db'}"),
        llm=LLMConfig(enabled=False),  # patterns alone classify this sample
        masking=MaskingConfig(
            dry_run=False,
            seed="demo",
            rule_strategies={"email": "fake_email", "full_name": "fake_name", "city": "fake_city"},
        ),
    )

    print(f"Working in: {workdir}\n")

    with Runner(config) as runner:
        print("== SCAN ==")
        report = runner.scan()
        for d in report.decisions:
            flag = "SENSITIVE" if d.is_sensitive else "ok"
            rule = f" -> {d.rule}" if d.rule else ""
            print(f"  [{flag:9}] {d.table}.{d.column}{rule} ({d.source})")

        print("\n== MASK (apply) ==")
        results = runner.mask(report.decisions)
        for res in results:
            for sample in res.preview:
                print(f"  before: {sample['before']}")
                print(f"  after : {sample['after']}")

    print("\nDone. The demo database has been masked in place.")


if __name__ == "__main__":
    main()
