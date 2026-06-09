"""Historical decision store — requirement #2.

Every decision the pipeline makes is persisted here. On later runs we *first*
consult history: if a column was already classified, we reuse that decision
instead of re-asking patterns/LLM. This makes results reproducible and keeps
masking consistent (the same column always gets the same rule).

The store is itself just a database (SQLite by default, but any SQLAlchemy URL
works), so it is portable and easy to inspect/audit.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Column,
    DateTime,
    Float,
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

from datamask.detection.result import Decision, Sensitivity

_metadata = MetaData()

decisions_table = Table(
    "datamask_decisions",
    _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("col_key", String(1024), nullable=False),
    Column("database", String(256)),
    Column("schema", String(256)),
    Column("table", String(256)),
    Column("column", String(256)),
    Column("sensitivity", String(32)),
    Column("rule", String(128), nullable=True),
    Column("source", String(32)),
    Column("confidence", Float),
    Column("detail", Text),
    Column("token_usage", Integer, default=0),
    Column("decided_at", DateTime),
    Column("updated_at", DateTime),
    UniqueConstraint("col_key", name="uq_datamask_col_key"),
)


class HistoryStore:
    """Read/write access to historical decisions."""

    def __init__(self, url: str = "sqlite:///datamask_history.db"):
        self.url = url
        self.engine: Optional[Engine] = None

    def connect(self) -> None:
        self.engine = create_engine(self.url)
        _metadata.create_all(self.engine)

    def close(self) -> None:
        if self.engine is not None:
            self.engine.dispose()
            self.engine = None

    def _require(self) -> Engine:
        if self.engine is None:
            raise RuntimeError("HistoryStore not connected. Call connect() first.")
        return self.engine

    # -- reads ----------------------------------------------------------------
    def get(self, database: str, schema: str, table: str, column: str) -> Optional[Decision]:
        key = f"{database}.{schema}.{table}.{column}".lower()
        stmt = select(decisions_table).where(decisions_table.c.col_key == key)
        with self._require().connect() as conn:
            row = conn.execute(stmt).mappings().first()
        if row is None:
            return None
        return Decision(
            database=row["database"],
            schema=row["schema"],
            table=row["table"],
            column=row["column"],
            sensitivity=Sensitivity(row["sensitivity"]),
            rule=row["rule"],
            source="history",
            confidence=row["confidence"] or 0.0,
            detail=f"Reused prior decision ({row['source']}): {row['detail']}",
            token_usage=0,
        )

    # -- writes ---------------------------------------------------------------
    def save(self, decision: Decision) -> None:
        now = datetime.now(timezone.utc)
        payload = {
            "col_key": decision.key,
            "database": decision.database,
            "schema": decision.schema,
            "table": decision.table,
            "column": decision.column,
            "sensitivity": decision.sensitivity.value,
            "rule": decision.rule,
            "source": decision.source,
            "confidence": decision.confidence,
            "detail": decision.detail,
            "token_usage": decision.token_usage,
            "decided_at": decision.decided_at,
            "updated_at": now,
        }
        engine = self._require()
        with engine.begin() as conn:
            existing = conn.execute(
                select(decisions_table.c.id).where(
                    decisions_table.c.col_key == decision.key
                )
            ).first()
            if existing:
                conn.execute(
                    decisions_table.update()
                    .where(decisions_table.c.col_key == decision.key)
                    .values(**payload)
                )
            else:
                conn.execute(decisions_table.insert().values(**payload))

    def all_decisions(self) -> list[Decision]:
        with self._require().connect() as conn:
            rows = conn.execute(select(decisions_table)).mappings().all()
        return [
            Decision(
                database=r["database"],
                schema=r["schema"],
                table=r["table"],
                column=r["column"],
                sensitivity=Sensitivity(r["sensitivity"]),
                rule=r["rule"],
                source=r["source"],
                confidence=r["confidence"] or 0.0,
                detail=r["detail"] or "",
                token_usage=r["token_usage"] or 0,
            )
            for r in rows
        ]

    def __enter__(self) -> "HistoryStore":
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
