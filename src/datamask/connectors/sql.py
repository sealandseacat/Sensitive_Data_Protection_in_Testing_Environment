"""SQLAlchemy-backed connector that works with every supported dialect.

Because it relies on SQLAlchemy's ``Inspector`` and the Core expression
language, the *same code path* drives PostgreSQL, MySQL/MariaDB, SQL Server,
Oracle, SQLite, and anything else with a SQLAlchemy dialect installed. This is
how we satisfy requirement #1 ("handle all types of databases") without writing
per-database SQL like the original script did.
"""
from __future__ import annotations

from typing import Iterable, Optional

from sqlalchemy import (
    MetaData,
    Table,
    create_engine,
    func,
    inspect,
    select,
    and_,
)
from sqlalchemy.engine import Engine

from datamask.config import DatabaseConfig
from datamask.connectors.base import Connector


def build_url(cfg: DatabaseConfig) -> str:
    """Build a SQLAlchemy URL from a :class:`DatabaseConfig`.

    Prefers an explicit ``url``; otherwise assembles one from the parts.
    """
    if cfg.url:
        return cfg.url
    if not cfg.dialect:
        raise ValueError("database config needs either 'url' or 'dialect'")

    driver = f"+{cfg.driver}" if cfg.driver else ""
    auth = ""
    if cfg.username:
        auth = cfg.username
        if cfg.password:
            auth += f":{cfg.password}"
        auth += "@"
    host = cfg.host or ""
    if cfg.port:
        host += f":{cfg.port}"
    db = f"/{cfg.database}" if cfg.database else ""
    return f"{cfg.dialect}{driver}://{auth}{host}{db}"


