"""High-level orchestration used by the CLI and as a library entry point."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from datamask.config import Config
from datamask.connectors.sql import SQLConnector
from datamask.detection.overrides import FieldOverrides
from datamask.detection.patterns import PatternMatcher
from datamask.detection.pipeline import DetectionPipeline, TokenBudgetExceeded
from datamask.detection.result import Decision
from datamask.history.store import HistoryStore
from datamask.llm.factory import create_provider
from datamask.masking.engine import MaskingEngine, TableMaskResult
from datamask.validation.result import ValidationReport
from datamask.validation.validator import Validator


@dataclass
class ScanReport:
    decisions: list[Decision] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def sensitive(self) -> list[Decision]:
        return [d for d in self.decisions if d.is_sensitive]


class Runner:
    """Wires together connector, history, detection pipeline and masking."""

    def __init__(self, config: Config):
        self.config = config
        self.connector = SQLConnector(config.database)
        self.history: Optional[HistoryStore] = (
            HistoryStore(config.history.url) if config.history.enabled else None
        )
        overrides = FieldOverrides.load(config.detection.overrides_file)
        patterns = PatternMatcher()
        llm = create_provider(config.llm)
        self.pipeline = DetectionPipeline(
            config=config,
            history=self.history,
            overrides=overrides,
            patterns=patterns,
            llm=llm,
        )
        self.masker = MaskingEngine(config.masking)

    # -- lifecycle ------------------------------------------------------------
    def open(self) -> None:
        self.connector.connect()
        if self.history is not None:
            self.history.connect()

    def close(self) -> None:
        self.connector.close()
        if self.history is not None:
            self.history.close()
        self.masker.close()

    def __enter__(self) -> "Runner":
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    # -- operations -----------------------------------------------------------
    def scan(self) -> ScanReport:
        """Classify every column in the configured schemas."""
        report = ScanReport()
        for schema in self.connector.list_schemas():
            for table in self.connector.list_tables(schema):
                if self.pipeline.should_skip_table(table):
                    continue
                for column in self.connector.list_columns(schema, table):
                    try:
                        decision = self.pipeline.analyze_column(
                            self.connector, schema, table, column
                        )
                        report.decisions.append(decision)
                    except TokenBudgetExceeded as exc:
                        report.errors.append(str(exc))
                        return report
                    except Exception as exc:  # keep going on per-column failures
                        report.errors.append(f"{schema}.{table}.{column}: {exc}")
        return report

    def mask(self, decisions: Optional[list[Decision]] = None) -> list[TableMaskResult]:
        """Mask all sensitive columns. Runs a scan first if not given decisions."""
        if decisions is None:
            decisions = self.scan().decisions

        # Group sensitive decisions by (schema, table).
        grouped: dict[tuple[str, str], list[Decision]] = {}
        for d in decisions:
            if d.is_sensitive:
                grouped.setdefault((d.schema, d.table), []).append(d)

        results: list[TableMaskResult] = []
        for (schema, table), table_decisions in grouped.items():
            results.append(
                self.masker.mask_table(self.connector, schema, table, table_decisions)
            )
        return results

    # -- validation -----------------------------------------------------------
    def _sensitive_columns(self) -> list[tuple[str, str, str]]:
        """Find sensitive columns to validate.

        Priority: explicit ``validation.columns`` -> the history store ->
        a fresh scan.
        """
        explicit = self.config.validation.columns
        if explicit:
            out: list[tuple[str, str, str]] = []
            for entry in explicit:
                parts = entry.split(".")
                if len(parts) == 3:
                    out.append((parts[0], parts[1], parts[2]))
            return out

        if self.history is not None:
            cols = [
                (d.schema, d.table, d.column)
                for d in self.history.all_decisions()
                if d.is_sensitive
            ]
            if cols:
                return cols

        # Fall back to a fresh scan of the (masked) target database.
        return [(d.schema, d.table, d.column) for d in self.scan().sensitive]

    def validate(self) -> ValidationReport:
        """Validate the masked (target) database against the source database.

        Requires ``source_database`` to be configured. Opens its own source
        connection for the duration of the call.
        """
        if not (self.config.source_database.url or self.config.source_database.dialect):
            raise ValueError(
                "validation requires 'source_database' to be configured "
                "(the original, pre-masking database)."
            )

        validator = Validator(self.config.validation)
        sensitive = (
            self._sensitive_columns()
            if self.config.validation.check_masking_completeness
            else None
        )
        schemas = self.connector.list_schemas()

        source = SQLConnector(self.config.source_database)
        source.connect()
        try:
            return validator.validate(
                source=source,
                target=self.connector,
                schemas=schemas,
                sensitive_columns=sensitive,
            )
        finally:
            source.close()

