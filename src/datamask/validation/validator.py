"""Validation orchestrator — runs the enabled checks and aggregates results.

It needs two connectors: ``source`` (the original database) and ``target`` (the
masked database). Tables/columns are taken from the intersection of what both
sides expose, so it is safe even if the two databases differ slightly.
"""
from __future__ import annotations

from typing import Iterable, Optional

from datamask.config import ValidationConfig
from datamask.connectors.base import Connector
from datamask.validation.masking_completeness import MaskingCompletenessValidator
from datamask.validation.result import Status, ValidationIssue, ValidationReport
from datamask.validation.row_count import RowCountValidator
from datamask.validation.schema_elements import SchemaElementValidator


class Validator:
    """Runs row-count, schema-element and masking-completeness checks."""

    def __init__(self, config: ValidationConfig):
        self.config = config
        self.row_count = RowCountValidator()
        self.schema_elements = SchemaElementValidator()
        self.masking_completeness = MaskingCompletenessValidator(config)

    def _shared_tables(self, source: Connector, target: Connector, schema: str) -> list[str]:
        tgt_tables = set(target.list_tables(schema))
        return [t for t in source.list_tables(schema) if t in tgt_tables]

    def validate(
        self,
        source: Connector,
        target: Connector,
        schemas: Optional[Iterable[str]] = None,
        sensitive_columns: Optional[Iterable[tuple[str, str, str]]] = None,
    ) -> ValidationReport:
        """Run validation.

        Parameters
        ----------
        sensitive_columns:
            Iterable of ``(schema, table, column)`` to run the masking-completeness
            check against. If ``None``, that check is skipped (the caller — the
            Runner — supplies these from history or a fresh scan).
        """
        report = ValidationReport()
        schema_list = list(schemas) if schemas is not None else target.list_schemas()

        # -- table-level checks: row counts + schema elements -----------------
        if self.config.check_row_counts or self.config.check_schema_elements:
            for schema in schema_list:
                for table in self._shared_tables(source, target, schema):
                    if self.config.check_row_counts:
                        report.add(self.row_count.validate_table(source, target, schema, table))
                    if self.config.check_schema_elements:
                        report.extend(
                            self.schema_elements.validate_table(source, target, schema, table)
                        )

        # -- column-level check: masking completeness -------------------------
        if self.config.check_masking_completeness:
            if not sensitive_columns:
                report.add(
                    ValidationIssue(
                        check="masking_completeness",
                        status=Status.SKIPPED,
                        message="No sensitive columns provided to validate.",
                    )
                )
            else:
                for schema, table, column in sensitive_columns:
                    report.add(
                        self.masking_completeness.validate_column(
                            source, target, schema, table, column
                        )
                    )

        return report