class SQLConnector(Connector):
    def __init__(self, cfg: DatabaseConfig):
        self.cfg = cfg
        self.url = build_url(cfg)
        self.name = cfg.name or cfg.database or "database"
        self.engine: Optional[Engine] = None
        self._metadata = MetaData()

    # -- lifecycle ------------------------------------------------------------
    def connect(self) -> None:
        self.engine = create_engine(self.url, connect_args=self.cfg.connect_args or {})
        # Validate the connection eagerly so failures surface early.
        with self.engine.connect():
            pass

    def close(self) -> None:
        if self.engine is not None:
            self.engine.dispose()
            self.engine = None

    def _require_engine(self) -> Engine:
        if self.engine is None:
            raise RuntimeError("Connector is not connected. Call connect() first.")
        return self.engine

    # -- introspection --------------------------------------------------------
    def list_schemas(self) -> list[str]:
        if self.cfg.schemas:
            return list(self.cfg.schemas)
        inspector = inspect(self._require_engine())
        return inspector.get_schema_names()

    def list_tables(self, schema: str) -> list[str]:
        inspector = inspect(self._require_engine())
        return inspector.get_table_names(schema=schema)

    def list_columns(self, schema: str, table: str) -> list[str]:
        inspector = inspect(self._require_engine())
        return [c["name"] for c in inspector.get_columns(table, schema=schema)]

    def primary_key_columns(self, schema: str, table: str) -> list[str]:
        inspector = inspect(self._require_engine())
        pk = inspector.get_pk_constraint(table, schema=schema)
        return list(pk.get("constrained_columns") or [])

    def _reflect(self, schema: str, table: str) -> Table:
        return Table(
            table,
            self._metadata,
            schema=schema,
            autoload_with=self._require_engine(),
            extend_existing=True,
        )

    # -- data access ----------------------------------------------------------
    def sample_values(
        self, schema: str, table: str, column: str, limit: int = 100
    ) -> list[str]:
        tbl = self._reflect(schema, table)
        col = tbl.c[column]
        stmt = (
            select(col)
            .where(col.isnot(None))
            .distinct()
            .limit(limit)
        )
        with self._require_engine().connect() as conn:
            rows = conn.execute(stmt).fetchall()
        return [str(r[0]) for r in rows if r[0] is not None]

    def iter_rows(
        self,
        schema: str,
        table: str,
        columns: Optional[list[str]] = None,
        batch_size: int = 1000,
    ) -> Iterable[dict]:
        tbl = self._reflect(schema, table)
        cols = [tbl.c[c] for c in columns] if columns else list(tbl.c)
        stmt = select(*cols)
        with self._require_engine().connect() as conn:
            result = conn.execution_options(stream_results=True).execute(stmt)
            for partition in result.partitions(batch_size):
                for row in partition:
                    yield dict(row._mapping)

    def update_rows(
        self, schema: str, table: str, key_columns: list[str], rows: list[dict]
    ) -> int:
        if not rows:
            return 0
        if not key_columns:
            raise ValueError(
                f"Cannot update {schema}.{table}: no key columns available. "
                "Provide a primary key or specify key columns explicitly."
            )
        tbl = self._reflect(schema, table)
        affected = 0
        engine = self._require_engine()
        with engine.begin() as conn:
            for row in rows:
                where = and_(*[tbl.c[k] == row[k] for k in key_columns])
                values = {k: v for k, v in row.items() if k not in key_columns}
                if not values:
                    continue
                result = conn.execute(tbl.update().where(where).values(**values))
                affected += result.rowcount or 0
        return affected

    def row_count(self, schema: str, table: str) -> int:
        tbl = self._reflect(schema, table)
        with self._require_engine().connect() as conn:
            return conn.execute(select(func.count()).select_from(tbl)).scalar_one()

    # -- validation support ---------------------------------------------------
    def distinct_values(
        self, schema: str, table: str, column: str, limit: int = 5000
    ) -> list:
        tbl = self._reflect(schema, table)
        col = tbl.c[column]
        stmt = select(col).where(col.isnot(None)).distinct().limit(limit)
        with self._require_engine().connect() as conn:
            return [r[0] for r in conn.execute(stmt).fetchall()]

    def fetch_rows_where(
        self,
        schema: str,
        table: str,
        columns: list[str],
        where_column: str,
        where_value,
        limit: int = 50,
    ) -> list[tuple]:
        tbl = self._reflect(schema, table)
        cols = [tbl.c[c] for c in columns]
        stmt = (
            select(*cols)
            .where(tbl.c[where_column] == where_value)
            .limit(limit)
        )
        with self._require_engine().connect() as conn:
            return [tuple(r) for r in conn.execute(stmt).fetchall()]

    def schema_elements(self, schema: str, table: str) -> dict:
        """Collect portable structural metadata via SQLAlchemy's inspector.

        Triggers and grants are intentionally omitted here because SQLAlchemy
        does not expose them portably across dialects. They can be added per
        dialect by overriding this method (the validator already has a slot
        for them and will simply report "not compared" when absent).
        """
        inspector = inspect(self._require_engine())

        def _norm_cols(items, key="column_names"):
            return [
                {k: (sorted(v) if isinstance(v, list) else v) for k, v in item.items()}
                for item in items
            ]

        columns = {
            c["name"]: str(c.get("type")) for c in inspector.get_columns(table, schema=schema)
        }
        pk = sorted(
            (inspector.get_pk_constraint(table, schema=schema) or {}).get(
                "constrained_columns"
            )
            or []
        )
        indexes = sorted(
            (
                (idx.get("name"), tuple(idx.get("column_names") or []), bool(idx.get("unique")))
                for idx in inspector.get_indexes(table, schema=schema)
            )
        )
        foreign_keys = sorted(
            (
                (
                    tuple(fk.get("constrained_columns") or []),
                    fk.get("referred_table"),
                    tuple(fk.get("referred_columns") or []),
                )
                for fk in inspector.get_foreign_keys(table, schema=schema)
            )
        )
        try:
            unique = sorted(
                tuple(uc.get("column_names") or [])
                for uc in inspector.get_unique_constraints(table, schema=schema)
            )
        except NotImplementedError:
            unique = []
        try:
            check = sorted(
                str(cc.get("sqltext"))
                for cc in inspector.get_check_constraints(table, schema=schema)
            )
        except NotImplementedError:
            check = []

        return {
            "columns": columns,
            "primary_key": pk,
            "indexes": indexes,
            "foreign_keys": foreign_keys,
            "unique_constraints": unique,
            "check_constraints": check,
        }

